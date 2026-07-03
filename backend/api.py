"""Hedgehog API — REST layer + terminal frontend host.

Boot sequence: try live Polymarket (mode auto/live), fall back to fixtures.
State is in-memory; a background task clears batches on the window cadence.
"""
from __future__ import annotations
import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import config
from .polymarket import gamma, clob, fixtures
from .engine.clusters import blended_cov, corr_from_cov
from .engine.pricing import solver_quote
from .engine.impact import lit_slippage_bps
from .darkpool.pool import DarkPool

FRONTEND = Path(__file__).parent.parent / "frontend" / "index.html"


class State:
    def __init__(self):
        self.mode = "demo"
        self.clusters: dict[str, dict] = {}
        self.cov: dict[str, np.ndarray] = {}
        self.cov_source: dict[str, str] = {}
        self.books: dict[str, dict] = {}
        self.token_index: dict[str, tuple[str, int]] = {}   # token → (cluster_id, idx)
        self.pool = DarkPool(config.BATCH_WINDOW_SECONDS)
        self.boot_ts = time.time()


S = State()


# ── data loading ────────────────────────────────────────────────────────────
async def load_live() -> bool:
    try:
        events = await gamma.fetch_events(limit=40)
        clusters = gamma.extract_clusters(events)
        if not clusters:
            return False
        for c in clusters[:6]:                       # top clusters by volume
            histories = []
            for o in c["outcomes"]:
                try:
                    h = await clob.fetch_price_history(o["token_yes"])
                except Exception:
                    h = []
                histories.append(h)
                try:
                    S.books[o["token_yes"]] = await clob.fetch_book(o["token_yes"])
                except Exception:
                    S.books[o["token_yes"]] = {"bids": [], "asks": []}
            _register(c, histories)
        S.mode = "live"
        return True
    except Exception:
        return False


def load_fixtures():
    for c in fixtures.fixture_clusters():
        histories = [fixtures.fixture_history(o["token_yes"]) for o in c["outcomes"]]
        for o in c["outcomes"]:
            S.books[o["token_yes"]] = fixtures.fixture_book(o["token_yes"])
        _register(c, histories)
    S.mode = "demo"


def _register(c: dict, histories: list[list[float]]):
    cid = c["cluster_id"]
    probs = np.array([o["p"] for o in c["outcomes"]])
    cov, src = blended_cov(probs, histories)
    S.clusters[cid] = c
    S.cov[cid] = cov
    S.cov_source[cid] = src
    for i, o in enumerate(c["outcomes"]):
        S.token_index[o["token_yes"]] = (cid, i)


# ── quoting ─────────────────────────────────────────────────────────────────
def make_quote(token_id: str, side: str, size: float):
    if token_id not in S.token_index:
        raise KeyError(token_id)
    cid, idx = S.token_index[token_id]
    c = S.clusters[cid]
    probs = np.array([o["p"] for o in c["outcomes"]])
    book = S.books.get(token_id, {"bids": [], "asks": []})
    depth = sum(l["size"] for l in book["bids"]) + sum(l["size"] for l in book["asks"])
    depth = max(depth, c["outcomes"][idx].get("liquidity", 0) / 50, 100)
    return solver_quote(idx, side, size, probs, S.cov[cid], c["outcomes"],
                        depth, config.IMPACT_LAMBDA, config.SOLVER_FEE_BPS,
                        S.cov_source[cid])


# ── app ─────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    ok = False
    if config.MODE in ("live", "auto"):
        ok = await load_live()
    if not ok:
        load_fixtures()
    task = asyncio.create_task(_batch_loop())
    # backtester
    from .engine.backtester import Backtester, backtest_loop
    S.backtester = Backtester(make_quote, lambda: S.clusters, lambda: S.books)
    bt_task = asyncio.create_task(backtest_loop(S.backtester))
    yield
    task.cancel()
    bt_task.cancel()


async def _batch_loop():
    while True:
        await asyncio.sleep(1.0)
        try:
            S.pool.maybe_clear(lambda t, s, q: make_quote(t, s, q))
        except Exception:
            pass


