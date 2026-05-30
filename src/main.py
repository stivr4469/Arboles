from fastapi import FastAPI
from src.core.config import settings

app = FastAPI(
    title="Ad-Pilot API",
    version="0.1.0",
    debug=settings.debug,
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": "0.1.0"}


@app.get("/")
async def root() -> dict:
    return {"service": "Ad-Pilot", "docs": "/docs"}
