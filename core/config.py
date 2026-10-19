"""集中配置：从 .env 读取，供核心引擎与 Web 后端共享。"""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _parse_models(raw: str) -> list[tuple[str, str]]:
    """解析 VISION_MODELS：'model:tag,model:tag'；缺省给一组快模型。"""
    out: list[tuple[str, str]] = []
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        model, _, tag = item.partition(":")
        model = model.strip()
        tag = tag.strip() or model.split("-")[-1]
        out.append((model, tag))
    return out or [("qwen3-vl-flash", "flash"), ("qwen3-vl-plus", "plus")]


def _parse_keywords(raw: str) -> list[str]:
    """解析 RED_PACKET_KEYWORDS：逗号分隔的关键词列表；缺省给 OKPay 标准文案。"""
    out = [item.strip() for item in (raw or "").split(",") if item.strip()]
    return out or ["发送了一个红包", "💵总金额", "总金额", "红包个数",
                   "验证码红包", "公开红包", "红包"]


def _load_vision_providers(models_fallback: list[tuple[str, str]],
                           default_api_key: str, default_base_url: str) -> list[dict]:
    """加载多厂商视觉模型配置：每模型独立 (api_key, base_url)。

    优先级：
    1. vision_providers.json 文件（多厂商并发）
    2. 回退到 VISION_MODELS + 默认 api_key/base_url（单厂商兼容）

    返回 [{'model','tag','api_key','base_url'}]。
    """
    providers_file = ROOT / os.getenv("VISION_PROVIDERS_FILE", "vision_providers.json")
    if providers_file.exists():
        try:
            data = json.loads(providers_file.read_text(encoding="utf-8"))
            if isinstance(data, list):
                out = []
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    model = (item.get("model") or "").strip()
                    if not model:
                        continue
                    tag = (item.get("tag") or "").strip() or model
                    api_key = (item.get("api_key") or "").strip()
                    base_url = (item.get("base_url") or "").strip()
                    out.append({"model": model, "tag": tag,
                                "api_key": api_key, "base_url": base_url})
                if out:
                    return out
        except (json.JSONDecodeError, OSError):
            pass

    # 回退：把 VISION_MODELS + 默认厂商展开为 providers
    return [{"model": m, "tag": t,
             "api_key": default_api_key, "base_url": default_base_url}
            for m, t in models_fallback]


