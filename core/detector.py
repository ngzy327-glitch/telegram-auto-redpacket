"""红包识别与分类：把一条消息归类并提取完整识别上下文 + 领取载荷。

classify(msg) 返回 RedPacket，含三层信息：
1. 识别维度（全 kind 共用）：wallet 钱包 / state 状态 / conditions 条件 / 金额·剩余·发送者
2. kind 分类（领取方式）：direct / captcha / dm_captcha / webapp / fulilai / locked
3. 领取载荷：按 kind 取用（direct_data / start_param / captcha_buttons / fulilai_hash ...）

领取分类优先级：direct > captcha > fulilai > webapp(vweb--) > dm_captcha。

- direct     : 含关键词 callback 按钮 → 点一下即到账（最快）
- captcha    : 文本含算式 + callback 选项按钮 → 群内解题点按钮（窗口验证码）
- fulilai    : 按钮 URL 含 fllqb hash → hCaptcha token 池 + HTTP 领取（福利来红包）
- webapp     : start_param 以 vweb-- 开头 → /start → RequestWebView → 网页 AI 图片验证码
- dm_captcha : 其余 t.me?start= URL 按钮 → /start → bot 私聊出回执/解题（私信验证）
- locked     : 红包未解锁（含「解锁」按钮）→ 只通知不领取，等解锁后的编辑事件再领

状态机：detect_state 优先于领取分类。exhausted→None；locked→kind='locked'；
claimable→走领取分类。编辑事件（解锁/倒计时）会重复触发，靠 grabber 去重。
"""
import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

from telethon.tl import types

from .buttons import btn_text, btn_type, cb_data, btn_url
from .charset import (MATH_RE as _MATH_RE, buttons_are_digit, normalize_char,
                      normalize_operand)
from .config import config

# telethon 1.45+ 把按钮结构改为 KeyboardInlineButton(text, type=...)，
# 旧 KeyboardButton* 类名虽保留为别名但 isinstance 恒为 False；
# 所有按钮类型判断/属性访问统一走 core.buttons 兼容层。

_ZERO_WIDTH = "\u200b\u200c\u200d\ufeff\u2060"

# ── 五类红包的唯一文案来源（grabber/runner 共用，前端 RecordsView 镜像保持一致）──
KIND_LABELS: dict[str, str] = {
    "direct": "关键词领取",
    "captcha": "窗口验证码",
    "dm_captcha": "私信验证",
    "webapp": "网页验证",
    "fulilai": "福利来红包",
    "locked": "未解锁红包",
}

# 真·网页验证（RequestWebView 图片验证码）的 bot 用户名白名单（小写）。
# 仅作回退：现在主要靠 start_param 前缀 `vweb--` 判定 webapp（见 classify）。
WEBAPP_BOTS: set[str] = {"okpay", "kkpay"}

# ── 钱包识别 ──────────────────────────────────────────────────────
# 新增钱包来源：只需在下面三张表各加一行（bot_id / username / 中文标签）。
WALLET_BOTS: dict[int, str] = {
    5703356189: "okpay",
    6505600437: "kkpay",
    8656187862: "wlqb",     # @HtjiamiBOT
    7826631206: "dlqb",
    5495837487: "fllqb",    # @fllqb 福利来
    8721454389: "tcqb",     # 汤臣钱包
}

# bot username（小写）→ 钱包标识；用于无 via_bot/from 时从 URL 按钮兜底识别
WALLET_USERNAMES: dict[str, str] = {
    "okpay": "okpay",
    "kkpay": "kkpay",
    "htjiamibot": "wlqb",
    "dlqb": "dlqb",
    "fllqb": "fllqb",
    "tcqb": "tcqb",       # 汤臣钱包
}

# callback data 前缀 → 钱包（bot_id 识别失败时的回退；kkpay/dlqb 前缀相同，靠 bot_id 区分）
_CB_WALLET_PREFIXES: list[tuple[str, str]] = [
    ("redpacket--", "okpay"),
    ("receive_redpacket", "wlqb"),
    ("locked_redpacket", "wlqb"),
    ("mhblock", "fllqb"),
    ("rpi--", "kkpay"),
    ("rpg--", "kkpay"),
]

WALLET_LABELS: dict[str, str] = {
    "okpay": "OKPay钱包", "kkpay": "KKPay钱包", "wlqb": "未来钱包",
    "dlqb": "达利钱包", "fllqb": "福利来钱包", "tcqb": "汤臣钱包",
    "unknown": "未知钱包",
}

CONDITION_LABELS: dict[str, str] = {
    "premium": "Premium会员", "group": "指定群组", "user": "指定用户",
    "turnover": "流水要求", "winloss": "输赢要求", "locked": "已锁定",
}

