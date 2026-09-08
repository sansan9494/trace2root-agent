from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

TEST_ROOT = Path("/tmp/trace2root-agent-tests")
shutil.rmtree(TEST_ROOT, ignore_errors=True)
TEST_ROOT.mkdir(parents=True)
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_ROOT / 'trace2root.db'}"
os.environ.pop("MILVUS_URI", None)
os.environ["VECTOR_DB_URI"] = str(TEST_ROOT / "milvus.db")
os.environ["UPLOAD_DIR"] = str(TEST_ROOT / "uploads")
os.environ["LLM_PROVIDER"] = "deterministic"
os.environ["EMBEDDING_PROVIDER"] = "hashing"
os.environ["RERANKER_PROVIDER"] = "lightweight"

from backend.app.main import app  # noqa: E402


@pytest_asyncio.fixture(scope="session")
async def client():
    # Importing the stdio MCP SDK from a venv stored on /mnt/c can exceed its
    # five-second default on a cold WSL filesystem cache.
    async with LifespanManager(app, startup_timeout=30):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            yield http
