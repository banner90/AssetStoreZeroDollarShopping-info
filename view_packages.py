# 查看 download_dir 下的 .unitypackage 列表，选中后显示 metadata 中的包详情
# 集成 fetch_package_info 抓取功能

import html
import json
import os
import re
import subprocess
import sys
import webbrowser
from datetime import datetime
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk, scrolledtext, messagebox

try:
    from tkinterweb import HtmlFrame
    HAS_HTML_FRAME = True
except ImportError:
    HAS_HTML_FRAME = False

try:
    from PIL import Image
    import pilmoji.source  # noqa: F401
    HAS_PILMOJI = True
except ImportError:
    HAS_PILMOJI = False

# 支持 PyInstaller 打包后路径
if getattr(sys, "frozen", False):
    _BASE = Path(sys.executable).resolve().parent
    ROOT = _BASE
    _MEIPASS = Path(sys._MEIPASS)
else:
    _BASE = Path(__file__).resolve().parent
    ROOT = _BASE.parent
    _MEIPASS = None

CONFIG_PATH = ROOT / "asset_store_config.json"
PURCHASES_PATH = ROOT / "purchases_snapshot.json"
MANUAL_MAPPING_PATH = ROOT / "manual_mapping.json"
PLUGIN_JSON = _BASE / "plugin.json"
METADATA_DIR = _BASE / "metadata" if not getattr(sys, "frozen", False) else ROOT / "metadata"
EMOJI_CACHE_DIR = ROOT / "emoji"
# 图标：打包后从 _MEIPASS 读取；开发时从 build/icon.png 或根目录 icon.png 读取
if getattr(sys, "frozen", False):
    ICON_PATH = _MEIPASS / "icon.png"
else:
    _icon_candidates = (_BASE / "build" / "icon.png", _BASE / "icon.png")
    ICON_PATH = next((p for p in _icon_candidates if p.exists()), _BASE / "icon.png")


def _load_plugins():
    """从 plugin.json 读取插件配置，返回 (hint, plugins_list)。plugins_list 每项为 {id, title, command, description}。未识别到 json 时返回空列表，不填充默认插件。"""
    default_hint = "以下插件可用于非exe运行时(exe运行可忽略)，根据提示安装，安装后需重启程序生效。(作者会根据实际需要更新插件文件，不需要用户自行修改)"
    if not PLUGIN_JSON.exists():
        return default_hint, []
    try:
        data = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
        hint = data.get("hint") or default_hint
        raw = data.get("plugins")
        if not isinstance(raw, list):
            return hint, []
        plugins = []
        for p in raw:
            if not isinstance(p, dict) or not p.get("title"):
                continue
            cmd = p.get("commands")
            if isinstance(cmd, list) and cmd:
                cmd = str(cmd[0]) if cmd else ""
            else:
                cmd = str(p.get("command", ""))
            plugins.append({
                "id": str(p.get("id", "")),
                "title": str(p.get("title", "")),
                "command": cmd,
                "description": str(p.get("description", "")),
            })
        return hint, plugins
    except Exception:
        return default_hint, []


def _get_plugin_title_by_id(plugin_id: str, fallback: str = "") -> str:
    """从 plugin.json 根据插件 id 读取对应插件的 title。未找到或读取失败时返回 fallback。"""
    if not PLUGIN_JSON.exists():
        return fallback
    try:
        data = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
        for p in data.get("plugins") or []:
            if isinstance(p, dict) and p.get("id") == plugin_id:
                return str(p.get("title", fallback)) or fallback
        return fallback
    except Exception:
        return fallback


def _load_version() -> str:
    """从 version.txt 读取版本号（打包时由 build_exe 注入）"""
    if getattr(sys, "frozen", False):
        p = _MEIPASS / "version.txt"
    else:
        p = _BASE / "build" / "version.txt"
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    return ""