# ── 条件解析（只匹配中英文关键词，忽略 emoji 前缀——各钱包装饰不同）──────
# 只覆盖本项目 5 个钱包（okpay/kkpay/wlqb/dlqb/fllqb）实测存在的条件：
# premium / 指定群 / 指定用户 / 流水 / 输赢。红包监控.py 里的发言/充值/关注公众号
# 等是别家 bot 的玩法，本项目用不到，不引入以免误判。
_PREMIUM_RE = re.compile(r"仅限\s*Premium\s*会员")
_GROUP_RE = re.compile(r"仅限群组成员领取")
_GROUP_NAME_RE = re.compile(r"【\s*(.+?)\s*】")
_USER_RE = re.compile(r"专属红包")
_USER_NAME_RE = re.compile(r"专属红包[:：]\s*(.+?)(?:\n|$)")
_TURNOVER_RE = re.compile(r"流水要求|流水金额")
_TURNOVER_AMOUNT_RE = re.compile(r"流水金额[:：]\s*(.+?)(?:\n|$)")
_WINLOSS_RE = re.compile(r"输赢要求|输赢金额")
_WINLOSS_AMOUNT_RE = re.compile(r"输赢金额[:：]\s*(.+?)(?:\n|$)")
_LOCKED_TEXT_RE = re.compile(r"红包已锁定")

# ── 元数据解析 ──────────────────────────────────────────────────
# 金额+币种：币种只认 ASCII 字母（CNY/KKCOIN/WLCOIN/USDT），避免误吞「剩余」等中文
# total_count 直接取「剩余 x/N」的分母 N，不另写 共X个/有效期 等本项目钱包没有的字段。
_TOTAL_RE = re.compile(r"总金额[:：]?\s*([\d,]+(?:\.\d+)?)\s*([A-Za-z]+)?")
_REMAIN_RE = re.compile(r"剩余[:：]?\s*(\d+)\s*/\s*(\d+)")
_SENDER_RE = re.compile(r"🧧\s*(.+?)\s*发送了一个红包")

# ── 锁定状态：按钮文字含「解锁/已锁定」或 callback data 含以下标记 ──────
_LOCK_DATA_MARKERS = ("unlock_lucky", "unlock_redpacket", "locked_redpacket", "mhblock")


def _raw(msg) -> str:
    """优先用 raw_text（无 markdown 噪声），便于元数据/条件正则提取。"""
    return getattr(msg, "raw_text", None) or getattr(msg, "text", None) or ""


def _cb_data_str(btn) -> str:
    """callback 按钮 data → 小写字符串（非 callback 或无 data 返回 ''）。"""
    data = cb_data(btn)
    if not data:
        return ""
    try:
        return data.decode("utf-8", "ignore").lower()
    except Exception:
        return ""


def detect_wallet(msg, whitelist: set[str] | None = None) -> str:
    """识别红包来源钱包：via_bot/from → 白名单用户名 → callback 前缀 → URL bot 名。

    whitelist（控制台「钱包白名单」，热更新）：bot username 集合（小写不带@）。
    命中白名单 → 返回该 username 作为钱包标识（未知钱包变已知，URL/小程序领取路径放行）。
    仅影响识别，不越过屏蔽策略与领取过滤（grabber._blocked / filters.check_filters）。"""
    via = getattr(msg, "via_bot_id", None)
    if via and via in WALLET_BOTS:
        return WALLET_BOTS[via]
    from_id = getattr(getattr(msg, "from_id", None), "user_id", None)
    if from_id and from_id in WALLET_BOTS:
        return WALLET_BOTS[from_id]

    # 白名单（via_bot 用户名优先；via_bot 对象在事件里不一定带，尽力取）
    if whitelist:
        uname = (getattr(getattr(msg, "via_bot", None), "username", None) or "")
        uname = uname.strip().lstrip("@").lower()
        if uname and uname in whitelist:
            return uname

    # callback data 前缀
    for row in _rows(msg):
        for btn in row.buttons:
            data = _cb_data_str(btn)
            if not data:
                continue
            for prefix, wallet in _CB_WALLET_PREFIXES:
                if data.startswith(prefix):
                    return wallet

    # URL 按钮的 bot username（仅匹配已知钱包/白名单，避免误把发包人主页当钱包）
    for row in _rows(msg):
        for btn in row.buttons:
            url = btn_url(btn)
            if not url:
                continue
            if "fllqb" in url:
                return "fllqb"
            parsed = urlparse(url)
            path = parsed.path.strip("/")
            bot = path.split("/")[0].lower() if path else ""
            if bot in WALLET_USERNAMES:
                return WALLET_USERNAMES[bot]
            if whitelist and bot and bot in whitelist:
                return bot
    return "unknown"


