# 支付服务业务接入手册

支付服务负责支付单创建、支付渠道路由和结果回调。生产环境服务名为 `payment-service`，核心上游为 `gateway-service`，数据库为 payment-db。只读监控和日志查询可由 Agent 自动执行，重启、回滚和修改连接池属于 L3 操作。
