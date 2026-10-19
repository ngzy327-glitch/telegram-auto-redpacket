"""telethon 新旧版本按钮兼容层（万能领取的地基）。

telethon ≤1.44：inline 按钮是 KeyboardButtonCallback(text, data) / KeyboardButtonUrl(text, url) /
    KeyboardButtonWebView(text, url) 等扁平类型，isinstance 判型，属性直取。

telethon 1.45+：按钮是 KeyboardInlineButton(text, type=InlineButtonTypeCallback(data=...) 等)，
    类型与数据藏在 .type 里；旧类型名虽保留为别名，但 isinstance(btn, 旧类型) 永远为 False
    ——这是按钮识别全线失效的根因，所有类型判断必须走本模块。

统一访问器（两种结构通吃）：
    iter_buttons(msg)  → 迭代消息所有 inline 按钮
    btn_text(btn)      → 按钮文字
    btn_type(btn)      → 'callback' | 'url' | 'webview' | 'switch_inline' | ...（小写短名）
    cb_data(btn)       → callback 按钮的 data(bytes)
    btn_url(btn)       → url / webview 按钮的 URL
"""


def iter_buttons(msg):
    """迭代消息里的所有 inline 按钮（含每行每列）。"""
    rows = getattr(getattr(msg, "reply_markup", None), "rows", None) or []
    for row in rows:
        for btn in (getattr(row, "buttons", None) or []):
            yield btn


def btn_text(btn) -> str:
    """按钮文字（新旧结构均有 .text）。"""
    return getattr(btn, "text", "") or ""


def btn_type(btn) -> str:
    """按钮类型（统一小写短名）：callback / url / webview / switch_inline / buy / game ...
    新结构看 .type 描述符的类名，旧结构看按钮自身的类名，去掉版本前缀差异。"""
    t = getattr(btn, "type", None)
    cls = t if t is not None else btn
    name = type(cls).__name__
    for prefix in ("InlineButtonType", "KeyboardButton"):
        if name.startswith(prefix):
            return name[len(prefix):].lower() or name.lower()
    return name.lower()


def cb_data(btn) -> bytes | None:
    """callback 按钮的 data：新结构 .type.data，旧结构 .data。"""
    t = getattr(btn, "type", None)
    if t is not None:
        return getattr(t, "data", None)
    return getattr(btn, "data", None)


def btn_url(btn) -> str | None:
    """url / webview 按钮的 URL：新结构 .type.url，旧结构 .url。"""
    t = getattr(btn, "type", None)
    if t is not None:
        return getattr(t, "url", None)
    return getattr(btn, "url", None)
