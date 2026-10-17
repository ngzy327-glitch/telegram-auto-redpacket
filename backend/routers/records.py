"""秒包记录路由：分页查询 + 统计。"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..db import get_session
from ..models import Account, GrabRecord, User

router = APIRouter(prefix="/api/records", tags=["records"])


async def _account_id_filter(user: User, s: AsyncSession) -> list[int] | None:
    """返回当前用户可见的 account_id 列表；管理员返回 None（不过滤）。"""
    if user.is_admin:
        return None
    rows = (await s.execute(
        select(Account.id).where(Account.owner_id == user.id))).scalars().all()
    return rows


@router.get("")
async def list_records(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    ok_only: bool = Query(False),
    user: User = Depends(get_current_user),
    s: AsyncSession = Depends(get_session),
):
    q = select(GrabRecord)
    if ok_only:
        q = q.where(GrabRecord.ok.is_(True))
    if not user.is_admin:
        ids = await _account_id_filter(user, s)
        if not ids:
            return {"total": 0, "page": page, "size": size, "items": []}
        q = q.where(GrabRecord.account_id.in_(ids))
    total = (await s.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
    rows = (await s.execute(
        q.order_by(desc(GrabRecord.created_at))
        .offset((page - 1) * size)
        .limit(size)
    )).scalars().all()
    return {
        "total": total,
        "page": page,
        "size": size,
        "items": [
            {
                "id": r.id,
                "account_name": r.account_name or "",
                "chat": r.chat or "",
                "target_bot": r.target_bot or "",
                "kind": r.kind or "",
                "wallet": r.wallet or "",
                "conditions": [c for c in (r.conditions or "").split(",") if c],
                "ok": r.ok,
                "amount": r.amount or "",
                "total_s": r.total_s,
                "report": r.report or "",
                "created_at": r.created_at.isoformat() if r.created_at else "",
            }
            for r in rows
        ],
    }


@router.get("/stats")
async def record_stats(user: User = Depends(get_current_user),
                       s: AsyncSession = Depends(get_session)):
    # 非管理员：限定到自己的账号
    account_ids: list[int] | None = None
    if not user.is_admin:
        account_ids = await _account_id_filter(user, s)
        if not account_ids:
            return {
                "total": 0, "success": 0, "failed": 0,
                "by_account": [], "by_bot": [], "by_wallet": [],
            }

    # 总数 + 成功数（直接在 where 上加账号过滤，不用 subquery 避免笛卡尔积）
    count_conds = []
    if account_ids is not None:
        count_conds.append(GrabRecord.account_id.in_(account_ids))
    row = (await s.execute(
        select(
            func.count(GrabRecord.id),
            func.count(GrabRecord.id).filter(GrabRecord.ok.is_(True)),
        ).where(*count_conds)
    )).one()
    total = row[0] or 0
    success = row[1] or 0

    # 三个分组聚合（用同一个 session 顺序执行，兼容 SQLite/PostgreSQL）
    async def _group(col, skip_null: bool):
        conds = [GrabRecord.ok.is_(True)]
        if account_ids is not None:
            conds.append(GrabRecord.account_id.in_(account_ids))
        if skip_null:
            conds.append(col.isnot(None))
        result = await s.execute(
            select(col, func.count(GrabRecord.id))
            .where(*conds)
            .group_by(col)
            .order_by(desc(func.count(GrabRecord.id)))
        )
        return result.all()

    by_account = await _group(GrabRecord.account_name, False)
    by_bot = await _group(GrabRecord.target_bot, True)
    by_wallet = await _group(GrabRecord.wallet, True)

    return {
        "total": total,
        "success": success,
        "failed": total - success,
        "by_account": [{"name": name or "未知", "count": cnt} for name, cnt in by_account],
        "by_bot": [{"bot": bot or "未知", "count": cnt} for bot, cnt in by_bot],
        "by_wallet": [{"wallet": w or "未知", "count": cnt} for w, cnt in by_wallet],
    }
