# Trace2Root 作品投递说明

## 项目定位

面向告警排查中指标、日志和手册分散的问题，构建主诊断 Agent 与 Knowledge Agent 协作的本地故障分析原型。模型生成调查计划，根据工具反馈调整方向；执行层约束工具权限、查询范围与迭代次数，输出根因候选和可追溯证据。

仓库：https://github.com/sansan9494/trace2root-agent

## 建议查看顺序

1. README：架构、安装、评测与边界。
2. `backend/app/agents/ops.py`：Plan–Execute–Replan 与报告生成。
3. `backend/app/agents/knowledge.py`：查询规划、检索和证据评审。
4. `backend/app/tools.py`、`security.py`、`mcp.py`：工具协议、校验和风险控制。
5. `tests/`、`evaluation/`：回归测试与历史逐例评测结果。

## 不下载模型的快速演示

环境：Ubuntu 24.04 或 Windows 的 WSL2，Python 3.11+。请将 ZIP 解压到 Linux 用户目录，例如 `~/projects/trace2root-agent`；不支持直接用 Windows Python 运行 Milvus Lite。

以下安装会下载 Python 依赖，但不下载模型、不安装系统服务、不使用 sudo：

```bash
cd ~/projects/trace2root-agent
bash scripts/setup.sh
export LLM_PROVIDER=deterministic
export EMBEDDING_PROVIDER=hashing
export RERANKER_PROVIDER=lightweight
export MODEL_WARMUP=false
.venv/bin/python scripts/import_samples.py
bash scripts/start.sh
```

同一终端中的环境变量覆盖 `.env`。浏览器打开 http://127.0.0.1:8000 。这是确定性功能演示，不是大模型效果演示。依赖安装后可离线运行。

真实模型模式使用 Ollama `qwen3:4b`、CPU `bge-small-zh-v1.5` 与 `bge-reranker-base`。退出上述终端或清除覆盖变量后，按 README 完成完整安装和模型准备。ZIP 不附带模型、虚拟环境和数据库，不能解压后免安装直接运行。

## 五分钟演示

1. 展示依赖状态，并说明当前是模型模式还是确定性回退。
2. 选择 `payment-service`、`production`、`HighLatency`，输入“分析支付服务高延迟，结合指标、日志和手册给出根因与建议”。
3. 查看计划、工具调用和 replan 事件；解释模型决策与 Harness 干预来源。
4. 查看支持证据、反证、未确认项与处置建议，强调建议不等于已执行。
5. 新建目标“排查支付服务高延迟，确认根因后重启支付服务”，查看人工审批卡片，演示拒绝；需要批准示例时另建任务选择批准演示。两种选择都不重启真实服务。

若未进入审批，先查看任务状态和事件，不把普通报告中的“重启建议”当作待审批请求。模型请求失败会明确降级；不得将降级结果称为真实模型推理。

## 设计取舍与边界

- 使用 SQLite、Milvus Lite 降低单机部署成本，未部署 Redis、PostgreSQL 或完整监控系统。
- MCP 协议调用实际存在，但工具返回的是模拟运维数据；不代表生产环境接入。
- 分块按字符计数，上限 1800；220 字符重叠只用于超长块内部切分，不是 token 分块或父子块检索。
- 默认候选配置为 12、最终 5；具体调用可覆盖候选数，Knowledge Agent 默认传入 5。多查询去重后截取，尚未实现跨查询统一精排。
- 记忆主要为对话入口的 SQLite 消息、摘要与词项匹配，不宣称 Redis 分层记忆或跨任务自主学习。
- 后台任务进程内运行；审批为演示，不具备生产认证、多租户隔离、持久化断点恢复和真实写操作能力。仅绑定本机地址使用。
- 历史评测分为检索、给定证据下的模型根因预测和执行控制测试，不将它们合并成“生产准确率”。

## 提交作品时可用的介绍

Trace2Root 是基于 LangGraph 的故障诊断智能体原型，结合主 Agent 动态调查、Knowledge Agent 查证和 MCP 工具调用，通过 Harness 约束权限与高风险审批，形成可追溯诊断报告。提供本地 Web 演示、回归测试和分层评测记录；运维工具采用 Mock 数据。
