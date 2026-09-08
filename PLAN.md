# Trace2Root Agent（迹因智能体）实施计划

本计划以 `REQUIREMENTS.md` 为需求基线，并应用本次轻量化约束：FastAPI、LangGraph、SQLite、Milvus Lite；不安装 Docker、Redis、PostgreSQL、Kubernetes 或完整 Prometheus；外部运维系统使用可替换 Mock 适配器。

## 环境基线

- 工作区：仓库根目录（建议保存在 WSL Linux 文件系统或可由 WSL 访问的目录）
- WSL2：Linux 6.18.33.2-microsoft-standard-WSL2，Ubuntu 24.04
- Python：3.12.3；Git：2.43.0
- GPU：NVIDIA RTX 4060 Laptop GPU，8188 MiB，WSL 内 `nvidia-smi` 可识别
- WSL 内存：约 7.4 GiB + 2 GiB swap；磁盘可用约 237 GiB
- Ollama：0.33.2，用户级安装于 `~/.local`；`qwen3:4b` 已下载并通过 RTX 4060 实际推理

## 阶段与验收标准

### 阶段 1：轻量工程基线

- [x] 创建后端、Web 页面、配置、SQLite 模型、脚本和测试目录。
- [x] 提供 `.env.example`、自动安装脚本和一键启动脚本。
- [x] 健康检查能明确显示 LLM、Embedding、Milvus Lite 与 Mock 工具状态。
- 验收：配置可加载、数据库可初始化、应用可导入。

### 阶段 2：知识库 Agent 与真实 RAG

- [x] 实现 Markdown/TXT/PDF Loader、安全校验、结构感知分片和元数据。
- [x] 下载并接入 CPU `BAAI/bge-small-zh-v1.5`（512 维），提供离线预热与哈希回退。
- [x] 使用 Milvus Lite 持久化向量并支持元数据过滤和 Top-K 检索。
- [x] 实现向量 + 中文 BM25 双路召回、RRF 融合、CPU `bge-reranker-base` Cross-Encoder 和文档级上下文多样性控制。
- [x] 准备订单、支付、用户服务的示例业务手册、Runbook 和历史事故资料。
- 验收：示例文档可导入、向量可检索、无证据问题拒答。

### 阶段 3：工具系统、对话 Agent 与 SSE

- [x] 实现结构化工具注册、Schema 校验、风险分级、超时/重试和审计。
- [x] 提供 Prometheus 告警、指标、日志、时间、消息 Mock 适配器与示例数据。
- [x] 使用 LangGraph 实现带最大循环次数的 ReAct 对话流程。
- [x] SQLite 持久化多轮消息、工具记录、增量摘要、结构化事实及相关历史片段召回。
- [x] 实现 `/chat`、`/chat_stream` 以及真实逐事件 SSE。
- 验收：工具路由、连续工具调用、记忆和 SSE 事件顺序通过测试。

### 阶段 4：运维 Agent（Plan-Execute-Replan）

- [x] 使用 LangGraph 实现独立 Planner、Executor、Replanner 和 Reporter 节点。
- [x] Replanner 能根据指标与日志证据修改后续计划，而非固定工作流。
- [x] SQLite 持久化任务、步骤、Replan 决策、工具调用和最终报告。
- [x] 实现告警去重、取消、事件查询和 L3 审批门。
- [x] 由 Ollama LLM 根据告警和工具目录生成结构化调查计划，并在每次工具观察后决定继续、修改、结束或请求人工。
- [x] Reporter 输出通用根因候选及逐项支持/反对证据；Harness 补齐最低证据路径并阻止模型越权、跨范围参数与过早结束。
- 验收：支付高延迟场景能检索文档、查询指标/日志、触发重规划并生成报告。

### 阶段 5：Web 演示与 API 补全

- [x] 单页 Web 支持告警轨迹、证据/报告、任务历史、取消、知识上传、来源、审批按钮、依赖状态和评测看板。
- [x] 提供知识上传、对话、任务详情、Webhook、审批和依赖状态接口。
- [x] 使用官方 MCP SDK 完成 stdio Server 初始化、工具发现、动态注册和 call_tool 调用，并保留安全降级。
- 验收：浏览器或 API 可完成核心演示路径。