def parse_conditions(msg) -> list[str]:
    """解析领取条件（只靠关键词，忽略 emoji）。返回如
    ['premium', 'group:28圈 @er888', 'user:淋雨', 'turnover:1.0 U']。
    每项格式 'base' 或 'base:value'，base ∈ CONDITION_LABELS。"""
    t = _raw(msg)
    conds: list[str] = []
    if _PREMIUM_RE.search(t):
        conds.append("premium")
    if _GROUP_RE.search(t):
        m = _GROUP_NAME_RE.search(t)
        name = _clean_inline(m.group(1)) if m else ""
        conds.append(f"group:{name}" if name else "group")
    if _USER_RE.search(t):
        m = _USER_NAME_RE.search(t)
        name = _clean_inline(m.group(1)) if m else ""
        conds.append(f"user:{name}" if name else "user")
    if _TURNOVER_RE.search(t):
        m = _TURNOVER_AMOUNT_RE.search(t)
        val = _clean_inline(m.group(1)) if m else ""
        conds.append(f"turnover:{val}" if val else "turnover")
    if _WINLOSS_RE.search(t):
        m = _WINLOSS_AMOUNT_RE.search(t)
        val = _clean_inline(m.group(1)) if m else ""
        conds.append(f"winloss:{val}" if val else "winloss")
    return conds


def _clean_inline(s: str) -> str:
    """清理提取出的内联文本：去 markdown 星号、折叠空白。"""
    s = (s or "").replace("*", " ")
    return re.sub(r"\s+", " ", s).strip()


def detect_state(msg) -> str:
    """红包当前状态：exhausted（已领完）| locked（未解锁）| claimable（可领）。

    注意「已解锁」是解锁完成的正向表述（如「已解锁，立即领取」），不能因
    含「解锁」二字误判为未解锁——先剔除「已解锁」再做子串匹配。"""
    raw = _raw(msg)
    if _EXHAUSTED_RE.search(raw):
        return "exhausted"
    for row in _rows(msg):
        for btn in row.buttons:
            if btn_type(btn) != "callback":
                continue
            label = clean(btn_text(btn))
            # 剔除正向表述「已解锁」后，剩下的「解锁/锁定」才是真锁按钮
            label_wo_unlocked = label.replace("已解锁", "")
            if "解锁" in label_wo_unlocked or "锁定" in label_wo_unlocked:
                return "locked"
            data = _cb_data_str(btn)
            if data and any(mk in data for mk in _LOCK_DATA_MARKERS):
                return "locked"
    return "claimable"


def parse_metadata(msg) -> dict:
    """提取金额/剩余份数/总数量/发送者昵称（与领取无关，纯展示用）。"""
    raw = _raw(msg)
    amount_text = remaining = sender = total_count = None
    m = _TOTAL_RE.search(raw)
    if m:
        num, unit = m.group(1), m.group(2)
        amount_text = f"{num} {unit}" if unit else num
    m = _REMAIN_RE.search(raw)
    if m:
        remaining = f"{m.group(1)}/{m.group(2)}"
        total_count = m.group(2)
    m = _SENDER_RE.search(raw)
    if m:
        sender = m.group(1).strip() or None
    return {"amount_text": amount_text, "remaining": remaining,
            "total_count": total_count, "sender_name": sender}


def clean(s: str) -> str:
    """剥离零宽字符/emoji/空格（按钮文字常插 \\u200d 等反爬），只留可见字符。"""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    out = []
    for ch in s:
        if ch in _ZERO_WIDTH:
            continue
        cat = unicodedata.category(ch)
        if cat.startswith("C") or cat == "So" or ch == " ":
            continue
        out.append(ch)
    return "".join(out)


def _rows(msg):
    return getattr(getattr(msg, "reply_markup", None), "rows", None) or []


# 原生 emoji 范围（不含 keycap 组合，单独处理）
_EMOJI_RANGES = (
    (0x1F300, 0x1FAFF),   # 杂项符号、交通、动物、食物、活动、物体、符号
    (0x2600, 0x27BF),     # 杂项符号、装饰符号、dingbats
    (0x1F000, 0x1F2FF),   # 麻将、纸牌、方块、字母表情
    (0x1F900, 0x1F9FF),   # 补充符号和象形
)


def _is_emoji_char(ch: str) -> bool:
    cp = ord(ch)
    for lo, hi in _EMOJI_RANGES:
        if lo <= cp <= hi:
            return True
    return False


def extract_emojis(text: str) -> list[str]:
    """提取文本中的原生 emoji（保留 ZWJ/VS16 组合序列，按 grapheme 粗切）。"""
    out: list[str] = []
    cur = ""
    for ch in text or "":
        cp = ord(ch)
        if _is_emoji_char(ch) or ch in ("\u200d", "\ufe0f"):
            cur += ch
        else:
            if cur:
                out.append(cur)
                cur = ""
    if cur:
        out.append(cur)
    return out


