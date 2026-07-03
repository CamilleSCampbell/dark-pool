"""Hedgehog configuration — all env-driven, demo mode needs nothing."""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

MODE = os.getenv("HEDGEHOG_MODE", "auto").lower()          # demo | live | auto
HOST = os.getenv("HEDGEHOG_HOST", "0.0.0.0")
PORT = int(os.getenv("HEDGEHOG_PORT", "8420"))

BATCH_WINDOW_SECONDS = int(os.getenv("BATCH_WINDOW_SECONDS", "30"))
SOLVER_FEE_BPS = float(os.getenv("SOLVER_FEE_BPS", "8"))
IMPACT_LAMBDA = float(os.getenv("IMPACT_LAMBDA", "0.9"))

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"

POLYMARKET_API_KEY = os.getenv("POLYMARKET_API_KEY", "")
POLYMARKET_API_SECRET = os.getenv("POLYMARKET_API_SECRET", "")
POLYMARKET_API_PASSPHRASE = os.getenv("POLYMARKET_API_PASSPHRASE", "")
POLYGON_PRIVATE_KEY = os.getenv("POLYGON_PRIVATE_KEY", "")
POLYGON_RPC_URL = os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com")
