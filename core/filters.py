"""领取策略过滤：关键词 / 币种 / 金额 / 条件 四道闸。

与账号身份无关，检测侧全局判定一次（grabber 早期去重之后），
命中即整体跳过：不分发领取、不进广播频道、不消耗重试次数。

设计原则：**识别不出的信息不拦**（金额/币种解析失败时放行，宁可多抢不误杀）。
配置来源：Web 系统配置（DB），经 RunConfig 下发，保存后热更新。
"""
import logging
import re

from .detector import CONDITION_LABELS, WALLET_NATIVE_COINS, clean

log = logging.getLogger("core.filters")

# 金额文本 → (数值, 币种)。币种只认 ASCII 字母（与 detector._TOTAL_RE 口径一致）
_AMOUNT_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*([A-Za-z]+)?")

# 任意币种兜底规则的 key（金额下限表里用）
ANY_CURRENCY = "*"


def parse_amount(amount_text: str | None) -> tuple[float | None, str | None]:
    """'1,000.5 KKCOIN' → (1000.5, 'KKCOIN')；'200' → (200.0, None)；解析失败 → (None, None)。"""
    if not amount_text:
        return None, None
    m = _AMOUNT_RE.search(amount_text)
    if not m:
        return None, None
    try:
        value = float(m.group(1).replace(",", ""))
    except ValueError:
        return None, None
    currency = (m.group(2) or "").upper() or None
    return value, currency


def check_filters(rp, text: str, run) -> str | None:
    """按 RunConfig 中的领取策略检查红包。返回跳过原因（中文），None=放行。

    rp:   detector.RedPacket（用 amount_text / conditions / wallet）
    text: 消息文本（关键词/币种关键词匹配用）
    run:  RunConfig（filter_* 字段）
    """
    # 0) 钱包黑名单：按已识别的钱包直接拦（最稳，不依赖文案/金额解析）
    block_wallets: set = getattr(run, "filter_block_wallets", None) or set()
    wallet = getattr(rp, "wallet", None)
    if wallet and wallet in block_wallets:
        from .detector import WALLET_LABELS
        return f"钱包 {WALLET_LABELS.get(wallet, wallet)} 在黑名单"

    # 1) 关键词黑名单：原文 + 去反爬清洗后双重子串匹配
    keywords: list[str] = getattr(run, "filter_keywords", None) or []
    if keywords:
        raw = text or ""
        cleaned = clean(raw)
        for kw in keywords:
            k = (kw or "").strip()
            if not k:
                continue
            ck = clean(k)
            if k in raw or (ck and ck in cleaned):
                return f"命中关键词「{k}」"

    # 1.5) 币种/代币关键词黑名单：扫描整条消息文本（独立于金额解析，
    #      避免备注里的金额干扰币种识别）。大小写不敏感，去反爬清洗后子串匹配。
    block_tokens: set = getattr(run, "filter_block_tokens", None) or set()
    if block_tokens:
        raw = (text or "").lower()
        cleaned = clean(text or "").lower()
        for tok in block_tokens:
            t = (tok or "").strip().lower()
            if not t:
                continue
            ct = clean(t)
            if t in raw or (ct and ct in cleaned):
                return f"命中币种关键词「{t}」"

    # 1.6) 钱包原生币种拦截：按钱包识别其原生币（如 kkpay→kkcoin），
    #      即使红包备注写"1000u"实际是 kkcoin 也能拦住，不依赖文案解析。
    if block_tokens and wallet and wallet in WALLET_NATIVE_COINS:
        for native in WALLET_NATIVE_COINS[wallet]:
            n = native.strip().lower()
            if not n:
                continue
            for tok in block_tokens:
                t = (tok or "").strip().lower()
                if t and (t == n or t in n or n in t):
                    return f"钱包 {wallet} 原生币「{native}」命中屏蔽"

    value, currency = parse_amount(getattr(rp, "amount_text", None))

    # 2) 币种白/黑名单（未识别出币种 → 不拦）
    mode: str = getattr(run, "filter_currency_mode", "off") or "off"
    cur_set: set = getattr(run, "filter_currencies", None) or set()
    if currency and mode != "off" and cur_set:
        if mode == "white" and currency not in cur_set:
            return f"币种 {currency} 不在白名单"
        if mode == "black" and currency in cur_set:
            return f"币种 {currency} 在黑名单"

    # 3) 金额下限：按币种查规则，没有则用 '*' 兜底（未识别出金额 → 不拦）
    mins: dict = getattr(run, "filter_min_amounts", None) or {}
    if value is not None and mins:
        limit = mins.get(currency) if currency else None
        if limit is None:
            limit = mins.get(ANY_CURRENCY)
        if limit is not None and value < float(limit):
            unit = currency or ""
            return f"总金额 {value:g}{unit} 低于下限 {float(limit):g}"

    # 3.5) 份数上限：红包总份数超过阈值即跳过（空投类红包份数极大，如 ♾️ 无限份）。
    #      total_count 取自「剩余 x/N」的分母 N；0 或未设置表示不过滤。
    max_pkts = getattr(run, "filter_max_packets", 0) or 0
    total_count = getattr(rp, "total_count", None)
    if max_pkts and total_count:
        try:
            n = int(total_count)
            if n > int(max_pkts):
                return f"份数 {n} 超过上限 {max_pkts}（疑似空投）"
        except (TypeError, ValueError):
            pass

    # 4) 条件红包：含勾选类型的领取条件即跳过（premium/group/user/turnover/winloss）
    skip_conds: set = getattr(run, "filter_skip_conditions", None) or set()
    if skip_conds:
        for cond in (getattr(rp, "conditions", None) or []):
            base = cond.split(":", 1)[0]
            if base in skip_conds:
                return f"含「{CONDITION_LABELS.get(base, base)}」条件"
    return None
