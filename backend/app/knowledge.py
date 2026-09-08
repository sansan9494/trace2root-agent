from __future__ import annotations

import hashlib
import math
import re
import uuid
from collections import Counter
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .db import Document, DocumentChunk, utcnow


ALLOWED_EXTENSIONS = {".md", ".txt", ".pdf"}


def search_tokens(text: str) -> list[str]:
    """Tokenize mixed Chinese/technical text without an external tokenizer."""
    normalized = text.lower()
    technical = re.findall(r"[a-z0-9][a-z0-9_.:/-]*", normalized)
    chinese: list[str] = []
    for sequence in re.findall(r"[\u4e00-\u9fff]+", normalized):
        if len(sequence) == 1:
            chinese.append(sequence)
        else:
            chinese.extend(sequence[index : index + 2] for index in range(len(sequence) - 1))
    return technical + chinese


def bm25_scores(
    query: str,
    documents: list[dict[str, Any]],
    *,
    k1: float = 1.5,
    b: float = 0.75,
) -> dict[str, float]:
    """Return BM25 scores keyed by chunk id for a small filtered corpus."""
    query_tokens = list(dict.fromkeys(search_tokens(query)))
    if not query_tokens or not documents:
        return {}
    tokenized = [search_tokens(str(item.get("content", ""))) for item in documents]
    average_length = sum(len(tokens) for tokens in tokenized) / len(tokenized) or 1.0
    document_frequency = {
        token: sum(1 for tokens in tokenized if token in set(tokens)) for token in query_tokens
    }
    scores: dict[str, float] = {}
    total = len(documents)
    for item, tokens in zip(documents, tokenized, strict=True):
        frequencies = Counter(tokens)
        score = 0.0
        for token in query_tokens:
            frequency = frequencies[token]
            if not frequency:
                continue
            frequency_in_docs = document_frequency[token]
            inverse_frequency = math.log(1 + (total - frequency_in_docs + 0.5) / (frequency_in_docs + 0.5))
            denominator = frequency + k1 * (1 - b + b * len(tokens) / average_length)
            score += inverse_frequency * frequency * (k1 + 1) / denominator
        scores[str(item["chunk_id"])] = score
    return scores


def hybrid_fuse(
    query: str,
    vector_hits: list[dict[str, Any]],
    lexical_candidates: list[dict[str, Any]],
    *,
    top_k: int,
    rrf_k: int = 60,
    bm25_k1: float = 1.5,
    bm25_b: float = 0.75,
) -> list[dict[str, Any]]:
    """Fuse dense and sparse rankings, then apply a bounded lexical-coverage rerank."""
    candidates = {str(item["chunk_id"]): dict(item) for item in lexical_candidates}
    candidates.update({str(item["chunk_id"]): dict(item) for item in vector_hits})
    sparse_scores = bm25_scores(query, list(candidates.values()), k1=bm25_k1, b=bm25_b)
    sparse_ranking = [
        chunk_id for chunk_id, score in sorted(sparse_scores.items(), key=lambda item: item[1], reverse=True)
        if score > 0
    ][:top_k]
    fused: dict[str, float] = {}
    sources: dict[str, set[str]] = {}
    vector_scores: dict[str, float] = {}
    for rank, item in enumerate(vector_hits[:top_k], start=1):
        chunk_id = str(item["chunk_id"])
        fused[chunk_id] = fused.get(chunk_id, 0.0) + 1 / (rrf_k + rank)
        sources.setdefault(chunk_id, set()).add("vector")
        vector_scores[chunk_id] = float(item.get("score", 0.0))
    for rank, chunk_id in enumerate(sparse_ranking, start=1):
        fused[chunk_id] = fused.get(chunk_id, 0.0) + 1 / (rrf_k + rank)
        sources.setdefault(chunk_id, set()).add("bm25")

    if not fused:
        return []
    max_fused = max(fused.values()) or 1.0
    query_tokens = set(search_tokens(query))
    results: list[dict[str, Any]] = []
    for chunk_id, reciprocal_score in fused.items():
        item = candidates[chunk_id]
        document_tokens = set(search_tokens(str(item.get("content", ""))))
        coverage = len(query_tokens & document_tokens) / len(query_tokens) if query_tokens else 0.0
        rerank_score = 0.8 * reciprocal_score / max_fused + 0.2 * coverage
        item.update({
            "score": rerank_score,
            "rrf_score": reciprocal_score,
            "bm25_score": sparse_scores.get(chunk_id, 0.0),
            "vector_score": vector_scores.get(chunk_id),
            "query_coverage": coverage,
            "retrieval_sources": sorted(sources.get(chunk_id, set())),
            "retrieval_strategy": "hybrid_rrf",
        })
        results.append(item)
    return sorted(results, key=lambda item: (item["score"], item["rrf_score"]), reverse=True)[:top_k]


