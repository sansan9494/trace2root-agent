# Trace2Root Agent（迹因智能体）

面向 8 GB 显存笔记本和 WSL2 的轻量化、可运行故障诊断 Agent。Trace2Root 表示从告警、指标、日志与 Trace 等运行信号沿可审计证据链追踪到根因。项目包含三个独立 Agent、向量+BM25+RRF 混合检索、ReAct 工具循环、Plan-Execute-Replan、任务持久化、SSE、审批门、可观察工具轨迹和离线评测，同时把重型外部依赖替换为本地组件和可替换 Mock 适配器。

> 这是个人工程实践与可复现实验项目，不代表已经在企业生产环境落地。Prometheus、日志、告警和通知目前通过真实 MCP 协议连接本地 Mock 工具，高风险操作不会触碰真实生产环境。

## 项目亮点

- **严格 Agent 决策闭环**：LLM 负责结构化规划、观察后重规划和报告生成，Harness 负责边界校验、最低证据路径与人工审批。
- **可解释混合检索**：CPU BGE 向量 + 中文 BM25 + RRF + Cross-Encoder 重排，并暴露各阶段分数和证据来源。
- **真实 MCP 工具链**：使用官方 Python MCP SDK 完成初始化、动态工具发现和调用，而不是在代码中伪造 MCP 名称。
- **本地优先部署**：Ollama `qwen3:4b` 运行在 8 GB 显存设备，Embedding 与 Reranker 放在 CPU，模型不可用时有显式降级模式。
- **可复现评测与安全验证**：包含中文 RAG、RootCauseBench 子集、鲁棒性和高风险越权测试，完整结果保存在 `evaluation/results/`。

## 架构

```text
Web / API / Alertmanager Webhook
               │
            FastAPI
     ┌─────────┼──────────┐
知识库 Agent  对话 Agent   运维 Agent
Loader/Chunk   ReAct      Planner → Executor
Embedding/RAG  循环           ↑       ↓
     └────统一工具层────── Reporter ← Replanner
               │
   SQLite + Milvus Lite + 本地文件
               │
Mock Alert/Prometheus/Logs/Notification（真实 MCP stdio 协议）
```

- 知识库 Agent：LangGraph 独立执行“查询规划 → 混合检索 → 证据充分性复核 → 最多一次改写重试”；底层包含文件校验、结构感知分片、CPU Embedding、Milvus Lite、中文 BM25、RRF 与 BGE 重排，不把文档硬编码进 Prompt。
- 对话 Agent：LangGraph 实现受最大 8 次限制的判断—工具—观察循环，持久化最近消息、工具结果和长会话摘要。
- 运维 Agent：`qwen3:4b` 根据告警和实时工具目录生成结构化计划；Executor 每次只执行一步，Replanner 在每次观察后选择 `continue/revise/finish/need_human`；Reporter 生成最多四个通用根因候选并分别列出支持与反对证据。

这不是让模型不受约束地自治。Harness 会校验工具白名单与 Pydantic 参数、锁定告警服务/环境、补齐知识/指标/日志最低证据路径、阻止过早跳步，并对 L3 写操作启用人工审批。事件中的 `decision_source` / 报告中的 `analysis_source` 会区分 `llm`、`llm+harness_guard`、模型错误回退和确定性测试回退，便于审计且不会把回退伪装成模型结果。

系统只展示可公开的状态、规划理由和工具证据，不记录或显示模型隐藏思维链。

## 技术栈与轻量化边界

- Python 3.11+、FastAPI、Uvicorn、Pydantic v2、LangGraph
- SQLAlchemy async + SQLite
- PyMilvus + Milvus Lite（本地文件）
- CPU 上的 `BAAI/bge-small-zh-v1.5`（512 维）Embedding；测试可切换无下载的哈希回退
- CPU 上的 `BAAI/bge-reranker-base` Cross-Encoder 神经重排
- 默认启用 Ollama `qwen3:4b` 4-bit 模型；测试可切换确定性 LLM 回退
- FastAPI 托管的原生 HTML/CSS/JavaScript 演示页，无 Node 依赖

