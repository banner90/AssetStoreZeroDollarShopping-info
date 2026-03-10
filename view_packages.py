# 查看 download_dir 下的 .unitypackage 列表，选中后显示 metadata 中的包详情
# 集成 fetch_package_info 抓取功能

import html
import json
import re
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk, scrolledtext

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

        notebook = ttk.Notebook(main)
        notebook.pack(fill=tk.BOTH, expand=True)

        # Tab 1: 包列表查看
        tab1 = ttk.Frame(notebook, padding=4)
        notebook.add(tab1, text="包列表查看")

        ttk.Label(
            tab1,
            text=f"unitypackage 下载目录 (asset_store_config.json 的 download_dir): {self.download_dir}",
        ).pack(anchor=tk.W)
        if not self.download_dir.exists():
            ttk.Label(tab1, text="(目录不存在)", foreground="red").pack(anchor=tk.W)

        paned = ttk.PanedWindow(tab1, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, pady=4)

        left_container = ttk.Frame(paned)
        paned.add(left_container, weight=1)
        left_frame = ttk.LabelFrame(left_container, text=".unitypackage 列表", padding=4)
        left_frame.pack(fill=tk.BOTH, expand=True)
        search_row = ttk.Frame(left_frame)
        search_row.pack(fill=tk.X, pady=(0, 4))
        self.sort_btn = ttk.Button(
            search_row,
            text="按购买顺序",
            command=lambda: self._toggle_sort(),
        )
        self.sort_btn.pack(side=tk.LEFT, padx=(0, 4))
        ttk.Label(search_row, text="搜索:").pack(side=tk.LEFT, padx=(0, 4))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._filter_list())
        self.search_entry = ttk.Entry(search_row, textvariable=self.search_var, width=16)
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        list_scroll = ttk.Scrollbar(left_frame)
        list_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox = tk.Listbox(
            left_frame,
            yscrollcommand=list_scroll.set,
            font=("Segoe UI", 10),
            selectmode=tk.SINGLE,
        )
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        list_scroll.config(command=self.listbox.yview)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)

        ttk.Button(left_container, text="刷新列表", command=self._refresh).pack(anchor=tk.W, pady=(4, 0))

        right_frame = ttk.LabelFrame(paned, text="包详情", padding=4)

        paned.add(right_frame, weight=2)
        self.detail_text = scrolledtext.ScrolledText(
            right_frame,
            wrap=tk.WORD,
            font=("Segoe UI", 10),
            state=tk.DISABLED,
        )
        self.detail_text.pack(fill=tk.BOTH, expand=True)

        # Tab 2: 获取包商店信息
        tab2 = ttk.Frame(notebook, padding=8)
        notebook.add(tab2, text="获取包商店信息")

        ttk.Label(
            tab2,
            text="根据 purchases_snapshot.json 文件获取每个包的详情到 metadata 目录，"
            "请保证已经执行过 unity_assets_downloader.py 的「拉取已购买资产列表」阶段。",
        ).pack(anchor=tk.W)
        row = ttk.Frame(tab2)
        row.pack(fill=tk.X, pady=4)
        ttk.Label(row, text="限制数量 (0=全部):").pack(side=tk.LEFT)
        self.limit_var = tk.IntVar(value=0)
        self.limit_spin = ttk.Spinbox(row, from_=0, to=99999, textvariable=self.limit_var, width=8)
        self.limit_spin.pack(side=tk.LEFT, padx=4)
        self.fetch_btn = ttk.Button(row, text="开始获取", command=self._start_fetch)
        self.fetch_btn.pack(side=tk.LEFT, padx=4)
        self.fetch_status = ttk.Label(row, text="")
        self.fetch_status.pack(side=tk.LEFT, padx=8)

        self.fetch_log = scrolledtext.ScrolledText(tab2, wrap=tk.WORD, font=("Consolas", 9), height=20)
        self.fetch_log.pack(fill=tk.BOTH, expand=True, pady=4)

        self._refresh()

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
            self.detail_text.config(state=tk.NORMAL)
            self.detail_text.delete(1.0, tk.END)
            self.detail_text.insert(tk.END, f"目录不存在: {self.download_dir}")
            self.detail_text.config(state=tk.DISABLED)
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
        self.detail_text.config(state=tk.NORMAL)
        self.detail_text.delete(1.0, tk.END)
        self.detail_text.insert(
            tk.END,
            f"共 {purchased_downloaded} 个已下载，{len(self.missing_items)} 个未下载（红色），合计 {purchased_downloaded + len(self.missing_items)} 个资源",
        )
        self.detail_text.config(state=tk.DISABLED)

    def _filter_list(self):
        keyword = (self.search_var.get() or "").strip().lower()
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
            return
        idx = sel[0]
        item = self.listbox_map.get(idx)
        if not item:
            return

        if isinstance(item, dict):
            filename = item["filename"]
            package_id = item["packageId"]
        else:
            filename = item.name
            package_id = self.filename_to_pid.get(filename)

        if package_id is None:
            self.detail_text.config(state=tk.NORMAL)
            self.detail_text.delete(1.0, tk.END)
            self.detail_text.insert(
                tk.END,
                f"【{filename}】\n\n未在 purchases_snapshot 中找到对应 packageId。",
            )
            self.detail_text.config(state=tk.DISABLED)
            return

        info_path = METADATA_DIR / f"{package_id}.json"
        is_missing = isinstance(item, dict)

        if is_missing:
            self.detail_text.config(state=tk.NORMAL)
            self.detail_text.delete(1.0, tk.END)
            if info_path.exists():
                try:
                    data = json.loads(info_path.read_text(encoding="utf-8"))
                    text = format_info(data)
                    self.detail_text.insert(tk.END, f"※ 未下载：下载目录中无此文件。\n\n{text}")
                except Exception:
                    self.detail_text.insert(
                        tk.END,
                        f"【{filename}】 packageId={package_id}\n\n"
                        "※ 未下载：下载目录中无此文件。请运行 unity_assets_downloader.py 下载。",
                    )
            else:
                self.detail_text.insert(
                    tk.END,
                    f"【{filename}】 packageId={package_id}\n\n"
                    "※ 未下载：下载目录中无此文件。请运行 unity_assets_downloader.py 下载。",
                )
            self.detail_text.config(state=tk.DISABLED)
            return

        if not info_path.exists():
            self.detail_text.config(state=tk.NORMAL)
            self.detail_text.delete(1.0, tk.END)
            self.detail_text.insert(
                tk.END,
                f"【{filename}】 packageId={package_id}\n\n"
                "未找到详情文件，请先在「获取包商店信息」中抓取。",
            )
            self.detail_text.config(state=tk.DISABLED)
            return

        try:
            data = json.loads(info_path.read_text(encoding="utf-8"))
            text = format_info(data)
        except Exception as e:
            text = f"读取失败: {e}"
        self.detail_text.config(state=tk.NORMAL)
        self.detail_text.delete(1.0, tk.END)
        self.detail_text.insert(tk.END, text)
        self.detail_text.config(state=tk.DISABLED)

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
