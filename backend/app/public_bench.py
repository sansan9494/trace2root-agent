from __future__ import annotations

import csv
import json
import math
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ALLOWED_REMEDIATIONS = {
    "config-revert",
    "escalate-upstream",
    "rollback",
    "roll-forward",
    "rotate-cert",
    "failover",
    "purge-bad-data",
    "reschedule",
    "scale",
    "investigate",
}


@dataclass(frozen=True)
class PublicScenario:
    name: str
    path: Path
    alert: dict[str, Any]
    ground_truth: dict[str, Any]


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def load_scenario(path: Path) -> PublicScenario:
    return PublicScenario(
        name=path.name,
        path=path,
        alert=read_json(path / "data" / "alert.json", {}),
        ground_truth=read_json(path / "ground_truth.json", {}),
    )


def summarize_metrics(path: Path, limit: int = 18) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[tuple[str, float]]] = {}
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                value = float(row["value"])
            except (KeyError, TypeError, ValueError):
                continue
            grouped.setdefault((row.get("service", "unknown"), row.get("metric", "unknown")), []).append(
                (row.get("timestamp", ""), value)
            )

    summaries: list[dict[str, Any]] = []
    for (service, metric), points in grouped.items():
        values = [item[1] for item in points]
        split = max(1, len(values) // 3)
        baseline_values = values[:split]
        incident_values = values[-split:]
        baseline = statistics.median(baseline_values)
        incident = statistics.median(incident_values)
        deviation = statistics.pstdev(baseline_values) if len(baseline_values) > 1 else 0.0
        scale = max(abs(baseline), deviation, 1e-6)
        relative_change = (incident - baseline) / scale
        summaries.append(
            {
                "service": service,
                "metric": metric,
                "baseline_median": round(baseline, 6),
                "incident_median": round(incident, 6),
                "last": round(values[-1], 6),
                "min": round(min(values), 6),
                "max": round(max(values), 6),
                "relative_change": round(relative_change, 3),
                "last_timestamp": points[-1][0],
                "_score": abs(relative_change),
            }
        )
    selected = sorted(summaries, key=lambda item: item["_score"], reverse=True)[:limit]
    for item in selected:
        item.pop("_score", None)
    return selected


def _normalize_log_message(message: str) -> str:
    message = re.sub(r"\b[0-9a-f]{12,}\b", "<id>", message, flags=re.IGNORECASE)
    message = re.sub(r"\b\d+(?:\.\d+)?\b", "<n>", message)
    return message[:500]


def sample_logs(path: Path, limit: int = 18) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        severity = str(item.get("severity_text", item.get("level", ""))).upper()
        if severity not in {"WARN", "WARNING", "ERROR", "FATAL", "CRITICAL"}:
            continue
        message = _normalize_log_message(str(item.get("msg", item.get("message", ""))))
        service = str(item.get("service", item.get("service_name", "unknown")))
        key = (service, message)
        if key in seen:
            continue
        seen.add(key)
        records.append(
            {
                "timestamp": item.get("timestamp"),
                "service": service,
                "severity": severity,
                "message": message,
                "trace_id": item.get("trace_id"),
            }
        )
    return records[-limit:]


def sample_traces(path: Path, limit: int = 20) -> list[dict[str, Any]]:
    traces = read_json(path, []) or []
    ranked = sorted(
        traces,
        key=lambda item: (
            str(item.get("status", "")).upper() in {"ERROR", "FAILED"},
            float(item.get("duration_ms", 0) or 0),
        ),
        reverse=True,
    )
    return [
        {
            key: item.get(key)
            for key in (
                "trace_id",
                "span_id",
                "parent_id",
                "service",
                "name",
                "duration_ms",
                "status",
                "http.status_code",
                "peer.service",
            )
            if key in item
        }
        for item in ranked[:limit]
    ]


def deployed_commits(data_dir: Path) -> dict[str, Any]:
    deploys = read_json(data_dir / "context" / "deploys.json", []) or []
    commits = read_json(data_dir / "context" / "commits.json", []) or []
    deployed_shas = {str(item.get("commit_sha")) for item in deploys}
    selected = []
    for item in commits:
        if str(item.get("sha")) not in deployed_shas:
            continue
        selected.append(
            {
                "sha": item.get("sha"),
                "timestamp": item.get("timestamp"),
                "message": item.get("message"),
                "files_changed": item.get("files_changed", [])[:8],
                "diff": str(item.get("diff") or "")[:1600],
            }
        )
    return {
        "deploys": deploys[-12:],
        "deployed_commit_details": selected[-12:],
        "feature_flags": (read_json(data_dir / "context" / "flags.json", []) or [])[-10:],
    }


def build_evidence(scenario: PublicScenario) -> dict[str, Any]:
    data_dir = scenario.path / "data"
    return {
        "alert": scenario.alert,
        "metric_anomalies": summarize_metrics(data_dir / "metrics.csv"),
        "log_patterns": (read_json(data_dir / "patterns.json", []) or [])[:16],
        "representative_error_logs": sample_logs(data_dir / "logs.ndjson"),
        "representative_traces": sample_traces(data_dir / "traces.json"),
        "change_context": deployed_commits(data_dir),
        "external_status": read_json(data_dir / "status_feed.json", []),
    }


def normalize_prediction(value: dict[str, Any]) -> dict[str, Any]:
    remediation = str(value.get("remediation", "investigate")).strip().lower()
    if remediation not in ALLOWED_REMEDIATIONS:
        remediation = "investigate"
    confidence = str(value.get("confidence", "low")).strip().lower()
    if confidence not in {"low", "medium", "high"}:
        confidence = "low"
    evidence = value.get("evidence", [])
    if not isinstance(evidence, list):
        evidence = []
    return {
        "first_failing_service": str(
            value.get("first_failing_service", value.get("root_cause_service", "unknown"))
        ).strip(),
        "causal_component": str(value.get("causal_component", "unknown")).strip(),
        "root_cause_type": str(value.get("root_cause_type", "unknown")).strip(),
        "remediation": remediation,
        "confidence": confidence,
        "evidence": [str(item)[:500] for item in evidence[:5]],
    }


def score_prediction(prediction: dict[str, Any], ground_truth: dict[str, Any]) -> dict[str, Any]:
    expected_service = str(ground_truth.get("first_failing_service", "")).lower()
    actual_service = str(prediction.get("first_failing_service", "")).lower()
    expected_remediation = str(ground_truth.get("remediation", "")).lower()
    actual_remediation = str(prediction.get("remediation", "")).lower()
    return {
        "first_failing_service_correct": actual_service == expected_service,
        "remediation_correct": actual_remediation == expected_remediation,
        "expected_first_failing_service": ground_truth.get("first_failing_service"),
        "expected_remediation": ground_truth.get("remediation"),
    }


def aggregate_scores(details: list[dict[str, Any]]) -> dict[str, float]:
    total = len(details)
    if not total:
        return {"first_failing_service_accuracy": math.nan, "remediation_accuracy": math.nan}
    return {
        "first_failing_service_accuracy": sum(item["scores"]["first_failing_service_correct"] for item in details) / total,
        "remediation_accuracy": sum(item["scores"]["remediation_correct"] for item in details) / total,
    }