### 阶段 6：测试、文档与最终验证

- [x] 单元测试覆盖分片、检索过滤、工具校验/超时、记忆摘要、规划结构、审批与去重。
- [x] 集成测试覆盖上传到检索、SSE、Mock 工具和端到端运维任务。
- [x] README 记录架构、模型选择、安装、启动、测试、演示、限制和排障。
- [x] 提供隔离 RAG/Agent 评测及本地 RAGAS 四指标，输出 Recall@K、MRR、NDCG、Context Precision/Recall、Faithfulness、Answer Relevancy、工具选择、重规划和越权率。
- [x] 在 WSL Python 虚拟环境中实际安装依赖、运行全量测试、启动服务并探测核心接口。
- [x] 知识检索升级为独立 Plan-Retrieve-Review Agent，并测试 LLM 查询扩展、证据复核和安全回退来源。
- 验收：记录真实命令与结果；任何跳过或失败项明确列出。

## 2026-09-03 最终验收结果

- 依赖检查：`pip check` 无冲突；PyTorch `2.14.0+cpu`，未安装 NVIDIA/CUDA Python 运行库。
- 模型自检：Embedding 512 维；Reranker 样例相关性分数 0.9818；14 个 Chunk 已重建到 `milvus_bge.db`。
- 测试：初版 28 passed；严格 Agent 升级后 34 passed，1 个 LangGraph 上游弃用 warning。
- 检索评测：9 条 Recall@3/MRR/NDCG@3 均 1.0（小样本饱和，不声称提升）。
- Agent 评测：4/4 场景通过，高风险未授权执行率 0%。
- RAGAS：3 条样本四指标无报错；0.972 / 0.667 / 0.875 / 0.486。
- 2026-09-07 扩展验收：RootCauseBench 32 场景 × 3 次；首故障服务准确率 0.8125±0.0000，处置准确率 0.5000±0.0000，错误数 0。
- 2026-09-07 中文 RAG：60 查询/15 意图，经独立 AI 二次复核后修改 18 条；Hybrid Recall@3 1.0000±0.0000、MRR 0.9833±0.0000。
- 2026-09-07 鲁棒性：无答案、证据冲突、工具失败、恶意 Prompt 四类 × 3 次全部通过。
- 在线问答：BGE 与 Reranker 已预热，知识与指标工具均成功，返回 5 个 `hybrid_rrf_cross_encoder` 来源。
- 在线告警：任务 completed；Replan 为 continue → revise → finish；高置信根因为支付网关上游超时；Mock 通知成功。
- L3 场景：状态 `waiting_approval`，实际重启执行为 false。
- 2026-09-07 严格 Agent 升级：33 passed；Agent 基准 4/4、越权执行率 0；四类鲁棒性 × 3 次全部通过。真实 Ollama 运行确认 Planner、逐观察 Replanner 和 Reporter 均产生 `llm` 决策来源，Harness 能阻止 4B 模型过早跳过最低证据步骤。

## 轻量化实现假设

1. SQLite 替代 PostgreSQL 和 Redis，分别承担业务持久化、事件存储、去重和轻量任务状态；接口边界保留，便于后续替换。
2. Milvus Lite 使用本地数据库文件，不启动独立 Milvus 服务。
3. Web 首版使用 FastAPI 托管的原生 HTML/CSS/JavaScript 页面，避免 Node 工具链占用；API 与展示能力不打折。
4. LLM 使用 Ollama `qwen3:4b` 的约 4B、4-bit 权重；适配器通过 raw generate 和结构化输出避免展示思维过程，不得选择大于 8B。Ollama 不可用时可显式切回可解释规则模型，并在状态页标为降级模式。
5. Embedding 首选 CPU 上的 `BAAI/bge-small-zh-v1.5`；模型不可用时使用确定性哈希向量回退，保证无网络环境可运行，但语义召回质量低于正式模型。
6. 不输出或持久化模型隐藏思维链，只展示状态、规划理由、工具调用与证据摘要。
