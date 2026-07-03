"""The correlated-cluster pricing engine — Hedgehog's actual IP.

A solver absorbing a fill on outcome i does NOT have to warehouse σ_i of risk.
It hedges with the rest of the cluster. The minimum-variance hedge and the risk
left after it are closed-form given the cluster covariance Σ:

    hedge  h*      = -Σ₋ᵢ,₋ᵢ⁻¹ · Σ₋ᵢ,ᵢ                (units of each sibling outcome)
    σ²_residual    = Σᵢᵢ − Σᵢ,₋ᵢ · Σ₋ᵢ,₋ᵢ⁻¹ · Σ₋ᵢ,ᵢ    (Schur complement)

The solver's quote charges for residual risk only:

    dark half-spread  = λ · σ_res  · √(q / depth) + fee
    naive half-spread = λ · σ_full · √(q / depth)

σ_res ≤ σ_full always; the gap widens exactly when the cluster is tightly
correlated — which is when lit-book trading is most dangerous. Same square-root
impact law both sides, so the comparison isolates the hedging edge itself.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np


@dataclass
class Quote:
    token_id: str
    outcome: str
    side: str                      # trader's side: "SELL" or "BUY"
    size: float
    mid: float
    dark_price: float              # solver's quoted execution price
    naive_price: float             # what a single-contract MM must quote
    dark_bps: float                # half-spread charged, bps of mid
    naive_bps: float
    sigma_full: float
    sigma_residual: float
    risk_reduction: float          # 1 − σ_res/σ_full
    hedge_legs: list = field(default_factory=list)
    fee_bps: float = 0.0
    cov_source: str = "structural"


def solver_quote(idx: int, side: str, size: float,
                 probs: np.ndarray, cov: np.ndarray,
                 outcomes: list[dict], depth: float,
                 lam: float, fee_bps: float, cov_source: str) -> Quote:
    k = cov.shape[0]
    mid = float(probs[idx])

    sigma_full = float(np.sqrt(max(cov[idx, idx], 1e-12)))

    if k > 1:
        mask = np.arange(k) != idx
        S_oo = cov[np.ix_(mask, mask)]
        S_oi = cov[mask, idx]
        w = np.linalg.solve(S_oo, S_oi)              # regression weights
        resid_var = float(cov[idx, idx] - S_oi @ w)
        hedge = -w                                    # min-variance hedge units
    else:
        resid_var = float(cov[idx, idx])
        hedge = np.array([])

    sigma_res = float(np.sqrt(max(resid_var, 1e-12)))

    depth = max(depth, 1.0)
    impact_dark = lam * sigma_res * np.sqrt(size / depth)
    impact_naive = lam * sigma_full * np.sqrt(size / depth)
    fee = mid * fee_bps / 10_000

    sgn = -1 if side.upper() == "SELL" else 1        # trader sells → fills below mid
    dark_price = float(np.clip(mid + sgn * (impact_dark + fee), 0.001, 0.999))
    naive_price = float(np.clip(mid + sgn * impact_naive, 0.001, 0.999))

    hedge_legs = []
    if k > 1:
        others = [o for j, o in enumerate(outcomes) if j != idx]
        # hedge scaled to fill size; solver takes sgn·size of i, hedges with -sgn·h
        for o, h in zip(others, hedge):
            units = float(-sgn * h * size)
            if abs(units) > size * 0.02:              # drop dust legs
                hedge_legs.append({
                    "outcome": o["outcome"],
                    "token_id": o["token_yes"],
                    "units": round(units, 1),
                    "direction": "BUY" if units > 0 else "SELL",
                })

    return Quote(
        token_id=outcomes[idx]["token_yes"],
        outcome=outcomes[idx]["outcome"],
        side=side.upper(), size=size, mid=mid,
        dark_price=round(dark_price, 4),
        naive_price=round(naive_price, 4),
        dark_bps=round(abs(dark_price - mid) / mid * 10_000, 1),
        naive_bps=round(abs(naive_price - mid) / mid * 10_000, 1),
        sigma_full=round(sigma_full, 5),
        sigma_residual=round(sigma_res, 5),
        risk_reduction=round(1 - sigma_res / sigma_full, 4) if sigma_full > 0 else 0.0,
        hedge_legs=hedge_legs,
        fee_bps=fee_bps,
        cov_source=cov_source,
    )
