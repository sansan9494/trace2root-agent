from __future__ import annotations

import argparse
import asyncio
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from backend.app.config import get_settings
from backend.app.db import init_db
from backend.app.knowledge import KnowledgeService
from backend.app.llm import ChatModel

ROOT = Path(__file__).resolve().parents[1]


async def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate generated RAG answers with local Ollama and RAGAS.")
    parser.add_argument("--max-cases", type=int, default=3)
    parser.add_argument("--output", default="evaluation/results/latest_ragas.json")
    args = parser.parse_args()
    settings = get_settings()
    if settings.llm_provider != "ollama":
        raise SystemExit("RAGAS 评测要求 LLM_PROVIDER=ollama")

    await init_db()
    answer_model = ChatModel(settings)
    if not await answer_model.available():
        raise SystemExit(f"Ollama 或模型 {settings.ollama_model} 不可用")

    # Deliberately imported only by this optional script: normal API startup is not
    # affected by RAGAS' large evaluation dependency tree.
    from ragas.embeddings import HuggingFaceEmbeddings
    from ragas.llms.base import InstructorBaseRagasLLM
    from ragas.metrics.collections import AnswerRelevancy, ContextPrecision, ContextRecall, Faithfulness

    class NativeOllamaJudge(InstructorBaseRagasLLM):
        """RAGAS structured-output adapter using Ollama's native JSON Schema API."""

        def generate(self, prompt, response_model):  # pragma: no cover - RAGAS uses async
            raise RuntimeError("本评测只支持异步调用")

        async def agenerate(self, prompt, response_model):
            payload = {
                "model": settings.ollama_model,
                "prompt": "只输出符合 JSON Schema 的结果，不要解释。\n" + prompt,
                "stream": False,
                "think": False,
                "format": response_model.model_json_schema(),
                "options": {"temperature": 0.0, "num_ctx": 8192, "num_predict": 2048},
            }
            async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds * 2) as http:
                response = await http.post(settings.ollama_base_url.rstrip("/") + "/api/generate", json=payload)
                response.raise_for_status()
            return response_model.model_validate_json(response.json()["response"])

    judge = NativeOllamaJudge()
    embeddings = HuggingFaceEmbeddings(
        model=settings.embedding_model,
        device="cpu",
        cache_folder=settings.model_cache_dir,
        normalize_embeddings=True,
        batch_size=4,
    )
    metrics = {
        "context_precision": ContextPrecision(llm=judge),
        "context_recall": ContextRecall(llm=judge),
        "faithfulness": Faithfulness(llm=judge),
        "answer_relevancy": AnswerRelevancy(llm=judge, embeddings=embeddings, strictness=1),
    }
    knowledge = KnowledgeService(settings)
    cases = json.loads((ROOT / "evaluation" / "ragas_cases.json").read_text(encoding="utf-8"))[: args.max_cases]
    details: list[dict[str, Any]] = []

    for case in cases:
        hits = knowledge.retrieve(
            case["query"], service_name=case["service_name"], environment="production",
            top_k=settings.rag_top_k, top_n=settings.rag_top_n,
        )
        contexts = [item["content"] for item in hits]
        fallback = "现有检索证据不足，需由值班工程师继续核查。"
        answer = await answer_model.generate(
            "你是 Trace2Root 知识助手。只能依据给定上下文，用中文简洁回答并指出高风险操作需要审批；证据不足时明确说明。",
            f"问题：{case['query']}\n上下文：{json.dumps(contexts, ensure_ascii=False)}",
            fallback,
        )
        values: dict[str, float | None] = {}
        errors: dict[str, str] = {}
        kwargs = {
            "user_input": case["query"], "response": answer,
            "retrieved_contexts": contexts, "reference": case["reference"],
        }
        for name, metric in metrics.items():
            accepted = {
                "context_precision": ("user_input", "reference", "retrieved_contexts"),
                "context_recall": ("user_input", "reference", "retrieved_contexts"),
                "faithfulness": ("user_input", "response", "retrieved_contexts"),
                "answer_relevancy": ("user_input", "response"),
            }[name]
            try:
                result = await metric.ascore(**{key: kwargs[key] for key in accepted})
                value = float(result.value)
                values[name] = None if math.isnan(value) else value
            except Exception as exc:
                values[name] = None
                errors[name] = f"{type(exc).__name__}: {exc}"[:500]
        details.append({
            "id": case["id"], "query": case["query"], "reference": case["reference"],
            "response": answer, "sources": [item.get("source_file") for item in hits],
            "scores": values, "errors": errors,
        })
        print(case["id"], values, errors)

    summary = {}
    for name in metrics:
        available = [row["scores"][name] for row in details if row["scores"][name] is not None]
        summary[name] = sum(available) / len(available) if available else None
    report = {
        "generated_at": datetime.now(UTC).isoformat(), "case_count": len(details),
        "generator_model": settings.ollama_model, "judge_model": settings.ollama_model,
        "embedding_model": settings.embedding_model, "summary": summary, "cases": details,
        "scope_warning": "本地小样本与同模型自评仅用于回归，不代表生产质量或独立裁判结论。",
    }
    target = ROOT / args.output
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if knowledge.store._client is not None:
        knowledge.store._client.close()
    print(json.dumps({"case_count": len(details), "summary": summary, "report": str(target.relative_to(ROOT))}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