app = FastAPI(title="Hedgehog", lifespan=lifespan)


@app.get("/")
async def root():
    return FileResponse(FRONTEND)


@app.get("/api/status")
async def status():
    return {"mode": S.mode, "clusters": len(S.clusters),
            "batch_id": S.pool.batch_id,
            "batch_seconds_remaining": round(S.pool.seconds_remaining(), 1),
            "batch_window": S.pool.window,
            "sealed_intents": len(S.pool.sealed),
            "avg_saved_bps": S.pool.avg_saved_bps(),
            "stats": S.pool.stats,
            "uptime_s": round(time.time() - S.boot_ts)}


@app.get("/api/clusters")
async def clusters():
    out = []
    for cid, c in S.clusters.items():
        out.append({"cluster_id": cid, "title": c["title"],
                    "volume_24h": c["volume_24h"], "neg_risk": c["neg_risk"],
                    "n_outcomes": len(c["outcomes"]),
                    "cov_source": S.cov_source[cid],
                    "top": [{"outcome": o["outcome"], "p": o["p"]}
                            for o in c["outcomes"][:4]]})
    return out


@app.get("/api/cluster/{cid}")
async def cluster_detail(cid: str):
    if cid not in S.clusters:
        raise HTTPException(404, "unknown cluster")
    c = S.clusters[cid]
    corr = corr_from_cov(S.cov[cid])
    return {**c, "cov_source": S.cov_source[cid],
            "corr": [[round(float(x), 3) for x in row] for row in corr]}


class QuoteReq(BaseModel):
    token_id: str
    side: str
    size: float


@app.post("/api/quote")
async def quote(req: QuoteReq):
    try:
        q = make_quote(req.token_id, req.side, req.size)
    except KeyError:
        raise HTTPException(404, "unknown token")
    book = S.books.get(req.token_id, {"bids": [], "asks": []})
    lit = lit_slippage_bps(book, req.side, req.size, q.mid)
    return {"quote": q.__dict__, "lit": lit,
            "edge_vs_lit_bps": (round(lit["slippage_bps"] - q.dark_bps, 1)
                                if lit["slippage_bps"] is not None else None)}


class IntentReq(BaseModel):
    token_id: str
    side: str
    size: float
    limit_price: float | None = None


@app.post("/api/intent")
async def intent(req: IntentReq):
    if req.token_id not in S.token_index:
        raise HTTPException(404, "unknown token")
    cid, idx = S.token_index[req.token_id]
    o = S.clusters[cid]["outcomes"][idx]
    it = S.pool.submit(req.token_id, o["outcome"], cid, req.side,
                       req.size, req.limit_price)
    return {"intent_id": it.intent_id, "commitment": it.commitment,
            "batch_id": S.pool.batch_id,
            "clears_in_s": round(S.pool.seconds_remaining(), 1)}


@app.get("/api/pool")
async def pool():
    return {"batch_id": S.pool.batch_id,
            "seconds_remaining": round(S.pool.seconds_remaining(), 1),
            "window": S.pool.window,
            "sealed": S.pool.sealed_view(),
            "tape": S.pool.tape_view(),
            "avg_saved_bps": S.pool.avg_saved_bps(),
            "stats": S.pool.stats}


@app.post("/api/batch/clear")
async def force_clear():
    prints = S.pool.clear(lambda t, s, q: make_quote(t, s, q))
    return {"cleared": len(prints), "prints": [p.__dict__ for p in prints]}


@app.get("/api/book/{token_id}")
async def book(token_id: str):
    if token_id not in S.books:
        raise HTTPException(404, "unknown token")
    return S.books[token_id]


@app.get("/api/backtest")
async def backtest_summary():
    if not hasattr(S, 'backtester'):
        return {"error": "backtester not initialized"}
    return S.backtester.summary()


DASHBOARD = Path(__file__).parent.parent / "frontend" / "dashboard.html"

@app.get("/dashboard")
async def dashboard():
    return FileResponse(DASHBOARD)
