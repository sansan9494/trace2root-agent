from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from backend.app.public_bench import aggregate_scores, build_evidence, load_scenario, normalize_prediction, score_prediction


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "data" / "public_datasets" / "rootcausebench"


OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "first_failing_service": {"type": "string"},
        "causal_component": {"type": "string"},
        "root_cause_type": {"type": "string"},
        "remediation": {
            "type": "string",
            "enum": [
                "config-revert", "escalate-upstream", "rollback", "roll-forward",
                "rotate-cert", "failover", "purge-bad-data", "reschedule", "scale", "investigate"
            ],
        },
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "evidence": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
    },
    "required": ["first_failing_service", "causal_component", "root_cause_type", "remediation", "confidence", "evidence"],
    "additionalProperties": False,
}


def bounded_evidence_text(evidence: dict[str, Any]) -> str:
    """Keep all evidence families while fitting an 8 GB-GPU/low-RAM WSL host."""
    budgets = {
        "alert": 1200,
        "metric_anomalies": 2200,
        "log_patterns": 1500,
        "representative_error_logs": 1800,
        "representative_traces": 1800,
        "change_context": 2200,
        "external_status": 1300,
    }
    sections: list[str] = []
    for name, budget in budgets.items():
        value = json.dumps(evidence.get(name), ensure_ascii=False, separators=(",", ":"))
        if len(value) > budget:
            value = value[:budget] + "…[本节已按字符预算截断]"
        sections.append(f"[{name}]\n{value}")
    return "\n".join(sections)


async def predict(client: httpx.AsyncClient, base_url: str, model: str, evidence: dict[str, Any]) -> dict[str, Any]:
    prompt = (
        "你是值班故障诊断工程师。下面是一个公开基准场景，只能依据提供的告警、指标、日志、Trace、部署和外部状态证据判断。"
        "不要把告警服务自动当作根因，不要默认最近部署就是原因；对齐异常首次出现时间，并区分代码故障、容量问题、证书过期和外部依赖故障。"
        "first_failing_service 必须填写第一个出现可观测错误的本系统服务，不是证书/数据库等资源的宿主，也不能填写 Stripe 等外部系统；"
        "causal_component 单独填写真正的致因服务、外部系统或资源。"
        "remediation 语义：代码部署回退选 rollback；独立配置变更撤销选 config-revert；缺少迁移或注册数据且应补发修复选 roll-forward；"
        "证书过期选 rotate-cert；外部服务可切换备用端点选 failover；需要外部平台或基础设施团队处理选 escalate-upstream；"
        "真实业务流量超过容量选 scale；节点噪声邻居导致调度问题选 reschedule；毒数据阻塞消费选 purge-bad-data；证据不足选 investigate。"
        "请直接输出满足 JSON Schema 的对象，不要输出思维过程。\n\n"
        f"证据分区：\n{bounded_evidence_text(evidence)}\n/no_think"
    )
    response = await client.post(
        f"{base_url.rstrip('/')}/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "think": False,
            "format": OUTPUT_SCHEMA,
            "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 320},
        },
    )
    response.raise_for_status()
    body = response.json()
    raw = body.get("response", "")
    if not raw:
        raise ValueError(f"Ollama 未返回最终答案；thinking_chars={len(body.get('thinking', ''))}")
    return normalize_prediction(json.loads(raw))