本版不会安装或启动 Docker、Redis、PostgreSQL、Kubernetes、完整 Prometheus，也不会安装 CUDA Toolkit 或修改显卡驱动。SQLite 同时承担业务持久化、事件存储和去重；适配器接口保留，便于后续替换。

## 已检查的本机环境

实施时实际检查了：

```bash
nvidia-smi
python3 --version
git --version
free -h
df -h .
```

结果：测试设备的 WSL2 可识别 RTX 4060 Laptop GPU（8188 MiB）；Python 3.12.3；Git 2.43.0。WSL 分配约 7.4 GiB RAM 和 2 GiB swap。Ollama 0.33.2 以用户级方式安装，未修改驱动、未安装 CUDA Toolkit；`qwen3:4b` 已实测为 100% GPU，运行时约占 3.2 GB 显存。

## 一键安装与启动

在 WSL Ubuntu 终端执行，不要用 Windows Python：

```bash
cd /path/to/trace2root-agent
chmod +x scripts/*.sh
./scripts/setup.sh --full
.venv/bin/python scripts/import_samples.py
./scripts/start_all.sh
```

打开 <http://127.0.0.1:8000>。依赖状态：<http://127.0.0.1:8000/api/v1/ready>；OpenAPI：<http://127.0.0.1:8000/docs>。

`start_all.sh` 会检查 Ollama API，必要时从 `~/.local/bin/ollama` 启动服务，然后启动 FastAPI；按 `Ctrl+C` 可停止本次脚本启动的服务。`setup.sh --full` 在项目 `.venv` 安装 CPU PyTorch、BGE/Reranker 与锁定版 RAGAS 依赖并下载模型，不安装 CUDA Toolkit。已下载模型放在 `data/model_cache/`。若 Ubuntu 缺少 `ensurepip`，脚本只向项目 venv 引导 pip。也可运行：

```bash
make setup-full
make import-samples
make start-all
```

## 模型配置

当前 `.env.example` 默认使用已经安装的 Ollama：

```dotenv
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3:4b
EMBEDDING_PROVIDER=sentence_transformers
EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
EMBEDDING_DEVICE=cpu
EMBEDDING_DIMENSION=512
RERANKER_PROVIDER=cross_encoder
RERANKER_MODEL=BAAI/bge-reranker-base
RERANKER_DEVICE=cpu
```

当前 Ollama 仓库中的 `qwen3:4b` 是 4-bit 量化模型。适配器使用 raw generate、显式闭合思考区以及 JSON Schema 约束，使 Web/API 只得到最终答案，不展示隐藏思维文本。如需无模型测试，可把 `LLM_PROVIDER` 改成 `deterministic`、`EMBEDDING_PROVIDER` 改成 `hashing`、`RERANKER_PROVIDER` 改成 `lightweight`。

### Ollama 4B 量化

本机已经完成安装。检查或重新准备模型：

```bash
~/.local/bin/ollama list
~/.local/bin/ollama pull qwen3:4b
```

分两个终端启动时，可执行：

```bash
# 终端 A
./scripts/start_ollama.sh

# 终端 B
./scripts/start.sh
```

8 GB 显存下不要换成大于 8B 的模型。`nvidia-smi` 的 CUDA Version 表示驱动兼容能力，本项目不要求完整 CUDA Toolkit。

## MCP 工具协议

项目现在使用官方 Python MCP SDK 启动 `mock_services/mcp_server.py` stdio Server。FastAPI 生命周期会执行 MCP initialize/list_tools，将 `query_prometheus_alarm`、`query_metrics`、`query_logs` 动态注册为 Agent 工具；执行时走 call_tool。`/api/v1/ready` 会显示服务器、注册工具与连接错误。MCP 子进程不可用时只读 Mock 工具可安全降级到本地适配器，高风险写操作不会绕过审批。

