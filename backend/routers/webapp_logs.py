"""WebApp 日志路由：分页查询 + 清空。

一次 webapp 领取对应一行，字段只保留关键节点：
点击网页(ms_webview) → 执行操作(ms_submit) → 等待回执(ms_result) → 结果/金额。
"""
import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, func, select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..db import get_session
from ..models import Account, User, WebappLog

router = APIRouter(prefix="/api/webapp-logs", tags=["webapp_logs"])


async def _account_id_filter(user: User, s: AsyncSession) -> list[int] | None:
    """返回当前用户可见的 account_id 列表；管理员返回 None（不过滤）。"""
    if user.is_admin:
        return None
    rows = (await s.execute(
        select(Account.id).where(Account.owner_id == user.id))).scalars().all()
    return rows


def _serialize(r: WebappLog) -> dict:
    try:
        lines = json.loads(r.detail) if r.detail else []
    except Exception:
        lines = []
    return {
        "id": r.id,
        "account_name": r.account_name or "",
        "chat": r.chat or "",
        "bot": r.bot or "",
        "step": r.step,
        "ok": r.ok,
        "fail_reason": r.fail_reason or "",
        "amount": r.amount or "",
        "winner": r.winner or "",
        "output": r.output or "",
        "ms_webview": r.ms_webview,
        "ms_submit": r.ms_submit,
        "ms_result": r.ms_result,
        "total_ms": r.total_ms,
        "lines": lines,
        "created_at": r.created_at.isoformat() if r.created_at else "",
    }


@router.get("")
async def list_logs(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    result: str = Query("", pattern="^(ok|fail)?$"),
    user: User = Depends(get_current_user),
    s: AsyncSession = Depends(get_session),
):
    q = select(WebappLog)
    if result == "ok":
        q = q.where(WebappLog.ok.is_(True))
    elif result == "fail":
        q = q.where(WebappLog.ok.is_(False))
    if not user.is_admin:
        ids = await _account_id_filter(user, s)
        if not ids:
            return {"total": 0, "page": page, "size": size, "items": []}
        q = q.where(WebappLog.account_id.in_(ids))
    total = (await s.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
    rows = (await s.execute(
        q.order_by(desc(WebappLog.created_at), desc(WebappLog.id))
        .offset((page - 1) * size)
        .limit(size)
    )).scalars().all()
    return {
        "total": total,
        "page": page,
        "size": size,
        "items": [_serialize(r) for r in rows],
    }


@router.delete("")
async def clear_logs(
    user: User = Depends(get_current_user),
    s: AsyncSession = Depends(get_session),
):
    """清空日志：管理员清空全部；普通用户只清空自己账号产生的日志。"""
    stmt = delete(WebappLog)
    if not user.is_admin:
        ids = await _account_id_filter(user, s)
        if not ids:
            return {"ok": True, "deleted": 0}
        stmt = stmt.where(WebappLog.account_id.in_(ids))
    deleted = (await s.execute(stmt)).rowcount or 0
    await s.commit()
    return {"ok": True, "deleted": deleted}