def find_tcqb_emoji_quiz(msg, text: str) -> tuple[str, list[tuple[str, bytes]]] | None:
    """汤臣钱包(tcqb) emoji 验证码：红包备注含答案 emoji，按钮是 emoji 选项。

    从按钮中收集所有 emoji 选项，再从消息文本里找出第一个出现在按钮中的 emoji 作为答案。
    返回 (答案emoji, [(按钮emoji, callback_data), ...])，无匹配返回 None。
    """
    buttons: list[tuple[str, bytes]] = []
    for row in _rows(msg):
        for btn in row.buttons:
            if btn_type(btn) != "callback":
                continue
            label = btn_text(btn).strip()
            emojis = extract_emojis(label)
            if emojis:
                # 按钮文字里第一个 emoji 作为该按钮的标识
                buttons.append((emojis[0], cb_data(btn)))
    if not buttons:
        return None

    btn_emojis = {e for e, _ in buttons}
    # 在消息全文里找第一个命中按钮集合的 emoji（通常在备注"答案"附近）
    for e in extract_emojis(text):
        if e in btn_emojis:
            return e, buttons
    return None


_EXHAUSTED_RE = re.compile(r"剩余[:：]\s*0\s*/")


def is_red_packet(text: str) -> bool:
    """是否为红包消息（且仍有余额可领）。
    1) 命中可配关键词（OKPay 类：发送了一个红包 / 💵总金额）
    2) 结构识别：含算式 `X op Y = ?`——验证码红包文案千变万化
    3) 结构识别：含「验证码/答案/选择/领取」任一关键词（通用验证码红包，不依赖特定 bot）
    4) 排除已领完：文本含「剩余:0/N」→ 已无余额，直接跳过"""
    t = text or ""
    # 已领完 → 不是可领红包
    if _EXHAUSTED_RE.search(t):
        return False
    if any(kw in t for kw in config.red_packet_keywords):
        return True
    if _MATH_RE.search(t):
        return True
    # 通用验证码红包门槛：不绑定任何钱包/bot 文案
    if any(kw in t for kw in ("验证码", "答案", "请选择", "选择", "领取")):
        return True
    return False


# 非领取的通用 start 参数（bot 首页/帮助等导航链接），不应触发任何领取流程
_GENERIC_START = {"home", "start", "help", "menu", "settings", "about", "wallet"}


def extract_start(msg, keywords: list[str] | None = None) -> tuple[str, str] | None:
    """提取领取相关的 URL 按钮 → (bot_username, start_param)。

    双重过滤，防止用账号给陌生 bot 发 /start 导致 spam 风控：
    1. 按钮文字必须含直接关键词（「查看详情」「点击进入」等广告按钮一律忽略）；
    2. 过滤通用导航参数（home/help/wallet 等），bot 首页链接不当领取入口。"""
    kws = keywords or ["领取"]
    for row in _rows(msg):
        for btn in row.buttons:
            url = btn_url(btn)
            if not url:
                continue
            # 1) 按钮文字含直接关键词才认（广告按钮「立即进入」等不触发）
            if not any(kw in clean(btn_text(btn)) for kw in kws):
                continue
            parsed = urlparse(url)
            if "t.me" not in parsed.netloc and "t.me" not in parsed.path:
                continue
            start = parse_qs(parsed.query).get("start", [None])[0]
            if not start:
                continue
            if start.lower() in _GENERIC_START:
                continue
            # t.me/kkpay?start=xxx → bot_username = kkpay
            path = parsed.path.strip("/")
            bot = path.split("/")[-1] if path else ""
            return (bot, start)
    return None


def _label_is_lock(text: str) -> bool:
    """按钮文字是否锁/解锁类。

    「已解锁」是解锁完成的正向表述（如「已解锁，立即领取」），必须先剔除
    再做「解锁/锁定」子串匹配，否则解锁完成的红包被当成锁按钮跳过。"""
    t = text.replace("已解锁", "")
    return "解锁" in t or "锁定" in t


def has_direct_keyword_button(msg, keywords: list[str] | None = None) -> bool:
    """是否存在含指定关键词的按钮（不返回 data，仅做门控判断）。

    用于放宽 is_red_packet 门槛：消息正文不含红包关键词、但按钮含「领取」时，
    仍应进入 direct 判定链路，避免漏识别。
    检查所有按钮类型（callback/URL/WebView 等）：门控只判断「像不像红包」，
    具体领取方式由 classify 后续分支决定，不影响安全性。"""
    kws = keywords or ["领取"]
    for row in _rows(msg):
        for btn in row.buttons:
            text = clean(btn_text(btn))
            if not text or _label_is_lock(text):
                continue
            if any(kw in text for kw in kws):
                return True
    return False