def sanitize_filename(name: str) -> str:
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip()
    sanitized = sanitized.rstrip(". ")
    return sanitized or "unnamed_asset"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def load_purchases() -> list:
    if not PURCHASES_PATH.exists():
        return []
    data = json.loads(PURCHASES_PATH.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def load_manual_mapping() -> dict:
    """加载 manual_mapping.json：packageId(str) -> filename"""
    if not MANUAL_MAPPING_PATH.exists():
        return {}
    try:
        data = json.loads(MANUAL_MAPPING_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_manual_mapping(mapping: dict) -> None:
    """保存 manual_mapping.json，去重后写入"""
    out = {str(k): str(v) for k, v in mapping.items()}
    MANUAL_MAPPING_PATH.write_text(
        json.dumps(out, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_filename_to_package_id(purchases: list) -> dict:
    mapping = {}
    for item in purchases:
        pid = item.get("packageId")
        display_name = str(item.get("displayName") or "")
        if not pid or not display_name:
            continue
        filename = sanitize_filename(display_name) + ".unitypackage"
        mapping[filename] = int(pid) if isinstance(pid, (int, str)) else pid
    return mapping


def strip_html(html_text: str) -> str:
    """去除 HTML 标签，转为纯文本（会丢失超链接）"""
    if not html_text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", html_text, flags=re.I)
    text = re.sub(r"</p>", "\n", text)
    text = re.sub(r"</li>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def html_to_safe_html(html_text: str, base_url: str = "https://assetstore.unity.com") -> str:
    """
    将描述 HTML 转为安全展示用 HTML，保留超链接。
    移除 script/iframe 等危险标签，保留 <a href> 并加 target="_blank"。
    支持 Markdown 链接 [text](url)。
    """
    if not html_text:
        return ""
    text = html_text
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.I | re.DOTALL)
    text = re.sub(r"<iframe[^>]*>.*?</iframe>", "", text, flags=re.I | re.DOTALL)
    text = re.sub(r"<object[^>]*>.*?</object>", "", text, flags=re.I | re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.I | re.DOTALL)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p>", "\n", text)
    text = re.sub(r"</li>", "\n", text)
    links = []

    def _link_save(m):
        url = (m.group(1) or "").strip()
        inner_raw = (m.group(2) or "").strip()
        inner = strip_html(inner_raw) if inner_raw else ""
        if not url or url.startswith("javascript:"):
            return inner_raw or ""
        if not url.startswith(("http://", "https://")):
            url = (base_url.rstrip("/") + "/" + url.lstrip("/")) if url.startswith("/") else base_url
        idx = len(links)
        links.append((url, inner or inner_raw))
        return f"\x00LINK{idx}\x00"

    text = re.sub(r'<a\s+[^>]*href\s*=\s*["\']([^"\']+)["\'][^>]*>([\s\S]*?)</a>', _link_save, text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    for i, (url, inner) in enumerate(links):
        text = text.replace(f"\x00LINK{i}\x00", f'<a href="{html.escape(url)}" target="_blank" rel="noopener">{html.escape(inner)}</a>')
    def _md_link(m):
        u = (m.group(2) or "").strip()
        t = (m.group(1) or "").strip()
        if not u or u.startswith("javascript:"):
            return m.group(0)
        if not u.startswith(("http://", "https://")):
            u = (base_url.rstrip("/") + "/" + u.lstrip("/")) if u.startswith("/") else base_url
        return f'<a href="{html.escape(u)}" target="_blank" rel="noopener">{html.escape(t)}</a>'
    text = re.sub(r"\[([^\]]*)\]\(([^)]+)\)", _md_link, text)
    return text.strip()


def _escape_html(text: str) -> str:
    """转义 HTML 特殊字符"""
    return html.escape(str(text or ""), quote=False)


_EMOJI_IMG_CACHE = {}  # 缓存 emoji -> base64 data URL，避免重复渲染
_EMOJI_FAIL_CACHE = set()  # 已确认无法获取的 emoji，避免重复尝试


def _get_platform_default_emoji_style() -> str:
    """默认用 twemoji，CDN 稳定且无需多次重试"""
    return "twemoji"


def _get_pilmoji_source(style: str | None = None):
    """按 style 返回 pilmoji 源。可选: google, twemoji, apple, microsoft"""
    if style is None:
        raw = load_config().get("emoji_style")
        style = (raw or _get_platform_default_emoji_style()).strip().lower()
    try:
        if style == "google":
            from pilmoji.source import GoogleEmojiSource
            return GoogleEmojiSource()
        if style == "apple":
            from pilmoji.source import AppleEmojiSource
            return AppleEmojiSource()
        if style == "microsoft":
            from pilmoji.source import MicrosoftEmojiSource
            return MicrosoftEmojiSource()
        if style in ("twemoji", "twitter"):
            from pilmoji.source import Twemoji
            return Twemoji()
    except ImportError:
        pass
    return None


def _get_effective_emoji_style() -> str:
    """返回当前生效的 emoji_style（含平台默认）"""
    raw = load_config().get("emoji_style")
    return (raw or _get_platform_default_emoji_style()).strip().lower()


def _emoji_cache_file(primary: str, emoji_char: str, size: int = 18) -> Path:
    """按风格/字符/尺寸生成本地缓存文件路径（ROOT/emoji）。"""
    codepoints = "-".join(f"{ord(ch):x}" for ch in emoji_char)
    safe_primary = re.sub(r"[^a-z0-9_-]", "_", (primary or "twemoji").lower())
    return EMOJI_CACHE_DIR / f"{safe_primary}_{size}_{codepoints}.png"


def _render_emoji_to_data_url(emoji_char: str, size: int = 18) -> str | None:
    """优先读本地缓存；未命中时从 CDN 获取并写入 ROOT/emoji 缓存。"""
    if not HAS_PILMOJI or not emoji_char:
        return None
    primary = _get_effective_emoji_style()
    cache_key = f"{primary}:{emoji_char}"
    if cache_key in _EMOJI_IMG_CACHE:
        return _EMOJI_IMG_CACHE[cache_key]
    if cache_key in _EMOJI_FAIL_CACHE:
        return None
    import base64
    import io
    cache_file = _emoji_cache_file(primary, emoji_char, size)
    try:
        if cache_file.exists():
            b64 = base64.b64encode(cache_file.read_bytes()).decode("ascii")
            url = f"data:image/png;base64,{b64}"
            _EMOJI_IMG_CACHE[cache_key] = url
            return url
    except Exception:
        pass
    source = _get_pilmoji_source(primary)
    if source is None:
        try:
            from pilmoji.source import Twemoji
            source = Twemoji()
        except ImportError:
            return None
    try:
        stream = source.get_emoji(emoji_char)
        if stream:
            orig = Image.open(stream).convert("RGBA")
            resized = orig.resize((size, size), Image.LANCZOS)
            buf = io.BytesIO()
            resized.save(buf, format="PNG")
            png_bytes = buf.getvalue()
            try:
                EMOJI_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cache_file.write_bytes(png_bytes)
            except Exception:
                pass
            b64 = base64.b64encode(png_bytes).decode("ascii")
            url = f"data:image/png;base64,{b64}"
            _EMOJI_IMG_CACHE[cache_key] = url
            return url
    except Exception:
        pass
    _EMOJI_FAIL_CACHE.add(cache_key)
    return None


def _collect_emoji_chars(s: str) -> list[str]:
    """从 HTML 中提取所有需要渲染的 emoji 字符（已去重）"""
    if not s:
        return []
    chars = set()
    for m in re.finditer(r"&#(x[0-9a-fA-F]+|\d+);", s):
        g = m.group(1)
        code = int(g[1:], 16) if g.startswith("x") else int(g, 10)
        if code >= 0x10000 and code <= 0x10FFFF:
            chars.add(chr(code))
        elif code in (0x2705, 0x274C, 0x274E, 0x2B50, 0x2B55):
            chars.add(chr(code))
    for m in re.finditer(r"[\U00010000-\U0010FFFF]", s):
        chars.add(m.group(0))
    for c in "\u2705\u274c\u274e\u2b50\u2b55":
        if c in s:
            chars.add(c)
    return list(chars)


def _prefetch_emoji_batch(chars: list[str], callback=None):
    """在后台线程批量预取 emoji 图片到缓存，完成后调用 callback（在主线程外）"""
    if not HAS_PILMOJI or not chars:
        if callback:
            callback()
        return
    primary = _get_effective_emoji_style()
    uncached = [c for c in chars if f"{primary}:{c}" not in _EMOJI_IMG_CACHE and f"{primary}:{c}" not in _EMOJI_FAIL_CACHE]
    if not uncached:
        if callback:
            callback()
        return

    def _worker():
        try:
            for char in uncached:
                _render_emoji_to_data_url(char)
        finally:
            if callback:
                callback()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


def _replace_emoji_for_tkhtml(s: str, fallback: str = "\u2022", use_pilmoji: bool = False) -> str:
    """
    Tkhtml/Tcl-Tk 无法正确渲染 emoji。默认用文本等效符号（✓• 等）；use_pilmoji=True 时尝试转为图片（可能显示异常）。
    """
    if not s:
        return s

    _bmp_emoji_map = {
        "\u2705": "\u2713", "\u274c": "\u2717", "\u274e": "\u2717",
        "\u2b50": "*", "\u2b55": "\u2022",
    }
    _img_tpl = '<img src="{}" alt="" style="vertical-align:-4px;width:18px;height:18px;display:inline-block;margin:0 1px">'

    def _to_img(char: str):
        url = _render_emoji_to_data_url(char) if (use_pilmoji and HAS_PILMOJI) else None
        return _img_tpl.format(url) if url else None

    def replace_entity(match):
        g = match.group(1)
        code = int(g[1:], 16) if g.startswith("x") else int(g, 10)
        if code > 0x10FFFF:
            return match.group(0)
        char = chr(code)
        if code >= 0x10000:
            return _to_img(char) or fallback
        if code in (0x2705, 0x274C, 0x274E, 0x2B50, 0x2B55):
            return _to_img(char) or _bmp_emoji_map.get(char, fallback)
        return match.group(0)

    s = re.sub(r"&#(x[0-9a-fA-F]+|\d+);", replace_entity, s)
    s = re.sub(r"\ufe0f", "", s)

    def replace_supp(match):
        c = match.group(0)
        return _to_img(c) or fallback

    s = re.sub(r"[\U00010000-\U0010FFFF]", replace_supp, s)

    for emoji_char, repl in _bmp_emoji_map.items():
        if emoji_char in s:
            base = emoji_char.replace("\ufe0f", "")
            s = s.replace(emoji_char, _to_img(base) or repl)
    return s


def _format_date_official(iso_date: str) -> str:
    """将 ISO 日期转为官方格式「2025年12月5日」"""
    if not iso_date or not isinstance(iso_date, str):
        return str(iso_date or "")
    s = iso_date.strip()[:10]
    try:
        dt = datetime.strptime(s, "%Y-%m-%d")
        return f"{dt.year}年{dt.month}月{dt.day}日"
    except (ValueError, TypeError):
        return s


def _format_publish_notes_official(notes_html: str) -> str:
    """将 publishNotes HTML 转为官方格式：版本号加粗，更新项以 - 列表展示"""
    if not notes_html:
        return ""
    text = strip_html(notes_html)
    text = html.unescape(text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    out = []
    for i, ln in enumerate(lines):
        if re.match(r'^\d+\.\d+(\.\d+)*$', ln):
            if out:
                out.append("<br>")
            out.append(f'<strong>{_escape_html(ln)}</strong><br>')
        elif ln.startswith("-"):
            out.append(f'{_escape_html(ln)}<br>')
        else:
            out.append(f'{_escape_html(ln)}<br>')
    result = "".join(out)
    if len(result) > 8000:
        result = result[:8000] + "<br>... (已截断)"
    return result


def _build_srp_compat_table(detail: dict) -> str:
    """
    构建「可编程渲染管线 (SRP) 兼容性」表格，与 Unity 官网概述一致。
    表头：Unity版本 | 内置渲染管线 | 通用渲染管线 (URP) | 高清渲染管线 (HDRP)
    """
    uploads = detail.get("uploads") or {}
    if not isinstance(uploads, dict):
        return ""
    compat_cols = [("standard", "内置"), ("lightweight", "URP"), ("hd", "HDRP")]
    rows = []
    for unity_ver, info in uploads.items():
        if not isinstance(info, dict):
            continue
        srps = info.get("srps") or []
        if not isinstance(srps, list):
            continue
        srp_set = {str(s).lower() for s in srps}
        cells = [_escape_html(unity_ver)]
        seen_urp = False
        for k, _ in compat_cols:
            if k in ("lightweight", "urp"):
                if seen_urp:
                    continue
                has = "lightweight" in srp_set or "urp" in srp_set
                seen_urp = True
            else:
                has = k in srp_set
            cells.append("兼容" if has else "—")
        rows.append(cells)
    if not rows:
        return ""
    header = '<div class="technical-details"><table><thead><tr><th>Unity版本</th><th>内置渲染管线</th><th>通用渲染管线 (URP)</th><th>高清渲染管线 (HDRP)</th></tr></thead><tbody>'
    body = "".join(f"<tr>{''.join(f'<td>{c}</td>' for c in r)}</tr>" for r in rows)
    return header + body + "</tbody></table></div>"


def format_info_html(data: dict, extra_notice: str = "", dark: bool = False, use_pilmoji: bool = None) -> str:
    """生成 Unity Asset Store 风格的 HTML；dark=True 时使用深色背景；use_pilmoji=True 时用 pilmoji 渲染 emoji 为图片（可在 asset_store_config.json 中配置 use_emoji_images）"""
    pid = data.get("packageId")
    display_name = data.get("displayName", "")
    detail = data.get("detail")

    _emoji_fonts = '"Segoe UI Emoji", "Segoe UI Symbol", "Apple Color Emoji", "Noto Color Emoji"'
    if dark:
        style = f"""
    body {{ font-family: Inter, "Noto Sans SC", Roboto, "Segoe UI", {_emoji_fonts}, sans-serif; background: #1a1a1a; color: #ffffff; margin: 12px 16px; font-size: 16px; line-height: 1.5; }}
    .title {{ font-size: 1.125rem; font-weight: 600; color: #ffffff; margin-bottom: 16px; }}
    .meta {{ color: #ffffff; margin-bottom: 16px; }}
    .meta-row {{ margin: 4px 0; }}
    .meta-label {{ color: #b0b0b0; font-size: 0.875rem; }}
    .section {{ margin-top: 20px; padding-top: 16px; border-top: 1px solid #444; }}
    .section-title {{ font-size: 0.875rem; font-weight: 600; color: #ffffff; margin-bottom: 8px; }}
    .desc, .notes {{ color: #ffffff; white-space: pre-wrap; word-wrap: break-word; }}
    .notice {{ background: #3d3d00; color: #e0e0a0; padding: 8px 12px; border-radius: 4px; margin-bottom: 12px; }}
    .uploads {{ margin: 4px 0; }}
    .uploads-item {{ padding: 2px 0; color: #ffffff; }}
    .technical-details table, .rpc table {{ border-collapse: collapse; margin: 8px 0; }}
    .technical-details td, .rpc td {{ border: 1px solid #444; padding: 4px 8px; }}
    a {{ color: #7eb8ff; text-decoration: none; }}
    a:hover {{ color: #9ec8ff; text-decoration: underline; }}
    """
    else:
        style = """
    body {
        font-family: Inter, "Noto Sans SC", "Noto Sans JP", "Noto Sans KR", Roboto, -apple-system, BlinkMacSystemFont, "Segoe UI", """ + _emoji_fonts + """, Oxygen, Ubuntu, Cantarell, "Fira Sans", "Droid Sans", "Helvetica Neue", Helvetica, Arial, sans-serif;
        background: #fff;
        color: #212121;
        margin: 12px 16px;
        font-size: 16px;
        font-weight: 400;
        line-height: 1.5;
        -webkit-font-smoothing: antialiased;
        -moz-osx-font-smoothing: grayscale;
    }
    .title { font-size: 1.125rem; font-weight: 600; color: #212121; margin-bottom: 16px; }
    .meta { color: #212121; margin-bottom: 16px; }
    .meta-row { margin: 4px 0; }
    .meta-label { color: #757575; font-size: 0.875rem; }
    .section { margin-top: 20px; padding-top: 16px; border-top: 1px solid #eceff1; }
    .section-title { font-size: 0.875rem; font-weight: 600; color: #212121; margin-bottom: 8px; }
    .desc, .notes { color: #212121; white-space: pre-wrap; word-wrap: break-word; }
    .notice { background: #fff3cd; color: #856404; padding: 8px 12px; border-radius: 4px; margin-bottom: 12px; }
    .uploads { margin: 4px 0; }
    .uploads-item { padding: 2px 0; color: #212121; }
    .technical-details table, .rpc table { border-collapse: collapse; margin: 8px 0; }
    .technical-details td, .rpc td { border: 1px solid #ddd; padding: 4px 8px; }
    a { color: #3a5bc7; text-decoration: none; }
    a:hover { color: #4268e6; text-decoration: underline; }
    """
    parts = [f'<html><head><meta charset="utf-8"><style>{style}</style></head><body>']

    if extra_notice:
        parts.append(f'<div class="notice">{_escape_html(extra_notice)}</div>')

    parts.append(f'<div class="title">{_escape_html(display_name)}</div>')
    parts.append(f'<div class="meta meta-row"><span class="meta-label">packageId</span> {_escape_html(str(pid))}</div>')

    if not detail:
        muted = "#b0b0b0" if dark else "#757575"
        parts.append(f'<p style="color:{muted};">无详情信息，请先在「获取包商店信息」中抓取。</p>')
        parts.append("</body></html>")
        return "".join(parts)

    name = detail.get("name") or detail.get("displayName") or ""
    if name and name != display_name:
        parts.append(f'<div class="meta meta-row"><span class="meta-label">名称</span> {_escape_html(name)}</div>')

    ver = detail.get("version", {})
    if isinstance(ver, dict):
        vname = ver.get("name", "")
        vdate = ver.get("publishedDate", "")
        if vname:
            parts.append(f'<div class="meta meta-row"><span class="meta-label">版本</span> {_escape_html(vname)} (当前版本)</div>')
        if vdate:
            parts.append(f'<div class="meta meta-row"><span class="meta-label">发布时间</span> {_escape_html(_format_date_official(vdate))}</div>')

    pub = detail.get("productPublisher", {})
    if isinstance(pub, dict) and pub.get("name"):
        url = pub.get("url", "")
        if url:
            parts.append(f'<div class="meta meta-row"><span class="meta-label">出版商</span> <a href="{_escape_html(url)}" target="_blank">{_escape_html(pub["name"])}</a></div>')
        else:
            parts.append(f'<div class="meta meta-row"><span class="meta-label">出版商</span> {_escape_html(pub["name"])}</div>')

    cat = detail.get("category", {})
    if isinstance(cat, dict) and cat.get("name"):
        parts.append(f'<div class="meta meta-row"><span class="meta-label">分类</span> {_escape_html(cat["name"])}</div>')

    loc = detail.get("localizations") or {}
    uploads = detail.get("uploads", {})
    if isinstance(uploads, dict) and uploads:
        parts.append('<div class="section"><div class="section-title">资源包内容</div><div class="uploads">')
        for unity_ver, info in uploads.items():
            if isinstance(info, dict):
                size = info.get("downloadSize", "")
                count = info.get("assetCount", "")
                size_str = ""
                if size:
                    try:
                        size_kb = int(size) / 1024
                        size_str = f"{size_kb:.1f} KB"
                    except (ValueError, TypeError):
                        size_str = f"{size} bytes"
                count_str = str(count) if count else ""
                rows = []
                if size_str:
                    rows.append(f'文件大小: {size_str}')
                if count_str:
                    rows.append(f'文件数量: {count_str}')
                if rows:
                    line = " · ".join(rows) if len(uploads) == 1 else f'<span class="meta-label">{_escape_html(unity_ver)}</span> ' + " · ".join(rows)
                    parts.append(f'<div class="uploads-item">{line}</div>')
        parts.append("</div></div>")

    # 描述前：elevatorPitch（如「FPS 计数器：...」）
    pitch = None
    if isinstance(loc, dict) and isinstance(loc.get("zh-CN"), dict):
        pitch = loc["zh-CN"].get("elevatorPitch")
    pitch = pitch or detail.get("elevatorPitch")
    if pitch:
        pitch_safe = html_to_safe_html(str(pitch))
        parts.append('<div class="section"><div class="section-title">概述</div><div class="desc">')
        parts.append(pitch_safe.replace("\n", "<br>"))
        parts.append("</div></div>")

    # 可编程渲染管线 (SRP) 兼容性表格（与官网概述一致）
    _srp_table = _build_srp_compat_table(detail)
    if _srp_table:
        parts.append('<div class="section"><div class="section-title">可编程渲染管线 (SRP) 兼容性</div>')
        parts.append(_srp_table)
        parts.append("</div>")
    kw = detail.get("keywords") or detail.get("relatedKeywords")
    if kw:
        parts.append('<div class="section"><div class="section-title">相关关键词</div><div class="keywords">')
        if isinstance(kw, list):
            parts.append(_escape_html(", ".join(str(x) for x in kw)))
        else:
            parts.append(_escape_html(str(kw)))
        parts.append("</div></div>")
    lnks = detail.get("links") or detail.get("externalLinks") or detail.get("productLinks") or detail.get("productLinksList")
    if lnks:
        parts.append('<div class="section"><div class="section-title">链接</div><div class="links">')
        if isinstance(lnks, list):
            frags = []
            for it in lnks:
                if isinstance(it, dict):
                    href = it.get("url") or it.get("href") or it.get("link")
                    lbl = it.get("label") or it.get("name") or it.get("title") or href or ""
                    if href:
                        frags.append(f'<a href="{html.escape(str(href), quote=True)}" target="_blank" rel="noopener">{_escape_html(lbl)}</a>')
                elif isinstance(it, str):
                    frags.append(_escape_html(it))
            parts.append(" | ".join(frags))
        else:
            parts.append(_escape_html(str(lnks)))
        parts.append("</div></div>")
    desc = detail.get("description") or ""
    loc = detail.get("localizations", {}).get("zh-CN", {})
    if isinstance(loc, dict) and loc.get("description"):
        desc = loc["description"]
    if desc:
        desc_safe = html_to_safe_html(desc)
        if len(desc_safe) > 8000:
            desc_safe = desc_safe[:8000] + "\n... (已截断)"
        parts.append('<div class="section"><div class="section-title">描述</div><div class="desc">')
        parts.append(desc_safe.replace("\n", "<br>"))
        parts.append("</div></div>")

    # 技术细节：放在描述后面（与官网一致）
    _loc_full = detail.get("localizations") or {}
    _kf = (isinstance(_loc_full, dict) and (_loc_full.get("zh-CN") or {}) or {}).get("keyFeatures") or detail.get("keyFeatures")
    tech = _kf or detail.get("technicalDetails") or detail.get("renderPipelineCompatibility")
    if tech:
        parts.append('<div class="section"><div class="section-title">技术细节</div><div class="technical-details">')
        if isinstance(tech, str):
            if tech.strip().startswith("<"):
                tech_safe = re.sub(r"<script[^>]*>[\s\S]*?</script>", "", tech)
                tech_safe = re.sub(r"<iframe[^>]*>[\s\S]*?</iframe>", "", tech_safe)
                tech_safe = tech_safe.replace("</br>", "<br>")
                parts.append(tech_safe)
            else:
                parts.append(_escape_html(tech).replace("\n", "<br>"))
        elif isinstance(tech, dict):
            parts.append("<table><tbody>")
            for k, v in tech.items():
                parts.append(f"<tr><td>{_escape_html(str(k))}</td><td>{_escape_html(str(v))}</td></tr>")
            parts.append("</tbody></table>")
        elif isinstance(tech, list):
            for item in tech:
                if isinstance(item, dict):
                    parts.append("<table><tbody>")
                    for k, v in item.items():
                        parts.append(f"<tr><td>{_escape_html(str(k))}</td><td>{_escape_html(str(v))}</td></tr>")
                    parts.append("</tbody></table>")
                else:
                    parts.append(_escape_html(str(item)) + "<br>")
        else:
            parts.append(_escape_html(str(tech)))
        parts.append("</div></div>")
    rpc = detail.get("renderPipelineCompatibility")
    if rpc and rpc is not tech:
        parts.append('<div class="section"><div class="section-title">渲染管线兼容性</div><div class="rpc">')
        if isinstance(rpc, str):
            if rpc.strip().startswith("<"):
                parts.append(rpc)
            else:
                parts.append(_escape_html(rpc).replace("\n", "<br>"))
        elif isinstance(rpc, (dict, list)):
            parts.append(_escape_html(str(rpc)).replace("\n", "<br>"))
        else:
            parts.append(_escape_html(str(rpc)))
        parts.append("</div></div>")

    notes = detail.get("publishNotes") or (loc.get("publishNotes") if isinstance(loc, dict) else "")
    if notes:
        notes_html = _format_publish_notes_official(str(notes))
        parts.append('<div class="section"><div class="section-title">更新说明</div><div class="notes">')
        parts.append(notes_html)
        parts.append("</div></div>")

    parts.append("</body></html>")
    html_result = "".join(parts)
    if use_pilmoji is None:
        use_pilmoji = load_config().get("use_emoji_images", True)  # 默认启用 pilmoji 图片，设为 false 则用 • 等文本
    return _replace_emoji_for_tkhtml(html_result, use_pilmoji=use_pilmoji)


def format_info(data: dict) -> str:
    lines = []
    pid = data.get("packageId")
    display_name = data.get("displayName", "")
    detail = data.get("detail")
    lines.append(f"【{display_name}】 packageId={pid}\n")

    if not detail:
        lines.append("\n(无详情信息，请先在「获取包商店信息」中抓取)")
        return "\n".join(lines)

    name = detail.get("name") or detail.get("displayName") or ""
    if name:
        lines.append(f"\n名称: {name}")

    ver = detail.get("version", {})
    if isinstance(ver, dict):
        vname = ver.get("name", "")
        vdate = ver.get("publishedDate", "")
        if vname:
            lines.append(f"版本: {vname}")
        if vdate:
            lines.append(f"发布日期: {vdate}")

    pub = detail.get("productPublisher", {})
    if isinstance(pub, dict) and pub.get("name"):
        lines.append(f"出版商: {pub.get('name')}")
        if pub.get("url"):
            lines.append(f"  官网: {pub['url']}")

    cat = detail.get("category", {})
    if isinstance(cat, dict) and cat.get("name"):
        lines.append(f"分类: {cat['name']}")

    uploads = detail.get("uploads", {})
    if isinstance(uploads, dict) and uploads:
        lines.append("\n包大小 (按 Unity 版本):")
        for unity_ver, info in uploads.items():
            if isinstance(info, dict):
                size = info.get("downloadSize", "")
                count = info.get("assetCount", "")
                if size:
                    try:
                        size_kb = int(size) / 1024
                        lines.append(f"  {unity_ver}: {size_kb:.1f} KB, {count} 个文件")
                    except (ValueError, TypeError):
                        lines.append(f"  {unity_ver}: {size} bytes")

    tech = detail.get("technicalDetails") or detail.get("renderPipelineCompatibility")
    if tech:
        lines.append("\n--- 技术细节 ---")
        lines.append(tech if isinstance(tech, str) else str(tech))
    kw = detail.get("keywords") or detail.get("relatedKeywords")
    if kw:
        lines.append("\n--- 相关关键词 ---")
        lines.append(", ".join(str(x) for x in kw) if isinstance(kw, list) else str(kw))

    desc = detail.get("description") or ""
    loc = detail.get("localizations", {}).get("zh-CN", {})
    if isinstance(loc, dict) and loc.get("description"):
        desc = loc["description"]
    if desc:
        lines.append("\n--- 描述 ---")
        lines.append(strip_html(desc)[:8000])
        if len(strip_html(desc)) > 8000:
            lines.append("\n... (已截断)")

    notes = detail.get("publishNotes") or (loc.get("publishNotes") if isinstance(loc, dict) else "")
    if notes:
        lines.append("\n--- 更新说明 ---")
        lines.append(strip_html(str(notes))[:3000])
        if len(strip_html(str(notes))) > 3000:
            lines.append("\n... (已截断)")

    return "\n".join(lines)


class PackageViewerApp:
    def __init__(self):
        self.root = tk.Tk()
        ver = _load_version()
        title = "Unity AssetStore资源查看器" + (f" v{ver}" if ver else "")
        self.root.title(title)
        self.root.geometry("1000x750")
        self.root.minsize(600, 450)

        self._set_icon()

        config = load_config()
        download_dir = config.get("download_dir", "downloads")
        self.download_dir = Path(download_dir)
        if not self.download_dir.is_absolute():
            self.download_dir = (ROOT / download_dir).resolve()

        self.purchases = load_purchases()
        self.filename_to_pid = build_filename_to_package_id(self.purchases)
        self.package_files = []
        self.missing_items = []
        self.purchase_order = {}
        self.sort_by_snapshot = True
        self.listbox_map = {}
        self.fetch_running = False

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def _on_closing(self):
        """关闭窗口时若正在获取则先停止，确保进程能退出"""
        if getattr(self, "fetch_running", False):
            self._fetch_stop_requested = True
        self.root.destroy()
        if getattr(sys, "frozen", False):
            os._exit(0)  # 打包 exe 时强制退出，避免残留进程

    def _set_icon(self):
        if not ICON_PATH.exists():
            return
        try:
            img = tk.PhotoImage(file=str(ICON_PATH))
            self.root.wm_iconphoto(True, img)
            self._icon_img = img
        except Exception:
            pass

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=8)
        main.pack(fill=tk.BOTH, expand=True)
        self._main_frame = main  # 用于固定放置「明/暗」主题按钮

        self._notebook = ttk.Notebook(main)
        self._notebook.pack(fill=tk.BOTH, expand=True)
        notebook = self._notebook

        # 主题：浅色/深色（黑白色调），可切换；19:00～次日07:00 默认深色
        hour = datetime.now().hour
        self._dark_theme = hour >= 19 or hour < 7
        self._THEME_LIGHT = {
            "bg": "#f5f5f5", "fg": "#212121", "fg_muted": "#757575", "card": "#ffffff",
            "select_bg": "#e3f2fd", "border": "#eceff1", "btn_bg": "#e0e0e0",
            "btn_active": "#d0d0d0", "entry_bg": "#e0e0e0",
        }
        self._THEME_DARK = {
            "bg": "#1a1a1a", "fg": "#ffffff", "fg_muted": "#b0b0b0", "card": "#2d2d2d",
            "select_bg": "#1a3a52", "border": "#444444", "btn_bg": "#606060",
            "btn_active": "#707070", "entry_bg": "#505050",
        }
        _t = self._THEME_DARK if self._dark_theme else self._THEME_LIGHT
        self._web_bg = _t["bg"]
        self._web_fg = _t["fg"]
        self._web_fg_muted = _t["fg_muted"]
        self._web_card_bg = _t["card"]
        self._web_select_bg = _t["select_bg"]
        self._web_border = _t["border"]
        try:
            _sty = ttk.Style()
            _sty.theme_use("clam")  # Windows 下 vista 会忽略 Frame 等背景色，clam 支持自定义
            _sty.configure("Web.TFrame", background=self._web_bg)
            _sty.configure("Web.TLabel", background=self._web_bg, foreground=self._web_fg, font=("Segoe UI", 9))
            _sty.configure("Web.TButton", background=self._THEME_LIGHT["btn_bg"], foreground=self._web_fg, padding=(10, 4))
            _sty.map("Web.TButton", background=[("active", self._THEME_LIGHT["btn_active"]), ("pressed", "#bdbdbd")])
            _sty.configure("Web.TCheckbutton", background=self._web_bg, foreground=self._web_fg)
            _sty.configure("Web.Card.TLabel", background=self._web_card_bg, foreground=self._web_fg, font=("Segoe UI", 9))
            _sty.configure("Web.Card.TCheckbutton", background=self._web_card_bg, foreground=self._web_fg)
            _sb_bg, _sb_trough = (_t["btn_bg"], _t["card"]) if self._dark_theme else ("#b0b0b0", "#e8e8e8")
            _sty.configure("Vertical.TScrollbar", troughrelief="flat", background=_sb_bg, troughcolor=_sb_trough)
            _sty.configure("Web.Detail.TFrame", background=self._web_card_bg)
        except Exception:
            pass

        self._theme_frames_bg = []
        # Tab 1: 包列表查看（使用 Web.TFrame 以随主题变色）
        tab1 = ttk.Frame(notebook, style="Web.TFrame", padding=4)
        notebook.add(tab1, text="包列表查看")
        tab1_hint_row = tk.Frame(tab1, bg=self._web_bg)
        tab1_hint_row.pack(fill=tk.X)
        self._path_label = ttk.Label(
            tab1_hint_row,
            text=f"unitypackage 下载目录 (asset_store_config.json 的 download_dir): {self.download_dir}",
            style="Web.TLabel",
        )
        self._path_label.pack(side=tk.LEFT, fill=tk.X, expand=True, anchor=tk.W)
        self._theme_frames_bg.append(tab1_hint_row)
        tab1_manual_hint = tk.Frame(tab1, bg=self._web_bg)
        tab1_manual_hint.pack(fill=tk.X)
        self._theme_frames_bg.append(tab1_manual_hint)
        ttk.Label(
            tab1_manual_hint,
            text="手动映射数据保存在 manual_mapping.json，注意备份",
            style="Web.TLabel",
        ).pack(side=tk.LEFT, anchor=tk.W)
        self._file_count_label = ttk.Label(
            tab1_manual_hint,
            text="",
            style="Web.TLabel",
        )
        self._file_count_label.pack(side=tk.LEFT, anchor=tk.W, padx=(8, 0))
        if not self.download_dir.exists():
            ttk.Label(tab1, text="(目录不存在)", style="Web.TLabel", foreground="red").pack(anchor=tk.W)

        paned = ttk.PanedWindow(tab1, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, pady=4)
        self._main_paned = paned

        left_container = tk.Frame(paned, bg=self._web_bg)
        paned.add(left_container, weight=1)
        left_frame = tk.Frame(left_container, bg=self._web_bg, padx=8, pady=8)
        left_frame.pack(fill=tk.BOTH, expand=True)
        search_row = tk.Frame(left_frame, bg=self._web_bg)
        search_row.pack(fill=tk.X, pady=(0, 6))
        self.sort_btn = tk.Button(
            search_row,
            text="按购买顺序",
            command=lambda: self._toggle_sort(),
            font=("Segoe UI", 10),
            bg=self._THEME_LIGHT["btn_bg"],
            fg=self._web_fg,
            activebackground=self._THEME_LIGHT["btn_active"],
            activeforeground=self._web_fg,
            relief=tk.FLAT,
            padx=10,
            pady=4,
            cursor="hand2",
        )
        self.sort_btn.pack(side=tk.LEFT, padx=(0, 6))
        self.filter_btn = tk.Button(
            search_row,
            text="筛选",
            command=self._toggle_filter_panel,
            font=("Segoe UI", 10),
            bg=self._THEME_LIGHT["btn_bg"],
            fg=self._web_fg,
            activebackground=self._THEME_LIGHT["btn_active"],
            activeforeground=self._web_fg,
            relief=tk.FLAT,
            padx=10,
            pady=4,
            cursor="hand2",
        )
        self.filter_btn.pack(side=tk.LEFT, padx=(0, 6))
        self.listbox = tk.Listbox(
            left_frame,
            font=("Segoe UI", 10),
            selectmode=tk.SINGLE,
            bg=self._web_card_bg,
            fg=self._web_fg,
            selectbackground=self._web_select_bg,
            selectforeground=self._web_fg,
            relief=tk.FLAT,
            highlightthickness=0,
        )
        list_scroll = ttk.Scrollbar(left_frame, command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=list_scroll.set)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        list_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)
        self.listbox.bind("<MouseWheel>", lambda e: self.listbox.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        btn_row = tk.Frame(left_container, bg=self._web_bg)
        btn_row.pack(anchor=tk.W, pady=(6, 0))
        self._refresh_btn = tk.Button(
            btn_row,
            text="更新包下载状态",
            command=self._refresh,
            font=("Segoe UI", 10),
            bg=self._THEME_LIGHT["btn_bg"],
            fg=self._web_fg,
            activebackground=self._THEME_LIGHT["btn_active"],
            activeforeground=self._web_fg,
            relief=tk.FLAT,
            padx=10,
            pady=4,
            cursor="hand2",
        )
        self._refresh_btn.pack(side=tk.LEFT)
        self._theme_frames_bg.extend([left_container, left_frame, search_row, btn_row])

        right_frame = tk.Frame(paned, bg=self._web_bg, padx=8, pady=4)
        paned.add(right_frame, weight=2)
        self._right_show_filter = False

        # 右侧：详情 与 筛选 两个视图，同一时间只显示一个
        self.detail_container = tk.Frame(right_frame, bg=self._web_bg)
        self.detail_container.pack(fill=tk.BOTH, expand=True)
        self.filter_container = tk.Frame(right_frame, bg=self._web_bg)
        self._theme_frames_bg.extend([right_frame, self.detail_container, self.filter_container])

        self._use_html = HAS_HTML_FRAME
        if not self._use_html and not getattr(sys, "frozen", False):
            # 仅 Python 直接运行时显示；exe 模式下不显示此提示
            _no_html_hint = tk.Frame(self.detail_container, bg=self._web_bg, pady=4)
            _no_html_hint.pack(fill=tk.X)
            ttk.Label(
                _no_html_hint,
                style="Web.TLabel",
                text=f"(未检测到 tkinterweb，当前为纯文本模式。请在插件页签安装「{_get_plugin_title_by_id('tkinterweb', '网页风格')}」)",
                foreground=self._web_fg_muted,
                font=("Segoe UI", 9),
            ).pack(side=tk.LEFT, anchor=tk.W)
            self._theme_frames_bg.append(_no_html_hint)
        if self._use_html and not HAS_PILMOJI and not getattr(sys, "frozen", False):
            _no_pilmoji_hint = tk.Frame(self.detail_container, bg=self._web_bg, pady=4)
            _no_pilmoji_hint.pack(fill=tk.X)
            ttk.Label(
                _no_pilmoji_hint,
                style="Web.TLabel",
                text=f"(未检测到 pilmoji，emoji 图片将使用文本符号代替。请在插件页签安装「{_get_plugin_title_by_id('pilmoji', 'emoji表情包')}」)",
                foreground=self._web_fg_muted,
                font=("Segoe UI", 9),
            ).pack(side=tk.LEFT, anchor=tk.W)
            self._theme_frames_bg.append(_no_pilmoji_hint)
        if self._use_html:
            self.detail_widget = HtmlFrame(
                self.detail_container,
                messages_enabled=False,
                selection_enabled=True,
                on_link_click=lambda url: webbrowser.open(url),
                style="Web.Detail.TFrame",
            )
            self.detail_widget.pack(fill=tk.BOTH, expand=True)
        else:
            self.detail_widget = scrolledtext.ScrolledText(
                self.detail_container,
                wrap=tk.WORD,
                font=("Segoe UI", 10),
                state=tk.DISABLED,
                bg=self._web_card_bg,
                fg=self._web_fg,
            )
            self.detail_widget.pack(fill=tk.BOTH, expand=True)

        # 嵌入详情区域内部的右上角：返回文档、在Unity中打开、手动映射/取消映射
        _open_btn_border = "#1565c0"
        _manual_btn_border = "#c62828"  # 红色描边
        _btn_style = {
            "font": ("Segoe UI", 10),
            "bg": "#ffffff",
            "fg": "#212121",
            "activebackground": "#f5f5f5",
            "activeforeground": "#212121",
            "relief": tk.FLAT,
            "bd": 0,
            "padx": 12,
            "pady": 6,
            "cursor": "hand2",
        }
        _manual_btn_style = {**_btn_style, "width": 14}
        self._open_in_unity_frame = tk.Frame(self.detail_container, bg=_open_btn_border, padx=1, pady=1)
        self._open_in_unity_frame.place(relx=1, rely=0, x=-32, y=36, anchor=tk.NE)
        self._open_in_unity_frame.lift()
        self._open_in_unity_btn = tk.Button(
            self._open_in_unity_frame,
            text="在Unity中打开",
            command=self._open_in_unity,
            width=14,
            **_btn_style,
        )
        self._open_in_unity_btn.pack()
        self._manual_map_frame = tk.Frame(self.detail_container, bg=_manual_btn_border, padx=1, pady=1)
        self._manual_map_frame.place(relx=1, rely=0, x=-32, y=92, anchor=tk.NE)
        self._manual_map_frame.lift()
        self._manual_map_btn = tk.Button(
            self._manual_map_frame,
            text="手动映射",
            command=self._on_manual_map_click,
            **_manual_btn_style,
        )
        self._unmap_btn = tk.Button(
            self._manual_map_frame,
            text="取消映射",
            command=self._on_unmap_click,
            **_manual_btn_style,
        )
        self._update_open_in_unity_visibility()

        self._back_to_doc_frame = tk.Frame(self.detail_container, bg=_open_btn_border, padx=1, pady=1)
        self._back_to_doc_frame.place(relx=1, rely=0, x=-160, y=36, anchor=tk.NE)
        self._back_to_doc_frame.lift()
        self._back_to_doc_btn = tk.Button(
            self._back_to_doc_frame,
            text="返回文档",
            command=self._back_to_document,
            font=("Segoe UI", 10),
            bg="#ffffff",
            fg="#212121",
            activebackground="#f5f5f5",
            activeforeground="#212121",
            relief=tk.FLAT,
            bd=0,
            padx=12,
            pady=6,
            cursor="hand2",
        )
        self._back_to_doc_btn.pack()
        self._update_back_to_doc_visibility()

        self._build_filter_panel()
        self._main_sash_set = False
        self.root.after(400, self._set_main_sash_once)

        # Tab 2: 获取包商店信息（网页样式 + 随主题变色）
        tab2 = ttk.Frame(notebook, style="Web.TFrame", padding=8)
        notebook.add(tab2, text="获取包商店信息")
        self._tab2_container = tk.Frame(tab2, bg=self._web_bg)
        self._tab2_container.pack(fill=tk.BOTH, expand=True)
        self._theme_frames_bg.append(self._tab2_container)

        tab2_hint_row = tk.Frame(self._tab2_container, bg=self._web_bg)
        tab2_hint_row.pack(fill=tk.X)
        self._theme_frames_bg.append(tab2_hint_row)
        ttk.Label(
            tab2_hint_row,
            style="Web.TLabel",
            text="根据 purchases_snapshot.json 文件获取每个包的详情到 metadata 目录，"
            "请保证已经执行过 unity_assets_downloader.py 的「获取已购买资产列表」阶段。",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, anchor=tk.W)
        fetch_row = tk.Frame(self._tab2_container, bg=self._web_bg)
        fetch_row.pack(fill=tk.X, pady=4)
        self._theme_frames_bg.append(fetch_row)
        ttk.Label(fetch_row, text="限制数量 (0=全部):", style="Web.TLabel").pack(side=tk.LEFT)
        self.limit_var = tk.IntVar(value=0)
        _t2 = self._THEME_DARK if self._dark_theme else self._THEME_LIGHT
        self._limit_entry = tk.Entry(
            fetch_row,
            textvariable=self.limit_var,
            width=10,
            font=("Segoe UI", 10),
            bg=_t2["entry_bg"],
            fg=self._web_fg,
            insertbackground=self._web_fg,
            selectbackground=self._web_select_bg,
            selectforeground=self._web_fg,
            relief=tk.FLAT,
            highlightthickness=1,
            highlightcolor=self._web_border,
            highlightbackground=self._web_border,
        )
        self._limit_entry.pack(side=tk.LEFT, padx=4, ipady=2, ipadx=4)
        self.fetch_btn = tk.Button(
            fetch_row,
            text="开始获取",
            command=self._start_fetch,
            font=("Segoe UI", 10),
            bg=self._THEME_LIGHT["btn_bg"],
            fg=self._web_fg,
            activebackground=self._THEME_LIGHT["btn_active"],
            activeforeground=self._web_fg,
            relief=tk.FLAT,
            padx=10,
            pady=4,
            cursor="hand2",
        )
        self.fetch_btn.pack(side=tk.LEFT, padx=4)
        self._fetch_stop_btn = tk.Button(
            fetch_row,
            text="停止",
            command=self._stop_fetch,
            font=("Segoe UI", 10),
            bg=self._THEME_LIGHT["btn_bg"],
            fg=self._web_fg,
            activebackground=self._THEME_LIGHT["btn_active"],
            activeforeground=self._web_fg,
            relief=tk.FLAT,
            padx=10,
            pady=4,
            cursor="hand2",
            state=tk.DISABLED,
        )
        self._fetch_stop_btn.pack(side=tk.LEFT, padx=4)
        self._fetch_stop_requested = False
        tk.Frame(fetch_row, bg=self._web_bg, width=12).pack(side=tk.LEFT)
        self.fetch_status = ttk.Label(fetch_row, text="", style="Web.TLabel")
        self.fetch_status.pack(side=tk.LEFT, padx=(0, 4))
        _spacer = tk.Frame(fetch_row, bg=self._web_bg, width=24)
        _spacer.pack(side=tk.LEFT)
        self._theme_frames_bg.append(_spacer)
        ttk.Label(fetch_row, text="单独拉取 ID:", style="Web.TLabel").pack(side=tk.LEFT, padx=(0, 4))
        self._single_fetch_var = tk.StringVar(value="")
        self._single_fetch_entry = tk.Entry(
            fetch_row,
            textvariable=self._single_fetch_var,
            width=8,
            font=("Segoe UI", 10),
            bg=_t2["entry_bg"],
            fg=self._web_fg,
            insertbackground=self._web_fg,
            selectbackground=self._web_select_bg,
            selectforeground=self._web_fg,
            relief=tk.FLAT,
            highlightthickness=1,
            highlightcolor=self._web_border,
            highlightbackground=self._web_border,
        )
        self._single_fetch_entry.pack(side=tk.LEFT, padx=2, ipady=2, ipadx=4)
        self._single_fetch_btn = tk.Button(
            fetch_row,
            text="拉取",
            command=self._start_single_fetch,
            font=("Segoe UI", 10),
            bg=self._THEME_LIGHT["btn_bg"],
            fg=self._web_fg,
            activebackground=self._THEME_LIGHT["btn_active"],
            activeforeground=self._web_fg,
            relief=tk.FLAT,
            padx=8,
            pady=4,
            cursor="hand2",
        )
        self._single_fetch_btn.pack(side=tk.LEFT, padx=2)
        tk.Frame(fetch_row, bg=self._web_bg, width=8).pack(side=tk.LEFT)
        self._single_fetch_status = ttk.Label(fetch_row, text="", style="Web.TLabel")
        self._single_fetch_status.pack(side=tk.LEFT, padx=(0, 4))

        # 日志区：Text + ttk 扁平滚动条（与包列表一致）
        fetch_log_frame = tk.Frame(self._tab2_container, bg=self._web_bg)
        fetch_log_frame.pack(fill=tk.BOTH, expand=True, pady=4)
        self._theme_frames_bg.append(fetch_log_frame)
        self.fetch_log = tk.Text(
            fetch_log_frame,
            wrap=tk.WORD,
            font=("Consolas", 9),
            height=20,
            bg=self._web_card_bg,
            fg=self._web_fg,
            relief=tk.FLAT,
            highlightthickness=0,
        )
        fetch_log_scroll = ttk.Scrollbar(fetch_log_frame, command=self.fetch_log.yview)
        self.fetch_log.configure(yscrollcommand=fetch_log_scroll.set)
        self.fetch_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        fetch_log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.fetch_log.bind("<MouseWheel>", lambda e: self.fetch_log.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        # Tab 3: 插件
        tab3 = ttk.Frame(notebook, style="Web.TFrame", padding=8)
        notebook.add(tab3, text="插件")
        self._tab3_container = tk.Frame(tab3, bg=self._web_bg)
        self._tab3_container.pack(fill=tk.BOTH, expand=True)
        self._theme_frames_bg.append(self._tab3_container)

        plugins_hint, plugins_data = _load_plugins()
        empty_hint = "插件列表为空。请到项目 GitHub 仓库下载 plugin.json 文件，并与 exe 放在同一目录。"
        tab3_hint = tk.Frame(self._tab3_container, bg=self._web_bg)
        tab3_hint.pack(fill=tk.X, pady=(0, 12))
        self._theme_frames_bg.append(tab3_hint)
        ttk.Label(
            tab3_hint,
            style="Web.TLabel",
            text=empty_hint if not plugins_data else plugins_hint,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, anchor=tk.W)

        tab3_canvas = tk.Canvas(self._tab3_container, highlightthickness=0, bg=self._web_bg)
        tab3_scroll = ttk.Scrollbar(self._tab3_container, command=tab3_canvas.yview)
        tab3_inner = tk.Frame(tab3_canvas, bg=self._web_bg)
        _plug_win_id = tab3_canvas.create_window((0, 0), window=tab3_inner, anchor=tk.NW)
        def _plug_inner_configure(e):
            b = tab3_canvas.bbox("all")
            tab3_canvas.configure(scrollregion=tab3_canvas.bbox("all"))
            w = tab3_canvas.winfo_width()
            ch = tab3_canvas.winfo_height()
            content_h = (b[3] - b[1]) if b else 0
            if w > 1:
                tab3_canvas.itemconfig(_plug_win_id, width=w)
            tab3_canvas.itemconfig(_plug_win_id, height=max(content_h, ch) if ch > 0 else content_h)
        tab3_inner.bind("<Configure>", _plug_inner_configure)
        def _plug_canvas_configure(e):
            if e.width > 1:
                tab3_canvas.itemconfig(_plug_win_id, width=e.width)
            if e.height > 1:
                b = tab3_canvas.bbox("all")
                content_h = (b[3] - b[1]) if b else e.height
                tab3_canvas.itemconfig(_plug_win_id, height=max(content_h, e.height))
        tab3_canvas.bind("<Configure>", _plug_canvas_configure)
        tab3_canvas.configure(yscrollcommand=tab3_scroll.set)
        tab3_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tab3_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        def _plug_wheel(e):
            b = tab3_canvas.bbox("all")
            if b and tab3_canvas.winfo_height() > 0 and (b[3] - b[1]) > tab3_canvas.winfo_height():
                tab3_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        tab3_canvas.bind("<MouseWheel>", _plug_wheel)
        self._theme_frames_bg.extend([tab3_canvas, tab3_inner])

        self._plugin_cards = []
        self._plugin_entries = []
        _t_plug = self._THEME_DARK if self._dark_theme else self._THEME_LIGHT
        for p in plugins_data:
            name = p.get("title", "")
            cmd = p.get("command", "")
            desc = p.get("description", "")
            card = tk.Frame(tab3_inner, bg=self._web_card_bg, padx=12, pady=10)
            card.pack(fill=tk.X, pady=6)
            self._plugin_cards.append(card)

            row1 = tk.Frame(card, bg=self._web_card_bg)
            row1.pack(fill=tk.X)
            ttk.Label(row1, text=name, style="Web.Card.TLabel", font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)
            cmd_entry = tk.Entry(
                row1,
                width=50,
                font=("Consolas", 9),
                bg=_t_plug["entry_bg"],
                fg=self._web_fg,
                insertbackground=self._web_fg,
                readonlybackground=_t_plug["entry_bg"],
                selectbackground=self._web_select_bg,
                selectforeground=self._web_fg,
                relief=tk.FLAT,
                highlightthickness=1,
                highlightcolor=self._web_border,
                highlightbackground=self._web_border,
            )
            cmd_entry.insert(0, cmd)
            cmd_entry.config(state="readonly")
            cmd_entry.pack(side=tk.LEFT, padx=(12, 8), ipady=4, ipadx=6)

            def _copy_cmd(c=cmd):
                self.root.clipboard_clear()
                self.root.clipboard_append(c)
                self.root.update()

            copy_btn = tk.Button(
                row1, text="复制", command=_copy_cmd,
                font=("Segoe UI", 9),
                bg=_t_plug["btn_bg"], fg=self._web_fg,
                activebackground=_t_plug["btn_active"], activeforeground=self._web_fg,
                relief=tk.FLAT, padx=8, pady=2, cursor="hand2",
            )
            copy_btn.pack(side=tk.LEFT)
            self._plugin_entries.append(cmd_entry)
            self._plugin_copy_btns = getattr(self, "_plugin_copy_btns", [])
            self._plugin_copy_btns.append(copy_btn)

            row2 = tk.Frame(card, bg=self._web_card_bg)
            row2.pack(fill=tk.X, pady=(4, 0))
            desc_lbl = ttk.Label(row2, text=desc, style="Web.Card.TLabel", foreground=self._web_fg_muted)
            desc_lbl.pack(anchor=tk.W)
            self._plugin_desc_labels = getattr(self, "_plugin_desc_labels", [])
            self._plugin_desc_labels.append(desc_lbl)

        # 切换到「获取包商店信息」页签时不把焦点给限制数量输入框
        self._notebook.bind("<<NotebookTabChanged>>", self._on_notebook_tab_changed)

        self._refresh()

        # 「明/暗」主题：固定在整个 UI 内容区右上角，不随页签切换移动
        self._theme_btn = tk.Label(
            self._main_frame,
            text="暗",
            font=("Segoe UI", 11),
            cursor="hand2",
            bg=self._web_bg,
            fg=self._web_fg,
        )
        self._theme_btn.place(relx=1, rely=0, x=-12, y=29, anchor=tk.NE)
        self._theme_btn.bind("<Button-1>", lambda e: self._toggle_theme())
        self._theme_btn.lift()

        self._apply_theme()  # 按当前时段（或默认）应用明/暗主题
        self.root.after(150, self._apply_entry_colors)  # Windows 下延迟重设输入框配色，避免被系统覆盖

    def _apply_entry_colors(self):
        """单独重设所有输入框的配色，供首次显示或主题切换后修复 Windows 下的渲染问题"""
        t = self._theme_colors()
        cfg = dict(bg=t["entry_bg"], fg=self._web_fg, insertbackground=self._web_fg,
                   selectbackground=self._web_select_bg, selectforeground=self._web_fg,
                   highlightcolor=self._web_border, highlightbackground=self._web_border)
        for name in ("_filter_entry", "_pub_entry"):
            w = getattr(self, name, None)
            if w and w.winfo_exists():
                w.config(**cfg)
        if getattr(self, "_limit_entry", None) and self._limit_entry.winfo_exists():
            self._limit_entry.config(**cfg)
        if getattr(self, "_single_fetch_entry", None) and self._single_fetch_entry.winfo_exists():
            self._single_fetch_entry.config(**cfg)
        for e in getattr(self, "_plugin_entries", []):
            if e and e.winfo_exists():
                e.config(**dict(cfg, readonlybackground=t["entry_bg"]))

    def _on_notebook_tab_changed(self, event=None):
        """切换到「获取包商店信息」或「插件」页签时，焦点移到 notebook，避免输入框默认获焦"""
        try:
            idx = self._notebook.index(self._notebook.select())
            if idx in (1, 2):  # 1=获取包商店信息, 2=插件
                self.root.after(0, self._notebook.focus_set)
        except (tk.TclError, ValueError):
            pass

    def _toggle_filter_panel(self):
        """切换筛选界面：打开则显示筛选面板，关闭则显示包详情"""
        if self._right_show_filter:
            self._show_detail_view()
            self.filter_btn.config(text="筛选")
        else:
            self._right_show_filter = True
            self.detail_container.pack_forget()
            self.filter_container.pack(fill=tk.BOTH, expand=True)
            self.filter_btn.config(text="关闭筛选")
            # 延迟设置 sash，使类型列固定较窄（约 220px），发行商列露出足够空间和滚动条
            self.root.after(80, self._set_filter_sash)

    def _update_back_to_doc_visibility(self):
        """链接已改为系统浏览器打开，不再显示「返回文档」按钮"""
        frame = getattr(self, "_back_to_doc_frame", None)
        if frame and frame.winfo_exists():
            frame.place_forget()

    def _back_to_document(self):
        """返回包详情文档页"""
        ddata = getattr(self, "_current_detail_data", None)
        if ddata and getattr(self, "_use_html", False):
            extra = getattr(self, "_current_detail_extra", "") or ""
            self._show_html_with_async_emoji(ddata, extra_notice=extra, dark=self._dark_theme)

    def _update_open_in_unity_visibility(self):
        """根据选中项类型显示：missing 显示「手动映射」；existing 显示「在Unity中打开」；manual-mapped 额外显示「取消映射」（在下方固定位置，红边）"""
        open_frame = getattr(self, "_open_in_unity_frame", None)
        manual_frame = getattr(self, "_manual_map_frame", None)
        open_btn = getattr(self, "_open_in_unity_btn", None)
        manual_btn = getattr(self, "_manual_map_btn", None)
        unmap_btn = getattr(self, "_unmap_btn", None)
        if not open_frame or not open_frame.winfo_exists():
            return
        sel = self.listbox.curselection()
        if not sel:
            open_frame.place_forget()
            if manual_frame and manual_frame.winfo_exists():
                manual_frame.place_forget()
            self._update_back_to_doc_visibility()
            return
        idx = sel[0]
        item = self.listbox_map.get(idx)
        if item is None:
            open_frame.place_forget()
            if manual_frame and manual_frame.winfo_exists():
                manual_frame.place_forget()
            self._update_back_to_doc_visibility()
            return
        manual = load_manual_mapping()
        if isinstance(item, dict):
            open_frame.place_forget()
            manual_frame.place(relx=1, rely=0, x=-32, y=36, anchor=tk.NE)
            unmap_btn.pack_forget()
            manual_btn.pack()
        else:
            open_frame.place(relx=1, rely=0, x=-32, y=36, anchor=tk.NE)
            open_frame.lift()
            filename = item.name if hasattr(item, "name") else str(item)
            pid = self.filename_to_pid.get(filename)
            pid_str = str(int(pid)) if isinstance(pid, (int, float)) else (str(pid) if pid is not None else "")
            is_manual = pid_str in manual and manual.get(pid_str) == filename
            if is_manual:
                manual_frame.place(relx=1, rely=0, x=-32, y=92, anchor=tk.NE)
                manual_frame.lift()
                manual_btn.pack_forget()
                unmap_btn.pack()
            else:
                manual_frame.place_forget()
        if manual_frame and manual_frame.winfo_exists():
            manual_frame.lift()
        self._update_back_to_doc_visibility()

    def _open_in_unity(self):
        """将当前选中的 .unitypackage 在 Unity 中打开导入（与 import_assets_to_unity.py 一致：先检查 Unity 是否运行，再 os.startfile）"""
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showinfo("在Unity中打开", "请先在左侧列表中选中一个已下载的资源。")
            return
        idx = sel[0]
        item = self.listbox_map.get(idx)
        if not item:
            messagebox.showinfo("在Unity中打开", "请先在左侧列表中选中一个已下载的资源。")
            return
        if isinstance(item, dict):
            messagebox.showinfo("在Unity中打开", "当前资源未下载，无法在 Unity 中打开。请先下载该资源。")
            return
        package_path = Path(item) if not isinstance(item, Path) else item
        if not package_path.exists():
            messagebox.showerror("在Unity中打开", f"文件不存在：\n{package_path}")
            return
        try:
            if sys.platform == "win32":
                r = subprocess.run(
                    ["tasklist", "/FI", "IMAGENAME eq Unity.exe"],
                    capture_output=True,
                    text=True,
                )
                if "Unity.exe" not in (r.stdout or ""):
                    messagebox.showwarning("在Unity中打开", "未检测到 Unity 进程。请先打开 Unity 项目后再点击「在Unity中打开」。")
                    return
            os.startfile(str(package_path))
        except Exception as e:
            messagebox.showerror("在Unity中打开", f"打开失败：{e}")

    def _fuzzy_match_files(self, display_name: str) -> list:
        """根据 displayName 模糊搜索下载目录中的 .unitypackage 文件"""
        words = set(re.findall(r"[a-zA-Z0-9\u4e00-\u9fff]+", display_name.lower()))
        words = {w for w in words if len(w) > 1}
        if not words:
            return list(self.download_dir.glob("*.unitypackage"))[:30]
        candidates = []
        for p in self.download_dir.glob("*.unitypackage"):
            stem = p.stem.lower()
            score = sum(1 for w in words if w in stem)
            if score > 0:
                candidates.append((score, p))
        candidates.sort(key=lambda x: -x[0])
        return [p for _, p in candidates[:20]]

    def _on_manual_map_click(self):
        """手动映射：模糊搜索候选文件，用户选择后写入映射"""
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showinfo("手动映射", "请先在左侧列表中选中一个缺失文件的资源。")
            return
        idx = sel[0]
        item = self.listbox_map.get(idx)
        if not item or not isinstance(item, dict):
            messagebox.showinfo("手动映射", "请选中标记为红色的缺失资源。")
            return
        display_name = item.get("displayName") or ""
        pid = item.get("packageId")
        if not pid:
            messagebox.showerror("手动映射", "无法获取 packageId。")
            return
        pid_str = str(int(pid)) if isinstance(pid, (int, float)) else str(pid)
        candidates = self._fuzzy_match_files(display_name)
        if not candidates:
            messagebox.showinfo("手动映射", "未在下载目录中找到匹配的文件。")
            return

        self.root.update_idletasks()
        frame = getattr(self, "_manual_map_frame", None)
        win = tk.Toplevel(self.root)
        win.title("选择对应文件")
        win.transient(self.root)
        win.grab_set()
        if frame and frame.winfo_exists():
            bx = frame.winfo_rootx()
            by = frame.winfo_rooty() + frame.winfo_height()
            fw = frame.winfo_width()
            wx, wy = 500, 300
            win_x = bx + fw - wx
            win_x = max(0, win_x)
            win.geometry(f"{wx}x{wy}+{win_x}+{by}")
        else:
            win.geometry("500x300")
        lb = tk.Listbox(win, font=("Segoe UI", 10), selectmode=tk.SINGLE)
        sb = ttk.Scrollbar(win, orient=tk.VERTICAL, command=lb.yview)
        for p in candidates:
            lb.insert(tk.END, p.name)
        lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        lb.config(yscrollcommand=sb.set)
        if candidates:
            lb.selection_set(0)

        def on_confirm():
            sel_idx = lb.curselection()
            if not sel_idx:
                return
            chosen = candidates[sel_idx[0]]
            chosen_name = chosen.name
            mapping = load_manual_mapping()
            mapping[pid_str] = chosen_name
            save_manual_mapping(mapping)
            win.destroy()
            self._refresh()
            self._update_open_in_unity_visibility()
            self._refresh_detail_after_mapping(chosen_name)

        def on_cancel():
            win.destroy()

        btn_frame = tk.Frame(win)
        btn_frame.pack(fill=tk.X, padx=8, pady=8)
        tk.Button(btn_frame, text="确认", command=on_confirm, font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=4)
        tk.Button(btn_frame, text="取消", command=on_cancel, font=("Segoe UI", 10)).pack(side=tk.LEFT)
        lb.bind("<Double-1>", lambda e: on_confirm())

    def _refresh_detail_after_mapping(self, chosen_filename: str = "", pid_str: str = ""):
        """映射/取消映射后刷新当前详情，可选重新选中指定文件或缺失项"""
        if chosen_filename:
            for idx, data in self.listbox_map.items():
                if hasattr(data, "name") and data.name == chosen_filename:
                    self.listbox.selection_clear(0, tk.END)
                    self.listbox.selection_set(idx)
                    self.listbox.see(idx)
                    break
        elif pid_str:
            for idx, data in self.listbox_map.items():
                if isinstance(data, dict):
                    p = data.get("packageId")
                    pstr = str(int(p)) if isinstance(p, (int, float)) else str(p) if p else ""
                    if pstr == pid_str:
                        self.listbox.selection_clear(0, tk.END)
                        self.listbox.selection_set(idx)
                        self.listbox.see(idx)
                        break
        sel = self.listbox.curselection()
        if sel:
            self._on_select(None)

    def _on_unmap_click(self):
        """取消手动映射"""
        sel = self.listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        item = self.listbox_map.get(idx)
        if not item or isinstance(item, dict):
            messagebox.showinfo("取消映射", "请选中已通过手动映射关联的资源。")
            return
        filename = item.name if hasattr(item, "name") else str(item)
        package_id = self.filename_to_pid.get(filename)
        if package_id is None:
            return
        pid_str = str(int(package_id)) if isinstance(package_id, (int, float)) else str(package_id)
        mapping = load_manual_mapping()
        if pid_str not in mapping:
            messagebox.showinfo("取消映射", "该资源非手动映射，无需取消。")
            return
        del mapping[pid_str]
        save_manual_mapping(mapping)
        self._refresh()
        self._update_open_in_unity_visibility()
        self._refresh_detail_after_mapping(pid_str=pid_str)

    def _show_detail_view(self):
        """显示包详情视图（选中列表项或点击关闭筛选时调用）"""
        self._right_show_filter = False
        self.filter_container.pack_forget()
        self.detail_container.pack(fill=tk.BOTH, expand=True)
        self.filter_btn.config(text="筛选")

    def _set_main_sash_once(self):
        """窗口显示后把资源列表设为约 35%，窗口未就绪时自动重试"""
        if getattr(self, "_main_sash_set", True):
            return
        try:
            pw = getattr(self, "_main_paned", None)
            if not pw or not pw.winfo_exists():
                return
            w = pw.winfo_width()
            if w > 200:
                pw.sashpos(0, int(w * 0.35))
                self._main_sash_set = True
            else:
                self.root.after(200, self._set_main_sash_once)
        except Exception:
            pass

    def _set_filter_sash(self):
        """收窄发行商列所占宽度（类型约 300px），使发行商列整列含滚动条能露出"""
        try:
            pw = getattr(self, "_filter_two_col", None)
            if pw and pw.winfo_exists():
                pw.sashpos(0, 300)
        except Exception:
            pass

    def _theme_colors(self):
        """当前主题色字典"""
        return self._THEME_DARK if self._dark_theme else self._THEME_LIGHT

    def _toggle_theme(self):
        """切换浅色/深色主题"""
        self._dark_theme = not self._dark_theme
        self._apply_theme()
        self.root.after(100, self._redraw_detail_html)  # 延迟重绘网页区，确保 ttk 样式更新完成后再刷新
        self.root.after(150, self._apply_entry_colors)

    def _apply_theme(self):
        """应用当前主题到所有相关控件"""
        t = self._theme_colors()
        self._web_bg = t["bg"]
        self._web_fg = t["fg"]
        self._web_fg_muted = t["fg_muted"]
        self._web_card_bg = t["card"]
        self._web_select_bg = t["select_bg"]
        self._web_border = t["border"]
        try:
            s = ttk.Style()
            # 不在每次切换时 theme_use，避免 ttk 全局刷新导致 HtmlFrame 内容被重置
            s.configure("Web.TFrame", background=self._web_bg)
            s.configure("Web.TLabel", background=self._web_bg, foreground=self._web_fg, font=("Segoe UI", 9))
            s.configure("Web.TButton", background=t["btn_bg"], foreground=self._web_fg, padding=(10, 4))
            s.map("Web.TButton", background=[("active", t["btn_active"]), ("pressed", "#505050" if self._dark_theme else "#bdbdbd")])
            s.configure("Web.TCheckbutton", background=self._web_bg, foreground=self._web_fg)
            s.configure("Web.Card.TLabel", background=self._web_card_bg, foreground=self._web_fg, font=("Segoe UI", 9))
            s.configure("Web.Card.TCheckbutton", background=self._web_card_bg, foreground=self._web_fg)
            s.configure("Web.Detail.TFrame", background=self._web_card_bg)
            sb_thumb, sb_trough = (t["btn_bg"], t["card"]) if self._dark_theme else ("#b0b0b0", "#e8e8e8")
            s.configure("Vertical.TScrollbar", troughrelief="flat", background=sb_thumb, troughcolor=sb_trough)
        except Exception:
            pass
        for w in getattr(self, "_theme_frames_bg", []):
            if w.winfo_exists():
                w.config(bg=self._web_bg)
        if getattr(self, "listbox", None) and self.listbox.winfo_exists():
            self.listbox.config(bg=self._web_card_bg, fg=self._web_fg, selectbackground=self._web_select_bg, selectforeground=self._web_fg)
        for btn in (getattr(self, "sort_btn", None), getattr(self, "filter_btn", None), getattr(self, "_refresh_btn", None)):
            if btn and btn.winfo_exists():
                btn.config(bg=t["btn_bg"], fg=self._web_fg, activebackground=t["btn_active"], activeforeground=self._web_fg)
        # 整个 UI 右上角固定的「明/暗」：暗=当前深色（点一下切回浅色）；明=当前浅色（点一下切深色）
        theme_text = "明" if self._dark_theme else "暗"
        theme_fg = "#ffffff" if self._dark_theme else "#212121"
        if getattr(self, "_theme_btn", None) and self._theme_btn.winfo_exists():
            self._theme_btn.config(bg=self._web_bg, fg=theme_fg, text=theme_text)
        if getattr(self, "_open_in_unity_frame", None) and self._open_in_unity_frame.winfo_exists():
            if self._open_in_unity_btn and self._open_in_unity_btn.winfo_exists():
                self._open_in_unity_btn.config(bg=t["btn_bg"], fg=self._web_fg, activebackground=t["btn_active"], activeforeground=self._web_fg)
        if getattr(self, "_manual_map_frame", None) and self._manual_map_frame.winfo_exists():
            for btn in (self._manual_map_btn, self._unmap_btn):
                if btn and btn.winfo_exists():
                    btn.config(bg=t["btn_bg"], fg=self._web_fg, activebackground=t["btn_active"], activeforeground=self._web_fg)
        if getattr(self, "_back_to_doc_frame", None) and self._back_to_doc_frame.winfo_exists():
            self._back_to_doc_btn.config(bg=t["btn_bg"], fg=self._web_fg, activebackground=t["btn_active"], activeforeground=self._web_fg)
        if getattr(self, "_filter_clear_btn", None) and self._filter_clear_btn.winfo_exists():
            self._filter_clear_btn.config(bg=t["btn_bg"], fg=self._web_fg, activebackground=t["btn_active"], activeforeground=self._web_fg)
        for name in ("_filter_search_row", "_filter_type_frame", "_filter_type_canvas", "_filter_pub_frame", "_filter_pub_canvas", "_filter_pub_inner"):
            w = getattr(self, name, None)
            if w and w.winfo_exists():
                w.config(bg=self._web_bg)
        if getattr(self, "_filter_type_inner", None) and self._filter_type_inner.winfo_exists():
            self._update_type_tree_theme(self._filter_type_inner)
        if getattr(self, "_filter_pub_lf", None) and self._filter_pub_lf.winfo_exists():
            self._filter_pub_lf.config(bg=self._web_card_bg)
        _ent_cfg = dict(bg=t["entry_bg"], fg=self._web_fg, insertbackground=self._web_fg, selectbackground=self._web_select_bg, selectforeground=self._web_fg, highlightcolor=self._web_border, highlightbackground=self._web_border)
        for name in ("_filter_entry", "_pub_entry"):
            w = getattr(self, name, None)
            if w and w.winfo_exists():
                w.config(**_ent_cfg)
        # 获取包商店信息页签：输入框、按钮、日志区随主题
        if getattr(self, "_limit_entry", None) and self._limit_entry.winfo_exists():
            self._limit_entry.config(**_ent_cfg)
        if getattr(self, "_single_fetch_entry", None) and self._single_fetch_entry.winfo_exists():
            self._single_fetch_entry.config(**_ent_cfg)
        if getattr(self, "_single_fetch_btn", None) and self._single_fetch_btn.winfo_exists():
            self._single_fetch_btn.config(bg=t["btn_bg"], fg=self._web_fg, activebackground=t["btn_active"], activeforeground=self._web_fg)
        if getattr(self, "fetch_btn", None) and self.fetch_btn.winfo_exists():
            self.fetch_btn.config(bg=t["btn_bg"], fg=self._web_fg, activebackground=t["btn_active"], activeforeground=self._web_fg)
        if getattr(self, "_fetch_stop_btn", None) and self._fetch_stop_btn.winfo_exists():
            self._fetch_stop_btn.config(bg=t["btn_bg"], fg=self._web_fg, activebackground=t["btn_active"], activeforeground=self._web_fg)
        if getattr(self, "fetch_log", None) and self.fetch_log.winfo_exists():
            self.fetch_log.config(bg=self._web_card_bg, fg=self._web_fg)
        for card in getattr(self, "_plugin_cards", []):
            if card.winfo_exists():
                card.config(bg=self._web_card_bg)
                for child in card.winfo_children():
                    if child.winfo_exists():
                        child.config(bg=self._web_card_bg)
        for e in getattr(self, "_plugin_entries", []):
            if e and e.winfo_exists():
                cfg = dict(_ent_cfg, readonlybackground=t["entry_bg"])
                e.config(**cfg)
        for btn in getattr(self, "_plugin_copy_btns", []):
            if btn and btn.winfo_exists():
                btn.config(bg=t["btn_bg"], fg=self._web_fg, activebackground=t["btn_active"], activeforeground=self._web_fg)
        for lbl in getattr(self, "_plugin_desc_labels", []):
            if lbl and lbl.winfo_exists():
                lbl.config(foreground=self._web_fg_muted)
        if getattr(self, "detail_widget", None) and self.detail_widget.winfo_exists():
            if getattr(self, "_use_html", True):
                try:
                    ttk.Style().configure("Web.Detail.TFrame", background=self._web_card_bg)
                except Exception:
                    pass
            else:
                self.detail_widget.config(bg=self._web_card_bg, fg=self._web_fg)
        self._redraw_detail_html()

    def _redraw_detail_html(self):
        """按当前主题重绘详情区 HTML，供主题切换或筛选关闭时调用"""
        if not getattr(self, "_use_html", True) or not getattr(self, "detail_widget", None) or not self.detail_widget.winfo_exists():
            return
        dtype = getattr(self, "_current_detail_type", None)
        ddata = getattr(self, "_current_detail_data", None)
        extra = getattr(self, "_current_detail_extra", "") or ""
        dark = self._dark_theme
        if dtype == "package" and isinstance(ddata, dict):
            self._show_html_with_async_emoji(ddata, extra_notice=extra, dark=dark)
        elif dtype == "plain" and ddata:
            if dark:
                simple_style = """
    html, body { font-family: Segoe UI, sans-serif; background: #1a1a1a; color: #ffffff; margin: 0; padding: 0; font-size: 14px; }
    .wrap { background: #1a1a1a; color: #ffffff; padding: 12px; min-height: 100%; }
    """
                wrap, wrap_end = '<div class="wrap">', '</div>'
            else:
                simple_style = """
    html, body { font-family: Segoe UI, sans-serif; background: #fff; color: #222; margin: 0; padding: 12px; font-size: 14px; }
    """
                wrap, wrap_end = "", ""
            plain_html = f'<html><head><meta charset="utf-8"><style>{simple_style}</style></head><body>{wrap}<pre style="margin:0;white-space:pre-wrap;">{html.escape(str(ddata))}</pre>{wrap_end}</body></html>'
            self.detail_widget.load_html(plain_html)
        elif dtype == "summary" and ddata:
            if dark:
                summary_style = """
    html, body { font-family: Segoe UI, sans-serif; background: #1a1a1a; color: #ffffff; margin: 0; padding: 0; font-size: 14px; }
    .wrap { background: #1a1a1a; color: #ffffff; padding: 12px; min-height: 100%; }
    """
                html_msg = f'<html><head><meta charset="utf-8"><style>{summary_style}</style></head><body><div class="wrap"><p style="margin:0;font-weight:bold;">{html.escape(str(ddata))}</p></div></body></html>'
            else:
                summary_style = """
    html, body { font-family: Segoe UI, sans-serif; background: #fff; color: #212121; margin: 0; padding: 12px; font-size: 14px; }
    """
                html_msg = f'<html><head><meta charset="utf-8"><style>{summary_style}</style></head><body><p style="margin:0;font-weight:bold;">{html.escape(str(ddata))}</p></body></html>'
            self.detail_widget.load_html(html_msg)

    def _build_filter_panel(self):
        """构建 Unity 风格的筛选面板。
        逻辑与数据说明：
        - 搜索我的资源：有逻辑，筛选面板打开时过滤左侧列表（按文件名/显示名）。
        - 清除筛选器：有逻辑，清空所有勾选与搜索框并刷新列表。
        - 类型：有数据（metadata detail.category）且有逻辑，勾选后按分类过滤列表；无 metadata 时显示默认类型名但无筛选效果。
        - 发行商：有数据（metadata detail.productPublisher）且有逻辑，勾选后按发行商过滤列表；无 metadata 时仅显示提示。
        """
        bg, fg, card, border = self._web_bg, self._web_fg, self._web_card_bg, self._web_border
        # 搜索行：左侧为「搜索我的资源」+ 输入框，右侧为「清除筛选器」贴边
        search_row = tk.Frame(self.filter_container, bg=bg)
        search_row.pack(fill=tk.X, pady=(0, 10))
        self._filter_search_row = search_row
        ttk.Label(search_row, text="搜索我的资源", style="Web.TLabel").pack(side=tk.LEFT, padx=(0, 6))
        self._filter_search_var = tk.StringVar()
        self._filter_search_var.trace_add("write", lambda *_: self._apply_filter_to_list())
        t_f = self._theme_colors()
        self._filter_entry = tk.Entry(
            search_row, textvariable=self._filter_search_var, width=28,
            bg=t_f["entry_bg"], fg=fg, insertbackground=fg,
            selectbackground=self._web_select_bg, selectforeground=fg,
            relief=tk.FLAT, highlightthickness=1,
            highlightcolor=border, highlightbackground=border, font=("Segoe UI", 10),
        )
        self._filter_entry.pack(side=tk.LEFT, padx=(0, 8), ipady=4, ipadx=6)
        self._filter_clear_btn = tk.Button(
            search_row,
            text="清除筛选器",
            command=self._filter_clear,
            font=("Segoe UI", 10),
            bg=self._THEME_LIGHT["btn_bg"],
            fg=fg,
            activebackground=self._THEME_LIGHT["btn_active"],
            activeforeground=fg,
            relief=tk.FLAT,
            padx=10,
            pady=4,
            cursor="hand2",
        )
        self._filter_clear_btn.pack(side=tk.RIGHT)

        # 类型 与 发行商 并排，各自独立上下滑动（无 sash 分割线）
        try:
            _ps = ttk.Style()
            _ps.configure("TPanedwindow", sashwidth=0)
        except Exception:
            pass
        two_col = ttk.PanedWindow(self.filter_container, orient=tk.HORIZONTAL)
        two_col.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        self._filter_two_col = two_col  # 用于打开筛选时固定类型列宽度
        # 窗口拉大时保持类型列 300px，多出的宽度都给发行商列，避免滚动条被挡住
        def _keep_filter_sash(e):
            try:
                if e.widget.winfo_width() > 320:
                    e.widget.sashpos(0, 300)
            except Exception:
                pass
        two_col.bind("<Configure>", _keep_filter_sash)

        # 左列：类型（固定较窄宽度）；右列发行商占剩余空间
        type_frame = tk.Frame(two_col, bg=bg)
        two_col.add(type_frame, weight=2)
        self._filter_type_frame = type_frame
        type_canvas = tk.Canvas(type_frame, highlightthickness=0, bg=bg)
        type_scroll = ttk.Scrollbar(type_frame, command=type_canvas.yview)
        type_canvas.configure(yscrollcommand=type_scroll.set)
        type_inner = tk.Frame(type_frame, bg=card, padx=10, pady=10)
        self._filter_type_canvas = type_canvas
        self._filter_type_inner = type_inner
        ttk.Label(type_inner, text="类型", style="Web.Card.TLabel", font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, pady=(0, 6))
        type_win_id = type_canvas.create_window((0, 0), window=type_inner, anchor=tk.NW)
        def _type_on_configure(e):
            b = type_canvas.bbox("all")
            type_canvas.configure(scrollregion=type_canvas.bbox("all"))
            w = type_canvas.winfo_width()
            ch = type_canvas.winfo_height()
            content_h = (b[3] - b[1]) if b else 0
            if w > 1:
                type_canvas.itemconfig(type_win_id, width=w)
            type_canvas.itemconfig(type_win_id, height=max(content_h, ch) if ch > 0 else content_h)
        type_inner.bind("<Configure>", _type_on_configure)
        def _type_canvas_configure(e):
            if e.width > 1:
                type_canvas.itemconfig(type_win_id, width=e.width)
            if e.height > 1:
                b = type_canvas.bbox("all")
                content_h = (b[3] - b[1]) if b else e.height
                type_canvas.itemconfig(type_win_id, height=max(content_h, e.height))
        type_canvas.bind("<Configure>", _type_canvas_configure)
        type_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        type_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        def _type_wheel(e):
            b = type_canvas.bbox("all")
            if b and type_canvas.winfo_height() > 0 and (b[3] - b[1]) > type_canvas.winfo_height():
                type_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        type_canvas.bind("<MouseWheel>", _type_wheel)

        self._filter_type_vars = {}
        self._filter_type_group_frames = {}
        self._filter_type_children = {}
        self._filter_type_checkbuttons = {}
        type_counts = self._collect_category_counts()
        if type_counts:
            self._build_type_tree(type_inner, type_counts)
        else:
            ttk.Label(type_inner, text="(暂无类型数据，请先获取包商店信息)", style="Web.Card.TLabel", foreground=self._web_fg_muted).pack(anchor=tk.W)

        # 右列：发行商（独立滚动，weight 更大让发行商区域露出更多）
        pub_frame = tk.Frame(two_col, bg=bg)
        two_col.add(pub_frame, weight=4)
        self._filter_pub_frame = pub_frame
        pub_canvas = tk.Canvas(pub_frame, highlightthickness=0, bg=bg)
        pub_scroll = ttk.Scrollbar(pub_frame, command=pub_canvas.yview)
        pub_canvas.configure(yscrollcommand=pub_scroll.set)
        pub_inner = tk.Frame(pub_canvas, bg=bg)
        self._filter_pub_canvas = pub_canvas
        self._filter_pub_inner = pub_inner
        pub_win_id = pub_canvas.create_window((0, 0), window=pub_inner, anchor=tk.NW)
        def _pub_on_configure(e):
            b = pub_canvas.bbox("all")
            pub_canvas.configure(scrollregion=pub_canvas.bbox("all"))
            w = pub_canvas.winfo_width()
            ch = pub_canvas.winfo_height()
            content_h = (b[3] - b[1]) if b else 0
            if w > 1:
                pub_canvas.itemconfig(pub_win_id, width=w)
            pub_canvas.itemconfig(pub_win_id, height=max(content_h, ch) if ch > 0 else content_h)
        pub_inner.bind("<Configure>", _pub_on_configure)
        def _pub_canvas_configure(e):
            if e.width > 1:
                pub_canvas.itemconfig(pub_win_id, width=e.width)
            if e.height > 1:
                b = pub_canvas.bbox("all")
                content_h = (b[3] - b[1]) if b else e.height
                pub_canvas.itemconfig(pub_win_id, height=max(content_h, e.height))
        pub_canvas.bind("<Configure>", _pub_canvas_configure)
        pub_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        pub_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        def _pub_wheel(e):
            b = pub_canvas.bbox("all")
            if b and pub_canvas.winfo_height() > 0 and (b[3] - b[1]) > pub_canvas.winfo_height():
                pub_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        pub_canvas.bind("<MouseWheel>", _pub_wheel)

        pub_lf = tk.Frame(pub_inner, bg=card, padx=10, pady=10)
        self._filter_pub_lf = pub_lf
        pub_lf.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        ttk.Label(pub_lf, text="发行商", style="Web.Card.TLabel", font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, pady=(0, 6))
        self._filter_pub_search = tk.StringVar()
        ttk.Label(pub_lf, text="搜索发行商", style="Web.Card.TLabel").pack(anchor=tk.W)
        self._pub_entry = tk.Entry(
            pub_lf, textvariable=self._filter_pub_search, width=26,
            bg=t_f["entry_bg"], fg=fg, insertbackground=fg,
            selectbackground=self._web_select_bg, selectforeground=fg,
            relief=tk.FLAT, highlightthickness=1,
            highlightcolor=border, highlightbackground=border, font=("Segoe UI", 10),
        )
        self._pub_entry.pack(fill=tk.X, pady=(4, 8), ipady=4, ipadx=6)
        self._filter_pub_vars = {}
        pub_counts = self._collect_publisher_counts()
        for pub_name, count in sorted(pub_counts.items(), key=lambda x: -x[1])[:20]:
            self._filter_pub_vars[pub_name] = tk.BooleanVar(value=False)
            ttk.Checkbutton(
                pub_lf,
                text=f"{pub_name} ({count})",
                variable=self._filter_pub_vars[pub_name],
                command=self._apply_filter_to_list,
                style="Web.Card.TCheckbutton",
            ).pack(anchor=tk.W)
        if not pub_counts:
            ttk.Label(pub_lf, text="(暂无发行商数据，请先获取包商店信息)", style="Web.Card.TLabel", foreground=self._web_fg_muted).pack(anchor=tk.W)

    def _collect_category_counts(self):
        """从 metadata 目录下的 json 汇总分类（类型）及数量"""
        counts = {}
        if not METADATA_DIR.exists():
            return counts
        for path in METADATA_DIR.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                detail = data.get("detail") or {}
                cat = detail.get("category") or {}
                name = cat.get("name") if isinstance(cat, dict) else None
                if name:
                    counts[name] = counts.get(name, 0) + 1
            except Exception:
                pass
        return counts

    def _set_type_checkbutton_state(self, name: str, checked: bool = False, partial: bool = False):
        """同步设置类型筛选复选框的显示状态。partial=True 时显示半勾选。"""
        var = self._filter_type_vars.get(name)
        if var is not None:
            var.set(bool(checked))
        cb = getattr(self, "_filter_type_checkbuttons", {}).get(name)
        if cb and cb.winfo_exists():
            if checked:
                cb.state(["selected", "!alternate"])
            elif partial:
                cb.state(["!selected", "alternate"])
            else:
                cb.state(["!selected", "!alternate"])

    def _refresh_type_parent_state(self, parent_name: str):
        """根据子级勾选情况回推父级：未选 / 半选 / 全选。"""
        children = getattr(self, "_filter_type_children", {}).get(parent_name, [])
        if not children:
            return
        selected_count = sum(1 for child in children if self._filter_type_vars.get(child) and self._filter_type_vars[child].get())
        if selected_count <= 0:
            self._set_type_checkbutton_state(parent_name, checked=False, partial=False)
        elif selected_count == len(children):
            self._set_type_checkbutton_state(parent_name, checked=True, partial=False)
        else:
            self._set_type_checkbutton_state(parent_name, checked=False, partial=True)

    def _on_type_parent_toggle(self, parent_name: str):
        """点击父级时，同步全选/取消该父级下的所有子级。"""
        target = bool(self._filter_type_vars[parent_name].get())
        for child_name in self._filter_type_children.get(parent_name, []):
            self._set_type_checkbutton_state(child_name, checked=target, partial=False)
        self._refresh_type_parent_state(parent_name)
        self._apply_filter_to_list()

    def _on_type_child_toggle(self, parent_name: str):
        """点击子级时，更新父级为未选、半选或全选。"""
        self._refresh_type_parent_state(parent_name)
        self._apply_filter_to_list()

    def _build_type_tree(self, parent, type_counts: dict):
        """将扁平的分类计数构建为可折叠的树形 UI（仿 Unity Asset Store 官网筛选样式）"""
        bg = parent.cget("bg")
        fg = self._web_fg
        fg_count = self._web_fg_muted
        tree = {}
        for full_name, count in type_counts.items():
            parts = full_name.split("/")
            top = parts[0]
            if top not in tree:
                tree[top] = {"_total": 0, "_self": 0, "_children": {}}
            tree[top]["_total"] += count
            if len(parts) == 1:
                tree[top]["_self"] = count
            else:
                tree[top]["_children"][full_name] = count

        for top_name in sorted(tree.keys(), key=lambda k: -tree[k]["_total"]):
            node = tree[top_name]
            children = node["_children"]

            if not children:
                cnt = node["_self"]
                self._filter_type_vars[top_name] = tk.BooleanVar(value=False)
                row = tk.Frame(parent, bg=bg)
                row.pack(anchor=tk.W, fill=tk.X, pady=1)
                cb = ttk.Checkbutton(
                    row, text=f"{top_name}",
                    variable=self._filter_type_vars[top_name],
                    command=self._apply_filter_to_list,
                    style="Web.Card.TCheckbutton",
                )
                cb.pack(side=tk.LEFT)
                self._filter_type_checkbuttons[top_name] = cb
                tk.Label(row, text=f"({cnt})", font=("Segoe UI", 9),
                         bg=bg, fg=fg_count).pack(side=tk.LEFT, padx=(2, 0))
                continue

            header = tk.Frame(parent, bg=bg)
            header.pack(anchor=tk.W, fill=tk.X, pady=(6, 1))
            expanded = tk.BooleanVar(value=False)

            self._filter_type_vars[top_name] = tk.BooleanVar(value=False)
            cb = ttk.Checkbutton(
                header, text=f"{top_name}",
                variable=self._filter_type_vars[top_name],
                command=lambda name=top_name: self._on_type_parent_toggle(name),
                style="Web.Card.TCheckbutton",
            )
            cb.pack(side=tk.LEFT)
            self._filter_type_checkbuttons[top_name] = cb
            tk.Label(header, text=f"({node['_total']})", font=("Segoe UI", 9),
                     bg=bg, fg=fg_count).pack(side=tk.LEFT, padx=(2, 0))

            arrow_lbl = tk.Label(header, text="∨", font=("Segoe UI", 10),
                                 bg=bg, fg=fg_count, cursor="hand2")
            arrow_lbl.pack(side=tk.RIGHT, padx=(0, 4))

            child_frame = tk.Frame(parent, bg=bg)
            self._filter_type_group_frames[top_name] = (arrow_lbl, child_frame, expanded)
            self._filter_type_children[top_name] = []

            top_depth = top_name.count("/") + 1
            for full_name in sorted(children.keys()):
                cnt = children[full_name]
                display = full_name.split("/")[-1]
                depth = full_name.count("/") - top_depth + 1
                indent = max(depth, 1) * 20
                self._filter_type_vars[full_name] = tk.BooleanVar(value=False)
                self._filter_type_children[top_name].append(full_name)
                row = tk.Frame(child_frame, bg=bg)
                row.pack(anchor=tk.W, fill=tk.X, pady=1)
                tk.Frame(row, width=indent, bg=bg).pack(side=tk.LEFT)
                cb = ttk.Checkbutton(
                    row, text=f"{display}",
                    variable=self._filter_type_vars[full_name],
                    command=lambda name=top_name: self._on_type_child_toggle(name),
                    style="Web.Card.TCheckbutton",
                )
                cb.pack(side=tk.LEFT)
                self._filter_type_checkbuttons[full_name] = cb
                tk.Label(row, text=f"({cnt})", font=("Segoe UI", 9),
                         bg=bg, fg=fg_count).pack(side=tk.LEFT, padx=(2, 0))

            def _toggle(arrow=arrow_lbl, frame=child_frame, var=expanded, after_w=header):
                if var.get():
                    frame.pack_forget()
                    arrow.config(text="∨")
                    var.set(False)
                else:
                    frame.pack(anchor=tk.W, fill=tk.X, after=after_w)
                    arrow.config(text="∧")
                    var.set(True)

            arrow_lbl.bind("<Button-1>", lambda e, t=_toggle: t())

    def _update_type_tree_theme(self, container):
        """递归更新类型筛选树的所有控件配色"""
        card = self._web_card_bg
        fg_m = self._web_fg_muted
        container.config(bg=card)
        for w in container.winfo_children():
            if not w.winfo_exists():
                continue
            if isinstance(w, tk.Frame):
                w.config(bg=card)
                self._update_type_tree_theme(w)
            elif isinstance(w, tk.Label):
                is_count = "(" in (w.cget("text") or "")
                is_arrow = w.cget("text") in ("∨", "∧")
                w.config(bg=card, fg=fg_m if (is_count or is_arrow) else self._web_fg)

    def _collect_publisher_counts(self):
        """从 metadata 目录下的 json 汇总发行商及数量"""
        counts = {}
        if not METADATA_DIR.exists():
            return counts
        for path in METADATA_DIR.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                detail = data.get("detail") or {}
                pub = detail.get("productPublisher") or {}
                name = pub.get("name") if isinstance(pub, dict) else None
                if name:
                    counts[name] = counts.get(name, 0) + 1
            except Exception:
                pass
        return counts

    def _filter_clear(self):
        """清除筛选器：取消所有勾选并清空搜索"""
        self._filter_search_var.set("")
        for name in self._filter_type_vars.keys():
            self._set_type_checkbutton_state(name, checked=False, partial=False)
        for v in getattr(self, "_filter_pub_vars", {}).values():
            v.set(False)
        self._filter_pub_search.set("")
        self._apply_filter_to_list()

    def _apply_filter_to_list(self):
        """根据筛选条件过滤左侧列表。已实现：搜索我的资源、类型、发行商。"""
        self._filter_list()

    def _set_detail_content(self, html_content: str = None, plain_text: str = None, _detail_type: str = None, _detail_data=None, _detail_extra: str = ""):
        """设置右侧详情内容。有 html_content 时用网页；否则用纯文本。_detail_type/_detail_data/_detail_extra 用于主题切换时重绘。"""
        dark = self._dark_theme
        if self._use_html:
            if html_content is not None:
                self.detail_widget.load_html(html_content)
                self._current_detail_type = _detail_type or "html"
                self._current_detail_data = _detail_data
                self._current_detail_extra = _detail_extra or ""
                self._current_summary_msg = None
            elif plain_text is not None:
                if dark:
                    simple_style = """
    html, body { font-family: Segoe UI, sans-serif; background: #1a1a1a; color: #ffffff; margin: 0; padding: 0; font-size: 14px; }
    .wrap { background: #1a1a1a; color: #ffffff; padding: 12px; min-height: 100%; }
    """
                    wrap = '<div class="wrap">'
                    wrap_end = '</div>'
                else:
                    simple_style = """
    html, body { font-family: Segoe UI, sans-serif; background: #fff; color: #222; margin: 0; padding: 12px; font-size: 14px; }
    """
                    wrap, wrap_end = "", ""
                simple_html = f'<html><head><meta charset="utf-8"><style>{simple_style}</style></head><body>{wrap}<pre style="margin:0;white-space:pre-wrap;">{html.escape(plain_text)}</pre>{wrap_end}</body></html>'
                self.detail_widget.load_html(simple_html)
                self._current_detail_type = "plain"
                self._current_detail_data = plain_text
                self._current_detail_extra = ""
                self._current_summary_msg = None
        else:
            self.detail_widget.config(state=tk.NORMAL)
            self.detail_widget.delete(1.0, tk.END)
            self.detail_widget.insert(tk.END, plain_text or html_content or "")
            self.detail_widget.config(state=tk.DISABLED)
            if hasattr(self, "_web_bg"):
                self.detail_widget.config(bg=self._web_card_bg, fg=self._web_fg)
            self._current_detail_type = _detail_type or "plain"
            self._current_detail_data = _detail_data
            self._current_summary_msg = None
        self._update_open_in_unity_visibility()

    def _toggle_sort(self):
        self.sort_by_snapshot = not self.sort_by_snapshot
        self.sort_btn.config(text="按购买顺序" if self.sort_by_snapshot else "按字母排序")
        self._filter_list()

    def _refresh(self):
        self.package_files = []
        self.missing_items = []
        self.filename_to_display_name = {}  # 文件名 -> 资源显示名（来自 purchases displayName）
        if not self.download_dir.exists():
            self.purchase_order = {}
            for item in self.purchases:
                display_name = str(item.get("displayName") or "")
                pid = item.get("packageId")
                grant_time = str(item.get("grantTime") or "9999-99-99")
                if display_name and pid:
                    fn = sanitize_filename(display_name) + ".unitypackage"
                    self.filename_to_display_name[fn] = display_name
                    self.purchase_order[fn] = grant_time
                    self.missing_items.append({
                        "filename": fn,
                        "displayName": display_name,
                        "packageId": pid,
                        "grantTime": grant_time,
                    })
            self._filter_list()
            msg = f"目录不存在: {self.download_dir}"
            self._set_detail_content(plain_text=msg)
            if getattr(self, "_file_count_label", None) and self._file_count_label.winfo_exists():
                self._file_count_label.config(text="（目录下共有文件 0 个，0|0|0）")
            self._update_open_in_unity_visibility()
            return

        existing_files = list(self.download_dir.glob("*.unitypackage"))  # 仅 .unitypackage，不含 .part 等
        existing_names = {p.name for p in existing_files}
        self.package_files = list(existing_files)
        manual = load_manual_mapping()

        self.purchase_order = {}
        purchased_downloaded = 0  # 购买列表中、对应文件已存在的数量
        count_raw = 0  # 完全相等
        count_sanitize = 0  # sanitize 匹配
        count_manual = 0  # 手动映射
        for item in self.purchases:
            display_name = str(item.get("displayName") or "")
            pid = item.get("packageId")
            grant_time = str(item.get("grantTime") or "9999-99-99")
            if display_name:
                fn = sanitize_filename(display_name) + ".unitypackage"
                self.filename_to_display_name[fn] = display_name
                self.purchase_order[fn] = grant_time
            if not display_name or not pid:
                continue
            pid_str = str(int(pid)) if isinstance(pid, (int, float)) else str(pid)
            # 1) 优先：完全相等（displayName 直接 + .unitypackage，无 sanitize），可能存在此类文件
            filename_raw = display_name + ".unitypackage"
            if filename_raw in existing_names:
                purchased_downloaded += 1
                count_raw += 1
                self.filename_to_pid[filename_raw] = int(pid) if isinstance(pid, (int, float)) else pid
                self.filename_to_display_name[filename_raw] = display_name
                self.purchase_order[filename_raw] = grant_time
                continue
            # 2) 其次：按 unity_assets_downloader 起名规则（sanitize）匹配
            filename = sanitize_filename(display_name) + ".unitypackage"
            if filename in existing_names:
                purchased_downloaded += 1
                count_sanitize += 1
                continue
            # 3) 再次：检查手动映射
            manual_fn = manual.get(pid_str)
            if manual_fn and manual_fn in existing_names:
                purchased_downloaded += 1
                count_manual += 1
                self.filename_to_pid[manual_fn] = int(pid) if isinstance(pid, (int, float)) else pid
                self.filename_to_display_name[manual_fn] = display_name
                self.purchase_order[manual_fn] = grant_time
                continue
            # 4) 以上均不匹配，视为未下载，需手动映射
            self.missing_items.append({
                "filename": filename,
                "displayName": display_name,
                "packageId": pid,
                "grantTime": grant_time,
            })

        if getattr(self, "_file_count_label", None) and self._file_count_label.winfo_exists():
            self._file_count_label.config(text=f"（目录下共有文件 {len(existing_files)} 个，{count_raw}|{count_sanitize}|{count_manual}）")
        self._filter_list()
        msg = f"共 {purchased_downloaded} 个已下载，{len(self.missing_items)} 个未下载（红色），合计 {purchased_downloaded + len(self.missing_items)} 个资源"
        self._current_summary_msg = msg
        self._current_detail_type = "summary"
        if self._use_html:
            if self._dark_theme:
                summary_style = """
    html, body { font-family: Segoe UI, sans-serif; background: #1a1a1a; color: #ffffff; margin: 0; padding: 0; font-size: 14px; }
    .wrap { background: #1a1a1a; color: #ffffff; padding: 12px; min-height: 100%; }
    """
                html_msg = (
                    f'<html><head><meta charset="utf-8"><style>{summary_style}</style></head><body><div class="wrap">'
                    f'<p style="margin:0;font-weight:bold;">{html.escape(msg)}</p></div></body></html>'
                )
            else:
                summary_style = """
    html, body { font-family: Segoe UI, sans-serif; background: #fff; color: #212121; margin: 0; padding: 12px; font-size: 14px; }
    """
                html_msg = (
                    f'<html><head><meta charset="utf-8"><style>{summary_style}</style></head><body>'
                    f'<p style="margin:0;font-weight:bold;">{html.escape(msg)}</p></body></html>'
                )
            self._set_detail_content(html_content=html_msg, _detail_type="summary", _detail_data=msg)
        else:
            self._set_detail_content(plain_text=msg)
        self._update_open_in_unity_visibility()

    def _filter_list(self):
        # 仅用「搜索我的资源」作为列表关键词（筛选面板内的输入框）
        sv = getattr(self, "_filter_search_var", None)
        keyword = (sv.get() if sv else "").strip().lower()
        self.listbox.delete(0, tk.END)
        self.listbox_map.clear()
        items = []
        if not self.package_files and not self.missing_items:
            return
        if keyword:
            for p in self.package_files:
                display_name = (getattr(self, "filename_to_display_name", {}) or {}).get(p.name) or p.name
                if keyword in p.name.lower() or keyword in display_name.lower():
                    items.append(("existing", p))
            for m in self.missing_items:
                if keyword in m["filename"].lower() or keyword in (m.get("displayName") or "").lower():
                    items.append(("missing", m))
        else:
            for p in self.package_files:
                items.append(("existing", p))
            for m in self.missing_items:
                items.append(("missing", m))

        # 类型（分类）筛选：若勾选了类型，只保留 metadata 中分类在勾选范围内的项
        selected_types = []
        if getattr(self, "_filter_type_vars", None):
            selected_types = [k for k, v in self._filter_type_vars.items() if v.get()]
        if selected_types:
            def _category_for_item(typ, data):
                if typ == "existing":
                    pid = self.filename_to_pid.get(data.name)
                else:
                    pid = data.get("packageId")
                if pid is None:
                    return None
                path = METADATA_DIR / f"{pid}.json"
                if not path.exists():
                    return None
                try:
                    d = json.loads(path.read_text(encoding="utf-8"))
                    cat = (d.get("detail") or {}).get("category") or {}
                    return cat.get("name") if isinstance(cat, dict) else None
                except Exception:
                    return None
            def _match_category(cat_name):
                if not cat_name:
                    return False
                for st in selected_types:
                    if cat_name == st or cat_name.startswith(st + "/"):
                        return True
                return False
            items = [(t, d) for t, d in items if _match_category(_category_for_item(t, d))]

        # 发行商筛选：若勾选了发行商，只保留 metadata 中发行商在勾选范围内的项
        selected_pubs = []
        if getattr(self, "_filter_pub_vars", None):
            selected_pubs = [k for k, v in self._filter_pub_vars.items() if v.get()]
        if selected_pubs:
            def _publisher_for_item(typ, data):
                if typ == "existing":
                    pid = self.filename_to_pid.get(data.name)
                else:
                    pid = data.get("packageId")
                if pid is None:
                    return None
                path = METADATA_DIR / f"{pid}.json"
                if not path.exists():
                    return None
                try:
                    d = json.loads(path.read_text(encoding="utf-8"))
                    pub = (d.get("detail") or {}).get("productPublisher") or {}
                    return pub.get("name") if isinstance(pub, dict) else None
                except Exception:
                    return None
            items = [(t, d) for t, d in items if _publisher_for_item(t, d) in selected_pubs]

        def sort_key(x):
            name = x[1].name if x[0] == "existing" else x[1]["filename"]
            if self.sort_by_snapshot:
                # 在购买列表中的用真实 grantTime；不在的用 0000 排到最后（避免文件名不匹配时 9999 排第一）
                grant_time = self.purchase_order.get(name, "0000-00-00")
                # 降序：最新领取的在前（grantTime 大的在前）
                return (grant_time, name.lower())
            return (0, name.lower())

        items.sort(key=sort_key, reverse=self.sort_by_snapshot)

        for i, (typ, data) in enumerate(items):
            if typ == "existing":
                # 有 displayName 时显示资源名（无后缀），否则显示文件名（带 .unitypackage）
                display_name = (getattr(self, "filename_to_display_name", {}) or {}).get(data.name) or data.name
                self.listbox_map[i] = data
                self.listbox.insert(tk.END, display_name)
            else:
                self.listbox_map[i] = data
                self.listbox.insert(tk.END, data["displayName"])
            if typ == "missing":
                self.listbox.itemconfig(i, fg="red")

    def _on_select(self, event):
        sel = self.listbox.curselection()
        if not sel:
            self._update_open_in_unity_visibility()
            return
        idx = sel[0]
        item = self.listbox_map.get(idx)
        if not item:
            self._update_open_in_unity_visibility()
            return

        # 选中列表项时切回包详情视图
        if self._right_show_filter:
            self._show_detail_view()

        if isinstance(item, dict):
            filename = item["filename"]
            package_id = item["packageId"]
        else:
            filename = item.name
            package_id = self.filename_to_pid.get(filename)

        if package_id is None:
            self._set_detail_content(plain_text=f"【{filename}】\n\n未在 purchases_snapshot 中找到对应 packageId。")
            self._update_open_in_unity_visibility()
            return

        info_path = METADATA_DIR / f"{package_id}.json"
        is_missing = isinstance(item, dict)

        if is_missing:
            if info_path.exists():
                try:
                    data = json.loads(info_path.read_text(encoding="utf-8"))
                    if self._use_html:
                        self._show_html_with_async_emoji(data, extra_notice="※ 未下载：下载目录中无此文件。", dark=self._dark_theme)
                    else:
                        self._set_detail_content(plain_text=f"※ 未下载：下载目录中无此文件。\n\n{format_info(data)}")
                except Exception:
                    self._set_detail_content(plain_text=f"【{filename}】 packageId={package_id}\n\n※ 未下载：下载目录中无此文件。请运行 unity_assets_downloader.py 下载。")
            else:
                self._set_detail_content(plain_text=f"【{filename}】 packageId={package_id}\n\n※ 未下载：下载目录中无此文件。请运行 unity_assets_downloader.py 下载。")
            self._update_open_in_unity_visibility()
            return

        if not info_path.exists():
            self._set_detail_content(plain_text=f"【{filename}】 packageId={package_id}\n\n未找到详情文件，请先在「获取包商店信息」中获取。")
            self._update_open_in_unity_visibility()
            return

        try:
            data = json.loads(info_path.read_text(encoding="utf-8"))
            if self._use_html:
                self._show_html_with_async_emoji(data, dark=self._dark_theme)
            else:
                self._set_detail_content(plain_text=format_info(data))
            self._update_open_in_unity_visibility()
        except Exception as e:
            self._set_detail_content(plain_text=f"读取失败: {e}")
            self._update_open_in_unity_visibility()

    def _show_html_with_async_emoji(self, data: dict, extra_notice: str = "", dark: bool = False):
        """两阶段渲染：立即用文本替代显示，后台获取 emoji 图片后自动刷新"""
        html_fast = format_info_html(data, extra_notice=extra_notice, dark=dark, use_pilmoji=False)
        self._set_detail_content(
            html_content=html_fast, _detail_type="package",
            _detail_data=data, _detail_extra=extra_notice,
        )
        detail = data.get("detail") or {}
        raw_parts = []
        for key in ("description", "elevatorPitch", "keyFeatures", "publishNotes"):
            v = detail.get(key)
            if v and isinstance(v, str):
                raw_parts.append(v)
        loc = detail.get("localizations")
        if isinstance(loc, dict):
            zh = loc.get("zh-CN")
            if isinstance(zh, dict):
                for key in ("description", "keyFeatures", "publishNotes"):
                    v = zh.get(key)
                    if v and isinstance(v, str):
                        raw_parts.append(v)
        all_text = " ".join(raw_parts)
        chars = _collect_emoji_chars(all_text)
        if not chars:
            return
        pid = data.get("packageId", "")

        def _on_done():
            try:
                def _on_main():
                    self._refresh_emoji_if_same(pid, data, extra_notice)
                self.root.after(0, _on_main)
            except Exception:
                pass

        _prefetch_emoji_batch(chars, callback=_on_done)

    def _refresh_emoji_if_same(self, pid, data, extra_notice):
        """后台 emoji 加载完毕后，如果当前仍显示同一个包，则用真实 emoji 刷新并保持滚动位置"""
        cur = getattr(self, "_current_detail_data", None)
        if not isinstance(cur, dict) or cur.get("packageId") != pid:
            return
        scroll_pos = 0.0
        try:
            scroll_pos = self.detail_widget.html.yview()[0]
        except Exception:
            pass
        html_full = format_info_html(data, extra_notice=extra_notice, dark=self._dark_theme, use_pilmoji=True)
        self._set_detail_content(
            html_content=html_full, _detail_type="package",
            _detail_data=data, _detail_extra=extra_notice,
        )
        if scroll_pos > 0.001:
            try:
                self.root.after(50, lambda: self.detail_widget.html.yview_moveto(scroll_pos))
            except Exception:
                pass

    def _log(self, msg: str):
        self.fetch_log.insert(tk.END, msg + "\n")
        self.fetch_log.see(tk.END)
        self.fetch_log.update_idletasks()

    def _stop_fetch(self):
        self._fetch_stop_requested = True

    def _start_single_fetch(self):
        pid_str = (self._single_fetch_var.get() or "").strip()
        if not pid_str:
            self._log("[ERROR] 请输入 package ID")
            return
        try:
            pid = int(pid_str)
        except ValueError:
            self._log(f"[ERROR] 无效的 ID: {pid_str}")
            return
        if self.fetch_running:
            self._log("[WARN] 正在获取中，请稍后再试")
            return
        self.fetch_running = True
        self.fetch_btn.config(state=tk.DISABLED)
        self._single_fetch_btn.config(state=tk.DISABLED)
        self._single_fetch_status.config(text="单独拉取中...")
        self.fetch_status.config(text="")
        self._log(f"[SINGLE] 开始拉取 packageId={pid}")

        def run():
            try:
                from fetch_package_info import fetch_one_package, load_config, load_cookie, OUTPUT_DIR

                config = load_config()
                bearer = (config.get("bearer_token") or "").strip()
                cookie = load_cookie()
                if not cookie:
                    raise ValueError("cookie.txt 为空，请先配置 cookie")
                timeout = int(config.get("request_timeout_sec") or 60)
                # 优先从 purchases 中取真实 displayName，与「开始获取」逻辑一致
                item = None
                for p in getattr(self, "purchases", []) or []:
                    if str(p.get("packageId", "")) == str(pid):
                        item = dict(p)
                        break
                if not item:
                    item = {"packageId": pid, "displayName": f"asset_{pid}"}
                result = fetch_one_package(item, bearer, cookie, config, timeout)
                out_file = OUTPUT_DIR / f"{pid}.json"
                out_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                ok = bool(result.get("detail"))
                self.root.after(0, lambda: self._single_fetch_done(pid, ok))
            except Exception as e:
                self.root.after(0, lambda x=str(e): self._single_fetch_error(x))

        threading.Thread(target=run, daemon=True).start()

    def _single_fetch_done(self, pid: int, ok: bool):
        self.fetch_running = False
        self.fetch_btn.config(state=tk.NORMAL)
        self._single_fetch_btn.config(state=tk.NORMAL)
        self._single_fetch_status.config(text=f"单独拉取完成: {'成功' if ok else '失败'}")
        self._log(f"[DONE] packageId={pid} 已保存到 {METADATA_DIR}")
        self._refresh()

    def _single_fetch_error(self, err: str):
        self.fetch_running = False
        self.fetch_btn.config(state=tk.NORMAL)
        self._single_fetch_btn.config(state=tk.NORMAL)
        self._single_fetch_status.config(text="错误")
        self._log(f"[ERROR] {err}")

    def _start_fetch(self):
        if self.fetch_running:
            return
        try:
            limit = int(self.limit_var.get() or 0)
        except (ValueError, tk.TclError):
            limit = 0
        self.fetch_running = True
        self._fetch_stop_requested = False
        self.fetch_btn.config(state=tk.DISABLED)
        self._fetch_stop_btn.config(state=tk.NORMAL)
        if getattr(self, "_single_fetch_btn", None) and self._single_fetch_btn.winfo_exists():
            self._single_fetch_btn.config(state=tk.DISABLED)
        self.fetch_status.config(text="获取中...")
        if getattr(self, "_single_fetch_status", None) and self._single_fetch_status.winfo_exists():
            self._single_fetch_status.config(text="")
        self.fetch_log.delete(1.0, tk.END)
        self._log(f"开始获取 (限制={limit or '全部'})...")

        def run():
            try:
                from fetch_package_info import run_fetch

                def cb(i, total, pid, name, ok, status="ok"):
                    tag = ("OK" if status == "ok" else ("SKIP" if status == "skipped" else "FAIL")).ljust(4)
                    msg = f"[{tag}] ({i}/{total}) {pid} {name}"
                    self.root.after(0, lambda m=msg: self._log(m))

                success, failed, skipped = run_fetch(
                    limit=limit,
                    progress_callback=cb,
                    stop_check=lambda: getattr(self, "_fetch_stop_requested", False),
                )
                self.root.after(0, lambda: self._fetch_done(success, failed, skipped))
            except Exception as e:
                err = str(e)
                self.root.after(0, lambda x=err: self._fetch_error(x))

        threading.Thread(target=run, daemon=True).start()

    def _fetch_done(self, success: int, failed: int, skipped: int = 0):
        self.fetch_running = False
        self.fetch_btn.config(state=tk.NORMAL)
        self._fetch_stop_btn.config(state=tk.DISABLED)
        if getattr(self, "_single_fetch_btn", None) and self._single_fetch_btn.winfo_exists():
            self._single_fetch_btn.config(state=tk.NORMAL)
        stopped = getattr(self, "_fetch_stop_requested", False)
        status_text = "已停止" if stopped else f"完成: 成功={success}, 失败={failed}, 跳过={skipped}"
        self.fetch_status.config(text=status_text)
        if stopped:
            self._log(f"\n[STOPPED] 用户停止获取，已处理 成功={success}, 失败={failed}, 跳过={skipped}")
        else:
            self._log(f"\n[DONE] 成功={success}, 失败={failed}, 跳过(无差异)={skipped} 个, 元数据库目录={METADATA_DIR}")

    def _fetch_error(self, err: str):
        self.fetch_running = False
        self.fetch_btn.config(state=tk.NORMAL)
        self._fetch_stop_btn.config(state=tk.DISABLED)
        if getattr(self, "_single_fetch_btn", None) and self._single_fetch_btn.winfo_exists():
            self._single_fetch_btn.config(state=tk.NORMAL)
        self.fetch_status.config(text="错误")
        self._log(f"\n[ERROR] {err}")

    def run(self):
        self.root.mainloop()


def main():
    app = PackageViewerApp()
    app.run()


if __name__ == "__main__":
    main()