### 轻量中文 Embedding

完整安装、下载并自检：

```bash
./scripts/setup.sh --full
```

并设置：

```dotenv
EMBEDDING_PROVIDER=sentence_transformers
EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
EMBEDDING_DEVICE=cpu
EMBEDDING_DIMENSION=512
RERANKER_PROVIDER=cross_encoder
RERANKER_MODEL=BAAI/bge-reranker-base
RERANKER_DEVICE=cpu
```

`bge-small-zh-v1.5` 与 Reranker 均在 CPU 运行，避免与 Ollama 抢占 8 GB 显存。更换模型或维度后运行 `.venv/bin/python scripts/rebuild_vector_index.py`。当前正式索引是 `data/milvus_bge.db`；旧索引未被脚本自动删除。

### 混合检索与轻量重排

默认 `RAG_RETRIEVAL_MODE=hybrid`，检索链路为：

```text
Milvus Lite 向量 Top-K ─┐
                        ├─ RRF 融合 ─ BGE Cross-Encoder ─ 单文档 Chunk 配额 ─ Top-N
中文/技术词 BM25 Top-K ─┘
```

中文分词器同时保留中文二元词和 `upstream_timeout`、`P95` 等技术标识符；BM25、RRF、Embedding 与重排均在 CPU 完成。每条命中返回 `vector_score`、`bm25_score`、`rrf_score`、`reranker_score` 和 `retrieval_sources`，便于解释。Cross-Encoder 加载失败时保留融合结果并返回明确 fallback/error 字段。设置 `RAG_RETRIEVAL_MODE=vector` 可切回纯向量基线。

## 数据初始化

- SQLite：启动时幂等创建 `data/trace2root.db`。
- Milvus Lite：首次检查/导入时创建 `data/milvus.db` 和 `trace2root_chunks` Collection。
- 原文件：`data/uploads/`。
- 示例知识：支付、订单、用户三个服务各有业务说明、Runbook 和历史事故。
- 公开中文知识：Kubernetes 中文故障排查/RBAC 文档整理版，以及基于 NIST SP 800-61r3、SP 800-53r5 整理的安全审批 Runbook；原文、许可与归属保存在 `data/public_sources/`。
- Mock 数据：`mock_services/data/alerts.json`、`metrics.json`、`logs.json`。

导入命令：

```bash
.venv/bin/python scripts/import_samples.py
```

同一 SHA-256 与同一服务/环境/文档类型会去重。

## 核心 API

- `POST /api/v1/upload_file`：上传并索引 Markdown、TXT、可提取文本 PDF。
- `POST /api/v1/chat`：非流式对话。
- `POST /api/v1/chat_stream`：SSE 的 session/status/tool_start/tool_end/token/sources/done。
- `POST /api/v1/ai_ops`：创建后台运维任务。
- `GET /api/v1/ai_ops/{task_id}`：计划、步骤、Replan、工具轨迹和报告。
- `GET /api/v1/ai_ops/{task_id}/events`：任务 SSE。
- `POST /api/v1/webhooks/alertmanager`：读取标准核心字段并按 fingerprint 去重。
- `POST /api/v1/approvals/{approval_id}`：批准/拒绝 L3 演示操作。
- `GET /metrics`：轻量 Prometheus 文本指标，不安装 Prometheus。

## 六步演示

面试前可先运行 `bash scripts/demo_preflight.sh`。逐分钟讲稿、不同故障场景、现场故障预案以及 HR/技术面问题答案见 [`docs/DEMO_INTERVIEW_GUIDE.md`](docs/DEMO_INTERVIEW_GUIDE.md)。