async def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate qwen/Ollama on a vendored RootCauseBench subset.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--model", default="qwen3:4b")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--max-cases", type=int, default=32, help="0 表示评测全部本地场景")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--scenario", action="append", default=[])
    parser.add_argument("--output", type=Path, default=ROOT / "evaluation" / "results" / "latest_public_rca.json")
    parser.add_argument("--no-resume", action="store_true", help="忽略同名checkpoint并从头开始")
    args = parser.parse_args()

    scenario_dirs = sorted(path for path in args.dataset.iterdir() if path.is_dir() and (path / "ground_truth.json").exists())
    if args.scenario:
        selected = set(args.scenario)
        scenario_dirs = [path for path in scenario_dirs if path.name in selected]
    if args.max_cases < 0:
        raise SystemExit("--max-cases 不能为负数")
    if args.repeats < 1:
        raise SystemExit("--repeats 至少为 1")
    if args.max_cases:
        scenario_dirs = scenario_dirs[: args.max_cases]
    if not scenario_dirs:
        raise SystemExit("没有找到公开评测场景")

    started = time.perf_counter()
    runs: list[dict[str, Any]] = []
    checkpoint_path = args.output.with_suffix(".checkpoint.json")
    signature = {
        "model": args.model,
        "repeats": args.repeats,
        "scenarios": [path.name for path in scenario_dirs],
        "schema_version": 3,
    }
    current_run = 1
    current_details: list[dict[str, Any]] = []
    if checkpoint_path.exists() and not args.no_resume:
        try:
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            if checkpoint.get("signature") == signature:
                runs = checkpoint.get("runs", [])
                current_run = int(checkpoint.get("current_run", len(runs) + 1))
                current_details = checkpoint.get("current_details", [])
                print(json.dumps({"resume": True, "completed_runs": len(runs), "current_run": current_run, "completed_cases": len(current_details)}, ensure_ascii=False), flush=True)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass

    def save_checkpoint(run_index: int, details: list[dict[str, Any]]) -> None:
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_path.write_text(json.dumps({
            "signature": signature,
            "runs": runs,
            "current_run": run_index,
            "current_details": details,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    async with httpx.AsyncClient(timeout=httpx.Timeout(180)) as client:
        tags = await client.get(f"{args.base_url.rstrip('/')}/api/tags")
        tags.raise_for_status()
        available = {item.get("name") for item in tags.json().get("models", [])}
        if args.model not in available:
            raise SystemExit(f"Ollama 中没有模型 {args.model}；当前模型：{sorted(available)}")
        for run_index in range(1, args.repeats + 1):
            if run_index <= len(runs):
                continue
            details = list(current_details) if current_run == run_index else []
            completed_names = {item["scenario"] for item in details}
            for path in scenario_dirs:
                if path.name in completed_names:
                    continue
                scenario = load_scenario(path)
                evidence = build_evidence(scenario)
                case_started = time.perf_counter()
                error = None
                try:
                    prediction = await predict(client, args.base_url, args.model, evidence)
                except Exception as exc:
                    prediction = normalize_prediction({})
                    error = f"{type(exc).__name__}: {exc}"[:500]
                scores = score_prediction(prediction, scenario.ground_truth)
                detail = {
                    "scenario": scenario.name,
                    "prediction": prediction,
                    "scores": scores,
                    "error": error,
                    "duration_seconds": round(time.perf_counter() - case_started, 3),
                    "evidence_counts": {
                        "metrics": len(evidence["metric_anomalies"]),
                        "log_patterns": len(evidence["log_patterns"]),
                        "logs": len(evidence["representative_error_logs"]),
                        "traces": len(evidence["representative_traces"]),
                        "deploys": len(evidence["change_context"]["deploys"]),
                    },
                }
                details.append(detail)
                save_checkpoint(run_index, details)
                print(json.dumps({"run": run_index, "case": len(details), "scenario": scenario.name, "scores": scores, "error": error}, ensure_ascii=False), flush=True)
            summary = aggregate_scores(details)
            runs.append({
                "run": run_index,
                "case_count": len(details),
                **summary,
                "error_count": sum(item["error"] is not None for item in details),
                "duration_seconds": round(sum(item["duration_seconds"] for item in details), 3),
                "details": details,
            })
            current_details = []
            current_run = run_index + 1
            save_checkpoint(current_run, current_details)

    metric_names = ("first_failing_service_accuracy", "remediation_accuracy")
    repeat_statistics = {
        metric: {
            "mean": statistics.mean(run[metric] for run in runs),
            "std": statistics.pstdev(run[metric] for run in runs),
        }
        for metric in metric_names
    }

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": "Edge Delta RootCauseBench public subset",
        "dataset_source": "https://github.com/edgedelta/root-cause-bench",
        "license": "Apache-2.0",
        "model": args.model,
        "case_count": len(scenario_dirs),
        "repeat_count": args.repeats,
        "standard_deviation_type": "population",
        "first_failing_service_accuracy": repeat_statistics["first_failing_service_accuracy"]["mean"],
        "remediation_accuracy": repeat_statistics["remediation_accuracy"]["mean"],
        "repeat_statistics": repeat_statistics,
        "error_count": sum(run["error_count"] for run in runs),
        "duration_seconds": round(time.perf_counter() - started, 3),
        "runs": runs,
        "details": runs[-1]["details"],
        "scope_warning": "本地保留的公开32场景子集；评测根因服务与处置类型，不等同于 RootCauseBench 官方完整排行榜成绩。",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    checkpoint_path.unlink(missing_ok=True)
    print(json.dumps({key: value for key, value in report.items() if key not in {"details", "runs"}}, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
