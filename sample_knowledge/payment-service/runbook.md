# 支付服务高延迟告警处理手册

## 现象

`payment-service` 的 P95 延迟持续超过 800ms 时触发 HighLatency。先检查请求量、错误率和数据库连接池，再检查支付网关上游依赖。

## 排查步骤

1. 查询 `http_request_duration_seconds` 的 P95 和错误率，确认是否为真实流量异常。
2. 检查 `db_pool_usage_ratio`。高于 0.9 时排查慢 SQL 和连接泄漏；正常时不要盲目扩容连接池。
3. 搜索 `upstream timeout`、`connection refused` 和 Trace ID。
4. 如果上游超时占比上升，联系网关值班并考虑开启降级；生产重启或回滚必须人工审批。

## 恢复标准

P95 连续十分钟低于 500ms，错误率低于 1%，且无新增高等级告警。