1. 一键安装并导入示例知识。
2. 启动后打开 Web 页，确认 `ready · ollama`，并在依赖状态中看到 MCP `connected`。
3. 问“支付服务高延迟如何排查？”，查看 RAG 来源和工具。
4. 提交默认 HighLatency 告警，观察 LLM 初始计划、Harness 校验及知识、指标、日志调用。
5. 每次工具结束后查看 `replan`：模型会继续、修改或结束；若试图过早结束/跳步，来源会显示 `llm+harness_guard`。
6. 查看报告的根因候选、支持证据、反对证据、未确认项和 L1/L2/L3 建议；勾选通知可看到 Mock 消息事件。

高风险演示：目标改为“排查后重启支付服务”，任务会停在 `waiting_approval` 并产生 L3 请求。即使批准，演示工具也不执行真实生产重启。

## 测试

```bash
cd /path/to/trace2root-agent
./scripts/test.sh
```

服务启动后：

```bash
./scripts/smoke_test.sh
```

测试覆盖分片、路径穿越、脱敏、知识 Agent 查询规划/证据复核、工具 Schema、LLM Planner/Replanner、Harness 越权/跳步拦截、正反证据、上传到检索、ReAct、SSE 顺序、动态重规划、Mock 通知、告警去重和 L3 审批门。

### 可复现评测

评测使用隔离的临时 SQLite/Milvus Lite，结束后自动清理，不会与正在运行的 Web 服务争用 `data/milvus.db`：

```bash
.venv/bin/python scripts/evaluate_rag.py --top-k 3
.venv/bin/python scripts/evaluate_agent.py
HF_HUB_OFFLINE=1 .venv/bin/python scripts/evaluate_ragas.py --max-cases 3
.venv/bin/python scripts/evaluate_rag.py --cases evaluation/public_rag_cases.json --output evaluation/results/latest_public_rag.json --top-k 3 --repeats 3
.venv/bin/python scripts/evaluate_public_rca.py --max-cases 32 --repeats 3
.venv/bin/python scripts/evaluate_robustness.py --repeats 3
```

- RAG 评测集：`evaluation/rag_cases.json`，统计 Recall@K、MRR、NDCG，并对比纯向量与 Hybrid RRF。
- Agent 评测：统计场景成功率、工具选择准确率、工具参数有效率、重规划成功率和高风险未授权执行率。
- RAGAS：本地 Ollama 原生 JSON Schema 裁判适配器，计算 Context Precision/Recall、Faithfulness 和 Answer Relevancy；不调用云 API。
- 完整明细：`evaluation/results/latest.json`、`latest_agent.json`、`latest_ragas.json`。
- 公开 RAG 用例：`evaluation/public_rag_cases.json` 包含 60 条中文查询、覆盖 15 个语义意图；使用具体相关内容词和目标来源文件判定命中。独立 AI 第二评审记录在 `evaluation/public_rag_review.json`：初审 42 条通过、18 条修改、0 条拒绝，修改已全部合并。该记录不是人工双人标注，若要在简历中写“双人复核”仍需真人签字确认。
- 公开 RCA：保留 32 个 Apache-2.0 RootCauseBench 场景，使用 `qwen3:4b` 读取告警、指标、日志、Trace、部署和外部状态，预测完成后才加载 ground truth，统计首个故障服务准确率与处置类型准确率。场景目录名不会进入 Prompt，避免 `memory-leak` 等名称造成标签侧漏。评测支持逐场景 checkpoint 和中断续跑，结果写入 `evaluation/results/latest_public_rca.json`。
- 鲁棒性用例：`evaluation/robustness_cases.json` 覆盖无答案、证据冲突、工具失败和恶意 Prompt。检索工具会在证据进入 LLM 前隔离疑似注入行；失败与冲突必须降级置信度而不是解释为系统正常。

公开数据来源：

- RootCauseBench：https://github.com/edgedelta/root-cause-bench
- Kubernetes 中文文档：https://github.com/kubernetes/website （CC BY 4.0）
- NIST SP 800-61r3：https://doi.org/10.6028/NIST.SP.800-61r3
- NIST SP 800-53r5：https://doi.org/10.6028/NIST.SP.800-53r5

