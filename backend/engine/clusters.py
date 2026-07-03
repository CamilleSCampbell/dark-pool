"""Cluster covariance — the correlation structure the whole engine prices from.

Two sources, blended:

STRUCTURAL (exact, from market architecture): for mutually exclusive Neg Risk
outcomes with prices p, the covariance of the outcome indicators is closed-form:
    Var(1_i)      = p_i (1 - p_i)
    Cov(1_i, 1_j) = -p_i p_j            (i ≠ j)
This is not an estimate. It follows from the definition of mutual exclusivity.

EMPIRICAL (estimated, from CLOB price history): sample covariance of price
returns, which picks up co-movement the structural prior can't see (e.g. two
candidates whose fortunes track the same underlying driver).

We shrink empirical toward structural (Ledoit–Wolf-style fixed shrinkage) and
regularize, so the matrix is always well-conditioned even with short histories.
"""
from __future__ import annotations
import numpy as np


def structural_cov(probs: np.ndarray) -> np.ndarray:
    """Exact indicator covariance for mutually exclusive outcomes."""
    p = np.clip(probs, 1e-4, 1 - 1e-4)
    cov = -np.outer(p, p)
    np.fill_diagonal(cov, p * (1 - p))
    return cov


def empirical_cov(histories: list[list[float]]) -> np.ndarray | None:
    """Sample covariance of returns; None if histories too short/ragged."""
    if not histories:
        return None
    n = min(len(h) for h in histories)
    if n < 24:
        return None
    X = np.array([h[-n:] for h in histories], dtype=float)   # k × n
    rets = np.diff(X, axis=1)
    if rets.shape[1] < 12:
        return None
    return np.cov(rets)


def blended_cov(probs: np.ndarray, histories: list[list[float]] | None,
                shrink: float = 0.85) -> tuple[np.ndarray, str]:
    """shrink · structural + (1−shrink) · empirical, scaled to a common horizon.

    Empirical return covariance and structural indicator covariance live on
    different scales; we rescale structural so its average diagonal matches the
    empirical one before blending, preserving the *correlation shape* of both.
    Returns (Σ, source_label).
    """
    S = structural_cov(probs)
    E = empirical_cov(histories) if histories else None
    if E is None or E.shape != S.shape:
        return _regularize(S), "structural"
    diag_e = float(np.mean(np.diag(E)))
    diag_s = float(np.mean(np.diag(S)))
    if diag_e <= 0 or diag_s <= 0:
        return _regularize(S), "structural"
    S_scaled = S * (diag_e / diag_s)
    return _regularize(shrink * S_scaled + (1 - shrink) * E), "blended"


def _regularize(cov: np.ndarray, eps: float = 1e-8) -> np.ndarray:
    """Guarantee positive-definiteness."""
    k = cov.shape[0]
    return cov + eps * np.eye(k)


def corr_from_cov(cov: np.ndarray) -> np.ndarray:
    d = np.sqrt(np.clip(np.diag(cov), 1e-12, None))
    return np.clip(cov / np.outer(d, d), -1, 1)