def diversify_hits(
    hits: list[dict[str, Any]], *, limit: int, max_chunks_per_document: int
) -> list[dict[str, Any]]:
    """Prevent one long document from consuming the complete context window."""
    selected: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for hit in hits:
        document_id = str(hit.get("document_id") or hit.get("source_file") or hit["chunk_id"])
        if counts[document_id] < max_chunks_per_document:
            selected.append(hit)
            counts[document_id] += 1
        else:
            deferred.append(hit)
        if len(selected) == limit:
            return selected
    selected.extend(deferred[: max(0, limit - len(selected))])
    return selected


class DocumentError(ValueError):
    pass


def safe_filename(filename: str) -> str:
    name = Path(filename).name
    if "/" in filename or "\\" in filename or name != filename or name in {"", ".", ".."}:
        raise DocumentError("文件名包含非法路径")
    if Path(name).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise DocumentError("仅支持 Markdown、TXT 和可提取文本的 PDF")
    return re.sub(r"[^\w.\-\u4e00-\u9fff]", "_", name)


def load_text(filename: str, content: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in {".md", ".txt"}:
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                return content.decode(encoding).strip()
            except UnicodeDecodeError:
                continue
        raise DocumentError("文本编码无法识别")
    if suffix == ".pdf":
        try:
            text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(content)).pages)
        except Exception as exc:
            raise DocumentError(f"PDF 解析失败: {exc}") from exc
        if not text.strip():
            raise DocumentError("PDF 中没有可提取文本；扫描件暂不支持")
        return text.strip()
    raise DocumentError("不支持的文件类型")


@dataclass
class TextChunk:
    content: str
    section_path: list[str]
    index: int


def chunk_text(text: str, max_chars: int = 1800, overlap_chars: int = 220) -> list[TextChunk]:
    """Structure-aware chunker; character limits approximate 800/100 Chinese tokens."""
    text = text.replace("\r\n", "\n").strip()
    if not text:
        raise DocumentError("解析后的文档为空")
    blocks = re.split(r"\n{2,}", text)
    chunks: list[TextChunk] = []
    headings: list[str] = []
    current = ""
    current_path: list[str] = []

    def emit(value: str, path: list[str]) -> None:
        value = value.strip()
        if not value:
            return
        start = 0
        while start < len(value):
            end = min(start + max_chars, len(value))
            if end < len(value):
                boundary = max(value.rfind("\n", start, end), value.rfind("。", start, end))
                if boundary > start + max_chars // 2:
                    end = boundary + 1
            part = value[start:end].strip()
            if part:
                chunks.append(TextChunk(part, path.copy(), len(chunks)))
            if end >= len(value):
                break
            start = max(end - overlap_chars, start + 1)

    for block in blocks:
        stripped = block.strip()
        match = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if match:
            emit(current, current_path)
            current = ""
            level, title = len(match.group(1)), match.group(2).strip()
            headings = headings[: level - 1] + [title]
            current_path = headings.copy()
            current = stripped
        elif len(current) + len(stripped) + 2 <= max_chars:
            current = f"{current}\n\n{stripped}".strip()
        else:
            emit(current, current_path)
            current = stripped
    emit(current, current_path)
    return chunks


class EmbeddingProvider:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._model: Any = None

    @property
    def mode(self) -> str:
        return self.settings.embedding_provider

    def _hash(self, text: str) -> list[float]:
        vector = [0.0] * self.settings.embedding_dimension
        normalized = re.sub(r"\s+", "", text.lower())
        tokens = list(normalized) + [normalized[i : i + 2] for i in range(max(0, len(normalized) - 1))]
        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % len(vector)
            vector[index] += 1.0 if digest[4] & 1 else -1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    def embed(self, texts: list[str], *, is_query: bool = False) -> list[list[float]]:
        if self.settings.embedding_provider == "sentence_transformers":
            if self._model is None:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(
                    self.settings.embedding_model,
                    device=self.settings.embedding_device,
                    cache_folder=self.settings.model_cache_dir,
                    local_files_only=self.settings.model_local_files_only,
                )
                dimension = self._model.get_embedding_dimension()
                if dimension != self.settings.embedding_dimension:
                    raise RuntimeError(
                        f"Embedding 维度不匹配：模型={dimension}，配置={self.settings.embedding_dimension}"
                    )
            encoded = texts
            if is_query:
                encoded = [self.settings.embedding_query_instruction + text for text in texts]
            return self._model.encode(
                encoded,
                normalize_embeddings=True,
                show_progress_bar=False,
            ).tolist()
        return [self._hash(text) for text in texts]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed(texts, is_query=False)

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text], is_query=True)[0]