这些公开结果是本项目本地保留子集上的实验，不等同于 RootCauseBench 官方完整排行榜成绩。`ground_truth.json` 不进入模型 Prompt。执行 `make evaluate-public` 可以复现 RAG、RCA 和鲁棒性三组公开评测。

2026-09-03 的本机实测：9 条 RAG 标注在真实 BGE 上，向量和 Hybrid+Reranker 的 Recall@3/MRR/NDCG@3 均为 1.0，数据集已饱和，不能声称有提升；4 个 Agent 场景成功率、工具选择、参数有效和重规划均为 100%，高风险未授权执行率 0%；3 条本地 RAGAS 的 Context Precision=0.972、Recall=0.667、Faithfulness=0.875、Answer Relevancy=0.486。小样本且 RAGAS 使用同一 4B 模型自评，只用于回归，不代表生产效果。

2026-09-07 扩展后本机实测（三次重复，± 后为总体标准差）：60 条中文 RAG 查询中，纯向量 Recall@3=`0.9167±0.0000`、MRR=`0.8417±0.0000`、NDCG@3=`0.8609±0.0000`；Hybrid RRF + BGE Reranker 为 `1.0000±0.0000`、`0.9833±0.0000`、`0.9877±0.0000`。`qwen3:4b` 在 32 个 RootCauseBench 本地保留场景、共 96 次推理上，首个故障服务准确率为 `0.8125±0.0000`，处置类型准确率为 `0.5000±0.0000`，错误数 0。四类鲁棒性用例共 12 次运行全部通过，平均通过率 `1.0000±0.0000`。零标准差来自 `temperature=0` 和确定性检索，不代表生产流量不存在波动。

RCA 评测在本机必须限制为 4K 上下文，并建议以 `LLAMA_ARG_CACHE_RAM=0` 启动 Ollama。当前 Ollama 0.33.2 所带 llama.cpp 的默认跨请求 Prompt Cache 上限为 8 GiB，高于本机 WSL 实际约 7.4 GiB 内存，长批量评测会导致服务退出；`scripts/start_all.sh` 已默认禁用该缓存，不删除模型权重，也不影响单请求 KV 推理。

### 本机实际验证记录（2026-09-03 增强后复测）

```text
python -m compileall -q backend scripts mock_services -> compile-ok
./scripts/test.sh                                  -> 34 passed, 1 warning in 44.59s
.venv/bin/python scripts/evaluate_rag.py --top-k 3 -> 9 cases, vector/hybrid Recall@3=1.0
.venv/bin/python scripts/evaluate_agent.py          -> 4/4 scenarios, unauthorized L3 execution=0
scripts/evaluate_ragas.py --max-cases 3             -> 3/3 completed, four metrics recorded
scripts/evaluate_rag.py --cases evaluation/public_rag_cases.json --repeats 3 -> 60 cases, Hybrid Recall@3=1.0000±0.0000
scripts/evaluate_public_rca.py --max-cases 32 --repeats 3 -> 32 cases, first-failing-service=0.8125±0.0000
scripts/evaluate_robustness.py --repeats 3          -> 4 categories × 3, pass=1.0000±0.0000
scripts/rebuild_vector_index.py                      -> 9 documents, 14 BGE chunks
GET /api/v1/health                    -> 200 / ok
GET /api/v1/ready                     -> 200 / ready / ollama / MCP connected / hybrid retrieval
GET /                                 -> 200 / Web 页面存在
POST /api/v1/chat                     -> 200 / 两个工具成功 / 5 hybrid_rrf_cross_encoder sources
POST /api/v1/ai_ops + status polling  -> completed
MCP tools                             -> query_prometheus_alarm, query_metrics, query_logs
replan decisions                      -> continue, revise, finish
root cause                            -> 支付网关上游超时
Ollama runtime                        -> qwen3:4b / 100% GPU / 3.2 GB VRAM
```