def find_direct_claim(msg, keywords: list[str] | None = None) -> bytes | None:
    """含指定关键词的 callback 按钮 → 直接领取（子串匹配，兼容各类钱包的隐藏字符/emoji）。

    排除锁定按钮：fllqb 的「🧧解锁后领取，管理叫解锁是⚠️诈骗」含「领取」却是锁按钮
    （data=mhblock...），文字锁类（剔除「已解锁」后含「解锁/锁定」）或 data 命中
    锁标记一律跳过。

    注意：本函数只看按钮文字，不校验钱包来源；钱包校验由调用方（classify）负责，
    避免广告 bot 的「领取优惠」按钮被误点。"""
    kws = keywords or ["领取"]
    for row in _rows(msg):
        for btn in row.buttons:
            if btn_type(btn) != "callback":
                continue
            text = clean(btn_text(btn))
            if _label_is_lock(text):
                continue
            data = _cb_data_str(btn)
            if data and (data.startswith(("mhblock", "unlock", "locked_"))
                         or any(mk in data for mk in _LOCK_DATA_MARKERS)):
                continue
            if any(kw in text for kw in kws):
                return cb_data(btn)
    return None


def _is_lock_button(btn) -> bool:
    """是否锁定/解锁类按钮（文字或 data 命中锁标记，文字先剔除「已解锁」）。"""
    if _label_is_lock(clean(btn_text(btn))):
        return True
    data = _cb_data_str(btn)
    if data and (data.startswith(("mhblock", "unlock", "locked_"))
                 or any(mk in data for mk in _LOCK_DATA_MARKERS)):
        return True
    return False


def find_webview_button(msg, keywords: list[str] | None = None) -> str | None:
    """inline WebView（小程序）按钮，且按钮文字含直接关键词 → 返回其 URL。

    关键词不命中不触发（防误开广告小程序）；按钮类型判断走兼容层。"""
    kws = keywords or ["领取"]
    for row in _rows(msg):
        for btn in row.buttons:
            if btn_type(btn) != "webview":
                continue
            if not any(kw in clean(btn_text(btn)) for kw in kws):
                continue
            url = btn_url(btn)
            if url:
                return url
    return None


def detect_bot_username(msg) -> str | None:
    """万能领取辅助：红包消息的来源 bot 用户名（via_bot 优先，URL 按钮兜底）。"""
    via = getattr(msg, "via_bot", None)
    if via and getattr(via, "username", None):
        return via.username
    for row in _rows(msg):
        for btn in row.buttons:
            url = btn_url(btn)
            if not url:
                continue
            parsed = urlparse(url)
            if "t.me" not in parsed.netloc and "t.me" not in parsed.path:
                continue
            path = parsed.path.strip("/")
            bot = path.split("/")[0] if path else ""
            if bot and not bot.startswith("$"):
                return bot
    return None


def _has_quiz_buttons(msg) -> bool:
    """是否有多个非领取 callback 按钮（验证码选项）。"""
    count = 0
    for row in _rows(msg):
        for btn in row.buttons:
            if btn_type(btn) == "callback":
                label = clean(btn_text(btn)).strip()
                if label and "领取" not in label:
                    count += 1
    return count >= 2


def _collect_quiz_buttons(msg) -> list[tuple[str, bytes]]:
    buttons: list[tuple[str, bytes]] = []
    for row in _rows(msg):
        for btn in row.buttons:
            if btn_type(btn) != "callback":
                continue
            label = clean(btn_text(btn)).strip()
            if label and "领取" not in label:
                buttons.append((label, cb_data(btn)))
    return buttons


# 验证码答案提取关键词（按优先级，越靠前优先级越高）
_ANSWER_PATTERNS = [
    r"验证码[:：\s]*([0-9A-Za-z]+)",
    r"答案为[:：\s]*([0-9A-Za-z]+)",
    r"答案[:：\s]*([0-9A-Za-z]+)",
    r"请选择[:：\s]*([0-9A-Za-z]+)",
    r"选择[:：\s]*([0-9A-Za-z]+)",
    r"为[:：]\s*([0-9A-Za-z]+)",
]

# 元数据行关键词：这些行里的数字是金额/份数/流水，不是验证码答案
_META_LINE_RE = re.compile(
    r"总金额|金额|剩余|流水|输赢|有效期|发送了一个红包|邀请|会员|加群|群组")
# 行内元数据片段：「剩余 3/10」「剩余：6272」等
_META_INLINE_RE = re.compile(r"剩余[:：]?\s*\d+(?:\s*/\s*\d+)?")
# 行内「数字+币种」：1000 USDT / 1,000.5 KKCOIN
_AMOUNT_INLINE_RE = re.compile(r"\d+(?:[.,]\d+)*\s*[A-Za-z]{2,10}\b")


