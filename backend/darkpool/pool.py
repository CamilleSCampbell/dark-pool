"""The pool — sealed intents, discrete batch auctions, settlement tape.

v0 privacy model: commit–reveal. The client submits SHA-256(intent ‖ salt) at
entry; plaintext is held server-side and treated as sealed until the batch
closes (the UI renders only the commitment). This gives correct MECHANICS —
batch clearing, uniform pricing, P2P crossing, solver residual fills — with a
documented upgrade path to threshold encryption (v1) and MPC matching (v2),
where no party, including this server, ever holds plaintext pre-match.

Clearing, per batch, per outcome token:
  1. Uniform clearing price = cluster-model mid (frozen at batch open)
  2. Cross buys against sells P2P at that price — zero spread, zero impact
  3. Residual imbalance goes to the solver at its cluster-hedged quote
  4. Everything prints to a public settlement tape (hide intent, never outcome)
"""
from __future__ import annotations
import hashlib
import itertools
import json
import time
import uuid
from dataclasses import dataclass, field, asdict

_seq = itertools.count(1)


@dataclass
class Intent:
    intent_id: str
    token_id: str
    outcome: str
    cluster_id: str
    side: str                  # BUY | SELL
    size: float
    limit_price: float | None
    commitment: str            # sha256 hex — the only thing "visible" pre-clear
    submitted_at: float
    status: str = "SEALED"     # SEALED → CROSSED | SOLVER_FILLED | PARTIAL | EXPIRED
    fill_price: float | None = None
    filled: float = 0.0


@dataclass
class Settlement:
    seq: int
    batch_id: str
    cluster_id: str
    token_id: str
    outcome: str
    mechanism: str             # P2P_CROSS | SOLVER
    price: float
    size: float
    ts: float
    dark_bps: float | None = None
    naive_bps: float | None = None
    saved_bps: float | None = None


