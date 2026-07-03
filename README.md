# CHIAROSCURO
### A dark pool for correlated event markets — built on Polymarket's Conditional Token Framework

> Hide the intent. Never hide the outcome.

Chiaroscuro is a private-execution layer for Polymarket positions. Orders are submitted as
sealed intents, cleared in discrete batch auctions at a uniform price, crossed peer-to-peer
where possible, and filled by a **correlated-cluster pricing engine** where not — so a large
exit never walks the public book or telegraphs its read on the event.

The name is the painting technique: the composition of light and shadow in a single image.
Lit market, dark pool, same underlying asset.

---

## Why this exists

### What Polymarket already hides — and what it doesn't

Polymarket trades are pseudonymous: wallet addresses, no names. In that narrow
sense, your *identity* is already partially concealed. But three other things
are completely exposed, and each one costs real money:

**1. Intent exposure (pre-trade).** The CLOB is public by design. When you
place a 5,000-contract sell on France, every bot on the planet sees the order
sitting in the book *before it fills*. They front-run it, copy it, and reprice
every correlated contract against you while your fill is still walking the
levels. Chiaroscuro's sealed intent book addresses this: orders enter as
encrypted commitments and are only revealed at batch clearing.

**2. Cascade exposure (during trade).** Event markets move in clusters, not
one contract at a time. A Fed decision or a World Cup match doesn't reprice
one outcome — it reprices every contract tied to the same event. Polymarket
encodes this directly: mutually exclusive outcomes are grouped under its
**Neg Risk** adapter, which means the correlation structure isn't inferred —
it's on-chain, in the market's own architecture. On a lit book, a large sell
on one outcome cascades across the entire cluster before the seller finishes
filling. Chiaroscuro's solver prices and hedges the *whole cluster at once*,
so the cascade never reaches the public book.

**3. Pattern exposure (post-trade).** Even if no one sees your single order,
Polymarket settles on-chain permanently. Over time, your wallet's trading
history reveals your strategy, your edge, your information sources. The
Columbia wash-trading paper (Sirolly, Ma, Kanoria, Sethi 2025) proved this is
tractable by tracing wallet clusters across months of on-chain data.
Whale-tracking dashboards follow known wallets in real time and copy-trade
them. Chiaroscuro v2 (ZK-proven settlement) addresses this: even post-trade,
individual orders and balances stay private — only the net settlement prints.

**Pseudonymity ≠ privacy.** Identity concealment is the part Polymarket
already provides (imperfectly — on-chain forensics and whale trackers erode
it daily). Intent and pattern concealment are the parts nobody provides yet.
That is what Chiaroscuro is for.

### How it works

Sealed-bid batch auctions (no pre-trade visibility), P2P crossing at a
uniform clearing price, and a solver that prices residual fills against the
**entire cluster's covariance** — hedging across correlated outcomes so it
can quote tighter than any single-contract market maker.

## Architecture

```
chiaroscuro/
├── backend/
│   ├── polymarket/          # Live data layer
│   │   ├── gamma.py         # Gamma API — market discovery, Neg Risk cluster grouping
│   │   ├── clob.py          # CLOB API — order books, midpoints, price history
│   │   └── fixtures.py      # Realistic offline fixtures (demo mode / no network)
│   ├── engine/              # The IP
│   │   ├── clusters.py      # Cluster construction + covariance (structural ⊗ empirical)
│   │   ├── pricing.py       # Min-variance cluster hedge → residual-risk solver quotes
│   │   └── impact.py        # Lit-book slippage: walks the real CLOB levels
│   ├── darkpool/
│   │   └── pool.py          # Sealed intents (commit–reveal v0), batch auctions, settlement tape
│   ├── api.py               # FastAPI — REST + serves the terminal frontend
│   └── main.py
├── frontend/
│   └── index.html           # The terminal (single file, no build step)
├── contracts/
│   └── ChiaroscuroSettlement.sol   # v0 on-chain settlement stub (atomic CTF/USDC swap)
├── .env.example
└── requirements.txt
```

## Quickstart

```bash
pip install -r requirements.txt
cp .env.example .env          # add keys for live mode; demo mode needs none
python -m backend.main
# open http://localhost:8420
```

**Demo mode** (default when offline or keyless): boots with realistic fixture clusters
(2026 World Cup winner Neg Risk cluster + a Fed-decision macro cluster) so every part of the
system — pricing, batching, crossing, settlement — runs end-to-end with zero setup.

**Live mode**: set `CHIAROSCURO_MODE=live` in `.env`. Market discovery and order books pull
from Polymarket's public Gamma/CLOB endpoints (reads need no auth). To *execute* fallback
routes on Polymarket's own CLOB you'll need trading credentials — see `.env.example`.

## The pricing engine (the part that matters)

For a Neg Risk cluster with outcome prices `p`, the structural covariance of the outcome
indicators is exact, not estimated:

- `Var(1ᵢ) = pᵢ(1−pᵢ)`
- `Cov(1ᵢ,1ⱼ) = −pᵢpⱼ` for mutually exclusive outcomes

We shrink empirical covariance (from CLOB price history) toward this structural prior,
then price a fill of size `q` on outcome `i` as:

```
σ²_residual = Σᵢᵢ − Σᵢ,₋ᵢ · Σ₋ᵢ,₋ᵢ⁻¹ · Σ₋ᵢ,ᵢ      (risk left AFTER the min-variance cluster hedge)
dark spread  = λ · σ_residual · √(q / depth) + fee
naive spread = λ · σᵢ         · √(q / depth)         (what a single-contract MM must charge)
```

The gap between those two spreads is the entire business.

## Roadmap (v0 → real)

Each version addresses a specific privacy layer from the threat model above:

| Stage | Solves | Privacy | Settlement |
|---|---|---|---|
| **v0 (this repo)** | Intent exposure (#1) | Commit–reveal intents, server-side batch | In-memory tape + Solidity stub |
| v1 | Intent exposure, trustlessly (#1) | Threshold encryption (no single party reads intents pre-match) | On-chain atomic CTF↔USDC settlement on Polygon |
| v2 | Intent + pattern exposure (#1+#3) | MPC matching à la Renegade / Bristol TPU emulation + ZK post-trade privacy | ZK-proven valid-match settlement — only net flow is public |

Identity exposure (#0 — "who is this wallet?") is out of scope. Polymarket
partially handles it. Tools like Tornado Cash tried to handle it fully and
drew sanctions. Chiaroscuro deliberately does not touch identity — it hides
*what you're doing*, not *who you are*.

## Honest legal note

This is research/prototype software. Operating a live matching venue for CFTC-regulated or
regulated-adjacent event contracts raises real, unresolved regulatory questions (ATS/SEF
classification, developer liability per the Tornado Cash precedent, insider-trading detection).
Before routing real funds for third parties, talk to a securities/commodities lawyer. Trading
your own account through your own pricing model is the low-exposure starting point.