def _answer_tokens(text: str) -> list[str]:
    """把文本切成候选答案 token：可归一化为字母/数字的连续字符段。

    按字符级归一化切分（而非 re.split [^0-9A-Za-z]），花式 Unicode
    数字/字母（𝟨𝟤𝟩𝟤、🅰 等）不会被当分隔符拆散；中文/标点/emoji 是分隔。"""
    tokens, buf = [], []
    for ch in text or "":
        if normalize_char(ch):
            buf.append(ch)
        else:
            if buf:
                tokens.append("".join(buf))
                buf = []
    if buf:
        tokens.append("".join(buf))
    return tokens


def _strip_metadata(text: str) -> str:
    """剔除金额/剩余/流水等元数据片段，只留可能含验证码答案的文本。

    数字/混合按钮反推答案专用：避免把「总金额 1000」「剩余 3/10」
    「流水 5000」里的数字误当验证码答案点错按钮。"""
    kept = [ln for ln in (text or "").splitlines()
            if not _META_LINE_RE.search(ln)]
    out = "\n".join(kept)
    out = _META_INLINE_RE.sub("", out)
    out = _AMOUNT_INLINE_RE.sub("", out)
    return out


def _has_quiz_button_set(msg) -> bool:
    """是否有 ≥3 个短字母数字 callback 按钮（验证码选项特征）。

    仅用于把「纯验证码文案」（正文无红包关键词）消息放进识别流程；
    识别失败不会点击任何按钮，只会打 🔍 日志，无广告误点风险。"""
    count = 0
    for row in _rows(msg):
        for btn in row.buttons:
            if btn_type(btn) != "callback":
                continue
            label = clean(btn_text(btn)).strip()
            if not label or "领取" in label:
                continue
            n = normalize_operand(label)
            if 2 <= len(n) <= 12:
                count += 1
    return count >= 3


def find_captcha_quiz(msg, decoded_text: str | None = None) -> tuple[str, list[tuple[str, bytes]]] | None:
    """验证码红包：文本含算式或明文验证码 + callback 按钮（数字或字母选项）。
    decoded_text: 已解码 custom emoji 后的文本（可选，未提供则用 raw_text）。
    返回 (算式文本或明文答案, [(按钮标签, callback_data), ...]) 或 None。

    三层识别（任一命中即可，全部失败返回 None）：
    1) 算式 X op Y = ?（数字或字母操作数）
    2) 关键词引导的明文答案：验证码/答案/请选择/选择/为： ABC
    3) 按钮反推：答案直接写在消息里，token 与按钮归一化后相等。
       字母/数字/字母数字混合按钮（6272、hsns、gYhB 等四选一至十二选一）
       均支持；数字/混合先剔除金额等元数据再匹配，防点错按钮"""
    text = decoded_text or getattr(msg, "text", None) or getattr(msg, "raw_text", None) or ""
    buttons = _collect_quiz_buttons(msg)
    if not buttons:
        return None

    # 1) 算式 X op Y = ?
    m = _MATH_RE.search(text)
    if m:
        return (m.group(0), buttons)

    # 2) 关键词引导的明文答案（字母或数字）
    for pat in _ANSWER_PATTERNS:
        m2 = re.search(pat, text)
        if m2:
            return (m2.group(1), buttons)

    # 3) 按钮反推：答案直接写在消息里（无算式、无引导关键词），
    #    文本 token 与按钮标签归一化后完全相等即答案。
    #    - 纯字母按钮：直接匹配（字母 token 不会与金额混淆）
    #    - 数字/字母数字混合按钮（如 6272/hsns/gYhB 四选一）：先剔除
    #      金额/剩余/流水等元数据片段再匹配；纯数字 token 需 ≥3 位
    #      （避免 2 位散数误中）；≥3 个选项才启用（防广告误点）
    btn_kind = buttons_are_digit([l for l, _ in buttons])
    if btn_kind is False or len(buttons) >= 3:
        # 归一化按钮标签，构造 {norm: (原label, data)} 表
        btn_norm: dict[str, tuple[str, bytes]] = {}
        for label, data in buttons:
            n = normalize_operand(label).upper()
            if n and n not in btn_norm:
                btn_norm[n] = (label, data)
        hay = text if btn_kind is False else _strip_metadata(text)
        for tok in _answer_tokens(hay):
            n = normalize_operand(tok).upper()
            if not n or n not in btn_norm:
                continue
            if n.isdigit() and len(n) < 3:
                continue
            return (n, buttons)

    return None