class DarkPool:
    def __init__(self, window_seconds: int = 30):
        self.window = window_seconds
        self.batch_id = self._new_batch_id()
        self.batch_opened = time.time()
        self.sealed: list[Intent] = []
        self.tape: list[Settlement] = []
        self.stats = {"crossed_volume": 0.0, "solver_volume": 0.0,
                      "total_saved_bps_weighted": 0.0, "settled_notional": 0.0}

    # ── intents ────────────────────────────────────────────────────────────
    def submit(self, token_id: str, outcome: str, cluster_id: str, side: str,
               size: float, limit_price: float | None = None) -> Intent:
        salt = uuid.uuid4().hex
        payload = json.dumps({"t": token_id, "s": side, "q": size,
                              "l": limit_price, "salt": salt}, sort_keys=True)
        intent = Intent(
            intent_id=uuid.uuid4().hex[:12],
            token_id=token_id, outcome=outcome, cluster_id=cluster_id,
            side=side.upper(), size=float(size), limit_price=limit_price,
            commitment=hashlib.sha256(payload.encode()).hexdigest(),
            submitted_at=time.time(),
        )
        self.sealed.append(intent)
        return intent

    # ── batch lifecycle ────────────────────────────────────────────────────
    def seconds_remaining(self) -> float:
        return max(0.0, self.window - (time.time() - self.batch_opened))

    def maybe_clear(self, quote_fn) -> list[Settlement] | None:
        """Clear if the window has elapsed. quote_fn(token_id, side, size) → Quote."""
        if self.seconds_remaining() > 0:
            return None
        return self.clear(quote_fn)

    def clear(self, quote_fn) -> list[Settlement]:
        prints: list[Settlement] = []
        by_token: dict[str, list[Intent]] = {}
        for it in self.sealed:
            by_token.setdefault(it.token_id, []).append(it)

        for token_id, intents in by_token.items():
            buys = sorted([i for i in intents if i.side == "BUY"],
                          key=lambda i: i.submitted_at)
            sells = sorted([i for i in intents if i.side == "SELL"],
                           key=lambda i: i.submitted_at)
            ref = quote_fn(token_id, "SELL", max(1.0, sum(i.size for i in intents)))
            clearing_px = ref.mid                       # uniform price, model mid

            # 1) P2P cross at uniform price — pro-rata on the heavy side
            buy_q = sum(b.size for b in buys)
            sell_q = sum(s.size for s in sells)
            crossed = min(buy_q, sell_q)
            if crossed > 0:
                for group, total in ((buys, buy_q), (sells, sell_q)):
                    ratio = crossed / total
                    for it in group:
                        fill = it.size * ratio
                        it.filled += fill
                        it.fill_price = clearing_px
                        it.status = "CROSSED" if ratio >= 0.999 else "PARTIAL"
                prints.append(self._print(
                    intents[0], "P2P_CROSS", clearing_px, crossed,
                    dark_bps=0.0, naive_bps=ref.naive_bps))
                self.stats["crossed_volume"] += crossed

            # 2) residual imbalance → solver at cluster-hedged quote
            heavy, resid = (buys, buy_q - crossed) if buy_q > sell_q else (sells, sell_q - crossed)
            if resid > 0.5:
                side = heavy[0].side
                q = quote_fn(token_id, side, resid)
                px = q.dark_price
                for it in heavy:
                    want = it.size - it.filled
                    if want <= 0:
                        continue
                    if it.limit_price is not None:
                        ok = px >= it.limit_price if side == "SELL" else px <= it.limit_price
                        if not ok:
                            it.status = "EXPIRED" if it.filled == 0 else it.status
                            continue
                    it.filled += want
                    it.fill_price = px if it.fill_price is None else it.fill_price
                    it.status = "SOLVER_FILLED" if it.status == "SEALED" else it.status
                filled_resid = sum(
                    1 for i in heavy if i.status in ("SOLVER_FILLED", "PARTIAL", "CROSSED"))
                if filled_resid:
                    prints.append(self._print(
                        heavy[0], "SOLVER", px, resid,
                        dark_bps=q.dark_bps, naive_bps=q.naive_bps))
                    self.stats["solver_volume"] += resid

        for it in self.sealed:
            if it.status == "SEALED":
                it.status = "EXPIRED"

        self.tape = (self.tape + prints)[-400:]
        self.sealed = []
        self.batch_id = self._new_batch_id()
        self.batch_opened = time.time()
        return prints

    # ── helpers ────────────────────────────────────────────────────────────
    def _print(self, ref_intent: Intent, mech: str, px: float, size: float,
               dark_bps: float | None, naive_bps: float | None) -> Settlement:
        saved = (naive_bps - dark_bps) if (naive_bps is not None
                                           and dark_bps is not None) else None
        notional = px * size
        self.stats["settled_notional"] += notional
        if saved is not None:
            self.stats["total_saved_bps_weighted"] += saved * notional
        return Settlement(
            seq=next(_seq), batch_id=self.batch_id,
            cluster_id=ref_intent.cluster_id, token_id=ref_intent.token_id,
            outcome=ref_intent.outcome, mechanism=mech,
            price=round(px, 4), size=round(size, 1), ts=time.time(),
            dark_bps=dark_bps, naive_bps=naive_bps,
            saved_bps=round(saved, 1) if saved is not None else None,
        )

    @staticmethod
    def _new_batch_id() -> str:
        return f"BX-{uuid.uuid4().hex[:8].upper()}"

    # ── public views ───────────────────────────────────────────────────────
    def sealed_view(self) -> list[dict]:
        """What outsiders see pre-clear: commitments only. Size/side redacted."""
        return [{"commitment": i.commitment[:20] + "…",
                 "cluster_id": i.cluster_id,
                 "submitted_at": i.submitted_at} for i in self.sealed]

    def tape_view(self, n: int = 60) -> list[dict]:
        return [asdict(s) for s in self.tape[-n:]][::-1]

    def avg_saved_bps(self) -> float:
        if self.stats["settled_notional"] <= 0:
            return 0.0
        return round(self.stats["total_saved_bps_weighted"]
                     / self.stats["settled_notional"], 1)
