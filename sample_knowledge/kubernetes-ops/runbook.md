# Kubernetes 应用告警排查 Runbook（公开文档整理版）

> 来源：Kubernetes Authors 的中文文档，按 CC BY 4.0 使用。本文件为了 OnCall 检索进行了摘录、重组和场景化补充，并非 Kubernetes 官方原文的完整副本。
>
> 原始页面：
> - https://kubernetes.io/zh-cn/docs/concepts/workloads/pods/probes/
> - https://kubernetes.io/zh-cn/docs/tasks/debug/debug-application/debug-running-pod/
> - https://kubernetes.io/zh-cn/docs/tasks/debug/debug-cluster/resource-metrics-pipeline/
> - https://kubernetes.io/zh-cn/docs/reference/access-authn-authz/rbac/

## Pod 不就绪或请求错误

先查看 Pod 状态、容器状态、Ready 条件、Restart Count 和近期 Event。就绪探针失败时，Pod 会被标记为未就绪，并从相应 Service 的 EndpointSlice 可用端点中移除；这通常表现为流量减少或上游出现无可用后端。不要看到探针失败就立即重启，应先区分应用故障、依赖故障和探针配置错误。

建议证据顺序：

1. 查看 Pod 当前状态、容器退出原因、重启次数及事件时间线。
2. 对齐告警时间与探针失败时间，检查 readiness、liveness、startup 的配置和失败阈值。
3. 检查应用日志和依赖健康状态，确认是应用自身失败还是后端依赖不可用。
4. 检查 Service EndpointSlice 是否仍包含该 Pod。
5. 涉及删除 Pod、重启、扩缩容或修改探针配置时，转入变更审批流程。

## 存活、就绪和启动探针区别

启动探针用于判断应用是否完成启动；配置后，在启动探针成功前不会执行存活或就绪探针。就绪探针决定容器是否可以接收流量，失败通常不会重启容器，而是将其从服务端点移除。存活探针用于判断容器是否需要被重启。慢启动应用应优先正确配置启动探针，避免存活探针在初始化阶段造成反复重启。

如果应用进程本身能够在不可恢复故障时退出，kubelet 可以按照 restartPolicy 处理，不一定需要激进的存活探针。探针端点必须轻量、有限并避免创建失控进程，否则诊断机制本身可能造成资源耗尽。

## CrashLoopBackOff 与容器重启

先确认容器是 Waiting、Running 还是 Terminated，并查看退出码、Reason、Last State 和 Restart Count。把首次异常日志、容器终止时间、配置变更和镜像发布时间放在同一时间线上。若应用启动时间超过探针窗口，应调整 startupProbe，而不是反复人工重启。

可以使用 describe 获取标签、资源申请、状态和事件；需要进入容器或节点执行高级调试时，应遵循最小权限原则。特权调试容器、节点 Shell 和写操作属于高风险动作，必须由有权限的值班人员审批和审计。

## CPU、内存和资源指标异常

资源指标管道提供面向节点和 Pod 的有限 CPU、内存指标，常由 metrics-server 从 kubelet 获取，并通过 Kubernetes API 提供给 `kubectl top` 或自动扩缩容组件。资源指标只适合回答当前使用量，不应代替完整的历史监控和告警系统。

CPU 或内存告警的基本排查顺序：

1. 确认告警对象、时间窗口和资源 requests/limits。
2. 对比节点与 Pod 的当前使用量，判断是单 Pod、单节点还是全局问题。
3. 检查 OOMKilled、驱逐事件、CPU throttling、重启次数和负载变化。
4. 将资源变化与部署、配置、流量和依赖延迟进行时间关联。
5. 扩容、修改 limits 或迁移工作负载属于变更，必须先记录影响和回退方案。

## RBAC 与最小权限

RBAC 通过 Role、ClusterRole、RoleBinding 和 ClusterRoleBinding 控制访问权限。诊断账号默认只应获得读取告警、指标、日志、Pod 状态和事件所需的权限。不要为了排障直接授予 cluster-admin，也不要把创建特权 Pod、读取 Secret、删除工作负载等能力授予自动化 Agent。

Agent 的建议与执行权限必须分离：Agent 可以提出重启、回滚或扩容建议，但实际写操作需要独立身份、显式审批、参数校验和审计记录。
