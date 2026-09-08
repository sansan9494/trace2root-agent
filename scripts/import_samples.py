from __future__ import annotations

import asyncio
from pathlib import Path

from backend.app.config import get_settings
from backend.app.db import SessionLocal, init_db
from backend.app.knowledge import KnowledgeService


async def main() -> None:
    await init_db()
    knowledge = KnowledgeService(get_settings())
    root = Path(__file__).resolve().parents[1] / "sample_knowledge"
    mapping = {"runbook.md": "alert_runbook", "service.md": "service_manual", "incidents.md": "incident_history"}
    async with SessionLocal() as db:
        for path in sorted(root.glob("*/*.md")):
            document = await knowledge.ingest(db, path.name, path.read_bytes(), path.parent.name, "production", mapping[path.name])
            print(f"{path.parent.name}/{path.name}: {document.status}, chunks={document.chunk_count}")


if __name__ == "__main__":
    asyncio.run(main())
