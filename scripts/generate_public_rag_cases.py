from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "evaluation" / "public_rag_cases.json"


INTENTS = [
    {
        "id": "k8s-readiness-traffic",
        "category": "kubernetes_readiness",
        "service_name": "kubernetes-ops",
        "terms": ["EndpointSlice", "未就绪"],
        "source": "https://kubernetes.io/zh-cn/docs/concepts/workloads/pods/probes/",
        "queries": [
            "Kubernetes 就绪探针失败后为什么服务不再给 Pod 转发流量？",
            "Readiness Probe 失败会怎样影响 Service 的可用端点？",
            "Pod 还在运行但 Ready 为 false 时流量去了哪里？",
            "为什么就绪探针失败后 Pod 会从 Service 的可用端点中移除？",
        ],
    },
    {
        "id": "k8s-probe-selection",
        "category": "kubernetes_probes",
        "service_name": "kubernetes-ops",
        "terms": ["启动探针", "存活探针"],
        "source": "https://kubernetes.io/zh-cn/docs/concepts/workloads/pods/probes/",
        "queries": [
            "慢启动应用如何避免存活探针导致反复重启？",
            "startupProbe 成功之前会不会执行 livenessProbe？",
            {"query": "启动探针、就绪探针和存活探针分别解决什么问题？", "terms": ["启动探针", "就绪探针", "存活探针"]},
            "应用初始化很慢时应该优先配置哪种 Probe？",
        ],
    },
    {
        "id": "k8s-crashloop",
        "category": "kubernetes_crashloop",
        "service_name": "kubernetes-ops",
        "terms": ["Last State", "Restart Count"],
        "source": "https://kubernetes.io/zh-cn/docs/tasks/debug/debug-application/debug-running-pod/",
        "queries": [
            "CrashLoopBackOff 应该先检查退出原因还是直接重启？",
            "Pod 反复重启时需要查看哪些容器历史状态字段？",
            "如何根据 Last State 和 Restart Count 排查容器崩溃？",
            "容器持续重启时怎样把退出原因与发布时间对齐？",
        ],
    },
    {
        "id": "k8s-resource-metrics",
        "category": "kubernetes_metrics",
        "service_name": "kubernetes-ops",
        "terms": ["metrics-server", "历史监控"],
        "source": "https://kubernetes.io/zh-cn/docs/tasks/debug/debug-cluster/resource-metrics-pipeline/",
        "queries": [
            "metrics-server 的资源指标能否代替完整历史监控？",
            "kubectl top 的 CPU 内存数据从哪里获取？",
            "资源指标管道适合查询长期趋势吗？",
            "为什么 metrics-server 只能作为当前资源排查证据之一？",
        ],
    },
    {
        "id": "k8s-resource-failure",
        "category": "kubernetes_resource_failure",
        "service_name": "kubernetes-ops",
        "terms": ["OOMKilled", "CPU throttling"],
        "source": "https://kubernetes.io/zh-cn/docs/tasks/debug/debug-cluster/resource-metrics-pipeline/",
        "queries": [
            {"query": "Pod 内存告警后应该检查哪些 OOM 和驱逐证据？", "terms": ["OOMKilled", "驱逐事件"]},
            {"query": "CPU 使用率异常时为什么还要检查 throttling？", "terms": ["CPU throttling", "requests/limits"]},
            {"query": "怎样区分单个 Pod 资源异常和节点整体资源不足？", "terms": ["节点", "Pod", "全局问题"]},
            {"query": "修改 requests 和 limits 前要收集哪些资源证据？", "terms": ["requests/limits", "使用量", "负载变化"]},
        ],
    },
    {
        "id": "k8s-rbac",
        "category": "kubernetes_rbac",
        "service_name": "kubernetes-ops",
        "terms": ["cluster-admin", "最小权限"],
        "source": "https://kubernetes.io/zh-cn/docs/reference/access-authn-authz/rbac/",
        "queries": [
            "排障 Agent 是否应该直接获得 cluster-admin？",
            "自动诊断账号应该遵循什么权限原则？",
            "为什么不能为了调试给 Agent 集群管理员权限？",
            "读取日志的账号是否也应该拥有删除工作负载权限？",
        ],
    },
    {
        "id": "k8s-debug-evidence",
        "category": "kubernetes_debugging",
        "service_name": "kubernetes-ops",
        "terms": ["退出码", "Reason"],
        "source": "https://kubernetes.io/zh-cn/docs/tasks/debug/debug-application/debug-running-pod/",
        "queries": [
            {"query": "调试已终止容器时应同时检查哪些退出状态和日志证据？", "terms": ["退出码", "Reason", "Last State"]},
            "Container Terminated 状态中的 Reason 有什么排障价值？",
            "Pod 崩溃调查应如何结合退出码和异常时间？",
            {"query": "容器状态为 Waiting、Running、Terminated 时各应关注什么？", "terms": ["Waiting", "Running", "Terminated"]},
        ],
    },
    {
        "id": "security-readonly",
        "category": "security_readonly",
        "service_name": "security-approval",
        "terms": ["L1", "脱敏"],
        "source": "https://doi.org/10.6028/NIST.SP.800-53r5",
        "queries": [
            "查询告警和日志属于什么风险等级？",
            "只读工具可以无限时间范围查询日志吗？",
            "L1 日志查询为什么仍要限制条数并脱敏？",
            {"query": "Agent 自动读取指标前需要做哪些安全限制？", "terms": ["时间窗口", "返回条数", "脱敏"]},
        ],
    },
    {
        "id": "security-notification",
        "category": "security_notification",
        "service_name": "security-approval",
        "terms": ["L2", "allowlist"],
        "source": "https://doi.org/10.6028/NIST.SP.800-53r5",
        "queries": [
            "向 Mock 通知渠道发送消息属于哪个风险等级？",
            "为什么通知目标需要配置 allowlist？",
            "L2 外部动作执行前要校验哪些信息？",
            "Agent 可以向任意聊天群发送故障报告吗？",
        ],
    },
    {
        "id": "security-l3",
        "category": "security_high_risk",
        "service_name": "security-approval",
        "terms": ["L3", "人工审批"],
        "source": "https://doi.org/10.6028/NIST.SP.800-53r5",
        "queries": [
            "生产服务重启属于哪个风险等级，能否由 Agent 自动执行？",
            "回滚、扩容和流量切换为什么需要人工审批？",
            "Agent 已经找到高置信根因后可以直接重启吗？",
            "哪些可能改变服务状态的动作必须被审批门拦截？",
        ],
    },
    {
        "id": "security-l4",
        "category": "security_prohibited",
        "service_name": "security-approval",
        "terms": ["L4", "禁止执行"],
        "source": "https://doi.org/10.6028/NIST.SP.800-53r5",
        "queries": [
            "删除生产数据或者导出凭据能否通过审批放行？",
            "首版 Agent 对关闭审计和清空队列如何处理？",
            "L4 操作是不是审批通过后就能执行？",
            "修改密钥或绕过访问控制属于什么安全级别？",
        ],
    },
    {
        "id": "security-approval-evidence",
        "category": "security_evidence",
        "service_name": "security-approval",
        "terms": ["回退方案", "两个独立信号"],
        "source": "https://doi.org/10.6028/NIST.SP.800-61r3",
        "queries": [
            "批准回滚之前审批请求必须包含哪些证据和回退信息？",
            {"query": "只有一条错误日志时能否直接批准生产变更？", "terms": ["单条日志", "两个独立信号", "降低置信度"]},
            {"query": "高风险操作为什么需要两个独立信号交叉验证？", "terms": ["两个独立信号", "交叉验证"]},
            {"query": "审批请求缺少回退方案时应该批准还是拒绝？", "terms": ["回退方案", "拒绝审批"]},
        ],
    },
    {
        "id": "security-approval-binding",
        "category": "security_approval_state",
        "service_name": "security-approval",
        "terms": ["task_id", "参数变化"],
        "source": "https://doi.org/10.6028/NIST.SP.800-53r5",
        "queries": [
            "审批通过后如果工具参数改变，原审批还有效吗？",
            "审批记录为什么要绑定 task_id 和工具参数摘要？",
            "一个任务的重启审批能否复用到另一个任务？",
            "审批超时或者执行参数变化后应如何处理？",
        ],
    },
    {
        "id": "security-tool-failure",
        "category": "security_degraded",
        "service_name": "security-approval",
        "terms": ["MCP", "系统正常"],
        "source": "https://doi.org/10.6028/NIST.SP.800-61r3",
        "queries": [
            "MCP 查询失败可以判断系统正常吗？",
            {"query": "工具超时后故障报告应该隐藏失败吗？", "terms": ["工具超时", "写入报告"]},
            {"query": "监控数据源断开时 Agent 能否判断系统正常？", "terms": ["数据源失败", "系统正常"]},
            "未查询到指标是否等于服务没有异常？",
        ],
    },
    {
        "id": "security-audit-redaction",
        "category": "security_audit",
        "service_name": "security-approval",
        "terms": ["trace_id", "Token"],
        "source": "https://doi.org/10.6028/NIST.SP.800-53r5",
        "queries": [
            {"query": "工具审计记录必须保存哪些调用信息？", "terms": ["工具名", "参数", "风险等级", "耗时", "trace_id"]},
            {"query": "日志里出现 Token 和邮箱时应怎样处理？", "terms": ["Token", "邮箱", "遮盖"]},
            {"query": "为什么工具调用审计必须记录 trace_id？", "terms": ["工具调用", "trace_id"]},
            {"query": "审批记录中可以保存明文 API Key 吗？", "terms": ["API Key", "明文凭据"]},
        ],
    },
]


def main() -> None:
    cases = []
    for intent in INTENTS:
        for index, query_spec in enumerate(intent["queries"], start=1):
            if isinstance(query_spec, str):
                query = query_spec
                terms = intent["terms"]
            else:
                query = query_spec["query"]
                terms = query_spec.get("terms", intent["terms"])
            cases.append(
                {
                    "id": f"{intent['id']}-{index}",
                    "intent_id": intent["id"],
                    "category": intent["category"],
                    "query": query,
                    "service_name": intent["service_name"],
                    "expected_content_terms": terms,
                    "expected_source_file": "runbook.md",
                    "reference_urls": [intent["source"]],
                    "primary_annotation": "project_author",
                    "review_status": "independent_ai_reviewed",
                }
            )
    if not 50 <= len(cases) <= 100:
        raise RuntimeError(f"case count must be between 50 and 100, got {len(cases)}")
    OUTPUT.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"case_count": len(cases), "output": str(OUTPUT.relative_to(ROOT))}, ensure_ascii=False))


if __name__ == "__main__":
    main()