class RerankerProvider:
    """Lazy CPU Cross-Encoder with a transparent lightweight fallback."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._model: Any = None
        self.last_error: str | None = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def rerank(self, query: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self.settings.reranker_provider != "cross_encoder" or not hits:
            return hits
        try:
            if self._model is None:
                from sentence_transformers import CrossEncoder

                self._model = CrossEncoder(
                    self.settings.reranker_model,
                    device=self.settings.reranker_device,
                    max_length=512,
                    trust_remote_code=False,
                    cache_folder=self.settings.model_cache_dir,
                    local_files_only=self.settings.model_local_files_only,
                )
            scores = self._model.predict(
                [(query, str(hit.get("content", ""))) for hit in hits],
                batch_size=self.settings.reranker_batch_size,
                show_progress_bar=False,
            )
            reranked = []
            for hit, score in zip(hits, scores, strict=True):
                item = dict(hit)
                item["pre_rerank_score"] = item.get("score")
                item["reranker_score"] = float(score)
                item["score"] = float(score)
                item["retrieval_strategy"] = f"{item.get('retrieval_strategy', 'vector')}_cross_encoder"
                reranked.append(item)
            self.last_error = None
            return sorted(reranked, key=lambda item: item["reranker_score"], reverse=True)
        except Exception as exc:
            self.last_error = str(exc)[:500]
            return [
                {**hit, "reranker_fallback": True, "reranker_error": self.last_error}
                for hit in hits
            ]


class MilvusLiteStore:
    collection = "trace2root_chunks"

    def __init__(self, settings: Settings, embeddings: EmbeddingProvider):
        self.settings = settings
        self.embeddings = embeddings
        self._client: Any = None

    @property
    def client(self):
        if self._client is None:
            from pymilvus import MilvusClient

            self._client = MilvusClient(self.settings.vector_db_uri)
            if not self._client.has_collection(self.collection):
                self._client.create_collection(
                    collection_name=self.collection,
                    dimension=self.settings.embedding_dimension,
                    metric_type="COSINE",
                    consistency_level="Strong",
                    auto_id=False,
                )
        return self._client

    def insert(self, rows: list[dict[str, Any]]) -> None:
        if rows:
            self.client.insert(collection_name=self.collection, data=rows)

    def delete_document(self, document_id: str) -> None:
        if self.client.has_collection(self.collection):
            safe = document_id.replace('"', "")
            self.client.delete(collection_name=self.collection, filter=f'document_id == "{safe}"')

    def search(
        self, query: str, *, service_name: str | None, environment: str | None, top_k: int
    ) -> list[dict[str, Any]]:
        self.client.load_collection(collection_name=self.collection)
        filters = []
        if service_name:
            filters.append(f'service_name == "{service_name.replace(chr(34), "")}"')
        if environment:
            filters.append(f'environment == "{environment.replace(chr(34), "")}"')
        result = self.client.search(
            collection_name=self.collection,
            data=[self.embeddings.embed_query(query)],
            limit=top_k,
            filter=" and ".join(filters) or "",
            output_fields=["chunk_id", "document_id", "service_name", "environment", "document_type", "title", "section_path", "content", "source_file", "chunk_index"],
        )
        hits: list[dict[str, Any]] = []
        for hit in result[0] if result else []:
            entity = dict(hit.get("entity", {}))
            entity["chunk_id"] = str(entity.get("chunk_id") or hit.get("id"))
            entity["score"] = float(hit.get("distance", 0.0))
            hits.append(entity)
        return hits

    def list_chunks(
        self, *, service_name: str | None, environment: str | None, limit: int
    ) -> list[dict[str, Any]]:
        self.client.load_collection(collection_name=self.collection)
        filters = []
        if service_name:
            filters.append(f'service_name == "{service_name.replace(chr(34), "")}"')
        if environment:
            filters.append(f'environment == "{environment.replace(chr(34), "")}"')
        rows = self.client.query(
            collection_name=self.collection,
            filter=" and ".join(filters) or "",
            limit=limit,
            output_fields=[
                "chunk_id", "document_id", "service_name", "environment", "document_type",
                "title", "section_path", "content", "source_file", "chunk_index",
            ],
        )
        return [dict(row) for row in rows]

    def hybrid_search(
        self, query: str, *, service_name: str | None, environment: str | None, top_k: int
    ) -> list[dict[str, Any]]:
        vector_hits = self.search(
            query, service_name=service_name, environment=environment, top_k=top_k
        )
        lexical_candidates = self.list_chunks(
            service_name=service_name,
            environment=environment,
            limit=self.settings.rag_candidate_pool,
        )
        return hybrid_fuse(
            query,
            vector_hits,
            lexical_candidates,
            top_k=top_k,
            rrf_k=self.settings.rag_rrf_k,
            bm25_k1=self.settings.rag_bm25_k1,
            bm25_b=self.settings.rag_bm25_b,
        )


class KnowledgeService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.embeddings = EmbeddingProvider(settings)
        self.store = MilvusLiteStore(settings, self.embeddings)
        self.reranker = RerankerProvider(settings)
        self.warmup_error: str | None = None

    def warmup(self) -> None:
        if not self.settings.model_warmup:
            return
        try:
            self.embeddings.embed_query("Trace2Root 模型预热")
            self.reranker.rerank("模型预热", [{"chunk_id": "warmup", "content": "模型预热"}])
            self.warmup_error = None
        except Exception as exc:
            self.warmup_error = f"{type(exc).__name__}: {exc}"[:500]

    async def ingest(
        self,
        db: AsyncSession,
        filename: str,
        content: bytes,
        service_name: str,
        environment: str,
        document_type: str,
        department_id: str = "ops",
    ) -> Document:
        filename = safe_filename(filename)
        if not content or len(content) > self.settings.max_upload_mb * 1024 * 1024:
            raise DocumentError("文件为空或超过大小限制")
        digest = hashlib.sha256(content).hexdigest()
        existing = await db.scalar(
            select(Document).where(
                Document.sha256 == digest,
                Document.service_name == service_name,
                Document.environment == environment,
                Document.document_type == document_type,
            )
        )
        if existing and existing.status != "deleted":
            return existing
        doc_id = str(uuid.uuid4())
        title = Path(filename).stem.replace("_", " ")
        document = Document(
            id=doc_id,
            filename=filename,
            sha256=digest,
            service_name=service_name,
            environment=environment,
            document_type=document_type,
            department_id=department_id,
            title=title,
            status="parsing",
        )
        db.add(document)
        await db.commit()
        try:
            text = load_text(filename, content)
            chunks = chunk_text(text)
            document.status = "embedding"
            await db.commit()
            vectors = self.embeddings.embed_documents([chunk.content for chunk in chunks])
            rows = []
            for chunk, vector in zip(chunks, vectors, strict=True):
                chunk_id = str(uuid.uuid4())
                db.add(DocumentChunk(id=chunk_id, document_id=doc_id, chunk_index=chunk.index, section_path=chunk.section_path, content=chunk.content))
                rows.append({
                    "id": int(uuid.UUID(chunk_id)) % (2**63 - 1),
                    "chunk_id": chunk_id,
                    "vector": vector,
                    "document_id": doc_id,
                    "service_name": service_name,
                    "environment": environment,
                    "document_type": document_type,
                    "title": title,
                    "section_path": "/".join(chunk.section_path),
                    "content": chunk.content,
                    "source_file": filename,
                    "chunk_index": chunk.index,
                    "created_at": utcnow().isoformat(),
                })
            self.store.insert(rows)
            target = Path(self.settings.upload_dir) / f"{doc_id}_{filename}"
            target.write_bytes(content)
            document.status = "indexed"
            document.chunk_count = len(chunks)
            await db.commit()
            await db.refresh(document)
            return document
        except Exception as exc:
            self.store.delete_document(doc_id)
            document.status = "failed"
            document.error = str(exc)[:1000]
            await db.commit()
            raise

    def retrieve(
        self,
        query: str,
        service_name: str | None = None,
        environment: str | None = None,
        top_k: int | None = None,
        *,
        top_n: int | None = None,
        mode: str | None = None,
        rerank: bool | None = None,
    ) -> list[dict[str, Any]]:
        candidate_count = top_k or self.settings.rag_top_k
        strategy = mode or self.settings.rag_retrieval_mode
        if strategy == "hybrid":
            hits = self.store.hybrid_search(
                query,
                service_name=service_name,
                environment=environment,
                top_k=candidate_count,
            )
        else:
            hits = self.store.search(
                query,
                service_name=service_name,
                environment=environment,
                top_k=candidate_count,
            )
            for hit in hits:
                hit.setdefault("vector_score", hit.get("score"))
                hit.setdefault("retrieval_sources", ["vector"])
                hit.setdefault("retrieval_strategy", "vector")
        should_rerank = self.settings.reranker_provider != "disabled" if rerank is None else rerank
        reranked = self.reranker.rerank(query, hits) if should_rerank else hits
        return diversify_hits(
            reranked,
            limit=top_n or self.settings.rag_top_n,
            max_chunks_per_document=self.settings.rag_max_chunks_per_document,
        )