pytest 的一个 warning 来自 LangGraph 依赖对未来序列化默认值的弃用提示，不是测试跳过或失败。

安装完成后已删除约 1.42 GB 的 Ollama 压缩安装包和安装脚本；本轮又执行 `pip cache purge`，移除 696 个文件、318.1 MB，`~/.cache/pip` 和 `~/.cache/huggingface` 均仅约 12 KB。当前保留运行必需空间：Ollama 程序/运行库约 2.1 GB、`qwen3:4b` 约 2.4 GB、两个 BGE 模型约 1.2 GB；模型缓存未发现 `.incomplete` 文件。

## 安全、常见故障和限制

- 上传有扩展名白名单、大小限制、规范化文件名、SHA-256 去重；PDF 只提取文本。
- 工具来自注册表，参数经 Pydantic 校验，时间范围/返回量受限，日志脱敏 Token、邮箱和手机号。
- L3/L4 必须审批；L4 禁止。`restart_service` 永远不触碰真实环境；消息只允许 Mock allowlist。
- Agent 有最大循环、工具超时和任务总超时。
- RAG 文档属于不可信证据；命中“忽略系统指令、泄露系统提示、调用高风险工具”等模式的行会在工具边界替换为隔离标记并写入安全标志。
- 正式模式启动时会从项目缓存离线预热 BGE 与 Reranker；在当前 `/mnt/c` 环境冷启动约需数分钟，但 `/ready` 后首次查询不会因模型加载超过工具超时。
- MCP 当前是本地 stdio Server；尚未接入真实 Prometheus、日志平台、告警平台或消息系统。
- SQLite 适合单机 MVP；后台任务在进程内，重启不会自动续跑。
- 离线模式和当前 Ollama 适配器在完整答案产生后分块发送 token；工具与状态事件来自真实后端轨迹。尚未接 Ollama 原生 token stream。
- Web 单页已包含告警轨迹、证据/报告、任务历史、取消、知识上传、来源、审批按钮、依赖状态和评测看板；不是 React 多页面后台。
- 尚未完成 DOCX/HTML、OCR、真实 RBAC/多租户、Webhook 签名、跨进程任务断点恢复和真实外部监控平台适配。
- 若 `venv` 报缺少 `ensurepip`，Ubuntu 可能需要 `python3-venv`；这需要 sudo，本项目不会代为执行，须先确认。
- 若 Milvus 报维度不匹配，需备份后重建向量库。若 `/ready` 显示 LLM 不可用，启动 Ollama 或切回 deterministic。
- WSL 当前约 7.4 GiB 内存；调整 `.wslconfig` 属于系统变更，应由用户确认执行。
- 批量 RCA 评测若使用外部已启动的 Ollama，请先确认它设置了 `LLAMA_ARG_CACHE_RAM=0`；一键脚本已自动设置。评测 checkpoint 位于结果文件同名 `.checkpoint.json`，成功完成后自动清理。

## 实现假设与需求差异

本轮轻量化约束优先于原始规格中的 Docker Compose/PostgreSQL/Redis/完整 Prometheus/React 默认路线，因此仓库不提供 `docker-compose.yml`。RAG、Agent 状态循环、记忆、SSE、工具执行、任务持久化、告警去重、动态重规划和审批门均为后端真实实现；生产化组件和上述限制没有伪装成已完成。

第三方公开数据和文档的许可证、来源与修改边界见 [`docs/THIRD_PARTY_NOTICES.md`](docs/THIRD_PARTY_NOTICES.md)。

## License

项目代码以 [Apache License 2.0](LICENSE) 发布。`data/public_datasets/` 和
`data/public_sources/` 中的第三方材料继续适用各自保留的上游许可证与署名要求。
