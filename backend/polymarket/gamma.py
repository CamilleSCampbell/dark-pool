"""Gamma API client — market discovery and Neg Risk cluster extraction.

Gamma is Polymarket's public, keyless discovery layer. The critical field for
Chiaroscuro is the Neg Risk grouping: mutually-exclusive outcome sets share a
negRiskMarketID, which IS the correlated cluster — read straight from the
market's own structure, no statistical inference required.
"""
from __future__ import annotations
import json
import httpx

from ..config import GAMMA_BASE

TIMEOUT = httpx.Timeout(8.0, connect=4.0)


async def fetch_events(limit: int = 40) -> list[dict]:
    """Active events sorted by volume. Each event bundles its child markets."""
    params = {
        "limit": limit,
        "active": "true",
        "closed": "false",
        "order": "volume24hr",
        "ascending": "false",
    }
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.get(f"{GAMMA_BASE}/events", params=params)
        r.raise_for_status()
        return r.json()


def _parse_json_field(raw) -> list:
    """Gamma returns some list fields as JSON-encoded strings."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return []
    return []


def extract_clusters(events: list[dict], min_outcomes: int = 3) -> list[dict]:
    """Turn Gamma events into cluster dicts Chiaroscuro can price.

    We keep events that are Neg Risk (mutually exclusive outcome sets) with at
    least `min_outcomes` live markets — below that, cluster hedging collapses
    into plain single-contract market making and the engine adds nothing.
    """
    clusters = []
    for ev in events:
        markets = ev.get("markets") or []
        neg_risk = bool(ev.get("negRisk") or ev.get("enableNegRisk"))
        live = []
        for m in markets:
            if m.get("closed"):
                continue
            prices = _parse_json_field(m.get("outcomePrices"))
            tokens = _parse_json_field(m.get("clobTokenIds"))
            try:
                p_yes = float(prices[0]) if prices else None
            except (TypeError, ValueError):
                p_yes = None
            if p_yes is None or not tokens:
                continue
            live.append({
                "market_id": m.get("id"),
                "condition_id": m.get("conditionId"),
                "question": m.get("question", ""),
                "outcome": (m.get("groupItemTitle") or m.get("question", ""))[:48],
                "p": max(0.001, min(0.999, p_yes)),
                "token_yes": str(tokens[0]),
                "volume_24h": float(m.get("volume24hr") or 0),
                "liquidity": float(m.get("liquidityNum") or m.get("liquidity") or 0),
            })
        if neg_risk and len(live) >= min_outcomes:
            live.sort(key=lambda x: -x["p"])
            clusters.append({
                "cluster_id": str(ev.get("id")),
                "slug": ev.get("slug", ""),
                "title": ev.get("title", "Untitled cluster"),
                "neg_risk": True,
                "volume_24h": float(ev.get("volume24hr") or 0),
                "outcomes": live[:12],   # cap for tractable covariance display
            })
    clusters.sort(key=lambda c: -c["volume_24h"])
    return clusters
