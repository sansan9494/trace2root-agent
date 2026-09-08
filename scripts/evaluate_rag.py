from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import statistics
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DOCUMENT_TYPES = {
    "runbook.md": "alert_runbook",
    "service.md": "service_manual",
    "incidents.md": "incident_history",
}


async def ensure_sample_index(knowledge: Any, init_db: Any, session_factory: Any) -> None:
    await init_db()
    async with session_factory() as db:
        for path in sorted((ROOT / "sample_knowledge").glob("*/*.md")):
            await knowledge.ingest(
                db,
                path.name,
                path.read_bytes(),
                path.parent.name,
                "production",
                DOCUMENT_TYPES[path.name],
            )


def rank_for_case(hits: list[dict[str, Any]], case: dict[str, Any]) -> int | None:
    for rank, hit in enumerate(hits, start=1):
        document_type = case.get("expected_document_type")
        source_file = case.get("expected_source_file")
        content_terms = case.get("expected_content_terms", [])
        content = str(hit.get("content", "")).lower()
        if document_type and hit.get("document_type") != document_type:
            continue
        if source_file and hit.get("source_file") != source_file:
            continue
        if content_terms and not all(str(term).lower() in content for term in content_terms):
            continue
        if document_type or source_file or content_terms:
            return rank
    return None


def evaluate_mode(
    knowledge: KnowledgeService,
    cases: list[dict[str, str]],
    *,
    mode: str,
    top_k: int,
) -> dict[str, Any]:
    details = []
    reciprocal_ranks = []
    discounted_gains = []
    hits_count = 0
    for case in cases:
        hits = knowledge.retrieve(
            case["query"],
            service_name=case["service_name"],
            environment="production",
            top_k=max(top_k, knowledge.settings.rag_top_k),
            top_n=top_k,
            mode=mode,
            rerank=mode == "hybrid",
        )
        rank = rank_for_case(hits, case)
        hit = rank is not None
        hits_count += int(hit)
        reciprocal_ranks.append(1 / rank if rank else 0.0)
        discounted_gains.append(1 / math.log2(rank + 1) if rank else 0.0)
        details.append({
            "id": case["id"],
            "hit": hit,
            "rank": rank,
            "top_document_types": [item.get("document_type") for item in hits],
            "top_sources": [item.get("source_file") for item in hits],
        })
    total = len(cases) or 1
    return {
        "mode": mode,
        f"recall@{top_k}": hits_count / total,
        "mrr": sum(reciprocal_ranks) / total,
        f"ndcg@{top_k}": sum(discounted_gains) / total,
        "cases": details,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate vector baseline and hybrid RAG retrieval.")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--cases", default="evaluation/rag_cases.json")
    parser.add_argument("--output", default="evaluation/results/latest.json")
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.top_k < 1:
        raise SystemExit("--top-k must be at least 1")
    if args.repeats < 1:
        raise SystemExit("--repeats must be at least 1")

    with tempfile.TemporaryDirectory(prefix="oncall-rag-eval-") as runtime:
        runtime_path = Path(runtime)
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{runtime_path / 'oncall.db'}"
        os.environ["VECTOR_DB_URI"] = str(runtime_path / "milvus.db")
        os.environ["UPLOAD_DIR"] = str(runtime_path / "uploads")

        # Import after isolating paths because the SQLAlchemy engine is built at import time.
        from backend.app.config import get_settings
        from backend.app.db import SessionLocal, init_db
        from backend.app.knowledge import KnowledgeService

        get_settings.cache_clear()
        settings = get_settings()
        knowledge = KnowledgeService(settings)
        await ensure_sample_index(knowledge, init_db, SessionLocal)
        cases_path = Path(args.cases)
        if not cases_path.is_absolute():
            cases_path = ROOT / cases_path
        cases = json.loads(cases_path.read_text(encoding="utf-8"))
        runs = []
        vector = hybrid = None
        metric_names = (f"recall@{args.top_k}", "mrr", f"ndcg@{args.top_k}")
        for run_index in range(1, args.repeats + 1):
            vector = evaluate_mode(knowledge, cases, mode="vector", top_k=args.top_k)
            hybrid = evaluate_mode(knowledge, cases, mode="hybrid", top_k=args.top_k)
            runs.append({
                "run": run_index,
                "vector_baseline": {key: vector[key] for key in metric_names},
                "hybrid_rrf": {key: hybrid[key] for key in metric_names},
            })
            print(json.dumps(runs[-1], ensure_ascii=False))
        assert vector is not None and hybrid is not None

        def repeated_stats(mode: str) -> dict[str, dict[str, float]]:
            return {
                metric: {
                    "mean": statistics.mean(run[mode][metric] for run in runs),
                    "std": statistics.pstdev(run[mode][metric] for run in runs),
                }
                for metric in metric_names
            }

        report = {
            "generated_at": datetime.now(UTC).isoformat(),
            "embedding_provider": settings.embedding_provider,
            "embedding_model": settings.embedding_model,
            "reranker_provider": settings.reranker_provider,
            "reranker_model": settings.reranker_model,
            "cases_file": str(cases_path),
            "case_count": len(cases),
            "repeat_count": args.repeats,
            "standard_deviation_type": "population",
            "runs": runs,
            "repeat_statistics": {
                "vector_baseline": repeated_stats("vector_baseline"),
                "hybrid_rrf": repeated_stats("hybrid_rrf"),
            },
            "vector_baseline": vector,
            "hybrid_rrf": hybrid,
            "improvement": {
                key: hybrid[key] - vector[key]
                for key in (f"recall@{args.top_k}", "mrr", f"ndcg@{args.top_k}")
            },
        }
        if knowledge.store._client is not None:
            knowledge.store._client.close()
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "case_count": report["case_count"],
        "repeat_count": report["repeat_count"],
        "embedding_provider": report["embedding_provider"],
        "vector_baseline": {key: value for key, value in vector.items() if key != "cases"},
        "hybrid_rrf": {key: value for key, value in hybrid.items() if key != "cases"},
        "improvement": report["improvement"],
        "repeat_statistics": report["repeat_statistics"],
        "report": str(output.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
