# RootCauseBench 子集来源

- 上游项目：https://github.com/edgedelta/root-cause-bench
- 许可证：Apache License 2.0（本目录保留 `LICENSE`）
- 获取日期：2026-09-06
- 获取方式：上游仓库 `main` 分支浅克隆后，保留 32 个场景的公开输入、任务说明和 ground truth。
- 本地用途：验证本项目对告警、指标、日志、Trace、部署记录及干扰证据的根因分析能力。

保留场景数：32。为控制本地仓库体积，仅排除以下 4 个单场景约 25 MB 的数据集：

- `ingest-partition-skew`
- `catalog-cache-key-cardinality`
- `orders-fanout-nplusone`
- `report-scheduler-lock-removal`

其余上游场景均保留；具体名称以本目录下含 `ground_truth.json` 的子目录为准。

评测时 `ground_truth.json` 只在模型完成预测后用于评分，不会拼入模型输入。
