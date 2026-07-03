"""Backtester — snapshot predictions, watch for fills, log accuracy.

Runs as a background task inside the existing FastAPI app. Every SNAPSHOT_INTERVAL
minutes it:
  1. Snapshots the order book for each outcome in each cluster
  2. Runs the pricing engine ("if someone sold SIZE here, model predicts X bps")
  3. Stores the prediction
  4. Checks recent trades for large fills that match previous snapshots
  5. Scores: was the model's predicted slippage close to reality?

Data lives in a single JSON-lines file (one JSON object per line). No database
needed. The dashboard page reads it directly.
"""
from __future__ import annotations
import asyncio
import json
import time
from pathlib import Path
from dataclasses import dataclass, asdict

import httpx

from ..config import CLOB_BASE

SNAPSHOT_INTERVAL = 300      # seconds between snapshots
TEST_SIZES = [1000, 5000, 10000]
LOG_FILE = Path("backtest_log.jsonl")
TIMEOUT = httpx.Timeout(8.0, connect=4.0)


@dataclass
class Snapshot:
    ts: float
    cluster_id: str
    cluster_title: str
    outcome: str
    token_id: str
    mid: float
    test_size: float
    predicted_lit_bps: float | None
    predicted_naive_bps: float
    predicted_dark_bps: float
    risk_reduction: float
    book_depth: float
    bid_levels: int
    ask_levels: int


@dataclass
class FillObservation:
    ts: float
    token_id: str
    outcome: str
    cluster_id: str
    side: str
    size: float
    vwap: float
    mid_at_time: float
    actual_bps: float
    matched_prediction_ts: float | None = None
    predicted_lit_bps: float | None = None
    edge_confirmed: bool | None = None


