# 查看 download_dir 下的 .unitypackage 列表，选中后显示 metadata 中的包详情
# 集成 fetch_package_info 抓取功能

import html
import json
import os
from datetime import datetime
import re
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk, scrolledtext, messagebox

try:
    from tkinterweb import HtmlFrame
    HAS_HTML_FRAME = True
except ImportError:
    HAS_HTML_FRAME = False

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
METADATA_DIR = _BASE / "metadata" if not getattr(sys, "frozen", False) else ROOT / "metadata"
# 图标：打包后从 _MEIPASS 读取；开发时从 build/icon.png 或根目录 icon.png 读取
if getattr(sys, "frozen", False):
    ICON_PATH = _MEIPASS / "icon.png"
else:
    _icon_candidates = (_BASE / "build" / "icon.png", _BASE / "icon.png")
    ICON_PATH = next((p for p in _icon_candidates if p.exists()), _BASE / "icon.png")


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
    if not html_text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", html_text, flags=re.I)
    text = re.sub(r"</p>", "\n", text)
    text = re.sub(r"</li>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _escape_html(text: str) -> str:
    """转义 HTML 特殊字符"""
    return html.escape(str(text or ""), quote=False)


def format_info_html(data: dict, extra_notice: str = "", dark: bool = False) -> str:
    """生成 Unity Asset Store 风格的 HTML；dark=True 时使用深色背景与白色文字"""
    pid = data.get("packageId")
    display_name = data.get("displayName", "")
    detail = data.get("detail")

    if dark:
        style = """
    body { font-family: Inter, "Noto Sans SC", Roboto, "Segoe UI", sans-serif; background: #1a1a1a; color: #ffffff; margin: 12px 16px; font-size: 16px; line-height: 1.5; }
    .title { font-size: 1.125rem; font-weight: 600; color: #ffffff; margin-bottom: 16px; }
    .meta { color: #ffffff; margin-bottom: 16px; }
    .meta-row { margin: 4px 0; }
    .meta-label { color: #b0b0b0; font-size: 0.875rem; }
    .section { margin-top: 20px; padding-top: 16px; border-top: 1px solid #444; }
    .section-title { font-size: 0.875rem; font-weight: 600; color: #ffffff; margin-bottom: 8px; }
    .desc, .notes { color: #ffffff; white-space: pre-wrap; word-wrap: break-word; }
    .notice { background: #3d3d00; color: #e0e0a0; padding: 8px 12px; border-radius: 4px; margin-bottom: 12px; }
    .uploads { margin: 4px 0; }
    .uploads-item { padding: 2px 0; color: #ffffff; }
    a { color: #7eb8ff; text-decoration: none; }
    a:hover { color: #9ec8ff; text-decoration: underline; }
    """
    else:
        style = """
    body {
        font-family: Inter, "Noto Sans SC", "Noto Sans JP", "Noto Sans KR", Roboto, -apple-system, BlinkMacSystemFont, "Segoe UI", Oxygen, Ubuntu, Cantarell, "Fira Sans", "Droid Sans", "Helvetica Neue", Helvetica, Arial, sans-serif;
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
            parts.append(f'<div class="meta meta-row"><span class="meta-label">版本</span> {_escape_html(vname)}</div>')
        if vdate:
            parts.append(f'<div class="meta meta-row"><span class="meta-label">发布日期</span> {_escape_html(vdate)}</div>')

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

    uploads = detail.get("uploads", {})
    if isinstance(uploads, dict) and uploads:
        parts.append('<div class="section"><div class="section-title">包大小（按 Unity 版本）</div><div class="uploads">')
        for unity_ver, info in uploads.items():
            if isinstance(info, dict):
                size = info.get("downloadSize", "")
                count = info.get("assetCount", "")
                if size:
                    try:
                        size_kb = int(size) / 1024
                        parts.append(f'<div class="uploads-item">{_escape_html(unity_ver)}: {size_kb:.1f} KB, {_escape_html(str(count))} 个文件</div>')
                    except (ValueError, TypeError):
                        parts.append(f'<div class="uploads-item">{_escape_html(unity_ver)}: {_escape_html(str(size))} bytes</div>')
        parts.append("</div></div>")

    desc = detail.get("description") or ""
    loc = detail.get("localizations", {}).get("zh-CN", {})
    if isinstance(loc, dict) and loc.get("description"):
        desc = loc["description"]
    if desc:
        desc_plain = strip_html(desc)
        desc_show = _escape_html(desc_plain[:8000])
        if len(desc_plain) > 8000:
            desc_show += "\n... (已截断)"
        parts.append('<div class="section"><div class="section-title">描述</div><div class="desc">')
        parts.append(desc_show.replace("\n", "<br>"))
        parts.append("</div></div>")

    notes = detail.get("publishNotes") or (loc.get("publishNotes") if isinstance(loc, dict) else "")
    if notes:
        notes_plain = strip_html(str(notes))
        notes_show = _escape_html(notes_plain[:3000])
        if len(notes_plain) > 3000:
            notes_show += "\n... (已截断)"
        parts.append('<div class="section"><div class="section-title">更新说明</div><div class="notes">')
        parts.append(notes_show.replace("\n", "<br>"))
        parts.append("</div></div>")

    parts.append("</body></html>")
    return "".join(parts)


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
            _sty.configure("Web.TFrame", background=self._web_bg)
            _sty.configure("Web.TLabel", background=self._web_bg, foreground=self._web_fg, font=("Segoe UI", 9))
            _sty.configure("Web.TButton", background=self._THEME_LIGHT["btn_bg"], foreground=self._web_fg, padding=(10, 4))
            _sty.map("Web.TButton", background=[("active", self._THEME_LIGHT["btn_active"]), ("pressed", "#bdbdbd")])
            _sty.configure("Web.TCheckbutton", background=self._web_bg, foreground=self._web_fg)
            _sty.configure("Web.Card.TLabel", background=self._web_card_bg, foreground=self._web_fg, font=("Segoe UI", 9))
            _sty.configure("Web.Card.TCheckbutton", background=self._web_card_bg, foreground=self._web_fg)
            _sty.configure("Vertical.TScrollbar", troughrelief="flat")
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
        if not self.download_dir.exists():
            ttk.Label(tab1, text="(目录不存在)", style="Web.TLabel", foreground="red").pack(anchor=tk.W)

        paned = ttk.PanedWindow(tab1, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, pady=4)
        self._main_paned = paned

        left_container = tk.Frame(paned, bg=self._web_bg)
        paned.add(left_container, weight=3)
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
            text="刷新列表",
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
        paned.add(right_frame, weight=1)
        self._right_show_filter = False

        # 右侧：详情 与 筛选 两个视图，同一时间只显示一个
        self.detail_container = tk.Frame(right_frame, bg=self._web_bg)
        self.detail_container.pack(fill=tk.BOTH, expand=True)
        self.filter_container = tk.Frame(right_frame, bg=self._web_bg)
        self._theme_frames_bg.extend([right_frame, self.detail_container, self.filter_container])

        self._use_html = HAS_HTML_FRAME
        if self._use_html:
            self.detail_widget = HtmlFrame(self.detail_container, messages_enabled=False)
            self.detail_widget.pack(fill=tk.BOTH, expand=True)
        else:
            self.detail_widget = scrolledtext.ScrolledText(
                self.detail_container,
                wrap=tk.WORD,
                font=("Segoe UI", 10),
                state=tk.DISABLED,
            )
            self.detail_widget.pack(fill=tk.BOTH, expand=True)

        # 嵌入详情区域内部的右上角：在Unity中打开（深蓝细描边、白底）；仅选中已下载资源时显示
        _open_btn_border = "#1565c0"
        self._open_in_unity_frame = tk.Frame(self.detail_container, bg=_open_btn_border, padx=1, pady=1)
        # 往左靠一点，不贴最右侧（右边缘距右约 80px）
        self._open_in_unity_frame.place(relx=1, rely=0, x=-32, y=36, anchor=tk.NE)
        self._open_in_unity_frame.lift()
        self._open_in_unity_btn = tk.Button(
            self._open_in_unity_frame,
            text="在Unity中打开",
            command=self._open_in_unity,
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
        self._open_in_unity_btn.pack()
        self._update_open_in_unity_visibility()  # 初始不显示，等有选中且已下载再显示

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
            "请保证已经执行过 unity_assets_downloader.py 的「拉取已购买资产列表」阶段。",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, anchor=tk.W)
        fetch_row = tk.Frame(self._tab2_container, bg=self._web_bg)
        fetch_row.pack(fill=tk.X, pady=4)
        self._theme_frames_bg.append(fetch_row)
        ttk.Label(fetch_row, text="限制数量 (0=全部):", style="Web.TLabel").pack(side=tk.LEFT)
        self.limit_var = tk.IntVar(value=0)
        self._limit_entry = tk.Entry(
            fetch_row,
            textvariable=self.limit_var,
            width=10,
            font=("Segoe UI", 10),
            bg=self._THEME_LIGHT["entry_bg"],
            fg=self._web_fg,
            insertbackground=self._web_fg,
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
        self.fetch_status = ttk.Label(fetch_row, text="", style="Web.TLabel")
        self.fetch_status.pack(side=tk.LEFT, padx=8)

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
        self._theme_btn.place(relx=1, rely=0, x=-12, y=25, anchor=tk.NE)
        self._theme_btn.bind("<Button-1>", lambda e: self._toggle_theme())
        self._theme_btn.lift()

        self._apply_theme()  # 按当前时段（或默认）应用明/暗主题

    def _on_notebook_tab_changed(self, event=None):
        """切换到「获取包商店信息」页签时，焦点移到 notebook，避免限制数量输入框默认获焦"""
        try:
            if self._notebook.index(self._notebook.select()) == 1:
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

    def _update_open_in_unity_visibility(self):
        """仅当有选中且选中项为已下载资源时显示「在Unity中打开」按钮"""
        frame = getattr(self, "_open_in_unity_frame", None)
        if not frame or not frame.winfo_exists():
            return
        sel = self.listbox.curselection()
        if not sel:
            frame.place_forget()
            return
        idx = sel[0]
        item = self.listbox_map.get(idx)
        if item is None or isinstance(item, dict):
            frame.place_forget()
            return
        frame.place(relx=1, rely=0, x=-32, y=36, anchor=tk.NE)
        frame.lift()

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

    def _show_detail_view(self):
        """显示包详情视图（选中列表项或点击关闭筛选时调用）"""
        self._right_show_filter = False
        self.filter_container.pack_forget()
        self.detail_container.pack(fill=tk.BOTH, expand=True)
        self.filter_btn.config(text="筛选")

    def _set_main_sash_once(self):
        """窗口显示后按当前宽度把资源列表设为约 35%，只执行一次"""
        if getattr(self, "_main_sash_set", True):
            return
        try:
            pw = getattr(self, "_main_paned", None)
            if not pw or not pw.winfo_exists():
                return
            w = pw.winfo_width()
            if w > 200:
                pos = int(w * 0.35)
                pw.sashpos(0, pos)
                self._main_sash_set = True
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
            s.configure("Web.TFrame", background=self._web_bg)
            s.configure("Web.TLabel", background=self._web_bg, foreground=self._web_fg, font=("Segoe UI", 9))
            s.configure("Web.TButton", background=t["btn_bg"], foreground=self._web_fg, padding=(10, 4))
            s.map("Web.TButton", background=[("active", t["btn_active"]), ("pressed", "#505050" if self._dark_theme else "#bdbdbd")])
            s.configure("Web.TCheckbutton", background=self._web_bg, foreground=self._web_fg)
            s.configure("Web.Card.TLabel", background=self._web_card_bg, foreground=self._web_fg, font=("Segoe UI", 9))
            s.configure("Web.Card.TCheckbutton", background=self._web_card_bg, foreground=self._web_fg)
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
            self._open_in_unity_btn.config(bg=t["btn_bg"], fg=self._web_fg, activebackground=t["btn_active"], activeforeground=self._web_fg)
        if getattr(self, "_filter_clear_btn", None) and self._filter_clear_btn.winfo_exists():
            self._filter_clear_btn.config(bg=t["btn_bg"], fg=self._web_fg, activebackground=t["btn_active"], activeforeground=self._web_fg)
        for name in ("_filter_search_row", "_filter_type_frame", "_filter_type_canvas", "_filter_pub_frame", "_filter_pub_canvas", "_filter_pub_inner"):
            w = getattr(self, name, None)
            if w and w.winfo_exists():
                w.config(bg=self._web_bg)
        if getattr(self, "_filter_type_inner", None) and self._filter_type_inner.winfo_exists():
            self._filter_type_inner.config(bg=self._web_card_bg)
        if getattr(self, "_filter_pub_lf", None) and self._filter_pub_lf.winfo_exists():
            self._filter_pub_lf.config(bg=self._web_card_bg)
        for name in ("_filter_entry", "_pub_entry"):
            w = getattr(self, name, None)
            if w and w.winfo_exists():
                w.config(bg=t["entry_bg"], fg=self._web_fg, insertbackground=self._web_fg, highlightcolor=self._web_border, highlightbackground=self._web_border)
        # 获取包商店信息页签：输入框、按钮、日志区随主题
        if getattr(self, "_limit_entry", None) and self._limit_entry.winfo_exists():
            self._limit_entry.config(bg=t["entry_bg"], fg=self._web_fg, insertbackground=self._web_fg, highlightcolor=self._web_border, highlightbackground=self._web_border)
        if getattr(self, "fetch_btn", None) and self.fetch_btn.winfo_exists():
            self.fetch_btn.config(bg=t["btn_bg"], fg=self._web_fg, activebackground=t["btn_active"], activeforeground=self._web_fg)
        if getattr(self, "fetch_log", None) and self.fetch_log.winfo_exists():
            self.fetch_log.config(bg=self._web_card_bg, fg=self._web_fg)
        if not getattr(self, "_use_html", True) and getattr(self, "detail_widget", None) and self.detail_widget.winfo_exists():
            self.detail_widget.config(bg=self._web_card_bg, fg=self._web_fg)
        # 主题切换后按当前类型重绘详情区（包详情 / 摘要），使 HTML 随主题变色
        dtype = getattr(self, "_current_detail_type", None)
        ddata = getattr(self, "_current_detail_data", None)
        if dtype == "package" and isinstance(ddata, dict) and self._use_html:
            self.detail_widget.load_html(format_info_html(ddata, dark=self._dark_theme))
        elif dtype == "summary" and ddata and self._use_html:
            body_bg = "#1a1a1a" if self._dark_theme else "#fff"
            body_fg = "#ffffff" if self._dark_theme else "#212121"
            html_msg = f'<html><body style="font-family:Segoe UI, sans-serif; background:{body_bg}; color:{body_fg}; padding:12px;"><p style="margin:0; font-weight:bold;">{html.escape(str(ddata))}</p></body></html>'
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
        self._filter_entry = tk.Entry(
            search_row, textvariable=self._filter_search_var, width=28,
            bg=self._THEME_LIGHT["entry_bg"], fg=fg, insertbackground=fg, relief=tk.FLAT, highlightthickness=1,
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
        type_counts = self._collect_category_counts()
        for cat_name, count in sorted(type_counts.items(), key=lambda x: -x[1]):
            self._filter_type_vars[cat_name] = tk.BooleanVar(value=False)
            ttk.Checkbutton(
                type_inner,
                text=f"{cat_name} ({count})",
                variable=self._filter_type_vars[cat_name],
                command=self._apply_filter_to_list,
                style="Web.Card.TCheckbutton",
            ).pack(anchor=tk.W)
        if not type_counts:
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
            bg=self._THEME_LIGHT["entry_bg"], fg=fg, insertbackground=fg, relief=tk.FLAT, highlightthickness=1,
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
        for v in self._filter_type_vars.values():
            v.set(False)
        for v in getattr(self, "_filter_pub_vars", {}).values():
            v.set(False)
        self._filter_pub_search.set("")
        self._apply_filter_to_list()

    def _apply_filter_to_list(self):
        """根据筛选条件过滤左侧列表。已实现：搜索我的资源、类型、发行商。"""
        self._filter_list()

    def _set_detail_content(self, html_content: str = None, plain_text: str = None, _detail_type: str = None, _detail_data=None):
        """设置右侧详情内容。有 html_content 时用网页；否则用纯文本。_detail_type/_detail_data 用于主题切换时重绘。"""
        frame = getattr(self, "_open_in_unity_frame", None)
        if frame and frame.winfo_exists():
            frame.place_forget()
        dark = self._dark_theme
        if self._use_html:
            if html_content is not None:
                self.detail_widget.load_html(html_content)
                self._current_detail_type = _detail_type or "html"
                self._current_detail_data = _detail_data
                self._current_summary_msg = None
            elif plain_text is not None:
                body_bg = "#1a1a1a" if dark else "#fff"
                body_fg = "#ffffff" if dark else "#222"
                simple_html = f'<html><body style="font-family:Segoe UI; background:{body_bg}; color:{body_fg}; padding:12px;"><pre style="margin:0; white-space:pre-wrap;">{html.escape(plain_text)}</pre></body></html>'
                self.detail_widget.load_html(simple_html)
                self._current_detail_type = "plain"
                self._current_detail_data = None
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
        self.root.after(800, self._update_open_in_unity_visibility)

    def _toggle_sort(self):
        self.sort_by_snapshot = not self.sort_by_snapshot
        self.sort_btn.config(text="按购买顺序" if self.sort_by_snapshot else "按字母排序")
        self._filter_list()

    def _refresh(self):
        self.package_files = []
        self.missing_items = []
        if not self.download_dir.exists():
            self.purchase_order = {}
            for item in self.purchases:
                display_name = str(item.get("displayName") or "")
                pid = item.get("packageId")
                grant_time = str(item.get("grantTime") or "9999-99-99")
                if display_name and pid:
                    fn = sanitize_filename(display_name) + ".unitypackage"
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
            self._update_open_in_unity_visibility()
            return

        existing_files = list(self.download_dir.glob("*.unitypackage"))
        existing_names = {p.name for p in existing_files}
        self.package_files = list(existing_files)

        self.purchase_order = {}
        purchased_downloaded = 0  # 购买列表中、对应文件已存在的数量
        for item in self.purchases:
            display_name = str(item.get("displayName") or "")
            pid = item.get("packageId")
            grant_time = str(item.get("grantTime") or "9999-99-99")
            if display_name:
                fn = sanitize_filename(display_name) + ".unitypackage"
                self.purchase_order[fn] = grant_time
            if not display_name or not pid:
                continue
            filename = sanitize_filename(display_name) + ".unitypackage"
            if filename in existing_names:
                purchased_downloaded += 1
            else:
                self.missing_items.append({
                    "filename": filename,
                    "displayName": display_name,
                    "packageId": pid,
                    "grantTime": grant_time,
                })

        self._filter_list()
        msg = f"共 {purchased_downloaded} 个已下载，{len(self.missing_items)} 个未下载（红色），合计 {purchased_downloaded + len(self.missing_items)} 个资源"
        self._current_summary_msg = msg
        self._current_detail_type = "summary"
        if self._use_html:
            body_bg = "#1a1a1a" if self._dark_theme else "#fff"
            body_fg = "#ffffff" if self._dark_theme else "#212121"
            html_msg = (
                f'<html><body style="font-family:Segoe UI, sans-serif; background:{body_bg}; color:{body_fg}; padding:12px;">'
                f'<p style="margin:0; font-weight:bold;">{html.escape(msg)}</p></body></html>'
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
                if keyword in p.name.lower():
                    items.append(("existing", p))
            for m in self.missing_items:
                if keyword in m["filename"].lower():
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
            items = [(t, d) for t, d in items if _category_for_item(t, d) in selected_types]

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
                name = data.name
                self.listbox_map[i] = data
            else:
                name = data["filename"]
                self.listbox_map[i] = data
            self.listbox.insert(tk.END, name)
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
                        self._set_detail_content(html_content=format_info_html(data, extra_notice="※ 未下载：下载目录中无此文件。", dark=self._dark_theme))
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
                self._set_detail_content(html_content=format_info_html(data, dark=self._dark_theme), _detail_type="package", _detail_data=data)
            else:
                self._set_detail_content(plain_text=format_info(data))
            self._update_open_in_unity_visibility()
        except Exception as e:
            self._set_detail_content(plain_text=f"读取失败: {e}")
            self._update_open_in_unity_visibility()

    def _log(self, msg: str):
        self.fetch_log.insert(tk.END, msg + "\n")
        self.fetch_log.see(tk.END)
        self.fetch_log.update_idletasks()

    def _start_fetch(self):
        if self.fetch_running:
            return
        try:
            limit = int(self.limit_var.get() or 0)
        except (ValueError, tk.TclError):
            limit = 0
        self.fetch_running = True
        self.fetch_btn.config(state=tk.DISABLED)
        self.fetch_status.config(text="获取中...")
        self.fetch_log.delete(1.0, tk.END)
        self._log(f"开始获取 (限制={limit or '全部'})...")

        def run():
            try:
                from fetch_package_info import run_fetch

                def cb(i, total, pid, name, ok, status="ok"):
                    tag = "OK" if status == "ok" else ("SKIP" if status == "skipped" else "FAIL")
                    msg = f"[{tag}] ({i}/{total}) {pid} {name}"
                    self.root.after(0, lambda m=msg: self._log(m))

                success, failed, skipped = run_fetch(limit=limit, progress_callback=cb)
                self.root.after(0, lambda: self._fetch_done(success, failed, skipped))
            except Exception as e:
                err = str(e)
                self.root.after(0, lambda x=err: self._fetch_error(x))

        threading.Thread(target=run, daemon=True).start()

    def _fetch_done(self, success: int, failed: int, skipped: int = 0):
        self.fetch_running = False
        self.fetch_btn.config(state=tk.NORMAL)
        self.fetch_status.config(text=f"完成: 成功={success}, 失败={failed}, 跳过={skipped}")
        self._log(f"\n[DONE] 成功={success}, 失败={failed}, 跳过已有={skipped} 个, 元数据库目录={METADATA_DIR}")

    def _fetch_error(self, err: str):
        self.fetch_running = False
        self.fetch_btn.config(state=tk.NORMAL)
        self.fetch_status.config(text="错误")
        self._log(f"\n[ERROR] {err}")

    def run(self):
        self.root.mainloop()


def main():
    app = PackageViewerApp()
    app.run()


if __name__ == "__main__":
    main()
