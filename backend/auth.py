"""Web 用户认证：密码哈希、token 签发/验证、当前用户依赖。

- 密码：pbkdf2_hmac(sha256)，salt 内嵌，格式 pbkdf2$iter$salt$hash
- Token：HMAC-SHA256 签名，格式 user_id.expire.signature（无第三方依赖）
- 首次启动若无用户，自动创建 admin（密码来自 .env ADMIN_PASSWORD，默认 admin123）
"""
import hashlib
import hmac
import os
import time
from typing import Optional

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import config

from .db import get_session
from .models import User

_TOKEN_SECRET = os.getenv("TOKEN_SECRET", os.getenv("SESSION_ENCRYPT_KEY", "changeme-secret"))
_TOKEN_TTL = 7 * 86400  # 7 天
_PBKDF2_ITERS = 100_000


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, _PBKDF2_ITERS)
    import base64
    return f"pbkdf2${_PBKDF2_ITERS}${base64.b64encode(salt).decode()}${base64.b64encode(h).decode()}"


def verify_password(pw: str, hashed: str) -> bool:
    try:
        import base64
        parts = hashed.split("$")
        if len(parts) != 4 or parts[0] != "pbkdf2":
            return False
        iters = int(parts[1])
        salt = base64.b64decode(parts[2])
        expected = base64.b64decode(parts[3])
        actual = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, iters)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def make_token(user_id: int) -> str:
    import base64
    exp = int(time.time()) + _TOKEN_TTL
    payload = f"{user_id}.{exp}"
    sig = hmac.new(_TOKEN_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}.{sig}".encode()).decode().rstrip("=")


def verify_token(token: str) -> Optional[int]:
    import base64
    try:
        # 补 padding
        pad = "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(token + pad).decode()
        user_id_str, exp_str, sig = raw.rsplit(".", 2)
        payload = f"{user_id_str}.{exp_str}"
        expected_sig = hmac.new(_TOKEN_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        if int(exp_str) < time.time():
            return None
        return int(user_id_str)
    except Exception:
        return None


async def get_current_user(
    authorization: Optional[str] = Header(None),
    token: Optional[str] = None,
    s: AsyncSession = Depends(get_session),
) -> User:
    """从 Authorization: Bearer <token> 或 ?token= 查询参数解析当前用户。

    支持 query 参数是为了让 <img src> 等浏览器原生请求也能通过认证
    （头像等图片资源无法在请求头里携带 Bearer token）。
    """
    raw = token
    if authorization and authorization.lower().startswith("bearer "):
        raw = authorization.split(" ", 1)[1].strip()
    if not raw:
        raise HTTPException(401, "未登录")
    user_id = verify_token(raw)
    if user_id is None:
        raise HTTPException(401, "登录已过期，请重新登录")
    user = await s.get(User, user_id)
    if not user:
        raise HTTPException(401, "用户不存在")
    return user


async def seed_admin_user(session: AsyncSession):
    """首次启动创建默认管理员（admin / ADMIN_PASSWORD 或 admin123）。"""
    existing = (await session.execute(select(User))).scalars().first()
    if existing:
        return
    admin_pw = os.getenv("ADMIN_PASSWORD", "admin123")
    admin = User(username="admin", password_hash=hash_password(admin_pw), is_admin=True)
    session.add(admin)
    await session.commit()
    import logging
    logging.getLogger("backend.auth").info(
        f"已创建默认管理员: admin / {admin_pw}（请在 .env 设置 ADMIN_PASSWORD 后重启）")
