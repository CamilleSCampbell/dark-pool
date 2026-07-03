"""Lit-book execution simulation — walk the actual CLOB levels for size q.

This is the honest baseline the dark quote competes against: not a model, the
literal volume-weighted price you'd get sweeping the visible book right now.
"""
from __future__ import annotations


def walk_book(book: dict, side: str, size: float) -> dict:
    """Sweep the book. side is the TRADER's side (SELL hits bids, BUY lifts asks)."""
    levels = book["bids"] if side.upper() == "SELL" else book["asks"]
    if not levels:
        return {"filled": 0.0, "vwap": None, "levels_used": 0,
                "exhausted": True, "worst_price": None}

    remaining = size
    notional = 0.0
    used = 0
    worst = levels[0]["price"]
    for lvl in levels:
        if remaining <= 0:
            break
        take = min(remaining, lvl["size"])
        notional += take * lvl["price"]
        remaining -= take
        worst = lvl["price"]
        used += 1

    filled = size - remaining
    return {
        "filled": round(filled, 2),
        "vwap": round(notional / filled, 4) if filled > 0 else None,
        "levels_used": used,
        "exhausted": remaining > 0,
        "unfilled": round(remaining, 2),
        "worst_price": worst,
        "best_price": levels[0]["price"],
    }


def lit_slippage_bps(book: dict, side: str, size: float, mid: float) -> dict:
    res = walk_book(book, side, size)
    if res["vwap"] is None or mid <= 0:
        return {**res, "slippage_bps": None}
    slip = abs(res["vwap"] - mid) / mid * 10_000
    # unfilled remainder is real cost too — penalize at worst price + 25%
    if res["exhausted"] and res["filled"] > 0:
        slip *= 1 + 0.25 * (res["unfilled"] / size)
    return {**res, "slippage_bps": round(slip, 1)}
