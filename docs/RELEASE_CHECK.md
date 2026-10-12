# 发布验证记录

验证日期：2026-10-12。发布目标：GitHub 源码与同一提交的作品 ZIP。

## 本次整理

- 纳入现有审批回归修复：用户明确要求的重启审批步骤不能被 Replanner 提前跳过；审批卡片显示在轨迹前并滚动定位。
- 补充作品导航、五分钟演示和无模型运行说明；移除演示文档中的个人绝对路径。
- 明确审批不执行真实生产重启、进程内任务不自动续跑，以及历史评测的统计口径。

## 本次实际执行

环境：已有 WSL2 Ubuntu-24.04 项目虚拟环境；未新安装依赖、未下载模型。

```bash
cd /path/to/trace2root-agent  # 在项目根目录执行
bash scripts/test.sh
# 36 passed, 1 warning in 58.66s

.venv/bin/python -m compileall -q backend scripts mock_services
bash -n scripts/setup.sh scripts/start.sh scripts/start_all.sh scripts/test.sh scripts/demo_preflight.sh
# STATIC_CHECKS_OK
```

测试使用 deterministic LLM、hashing Embedding 与 lightweight Reranker，覆盖单元/API 集成/MCP 等测试以及重规划不能绕过审批的回归用例。警告来自 LangGraph 依赖的序列化默认值弃用提示。

本次没有重跑真实 Ollama/BGE 全链路、浏览器人工视觉验收或完整评测；`evaluation/results/` 是此前保留的实验记录，不是本次新生成的数据。已有环境测试通过，不代表全新机器安装已验证。

## 分发原则

ZIP 使用 `git archive HEAD` 从已提交源码生成，包含代码、示例、公开资料授权、历史评测与文档；不包含 `.git`、`.env`、虚拟环境、模型权重、运行数据库、上传文件、个人简历和临时文件。模型与依赖需接收者按 README 另行准备。

仅建议本机演示。公开网络部署前仍需补全认证鉴权、真实数据源适配、任务可靠恢复、审批后执行控制和独立评测。
