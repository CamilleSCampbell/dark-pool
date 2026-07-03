"""CLOB API client — order books, midpoints, price history.

Market-data reads on Polymarket's CLOB are public. Authenticated endpoints
(order placement for the lit-fallback router) are stubbed behind keys in .env —
wire py-clob-client there when you're ready to route real orders.
"""
from __future__ import annotations
import httpx

from ..config import CLOB_BASE

TIMEOUT = httpx.Timeout(8.0, connect=4.0)


async def fetch_book(token_id: str) -> dict:
    """Full order book for one outcome token: {'bids': [{price,size}], 'asks': [...]}"""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.get(f"{CLOB_BASE}/book", params={"token_id": token_id})
        r.raise_for_status()
        raw = r.json()
    return {
        "bids": [{"price": float(x["price"]), "size": float(x["size"])}
                 for x in raw.get("bids", [])],
        "asks": [{"price": float(x["price"]), "size": float(x["size"])}
                 for x in raw.get("asks", [])],
    }


async def fetch_price_history(token_id: str, interval: str = "1d",
                              fidelity: int = 30) -> list[float]:
    """Price series for covariance estimation. Returns list of prices."""
    params = {"market": token_id, "interval": interval, "fidelity": fidelity}
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.get(f"{CLOB_BASE}/prices-history", params=params)
        r.raise_for_status()
        raw = r.json()
    return [float(pt["p"]) for pt in raw.get("history", [])]
