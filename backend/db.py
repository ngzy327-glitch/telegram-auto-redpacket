"""数据库连接与会话（PostgreSQL / SQLite 兼容）。"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from core.config import config

_is_sqlite = config.database_url.startswith("sqlite")

# SQLite 用 StaticPool（内存库需单连接），PostgreSQL 用连接池参数。
if _is_sqlite:
    from sqlalchemy.pool import StaticPool
    engine = create_async_engine(
        config.database_url,
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
else:
    # 远程 RDS 连接池：扩大常驻连接、定期回收避免被服务端空闲断连后首查卡顿。
    engine = create_async_engine(
        config.database_url,
        echo=False,
        pool_pre_ping=True,     # 取连接前 ping，避开失效连接
        pool_size=10,           # 常驻连接（默认 5）
        max_overflow=20,        # 峰值额外连接（默认 10）
        pool_recycle=1800,      # 30 分钟回收，规避 RDS 空闲超时
        pool_timeout=10,        # 取连接最多等 10s（默认 30s），快速失败
    )
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


# 轻量列迁移：create_all 只建缺失的表，不补缺失的列。新增列在此声明（幂等）。
_COLUMN_MIGRATIONS = [
    "ALTER TABLE monitored_groups ADD COLUMN IF NOT EXISTS chat_type VARCHAR(16) DEFAULT 'group'",
    "ALTER TABLE monitored_groups ADD COLUMN IF NOT EXISTS pinned BOOLEAN DEFAULT FALSE",
    "ALTER TABLE monitored_groups ADD COLUMN IF NOT EXISTS avatar_b64 TEXT",
    "ALTER TABLE grab_records ADD COLUMN IF NOT EXISTS wallet VARCHAR(16)",
    "ALTER TABLE grab_records ADD COLUMN IF NOT EXISTS conditions VARCHAR(255)",
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS proxy VARCHAR(255)",
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS avatar_b64 TEXT",
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS owner_id INTEGER",
]


async def init_db():
    """建表（首次启动自动创建）+ 增量列迁移（SQLite 跳过：create_all 已含全部列）。"""
    from . import models  # noqa: F401  注册映射
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if not _is_sqlite:
            for stmt in _COLUMN_MIGRATIONS:
                await conn.execute(text(stmt))


async def get_session():
    async with SessionLocal() as session:
        yield session
