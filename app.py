"""Vercel entrypoint — re-exports the FastAPI app."""
from backend.api import app  # noqa: F401
