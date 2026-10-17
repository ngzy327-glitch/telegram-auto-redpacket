"""屏蔽规则路由：群组 / 频道 / 用户 / 机器人 黑名单 + 私信总开关。

命中屏蔽的红包来源会在 grabber 早期被直接忽略（不检测 / 不领取 / 不通知 / 不广播）。
增删改后热推送到所有运行中 grabber，无需重启。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..db import get_session
from ..models import BlockRule, User
from ..runner import runner
from ..schemas import BlockPrivateUpdate, BlockRuleIn, BlockRuleOut
from ..settings_store import get_settings, load_blocklist, update_settings

log = logging.getLogger("backend.blocklist")
router = APIRouter(prefix="/api/blocklist", tags=["blocklist"])

_VALID_TYPES = {"group", "channel", "user", "bot"}


async def _hot_reload(s: AsyncSession):
    """重新装载屏蔽集合 + 私信开关，热推送到运行中 grabber。"""
    chats, senders = await load_blocklist(s)
    gs = await get_settings(s)
    runner.hot_reload_blocklist(chats, senders, (gs.get("block_private") or "0") == "1")


@router.get("")
async def list_rules(user: User = Depends(get_current_user),
                     s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(
        select(BlockRule).order_by(desc(BlockRule.created_at)))).scalars().all()
    gs = await get_settings(s)
    counts = {t: 0 for t in _VALID_TYPES}
    for r in rows:
        if r.target_type in counts:
            counts[r.target_type] += 1
    return {
        "rules": [BlockRuleOut.model_validate(r).model_dump(mode="json") for r in rows],
        "block_private": (gs.get("block_private") or "0") == "1",
        "counts": counts,
    }


@router.post("")
async def add_rule(body: BlockRuleIn, user: User = Depends(get_current_user),
                   s: AsyncSession = Depends(get_session)):
    if not user.is_admin:
        raise HTTPException(403, "仅管理员可修改屏蔽规则")
    if body.target_type not in _VALID_TYPES:
        raise HTTPException(400, "屏蔽类型不合法（应为 群组/频道/用户/机器人）")
    if body.target_id is None or (isinstance(body.target_id, str) and not body.target_id.strip()):
        raise HTTPException(400, "缺少屏蔽目标 ID")

    target_id = body.target_id
    target_name = body.target_name

    # 所有类型：若填的是 @用户名（字符串），统一解析为数字 ID 后存储
    if isinstance(target_id, str) and target_id.strip().startswith("@"):
        resolved = await runner.resolve_username(target_id)
        if resolved is None:
            raise HTTPException(
                400,
                f"无法解析用户名 {target_id}：请确认用户名正确，且至少有一个账号处于监听状态")
        target_id, resolved_name = resolved
        if not target_name:
            target_name = resolved_name or target_id.strip().lstrip("@")

    # 到此 target_id 必须是数字（用户/机器人正数，群/频道负数）
    try:
        target_id = int(target_id)
    except (TypeError, ValueError):
        raise HTTPException(400, "目标 ID 格式不合法，应为数字 ID 或 @用户名")

    # 群组 / 频道：必须是负数
    if body.target_type in ("group", "channel") and target_id >= 0:
        raise HTTPException(400, "群组/频道 ID 必须为负数（如 -100xxxx），或填写 @用户名")

    existing = (await s.execute(select(BlockRule).where(
        BlockRule.target_type == body.target_type,
        BlockRule.target_id == int(target_id)))).scalar_one_or_none()
    if existing:
        existing.target_name = target_name or existing.target_name
        if body.note is not None:
            existing.note = body.note
        row = existing
    else:
        row = BlockRule(target_type=body.target_type, target_id=int(target_id),
                        target_name=target_name, note=body.note)
        s.add(row)
    await s.commit()
    await s.refresh(row)
    await _hot_reload(s)
    return BlockRuleOut.model_validate(row).model_dump(mode="json")


@router.delete("/{rule_id}")
async def remove_rule(rule_id: int, user: User = Depends(get_current_user),
                      s: AsyncSession = Depends(get_session)):
    if not user.is_admin:
        raise HTTPException(403, "仅管理员可修改屏蔽规则")
    row = await s.get(BlockRule, rule_id)
    if not row:
        raise HTTPException(404, "屏蔽规则不存在")
    await s.delete(row)
    await s.commit()
    await _hot_reload(s)
    return {"ok": True}


@router.put("/private")
async def set_private(body: BlockPrivateUpdate, user: User = Depends(get_current_user),
                      s: AsyncSession = Depends(get_session)):
    if not user.is_admin:
        raise HTTPException(403, "仅管理员可修改屏蔽规则")
    await update_settings(s, {"block_private": "1" if body.enabled else "0"})
    await _hot_reload(s)
    return {"ok": True, "block_private": body.enabled}
