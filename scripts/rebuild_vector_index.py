from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict

from sqlalchemy import select

from backend.app.config import get_settings
from backend.app.db import Document, DocumentChunk, SessionLocal, init_db, utcnow
from backend.app.knowledge import KnowledgeService


async def main() -> None:
    settings = get_settings()
    await init_db()
    knowledge = KnowledgeService(settings)
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(DocumentChunk, Document)
                .join(Document, Document.id == DocumentChunk.document_id)
                .where(Document.status == "indexed")
                .order_by(Document.id, DocumentChunk.chunk_index)
            )
        ).all()

    by_document: dict[str, list[tuple[DocumentChunk, Document]]] = defaultdict(list)
    for chunk, document in rows:
        by_document[document.id].append((chunk, document))

    indexed = 0
    for document_id, document_rows in by_document.items():
        knowledge.store.delete_document(document_id)
        vectors = knowledge.embeddings.embed_documents([chunk.content for chunk, _ in document_rows])
        payload = []
        for (chunk, document), vector in zip(document_rows, vectors, strict=True):
            payload.append({
                "id": int(uuid.UUID(chunk.id)) % (2**63 - 1),
                "chunk_id": chunk.id,
                "vector": vector,
                "document_id": document.id,
                "service_name": document.service_name,
                "environment": document.environment,
                "document_type": document.document_type,
                "title": document.title,
                "section_path": "/".join(chunk.section_path),
                "content": chunk.content,
                "source_file": document.filename,
                "chunk_index": chunk.chunk_index,
                "created_at": utcnow().isoformat(),
            })
        knowledge.store.insert(payload)
        indexed += len(payload)
        print(f"indexed {document.filename}: {len(payload)} chunks")

    if knowledge.store._client is not None:
        knowledge.store._client.close()
    print(
        f"done: {indexed} chunks, embedding={settings.embedding_model}, "
        f"dimension={settings.embedding_dimension}, database={settings.vector_db_uri}"
    )


if __name__ == "__main__":
    asyncio.run(main())