@dataclass
class Config:
    # —— Telegram 应用凭据（登录新账号用，应用级共享）——
    api_id: int = int(os.getenv("API_ID") or "0")
    api_hash: str = os.getenv("API_HASH", "")

    # —— OKPay 红包识别 ——
    okpay_bot_ids: set[int] = field(default_factory=lambda: {5703356189})
    red_packet_keywords: list[str] = field(
        default_factory=lambda: _parse_keywords(os.getenv("RED_PACKET_KEYWORDS", "")))
    max_attempts: int = int(os.getenv("MAX_ATTEMPTS", "4"))

    # —— 通知机器人（领取状态由 bot 私聊推送给账号本人，不用账号自己发消息暴露）——
    notify_bot_token: str = os.getenv("NOTIFY_BOT_TOKEN", "")

    # —— 跨账号红包共享：通信频道（用 notify bot 广播红包事件，记录可见）——
    broadcast_channel: int = int(os.getenv("BROADCAST_CHANNEL") or "0")

    # —— 验证码视觉识别 ——
    vision_api_key: str = os.getenv("VISION_API_KEY", "")
    vision_base_url: str = os.getenv(
        "VISION_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    vision_model: str = os.getenv("VISION_MODEL", "qwen3-vl-flash")
    # 多模型并发（任一识别成功即领取，互不冲突）
    vision_models: list[tuple[str, str]] = field(
        default_factory=lambda: _parse_models(os.getenv("VISION_MODELS", "")))
    # 多厂商视觉模型并发：每个模型独立 api_key + base_url（vision_providers.json）
    # 文件不存在时回退到 vision_models + 默认 api_key/base_url（单厂商兼容）
    vision_providers: list[dict] = field(
        default_factory=lambda: _load_vision_providers(
            _parse_models(os.getenv("VISION_MODELS", "")),
            os.getenv("VISION_API_KEY", ""),
            os.getenv("VISION_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        ))

    # —— 浏览器（验证码页面用）——
    # backend: playwright(自带 chromium) | cloak(cloakbrowser 反检测, 需 pip install cloakbrowser)
    browser_backend: str = os.getenv("BROWSER_BACKEND", "playwright")
    headless: bool = (os.getenv("HEADLESS", "true").lower() != "false")
    # 指定浏览器可执行文件；留空=用所选后端自带 chromium（服务器/Linux 推荐留空）
    chrome_path: str = os.getenv("CHROME_PATH", "")

    # —— Web 服务 ——
    host: str = os.getenv("HOST", "127.0.0.1")
    port: int = int(os.getenv("PORT", "8000"))
    # 后端启动时自动恢复所有「已启用 + 已登录」账号的监听（服务器无人值守用）
    autostart_accounts: bool = (os.getenv("AUTOSTART_ACCOUNTS", "false").lower() == "true")

    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    # —— Session 加密（AES-256-GCM，开发留空=明文透传）——
    session_encrypt_key: str = os.getenv("SESSION_ENCRYPT_KEY", "")

    # —— 数据库（本地 PostgreSQL，存 session/账号/配置/记录）——
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://hongbao:changeme@127.0.0.1:5432/hongbao")


config = Config()


@dataclass
class RunConfig:
    """单个账号运行所需的有效配置。

    Web 后端为每个账号从数据库组装（StringSession + DB 设置 + 模块开关）；
    `from_config(session)` 仅作便捷构造（用全局 .env + 传入 StringSession）。
    """
    api_id: int
    api_hash: str
    session: str | None                  # StringSession 字符串（来自 DB，缺失 grabber 会报错）
    vision_api_key: str
    vision_base_url: str
    vision_model: str
    vision_models: list[tuple[str, str]]
    max_attempts: int
    notify_bot_token: str
    chrome_path: str
    modules: dict                        # {'direct': bool, 'captcha': bool, 'webapp': bool, 'fulilai': bool}
    direct_keywords: list[str] = None    # 直接领取按钮匹配关键词（子串匹配）
    vision_providers: list[dict] = None  # [{'model','tag','api_key','base_url'}]：多厂商并发
    capmonster_key: str = ""             # CapMonster Cloud API key（福利来 hCaptcha 用）
    disabled_chat_ids: set[int] = None   # 关闭了秒包的 chat_id 集合（不在集合内=默认开启）
    monitor_enabled: bool = True         # 是否监控红包消息
    claim_enabled: bool = True           # 是否参与秒包领取
    module_configs: dict = None          # 各模块独立配置 {"captcha": {...}, "webapp": {...}, "fulilai": {...}}
    # —— 领取策略过滤（见 core/filters.py，Web 系统配置下发，保存后热更新）——
    filter_keywords: list = None         # 关键词黑名单：消息文本命中即跳过
    filter_currency_mode: str = "off"    # 币种过滤模式 off|white|black
    filter_currencies: set = None        # 币种集合（大写），配合 mode 使用
    filter_min_amounts: dict = None      # 币种→最低总金额；key '*' = 任意币种兜底
    filter_skip_conditions: set = None   # 跳过的条件类型 {premium,group,user,turnover,winloss}
    filter_block_wallets: set = None     # 钱包黑名单（识别即拦，屏蔽策略至上）
    filter_block_tokens: set = None      # 币种/代币关键词黑名单（扫描整条消息文本）
    # —— 钱包白名单（控制台可配、保存即热更新）——
    # 仅影响「识别放行」：白名单内 bot 的 URL/小程序红包按已知钱包处理。
    # 屏蔽策略（block_rules/block_private）、钱包黑名单、币种/份数过滤仍然至上，
    # 白名单无法突破——见 grabber._blocked 与 filters.check_filters 的执行顺序。
    wallet_whitelist: set = None         # bot username 集合（小写、不带@）
    # —— 屏蔽规则 + 代理（backend blocklist / 账号代理，保存即热更新）——
    proxy: str | None = None             # 账号代理 socks5://user:pass@host:port（None=直连）
    blocked_chat_ids: set = None         # 屏蔽的群/频道 chat_id
    blocked_sender_ids: set = None       # 屏蔽的用户/机器人 id（发送者或 via_bot）
    block_private: bool = False          # 屏蔽所有私信红包

    def __post_init__(self):
        if self.direct_keywords is None:
            self.direct_keywords = ["领取"]
        if self.disabled_chat_ids is None:
            self.disabled_chat_ids = set()
        if self.module_configs is None:
            self.module_configs = {}
        if self.vision_providers is None:
            self.vision_providers = []
        if self.filter_keywords is None:
            self.filter_keywords = []
        if self.filter_currencies is None:
            self.filter_currencies = set()
        if self.filter_min_amounts is None:
            self.filter_min_amounts = {}
        if self.filter_skip_conditions is None:
            self.filter_skip_conditions = set()
        if self.filter_block_wallets is None:
            self.filter_block_wallets = set()
        if self.filter_block_tokens is None:
            self.filter_block_tokens = set()
        if self.wallet_whitelist is None:
            self.wallet_whitelist = set()
        if self.blocked_chat_ids is None:
            self.blocked_chat_ids = set()
        if self.blocked_sender_ids is None:
            self.blocked_sender_ids = set()

    @classmethod
    def from_config(cls, session: str | None = None) -> "RunConfig":
        return cls(
            api_id=config.api_id, api_hash=config.api_hash, session=session,
            vision_api_key=config.vision_api_key, vision_base_url=config.vision_base_url,
            vision_model=config.vision_model, vision_models=list(config.vision_models),
            vision_providers=list(config.vision_providers),
            max_attempts=config.max_attempts, notify_bot_token=config.notify_bot_token,
            chrome_path=config.chrome_path,
            modules={"direct": True, "captcha": True, "webapp": True, "fulilai": True},
        )