class Backtester:
    def __init__(self, make_quote_fn, clusters_fn, books_fn):
        """
        make_quote_fn(token_id, side, size) -> Quote
        clusters_fn() -> dict[str, dict]   (cluster_id -> cluster)
        books_fn() -> dict[str, dict]      (token_id -> book)
        """
        self.make_quote = make_quote_fn
        self.get_clusters = clusters_fn
        self.get_books = books_fn
        self.snapshots: list[Snapshot] = []
        self.fills: list[FillObservation] = []
        self.log_path = LOG_FILE
        self._load_history()

    def _load_history(self):
        if not self.log_path.exists():
            return
        try:
            for line in self.log_path.read_text().strip().split("\n"):
                if not line:
                    continue
                obj = json.loads(line)
                if obj.get("type") == "snapshot":
                    self.snapshots.append(Snapshot(**{k: v for k, v in obj.items() if k != "type"}))
                elif obj.get("type") == "fill":
                    self.fills.append(FillObservation(**{k: v for k, v in obj.items() if k != "type"}))
        except Exception:
            pass

    def _log(self, record_type: str, obj):
        d = asdict(obj) if hasattr(obj, '__dataclass_fields__') else obj
        d["type"] = record_type
        with open(self.log_path, "a") as f:
            f.write(json.dumps(d) + "\n")

    async def snapshot_all(self):
        clusters = self.get_clusters()
        books = self.get_books()
        now = time.time()
        new_snaps = []

        for cid, c in clusters.items():
            for o in c["outcomes"]:
                tok = o["token_yes"]
                book = books.get(tok, {"bids": [], "asks": []})
                depth = sum(l["size"] for l in book.get("bids", [])) + \
                        sum(l["size"] for l in book.get("asks", []))

                for sz in TEST_SIZES:
                    try:
                        q = self.make_quote(tok, "SELL", sz)
                    except Exception:
                        continue

                    from ..engine.impact import lit_slippage_bps
                    lit = lit_slippage_bps(book, "SELL", sz, q.mid)

                    snap = Snapshot(
                        ts=now, cluster_id=cid, cluster_title=c["title"],
                        outcome=o["outcome"], token_id=tok, mid=q.mid,
                        test_size=sz,
                        predicted_lit_bps=lit.get("slippage_bps"),
                        predicted_naive_bps=q.naive_bps,
                        predicted_dark_bps=q.dark_bps,
                        risk_reduction=q.risk_reduction,
                        book_depth=depth,
                        bid_levels=len(book.get("bids", [])),
                        ask_levels=len(book.get("asks", [])),
                    )
                    new_snaps.append(snap)
                    self._log("snapshot", snap)

        self.snapshots.extend(new_snaps)
        # keep last 48 hours
        cutoff = now - 48 * 3600
        self.snapshots = [s for s in self.snapshots if s.ts > cutoff]
        return len(new_snaps)

    async def check_fills(self):
        """Check CLOB trade history for large fills matching our snapshots."""
        clusters = self.get_clusters()
        now = time.time()
        new_fills = []

        for cid, c in clusters.items():
            for o in c["outcomes"]:
                tok = o["token_yes"]
                try:
                    trades = await self._fetch_trades(tok)
                except Exception:
                    continue

                for t in trades:
                    size = float(t.get("size", 0))
                    if size < 500:      # only care about large fills
                        continue
                    price = float(t.get("price", 0))
                    side = t.get("side", "").upper()
                    trade_ts = float(t.get("timestamp", now))

                    # find nearest prior snapshot for this token
                    matching = [s for s in self.snapshots
                                if s.token_id == tok
                                and s.ts < trade_ts
                                and trade_ts - s.ts < SNAPSHOT_INTERVAL * 2
                                and abs(s.test_size - size) / max(size, 1) < 1.5]

                    nearest = min(matching, key=lambda s: abs(s.test_size - size),
                                  default=None)

                    if nearest and nearest.mid > 0:
                        actual_bps = abs(price - nearest.mid) / nearest.mid * 10000
                        fill = FillObservation(
                            ts=trade_ts, token_id=tok, outcome=o["outcome"],
                            cluster_id=cid, side=side, size=size,
                            vwap=price, mid_at_time=nearest.mid,
                            actual_bps=round(actual_bps, 1),
                            matched_prediction_ts=nearest.ts,
                            predicted_lit_bps=nearest.predicted_lit_bps,
                            edge_confirmed=(actual_bps > nearest.predicted_dark_bps * 1.2
                                            if nearest.predicted_lit_bps else None),
                        )
                        new_fills.append(fill)
                        self._log("fill", fill)

        self.fills.extend(new_fills)
        cutoff = now - 48 * 3600
        self.fills = [f for f in self.fills if f.ts > cutoff]
        return len(new_fills)

    async def _fetch_trades(self, token_id: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(f"{CLOB_BASE}/trades",
                                 params={"asset_id": token_id, "limit": 50})
            r.raise_for_status()
            return r.json()

    # ── summary for the dashboard ──────────────────────────────────────
    def summary(self) -> dict:
        now = time.time()
        recent_snaps = [s for s in self.snapshots if now - s.ts < 24 * 3600]
        recent_fills = [f for f in self.fills if now - f.ts < 24 * 3600]
        confirmed = [f for f in recent_fills if f.edge_confirmed is True]
        denied = [f for f in recent_fills if f.edge_confirmed is False]

        by_cluster: dict[str, list] = {}
        for s in recent_snaps:
            by_cluster.setdefault(s.cluster_id, []).append(s)

        cluster_summaries = []
        for cid, snaps in by_cluster.items():
            at_5k = [s for s in snaps if s.test_size == 5000]
            if at_5k:
                avg_lit = sum(s.predicted_lit_bps or 0 for s in at_5k) / len(at_5k)
                avg_dark = sum(s.predicted_dark_bps for s in at_5k) / len(at_5k)
                avg_rr = sum(s.risk_reduction for s in at_5k) / len(at_5k)
                cluster_summaries.append({
                    "cluster_id": cid,
                    "title": at_5k[0].cluster_title,
                    "avg_lit_bps": round(avg_lit, 1),
                    "avg_dark_bps": round(avg_dark, 1),
                    "avg_risk_reduction": round(avg_rr, 3),
                    "edge_bps": round(avg_lit - avg_dark, 1),
                    "snapshots": len(at_5k),
                })

        return {
            "total_snapshots_24h": len(recent_snaps),
            "total_fills_observed": len(recent_fills),
            "edge_confirmed": len(confirmed),
            "edge_denied": len(denied),
            "hit_rate": (round(len(confirmed) / (len(confirmed) + len(denied)), 2)
                         if (confirmed or denied) else None),
            "clusters": cluster_summaries,
            "recent_snapshots": [asdict(s) for s in recent_snaps[-20:]],
            "recent_fills": [asdict(f) for f in recent_fills[-20:]],
        }


async def backtest_loop(bt: Backtester):
    """Background loop — runs inside the FastAPI lifespan."""
    while True:
        try:
            await bt.snapshot_all()
            await bt.check_fills()
        except Exception:
            pass
        await asyncio.sleep(SNAPSHOT_INTERVAL)
