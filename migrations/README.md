# Migrations

轻量 MVP 启动时通过 SQLAlchemy `create_all` 对 SQLite 进行幂等初始化。进入多实例或 PostgreSQL 阶段时，在本目录引入 Alembic 版本化迁移；当前不安装 PostgreSQL 或 Alembic。
