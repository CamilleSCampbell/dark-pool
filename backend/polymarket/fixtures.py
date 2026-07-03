"""Offline fixtures — realistic clusters so the whole system runs with no network.

Two clusters mirroring what live mode returns from Gamma:
  1. 2026 World Cup winner (Neg Risk, mutually exclusive) — Kalshi/Polymarket's
     actual dominant volume category right now.
  2. September 2026 Fed decision (Neg Risk) — the macro-cluster case the
     pricing engine was originally designed around.

Books and price history are synthetic but shaped like real CLOB data:
geometric depth decay away from mid, ~1–2% spreads, correlated random-walk
histories consistent with mutually exclusive outcomes.
"""
from __future__ import annotations
import math
import random

_rng = random.Random(2026)


def _synth_book(p: float, depth_scale: float) -> dict:
    """Geometric-decay book around mid price p."""
    tick = 0.01 if p > 0.05 else 0.001
    half_spread = max(tick, p * 0.012)
    bids, asks = [], []
    for lvl in range(8):
        decay = math.exp(-0.42 * lvl)
        px_b = round(max(tick, p - half_spread - lvl * tick), 3)
        px_a = round(min(1 - tick, p + half_spread + lvl * tick), 3)
        size = round(depth_scale * decay * _rng.uniform(0.75, 1.3), 1)
        bids.append({"price": px_b, "size": size})
        asks.append({"price": px_a, "size": size * _rng.uniform(0.85, 1.15)})
    return {"bids": bids, "asks": asks}


def _synth_histories(prices: list[float], n: int = 240) -> list[list[float]]:
    """Correlated random walks that respect sum≈1 (mutually exclusive)."""
    k = len(prices)
    cur = list(prices)
    out = [[] for _ in range(k)]
    for _ in range(n):
        # common shock hits the favorite one way, spills into others opposite
        shock = _rng.gauss(0, 0.004)
        idio = [_rng.gauss(0, 0.003) for _ in range(k)]
        for i in range(k):
            drift = shock * (1.6 if i == 0 else -1.6 * cur[i] / max(1e-6, 1 - cur[0]))
            cur[i] = min(0.995, max(0.005, cur[i] + drift + idio[i]))
        s = sum(cur)
        cur = [c / s for c in cur]                      # renormalize to sum 1
        for i in range(k):
            out[i].append(cur[i])
    return out


def _build(cluster_id, title, slug, names, probs, vol, depth):
    outcomes, books, hist = [], {}, {}
    series = _synth_histories(probs)
    for i, (name, p) in enumerate(zip(names, probs)):
        tok = f"fixture-{cluster_id}-{i}"
        outcomes.append({
            "market_id": f"m-{cluster_id}-{i}",
            "condition_id": f"c-{cluster_id}-{i}",
            "question": f"{title}: {name}?",
            "outcome": name,
            "p": p,
            "token_yes": tok,
            "volume_24h": vol * p * _rng.uniform(0.6, 1.4),
            "liquidity": depth * 100,
        })
        books[tok] = _synth_book(p, depth * (0.5 + p))
        hist[tok] = series[i]
    return {
        "cluster": {
            "cluster_id": cluster_id, "slug": slug, "title": title,
            "neg_risk": True, "volume_24h": vol, "outcomes": outcomes,
        },
        "books": books, "histories": hist,
    }


_WC = _build(
    "wc26", "2026 FIFA World Cup Winner", "fifa-world-cup-2026-winner",
    ["France", "Spain", "England", "Brazil", "Argentina", "Portugal", "Germany", "Netherlands"],
    [0.35, 0.16, 0.12, 0.10, 0.09, 0.07, 0.05, 0.03],
    vol=48_500_000, depth=900,
)

_FED = _build(
    "fed0926", "Fed Decision — September 2026", "fed-decision-september-2026",
    ["Cut 25 bps", "Hold", "Cut 50 bps", "Hike 25 bps"],
    [0.44, 0.33, 0.17, 0.04],
    vol=6_200_000, depth=520,
)

_FIXTURES = {f["cluster"]["cluster_id"]: f for f in (_WC, _FED)}


def fixture_clusters() -> list[dict]:
    return [f["cluster"] for f in _FIXTURES.values()]


def fixture_book(token_id: str) -> dict:
    for f in _FIXTURES.values():
        if token_id in f["books"]:
            return f["books"][token_id]
    raise KeyError(token_id)


def fixture_history(token_id: str) -> list[float]:
    for f in _FIXTURES.values():
        if token_id in f["histories"]:
            return f["histories"][token_id]
    raise KeyError(token_id)
