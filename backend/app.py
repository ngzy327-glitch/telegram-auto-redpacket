"""FastAPI 入口：CORS + 路由 + 启动建表/播种。

启动：uvicorn backend.app:app --reload --port 8000
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import select

from core.config import config

from .auth import get_current_user, hash_password, make_token, seed_admin_user, verify_password
from .db import SessionLocal, get_session, init_db
from .models import Account, User
from .routers import accounts, blocklist, groups, modules, overview, records, settings, webapp_logs
from .runner import runner
from .settings_store import seed_defaults

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
for _n in ("telethon", "httpx", "httpcore", "openai", "asyncio"):
    logging.getLogger(_n).setLevel(logging.WARNING)


class _AccessNoiseFilter(logging.Filter):
    """过滤 uvicorn access log 噪音：200 正常请求 + 头像 404（群组无头像属预期）。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name != "uvicorn.access":
            return True
        msg = record.getMessage()
        if "200" in msg:
            return False
        if "/api/groups/avatar/" in msg and "404" in msg:
            return False
        return True


logging.getLogger("uvicorn.access").addFilter(_AccessNoiseFilter())

log = logging.getLogger("backend")


async def _autostart():
    """根据 DB 中 enabled=True 的账号自动恢复监听。"""
    async with SessionLocal() as s:
        rows = (await s.execute(
            select(Account).where(
                Account.enabled.is_(True),
                Account.session_string.isnot(None)))).scalars().all()
    if not rows:
        return
    started = 0
    for acc in rows:
        try:
            await runner.start(acc)
            started += 1
        except Exception as e:
            log.error(f"自动启动账号 {acc.id} 失败: {e}")
    log.info(f"🔁 自动恢复 {started} 个账号监听")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    async with SessionLocal() as s:
        await seed_defaults(s)
        await seed_admin_user(s)
    log.info("✅ 后端就绪（DB 已建表/播种）")
    await _autostart()
    yield
    await runner.stop_all()
    from core.cache import close as close_redis
    await close_redis()


app = FastAPI(title="浅秋科技v1", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # 本地内网控制台，放开跨域
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(accounts.router)
app.include_router(settings.router)
app.include_router(modules.router)
app.include_router(groups.router)
app.include_router(records.router)
app.include_router(webapp_logs.router)
app.include_router(blocklist.router)
app.include_router(overview.router)


_ERR_MAP = {
    "SESSION_ENCRYPT_KEY 未配置": "服务端 Session 解密密钥未配置，请检查 .env 中的 SESSION_ENCRYPT_KEY",
    "session 失效": "账号 Session 已失效，请重新登录",
}


@app.exception_handler(Exception)
async def _global_error(request: Request, exc: Exception):
    msg = str(exc)
    for pattern, zh in _ERR_MAP.items():
        if pattern in msg:
            msg = zh
            break
    log.error("请求 %s 异常: %s", request.url.path, exc)
    return JSONResponse(status_code=500, content={"detail": msg})


@app.get("/api/health")
async def health():
    return {"ok": True}


# ── Web 用户认证 ──────────────────────────────────────────────

class _LoginIn(BaseModel):
    username: str
    password: str


class _UserCreate(BaseModel):
    username: str
    password: str
    is_admin: bool = False


@app.post("/api/auth/login")
async def auth_login(body: _LoginIn, s=Depends(get_session)):
    from sqlalchemy import select as _sel
    user = (await s.execute(_sel(User).where(User.username == body.username))).scalar_one_or_none()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    token = make_token(user.id)
    return {"token": token, "user": {"id": user.id, "username": user.username, "is_admin": user.is_admin}}


@app.get("/api/auth/me")
async def auth_me(user: User = Depends(get_current_user)):
    return {"id": user.id, "username": user.username, "is_admin": user.is_admin}


@app.post("/api/auth/users")
async def create_user(body: _UserCreate, user: User = Depends(get_current_user),
                      s=Depends(get_session)):
    if not user.is_admin:
        raise HTTPException(403, "仅管理员可创建用户")
    from sqlalchemy import select as _sel
    exists = (await s.execute(_sel(User).where(User.username == body.username))).scalar_one_or_none()
    if exists:
        raise HTTPException(400, "用户名已存在")
    u = User(username=body.username, password_hash=hash_password(body.password),
             is_admin=body.is_admin)
    s.add(u)
    await s.commit()
    return {"ok": True, "id": u.id, "username": u.username}


@app.get("/api/auth/users")
async def list_users(user: User = Depends(get_current_user), s=Depends(get_session)):
    if not user.is_admin:
        raise HTTPException(403, "仅管理员可查看用户列表")
    from sqlalchemy import select as _sel
    rows = (await s.execute(_sel(User).order_by(User.id))).scalars().all()
    return [{"id": r.id, "username": r.username, "is_admin": r.is_admin,
             "created_at": r.created_at} for r in rows]


@app.delete("/api/auth/users/{uid}")
async def delete_user(uid: int, user: User = Depends(get_current_user),
                      s=Depends(get_session)):
    if not user.is_admin:
        raise HTTPException(403, "仅管理员可删除用户")
    if uid == user.id:
        raise HTTPException(400, "不能删除自己")
    target = await s.get(User, uid)
    if not target:
        raise HTTPException(404, "用户不存在")
    await s.delete(target)
    await s.commit()
    return {"ok": True}


_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if _DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="static")

    @app.get("/{full_path:path}")
    async def _spa(full_path: str):
        file = _DIST / full_path
        if file.is_file():
            return FileResponse(file)
        return FileResponse(_DIST / "index.html")