def find_fulilai(msg) -> tuple[str, str] | None:
    """福利来红包：按钮 URL 含 fllqb + startapp=... → 返回 (startapp 完整参数, 红包标识)。
    URL 格式有两代：
    - 旧: ...startapp=botId=xxx_page=captcha_hash=<hex>     → id_key='hash'
    - 新: ...startapp=botId=xxx_page=captcha_dataId=<digits> → id_key='dataId'
    返回 (startapp_raw, packet_id) 供 claimer 用。"""
    for row in _rows(msg):
        for btn in row.buttons:
            url = btn_url(btn)
            if not url or "fllqb" not in url or "startapp=" not in url:
                continue
            # 提取 startapp= 后面的整串参数
            m_sa = re.search(r"startapp=([^&\s]+)", url)
            if not m_sa:
                continue
            startapp = m_sa.group(1)
            # 从 startapp 中提取红包标识（hash 或 dataId，取最后一个 key=value）
            for pattern in (r"hash=([a-f0-9]+)", r"dataId=(\d+)"):
                m_id = re.search(pattern, startapp)
                if m_id:
                    return (startapp, m_id.group(1))
    return None


@dataclass
class RedPacket:
    kind: str                          # 'direct'|'captcha'|'dm_captcha'|'webapp'|'fulilai'|'locked'
    # ── 识别维度（所有 kind 通用，供通知/汇总展示）──
    wallet: str = "unknown"            # 'okpay'|'kkpay'|'wlqb'|'dlqb'|'fllqb'|'unknown'
    state: str = "claimable"           # 'claimable'|'locked'|'exhausted'
    conditions: list[str] = field(default_factory=list)  # ['premium','group:28圈',...]
    amount_text: str | None = None     # "200 CNY"
    remaining: str | None = None       # "20/20"
    total_count: str | None = None     # "20"（总份数，取自剩余分母）
    sender_name: str | None = None     # "雨"
    # ── 领取载荷（按 kind 取用）──
    direct_data: bytes | None = None   # kind=direct
    start_param: str | None = None     # kind=webapp / dm_captcha
    webview_url: str | None = None     # kind=webapp：红包消息自带的 WebView 按钮直连 URL
    webapp_bot: str | None = None      # kind=webapp / dm_captcha: bot username
    captcha_expr: str | None = None    # kind=captcha / dm_captcha: 算式原文
    captcha_buttons: list = field(default_factory=list)  # kind=captcha: [(label, data)]
    emoji_answer: str | None = None    # kind=captcha(tcqb): 备注里的答案 emoji，直接匹配按钮
    needs_emoji_decode: bool = False   # captcha 中含自定义 emoji，需要异步解码
    fulilai_hash: str | None = None    # kind=fulilai: 红包标识（hash 或 dataId）
    fulilai_startapp: str | None = None  # kind=fulilai: startapp 完整参数


def _expr_line(text: str) -> str | None:
    """提取表达式行：最后一个含 = ? 的行。"""
    for line in reversed(text.split("\n")):
        if "= ?" in line or "= ？" in line:
            return line.strip()
    return None


def _has_custom_emoji_in_expr(msg) -> bool:
    """表达式行内是否含 Custom Emoji（而非装饰性）。"""
    from telethon.tl.types import MessageEntityCustomEmoji
    raw = getattr(msg, "raw_text", None) or ""
    expr_line = _expr_line(raw)
    if not expr_line:
        return False
    entities = getattr(msg, "entities", None) or []
    # 定位表达式行在 raw_text 中的位置
    line_start = raw.rfind(expr_line)
    if line_start < 0:
        return False
    line_end = line_start + len(expr_line)
    # UTF-16 偏移量计算
    utf16_pos = 0
    char_to_utf16: list[int] = []
    for ch in raw:
        char_to_utf16.append(utf16_pos)
        utf16_pos += 2 if ord(ch) > 0xFFFF else 1
    start_u16 = char_to_utf16[line_start] if line_start < len(char_to_utf16) else utf16_pos
    end_u16 = char_to_utf16[line_end] if line_end < len(char_to_utf16) else utf16_pos
    return any(
        isinstance(e, MessageEntityCustomEmoji) and start_u16 <= e.offset < end_u16
        for e in entities
    )


