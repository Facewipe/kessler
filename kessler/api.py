"""FastAPI application exposing the kessler conjunction screening API."""

from fastapi import FastAPI

app = FastAPI(title="kessler", description="Satellite conjunction screening API")


@app.get("/health")
async def health() -> dict[str, str]:
    """Return service health status."""
    return {"status": "ok"}
