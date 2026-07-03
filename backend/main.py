"""Entry point: python -m backend.main"""
import uvicorn
from . import config

if __name__ == "__main__":
    uvicorn.run("backend.api:app", host=config.HOST, port=config.PORT, reload=False)