def classify(msg, direct_keywords: list[str] | None = None,
             decoded_text: str | None = None,
             wallet_whitelist: set[str] | None = None) -> RedPacket | None:
    """把消息分类为红包并附带完整识别上下文。
    返回 None 仅当：非红包 / 已领完 / 无可识别按钮。
    locked（未解锁）会返回 kind='locked' 的 RedPacket（只通知不领取）。

    统一门槛（所有钱包一视同仁，无放行白名单）：
    - 门控：正文命中红包关键词，或按钮文字含「直接关键词」。
      钱包 bot 的余额通知、系统消息等按钮不含关键词 → 完全忽略。
    - 点击：按钮文字含「直接关键词」才点；「领取红包🧧」点、「余额💰」不点。
      按钮文字特殊时看 🔍 未识别日志里的 buttons= 再补充关键词。
    领取分类优先级：direct > captcha > fulilai > webapp(vweb--) > dm_captcha(其余 URL)。"""
    text = decoded_text or getattr(msg, "text", None) or getattr(msg, "raw_text", None) or ""
    if not getattr(msg, "reply_markup", None):
        return None

    # 统一门控（不做钱包放行：已知钱包的余额/系统消息等非红包一律不进流程）。
    # 验证码选项特征（≥3 个短字母数字按钮）也放行进识别——识别失败只会打
    # 🔍 日志、不会点击，无广告误点风险；纯验证码文案（正文无红包关键词）靠它进流程。
    if not (is_red_packet(text) or has_direct_keyword_button(msg, direct_keywords)
            or _has_quiz_button_set(msg)):
        return None

    # 识别维度：钱包 / 状态 / 条件 / 元数据（与具体领取方式无关，全 kind 共用）
    wallet = detect_wallet(msg, wallet_whitelist)

    state = detect_state(msg)
    conditions = parse_conditions(msg)
    meta = parse_metadata(msg)

    def _wrap(rp: RedPacket) -> RedPacket:
        rp.wallet = wallet
        rp.state = state
        rp.conditions = conditions
        rp.amount_text = meta["amount_text"]
        rp.remaining = meta["remaining"]
        rp.total_count = meta["total_count"]
        rp.sender_name = meta["sender_name"]
        return rp

    # 已领完 → 无事可做
    if state == "exhausted":
        return None

    # 未解锁 → 只通知不领取（等解锁后的编辑事件再领）
    if state == "locked":
        return _wrap(RedPacket("locked", state="locked"))

    # 1) 直接领取：callback 按钮文字含直接关键词 → 点击（关键词不命中一律不点）
    data = find_direct_claim(msg, direct_keywords)
    if data:
        return _wrap(RedPacket("direct", direct_data=data))

    # 2) 窗口验证码（群内算式或明文验证码 + callback 选项按钮）
    quiz = find_captcha_quiz(msg, decoded_text)
    if quiz:
        from core.claimers.captcha import solve_expr
        expr, buttons = quiz
        if solve_expr(expr) is not None:
            return _wrap(RedPacket("captcha", captcha_expr=expr, captcha_buttons=buttons))
        # 解不出 → 检查表达式行是否含 Custom Emoji
        if not decoded_text and _has_custom_emoji_in_expr(msg):
            return _wrap(RedPacket("captcha", captcha_buttons=buttons, needs_emoji_decode=True))
        return _wrap(RedPacket("captcha", captcha_expr=expr, captcha_buttons=buttons))

    # 2.5) 汤臣钱包(tcqb) emoji 验证码：备注含答案 emoji，按钮是 emoji 选项
    if wallet == "tcqb":
        eq = find_tcqb_emoji_quiz(msg, text)
        if eq:
            answer_emoji, buttons = eq
            return _wrap(RedPacket("captcha", captcha_buttons=buttons, emoji_answer=answer_emoji))

    # 3) 福利来红包（fllqb URL，startapp + hash/dataId，与 ?start= 不同）
    fll = find_fulilai(msg)
    if fll:
        startapp, packet_id = fll
        return _wrap(RedPacket("fulilai", fulilai_hash=packet_id, fulilai_startapp=startapp))

    # 4) WebView（小程序）按钮直连：文字含直接关键词 + 已知钱包才触发，
    #    不经过 /start 省一步往返；广告小程序（未知钱包）一律不开
    if wallet != "unknown":
        wv_url = find_webview_button(msg, direct_keywords)
        if wv_url:
            bot = detect_bot_username(msg)
            if bot:
                return _wrap(RedPacket("webapp", webview_url=wv_url, webapp_bot=bot))

    # 5) URL 按钮（t.me/bot?start=xxx）：按钮含关键词 + 已知钱包 bot 才发起领取。
    #    反 spam 关键：广告消息正文常带「红包」+ start 链接诱饵，若无钱包校验会
    #    用账号批量给陌生 bot 发 /start → 账号被 Telegram 风控（链接打不开）。
    #    start_param 以 vweb-- 开头即网页验证（okpay/kkpay/dlqb 共用 vweb 协议），
    #    其余按私信验证。新钱包红包先看 🔍 日志，把 bot 加入 WALLET_BOTS 后生效。
    if wallet != "unknown":
        start_info = extract_start(msg, direct_keywords)
        if start_info:
            bot, start = start_info
            expr_m = _MATH_RE.search(text)
            if start.startswith("vweb--") or bot.lower() in WEBAPP_BOTS:
                return _wrap(RedPacket("webapp", start_param=start, webapp_bot=bot))
            return _wrap(RedPacket("dm_captcha", start_param=start, webapp_bot=bot,
                                   captcha_expr=expr_m.group(0) if expr_m else None))
    return None