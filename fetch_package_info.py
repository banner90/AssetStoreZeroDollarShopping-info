# 从 purchases_snapshot.json 读取已购列表，抓取每个 package 的详情信息
# 复用主项目的认证方式：Cookie（assetstore）和 Bearer（packages-v2）
#
# 说明：Unity Package Manager 右侧详情（Overview、版本、大小等）来自
# packages-v2.unity.cn/api/product/{packageId}，需 Bearer 认证。
# 抓包确认的路径：/api/product/{ID}，返回 JSON。

import json
import queue
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests


def _get_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _get_output_dir() -> Path:
    if getattr(sys, "frozen", False):
        return _get_root() / "metadata"
    return Path(__file__).resolve().parent / "metadata"


ROOT = _get_root()
CONFIG_PATH = ROOT / "asset_store_config.json"
PURCHASES_PATH = ROOT / "purchases_snapshot.json"
COOKIE_PATH = ROOT / "cookie.txt"
OUTPUT_DIR = _get_output_dir()


def load_config() -> Dict:
    if not CONFIG_PATH.exists():
        return {}
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def load_cookie() -> str:
    if not COOKIE_PATH.exists():
        return ""
    return COOKIE_PATH.read_text(encoding="utf-8").strip()


def load_purchases() -> List[Dict]:
    if not PURCHASES_PATH.exists():
        raise FileNotFoundError(f"未找到 {PURCHASES_PATH}")
    data = json.loads(PURCHASES_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("purchases_snapshot.json 格式错误，期望 list")
    return data


def try_api(
    session: requests.Session,
    url: str,
    timeout: int = 30,
    method: str = "GET",
) -> Optional[Dict]:
    """尝试请求 API，返回解析后的 JSON，失败返回 None"""
    try:
        resp = session.request(method, url, timeout=timeout)
        if resp.status_code != 200:
            return None
        ct = (resp.headers.get("Content-Type") or "").lower()
        if "json" not in ct:
            return None
        return resp.json()
    except Exception:
        return None


def try_packages_v2(bearer: str, cookie: str, package_id: int, base: str) -> Optional[Dict]:
    """尝试 packages-v2.unity.cn 的包详情 API（Fiddler 抓包确认：/api/product/{id}）"""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "UnityEditor/2022.3.57f1c2 (Windows; U; Windows NT 10.0; zh)",
        "Accept": "*/*",
        "Authorization": f"Bearer {bearer}",
    })
    if cookie:
        session.headers["Cookie"] = cookie
    urls = [
        f"{base}/api/product/{package_id}",
        f"{base}/-/api/product/{package_id}",
        f"{base}/-/api/packages/{package_id}",
        f"{base}/-/api/package/{package_id}",
    ]
    for url in urls:
        data = try_api(session, url)
        if data and isinstance(data, dict):
            return data
    return None


def try_assetstore_api(cookie: str, package_id: int) -> Optional[Dict]:
    """尝试 assetstore.unity.com 的包详情 API"""
    if not cookie:
        return None
    session = requests.Session()
    session.headers.update({
        "Accept": "application/json, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://assetstore.unity.com/",
        "Origin": "https://assetstore.unity.com",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Cookie": cookie,
    })
    urls = [
        f"https://assetstore.unity.com/api/package/{package_id}",
        f"https://assetstore.unity.com/api/public/package/{package_id}",
        f"https://assetstore.unity.com/api/v1/package/{package_id}",
        f"https://assetstore.unity.com/api/package/{package_id}/info",
    ]
    for url in urls:
        data = try_api(session, url)
        if data and isinstance(data, dict):
            return data
    return None


def try_assetstore_search(cookie: str, query: str, package_id: Optional[int] = None) -> Optional[Dict]:
    """通过 Asset Store 搜索获取包信息，返回与 packageId 匹配的 product 或首个可用结果"""
    if not cookie:
        return None
    session = requests.Session()
    session.headers.update({
        "Accept": "application/json, */*",
        "Referer": "https://assetstore.unity.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
        "Cookie": cookie,
    })
    url = "https://assetstore.unity.com/api/search"
    try:
        resp = session.get(url, params={"q": query[:80], "rows": 10}, timeout=15)
        if resp.status_code != 200:
            return None
        data = resp.json()
        results = data.get("results") or data.get("items") or []
        for r in results:
            if not isinstance(r, dict):
                continue
            rid = r.get("id") or r.get("packageId") or r.get("package_id")
            if package_id and str(rid) == str(package_id):
                return r
            if r.get("description") or r.get("name") or r.get("displayName"):
                return r
        if results and isinstance(results[0], dict):
            return results[0]
    except Exception:
        pass
    return None


def _extract_from_deep(obj: Any, *keys: str) -> Any:
    """从嵌套 dict 中按多级 key 递归查找，返回第一个非空值"""
    if obj is None or not isinstance(obj, dict):
        return None
    for key in keys:
        val = obj.get(key)
        if val is not None:
            return val
    for v in obj.values():
        if isinstance(v, dict):
            found = _extract_from_deep(v, *keys)
            if found is not None:
                return found
        elif isinstance(v, list) and v and isinstance(v[0], dict):
            for item in v:
                found = _extract_from_deep(item, *keys)
                if found is not None:
                    return found
    return None


def _find_technical_in_json(obj: Any) -> Any:
    """在 JSON 中递归查找 technicalDetails/技术细节 相关内容"""
    if obj is None:
        return None
    if isinstance(obj, dict):
        for k in ("technicalDetails", "technical_details", "technicalDetailsHtml"):
            v = obj.get(k)
            if v is not None and (isinstance(v, str) and len(v) > 10 or isinstance(v, (dict, list))):
                return v
        for v in obj.values():
            r = _find_technical_in_json(v)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for item in obj:
            r = _find_technical_in_json(item)
            if r is not None:
                return r
    return None


def _normalize_next_data_to_detail(next_data: Dict) -> Dict:
    """将 __NEXT_DATA__ 解析为与 API detail 一致的归一化结构，提取技术详情、超链接等"""
    out: Dict[str, Any] = {}
    props = next_data.get("props") or {}
    page_props = props.get("pageProps") or props
    product = page_props.get("product") or page_props.get("package") or page_props

    def _walk(obj: Any, target: Dict, key_map: Dict[str, tuple]) -> None:
        if obj is None:
            return
        if isinstance(obj, dict):
            for our_key, api_keys in key_map.items():
                if our_key in target and target[our_key]:
                    continue
                for k in api_keys:
                    v = obj.get(k)
                    if v is not None:
                        target[our_key] = v
                        break
            for v in obj.values():
                _walk(v, target, key_map)

    key_map = {
        "name": ("name", "displayName", "title"),
        "description": ("description", "shortDescription", "fullDescription"),
        "elevatorPitch": ("elevatorPitch", "elevator_pitch", "shortDescription"),
        "keyFeatures": ("keyFeatures", "key_features", "keyFeaturesHtml"),
        "technicalDetails": ("technicalDetails", "technical_details", "technicalDetailsHtml"),
        "renderPipelineCompatibility": ("renderPipelineCompatibility", "compatibility", "pipelineCompatibility"),
        "keywords": ("keywords", "relatedKeywords", "tags"),
        "version": ("version",),
        "category": ("category",),
        "productPublisher": ("productPublisher", "publisher"),
        "publishNotes": ("publishNotes", "releaseNotes"),
        "localizations": ("localizations",),
        "uploads": ("uploads",),
        "links": ("links", "externalLinks", "productLinks", "productLinksList"),
    }
    _walk(product, out, key_map)
    _walk(page_props, out, key_map)
    _walk(next_data, out, key_map)

    if not out.get("technicalDetails"):
        td = _extract_from_deep(
            page_props, "technicalDetails", "technical_details", "technicalDetailsHtml"
        ) or _find_technical_in_json(next_data)
        if td is not None:
            out["technicalDetails"] = td if isinstance(td, (str, dict, list)) else str(td)
    if not out.get("technicalDetails") and isinstance(product, dict):
        for s in product.get("sections") or product.get("productSections") or []:
            if not isinstance(s, dict):
                continue
            st = str(s.get("type") or s.get("title") or "").lower()
            if "technical" in st or "细节" in st:
                c = s.get("content") or s.get("html") or s.get("body")
                if c:
                    out["technicalDetails"] = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
                    break

    if not out.get("renderPipelineCompatibility"):
        rpc = _extract_from_deep(page_props, "renderPipelineCompatibility", "compatibility", "pipelineCompatibility")
        if rpc is not None:
            out["renderPipelineCompatibility"] = rpc

    if not out and isinstance(product, dict):
        out = dict(product)
    return out


def try_assetstore_html(cookie: str, package_id: int, display_name: str) -> Optional[Dict]:
    """尝试从 Asset Store 页面 HTML 中解析 __NEXT_DATA__，提取技术详情、描述（含超链接）等"""
    if not cookie:
        return None
    session = requests.Session()
    session.headers.update({
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Cookie": cookie,
    })
    slug = re.sub(r"[^a-z0-9]+", "-", display_name.lower())[:50].strip("-")
    urls = [
        f"https://assetstore.unity.com/packages/-/-{package_id}",
        f"https://assetstore.unity.com/packages/0/{package_id}",
        f"https://assetstore.unity.com/packages/tools/utilities/{slug}-{package_id}",
        f"https://assetstore.unity.com/packages/tools/{slug}-{package_id}",
    ]
    for url in urls:
        try:
            resp = session.get(url, timeout=15, allow_redirects=True)
            if resp.status_code != 200:
                continue
            html = resp.text
            m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.+?)</script>', html, re.DOTALL)
            if m:
                try:
                    raw = json.loads(m.group(1))
                    detail = _normalize_next_data_to_detail(raw)
                    if detail and (detail.get("description") or detail.get("name") or detail.get("displayName")):
                        return detail
                    pp = (raw.get("props") or {}).get("pageProps") or {}
                    product = pp.get("product") or pp.get("package")
                    if isinstance(product, dict) and (product.get("description") or product.get("name")):
                        return product
                except Exception:
                    pass
            for pat in [
                r'[Tt]echnical[Dd]etails["\']?\s*:\s*["\']([^"\']*(?:\\.[^"\']*)*)["\']',
                r'"technicalDetails"\s*:\s*"((?:[^"\\]|\\.)*)"',
                r'technicalDetailsHtml["\']?\s*:\s*["\']([^"\']*(?:\\.[^"\']*)*)["\']',
            ]:
                td = re.search(pat, html)
                if td:
                    try:
                        raw_val = td.group(1)
                        desc = _safe_unescape_json_str(raw_val)
                        if len(desc) > 20:
                            return {"technicalDetails": desc, "source": "html_parse"}
                    except Exception:
                        pass
            collap = re.search(
                r'(?:技术细节|Technical\s+Details)[^<]*</[^>]+>[\s\S]*?<(?:div|section)[^>]*>([\s\S]*?)</(?:div|section)>',
                html,
                re.I,
            )
            if collap:
                frag = re.sub(r"<script[^>]*>[\s\S]*?</script>", "", collap.group(1))
                frag = re.sub(r"<style[^>]*>[\s\S]*?</style>", "", frag)
                if len(frag.strip()) > 30:
                    base = {"technicalDetails": frag.strip(), "source": "html_parse"}
                    m = re.search(r'"description"\s*:\s*"([^"]*(?:\\.[^"]*)*)"', html)
                    if m:
                        try:
                            base["description"] = _safe_unescape_json_str(m.group(1))
                        except Exception:
                            pass
                    return base
            m = re.search(r'"description"\s*:\s*"([^"]*(?:\\.[^"]*)*)"', html)
            if m:
                try:
                    desc = _safe_unescape_json_str(m.group(1))
                    return {"description": desc, "source": "html_parse"}
                except Exception:
                    pass
        except Exception:
            continue
    return None


def _technical_details_is_substantial(td: Any) -> bool:
    """判断 technicalDetails 是否为有意义的实质内容（非空壳 div 或过短占位）"""
    if not td:
        return False
    s = str(td).strip()
    if len(s) < 50:
        return False
    # 排除仅包含空 div 外壳（如 <div class="_1_3uP _1rkJa" data-reactid="312">）
    stripped = re.sub(r"<[^>]+>", "", s)
    if len(stripped.strip()) < 20:
        return False
    # 有表格或实质文本才算
    has_table = "<table" in s.lower() or "<tr" in s.lower() or "<td" in s.lower()
    has_text = re.search(r">[^<\s]{10,}<", s) or len(stripped.strip()) > 30
    return bool(has_table or has_text)


_SRPS_MAP = {
    "standard": "Built-in Render Pipeline",
    "hd": "High Definition RP (HDRP)",
    "lightweight": "Universal RP (URP)",
    "urp": "Universal RP (URP)",
    "custom": "Custom SRP",
}


def _build_technical_from_uploads(detail: Dict) -> Optional[str]:
    """从 supportedUnityVersions、uploads（含 srps）构建技术详情 HTML 表格"""
    unity_vers = detail.get("supportedUnityVersions")
    uploads = detail.get("uploads")
    if not isinstance(uploads, dict):
        return None
    rows = []
    # Unity 版本
    if isinstance(unity_vers, list) and unity_vers:
        rows.append(("Supported Unity versions", ", ".join(str(v) for v in unity_vers)))
    elif isinstance(unity_vers, str) and unity_vers:
        rows.append(("Supported Unity versions", unity_vers))
    # 每个 Unity 版本对应的上传信息（含 srps）
    for unity_ver, info in uploads.items():
        if not isinstance(info, dict):
            continue
        srps = info.get("srps")
        if isinstance(srps, list) and srps:
            rp_names = [_SRPS_MAP.get(str(s).lower(), str(s)) for s in srps]
            rows.append((f"Render Pipelines ({unity_ver})", ", ".join(rp_names)))
        elif isinstance(srps, str) and srps:
            rows.append((f"Render Pipeline ({unity_ver})", _SRPS_MAP.get(srps.lower(), srps)))
    if not rows:
        return None
    html_parts = ["<table><tbody>"]
    for k, v in rows:
        html_parts.append(f"<tr><td>{_escape_html_attr(str(k))}</td><td>{_escape_html_attr(str(v))}</td></tr>")
    html_parts.append("</tbody></table>")
    return "".join(html_parts)


def _safe_unescape_json_str(raw_val: str) -> str:
    """
    安全地反转义 JSON 字符串中的 \\uXXXX 和 \\UXXXXXXXX。
    若字符串已含原生 Unicode（如 emoji），不会损坏。
    """
    if not raw_val or ("\\u" not in raw_val and "\\U" not in raw_val):
        return raw_val
    try:
        # 仅替换转义序列，保留已有 Unicode 字符
        def replace_u(m):
            return chr(int(m.group(1), 16))
        out = re.sub(r"\\u([0-9a-fA-F]{4})", replace_u, raw_val)
        out = re.sub(r"\\U([0-9a-fA-F]{8})", replace_u, out)
        return out
    except Exception:
        return raw_val


def _escape_html_attr(text: str) -> str:
    """转义 HTML 属性/内容中的特殊字符"""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _enrich_technical_details(detail: Dict) -> None:
    """当 API 返回的 technicalDetails 为空壳时，从 uploads/supportedUnityVersions 构建"""
    td = detail.get("technicalDetails")
    if _technical_details_is_substantial(td):
        return
    if td:
        detail["technicalDetails"] = None
    built = _build_technical_from_uploads(detail)
    if built:
        detail["technicalDetails"] = built


def try_enhance_detail_from_html(cookie: str, package_id: int, display_name: str, detail: Dict) -> Dict:
    """
    当 API 已有 detail 但缺少技术详情、描述超链接时，用 HTML 页面补充。
    只填充 detail 中缺失的字段，不覆盖已有内容。
    """
    html_detail = try_assetstore_html(cookie, package_id, display_name)
    if not html_detail or not isinstance(html_detail, dict):
        return detail
    if "props" in html_detail:
        html_detail = _normalize_next_data_to_detail(html_detail)
    for key in ("technicalDetails", "renderPipelineCompatibility", "keywords", "links", "elevatorPitch", "keyFeatures"):
        if key in html_detail and html_detail[key] and not detail.get(key):
            detail[key] = html_detail[key]
    if html_detail.get("description"):
        d_has_link = "<a " in (detail.get("description") or "")
        h_has_link = "<a " in (html_detail.get("description") or "")
        if not detail.get("description") or (h_has_link and not d_has_link):
            detail["description"] = html_detail["description"]
    loc = detail.get("localizations") or {}
    if isinstance(loc, dict) and not loc.get("zh-CN", {}).get("description"):
        hloc = html_detail.get("localizations") or {}
        if isinstance(hloc, dict) and hloc.get("zh-CN"):
            if "localizations" not in detail:
                detail["localizations"] = {}
            detail["localizations"]["zh-CN"] = detail["localizations"].get("zh-CN") or {}
            detail["localizations"]["zh-CN"]["description"] = hloc["zh-CN"].get("description") or detail["localizations"]["zh-CN"].get("description")
    return detail


def fetch_one_package(
    item: Dict,
    bearer: str,
    cookie: str,
    config: Dict,
    timeout: int,
) -> Dict[str, Any]:
    """抓取单个 package 的详情，返回合并后的信息"""
    package_id = item.get("packageId")
    display_name = str(item.get("displayName") or f"asset_{package_id}")
    if not package_id:
        return {"error": "missing packageId", "item": item}
    try:
        pid = int(package_id)
    except Exception:
        return {"error": "invalid packageId", "item": item}

    result = {
        "packageId": pid,
        "displayName": display_name,
        "fromSnapshot": {
            "id": item.get("id"),
            "grantTime": item.get("grantTime"),
        },
        "detail": None,
        "source": None,
    }

    # 1. 尝试 packages-v2.unity.cn（中国区）
    if bearer:
        for base in ["https://packages-v2.unity.cn", "https://packages-v2.unity.com"]:
            detail = try_packages_v2(bearer, cookie, pid, base)
            if detail:
                result["detail"] = try_enhance_detail_from_html(cookie, pid, display_name, detail)
                result["source"] = f"{base}/-/api/packages"
                _enrich_technical_details(result["detail"])
                break
        if result["detail"]:
            return result

    # 2. 尝试 assetstore.unity.com API
    detail = try_assetstore_api(cookie, pid)
    if detail:
        result["detail"] = try_enhance_detail_from_html(cookie, pid, display_name, detail)
        result["source"] = "assetstore.unity.com api"
        _enrich_technical_details(result["detail"])
        return result

    # 3. 尝试 Asset Store 搜索（按 displayName）
    detail = try_assetstore_search(cookie, display_name, pid)
    if detail:
        result["detail"] = detail
        result["source"] = "assetstore.unity.com search"
        _enrich_technical_details(result["detail"])
        return result

    # 4. 尝试从 HTML 页面解析
    detail = try_assetstore_html(cookie, pid, display_name)
    if detail:
        result["detail"] = detail
        result["source"] = "assetstore.unity.com html"
        _enrich_technical_details(result["detail"])
        return result

    result["error"] = "all sources failed"
    return result


def _detail_equal(a: Any, b: Any) -> bool:
    """比较两个 detail 内容是否一致（忽略键序、无关格式）"""
    if a is b:
        return True
    if a is None or b is None:
        return a == b
    try:
        return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return a == b


def _detail_is_substantial(detail: Any) -> bool:
    """判断 detail 是否为可展示的完整结构（含 description/name/version 等）"""
    if not detail or not isinstance(detail, dict):
        return False
    has_core = any(detail.get(k) for k in ("description", "name", "displayName", "version"))
    has_product = "searchResults" in detail or "props" in detail
    return bool(has_core) and not has_product


def run_fetch(
    limit: int = 0,
    progress_callback: Optional[Callable[..., None]] = None,
    max_workers: int = 6,
    stop_check: Optional[Callable[[], bool]] = None,
) -> tuple[int, int, int]:
    """执行获取。返回 (success, failed, skipped)。
    progress_callback(current, total, package_id, display_name, success, status)
    status: "ok"|"skipped"|"failed"
    skipped: 内容与已有一致，无需更新
    stop_check: 返回 True 时立即停止获取
    """
    config = load_config()
    bearer = (config.get("bearer_token") or "").strip()
    cookie = load_cookie()
    if not cookie:
        raise ValueError("cookie.txt 为空，请先配置 cookie")
    timeout = int(config.get("request_timeout_sec") or 60)
    purchases = load_purchases()
    if not purchases:
        raise ValueError("purchases_snapshot.json 为空或不存在")
    if limit > 0:
        purchases = purchases[:limit]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    to_fetch = [(i, item) for i, item in enumerate(purchases, 1) if item.get("packageId")]

    if not to_fetch:
        return 0, 0, 0

    success, failed, skipped = 0, 0, 0
    total = len(purchases)

    def _fetch_task(args):
        i, item = args
        pid = item.get("packageId", "?")
        result = fetch_one_package(item, bearer, cookie, config, timeout)
        out_file = OUTPUT_DIR / f"{pid}.json"
        existing = None
        if out_file.exists():
            try:
                existing = json.loads(out_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        new_detail = result.get("detail")
        old_detail = existing.get("detail") if existing else None
        if new_detail and old_detail and _detail_equal(new_detail, old_detail):
            # detail 相同则跳过写入，但若 displayName/fromSnapshot 不同（如单独拉取曾用 asset_xxx），仍更新顶层字段
            if existing and (
                str(existing.get("displayName")) != str(result.get("displayName", ""))
                or existing.get("fromSnapshot") != result.get("fromSnapshot")
            ):
                merged = dict(existing)
                merged["displayName"] = result.get("displayName", merged["displayName"])
                merged["fromSnapshot"] = result.get("fromSnapshot", merged["fromSnapshot"])
                out_file.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
                return i, total, pid, str(item.get("displayName") or "?"), True, "ok"
            return i, total, pid, str(item.get("displayName") or "?"), False, "skipped"
        if not new_detail and old_detail:
            return i, total, pid, str(item.get("displayName") or "?"), False, "failed"
        if new_detail and old_detail and _detail_is_substantial(old_detail) and not _detail_is_substantial(new_detail):
            return i, total, pid, str(item.get("displayName") or "?"), False, "skipped"
        out_file.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        ok = bool(new_detail)
        return i, total, pid, str(item.get("displayName") or "?"), ok, "ok" if ok else "failed"

    task_queue = queue.Queue()
    result_queue = queue.Queue()
    _stop = threading.Event()

    def _worker():
        while not _stop.is_set():
            try:
                args = task_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if args is None:
                break
            try:
                r = _fetch_task(args)
                result_queue.put(r)
            except Exception:
                result_queue.put((args[0], total, args[1].get("packageId", "?"), str(args[1].get("displayName", "?")), False, "failed"))
            finally:
                task_queue.task_done()

    for t in to_fetch:
        task_queue.put(t)
    n_workers = min(max_workers, len(to_fetch))
    workers = [threading.Thread(target=_worker, daemon=True) for _ in range(n_workers)]
    for w in workers:
        w.start()

    stopped = False
    collected = 0
    poison_sent = False

    def _send_poison():
        nonlocal poison_sent
        if not poison_sent:
            poison_sent = True
            for _ in range(n_workers):
                try:
                    task_queue.put(None)
                except Exception:
                    pass

    while collected < len(to_fetch):
        if stop_check and stop_check():
            _stop.set()
            _send_poison()
            stopped = True
            break
        try:
            i, tot, pid, name, ok, status = result_queue.get(timeout=0.3)
        except queue.Empty:
            continue
        collected += 1
        if status == "skipped":
            skipped += 1
        elif ok:
            success += 1
        else:
            failed += 1
        if progress_callback:
            progress_callback(i, tot, pid, name, ok, status)
        time.sleep(0.05)

    if not stopped:
        _send_poison()
    for w in workers:
        w.join(timeout=1.0)

    return success, failed, skipped


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="从 purchases_snapshot 抓取包详情")
    parser.add_argument("--limit", type=int, default=0, help="限制抓取数量，0=全部")
    parser.add_argument("--package-id", type=int, default=0, help="仅拉取指定 packageId（如 14656），用于测试")
    args = parser.parse_args()

    print("Package Info Fetcher - 从 purchases_snapshot 抓取包详情")
    config = load_config()
    bearer = (config.get("bearer_token") or "").strip()
    cookie = load_cookie()
    if not cookie:
        print("[ERROR] cookie.txt 为空，请先配置 cookie")
        return 1
    single_pid = args.package_id
    if single_pid:
        purchases = [{"packageId": single_pid, "displayName": f"asset_{single_pid}"}]
        print(f"[INFO] 仅拉取 packageId={single_pid}")
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        print(f"[INFO] 元数据库目录: {OUTPUT_DIR}")
        timeout = int(config.get("request_timeout_sec") or 60)
        result = fetch_one_package(purchases[0], bearer, cookie, config, timeout)
        out_file = OUTPUT_DIR / f"{single_pid}.json"
        out_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        if result.get("detail"):
            print(f"[OK] 已保存到 {out_file}")
            return 0
        print("[FAIL] 未能获取详情")
        return 2
    try:
        purchases = load_purchases()
    except Exception as e:
        print(f"[ERROR] 加载购买列表失败: {e}")
        return 1
    limit = args.limit
    if limit > 0:
        print(f"[INFO] 限制抓取前 {limit} 个")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[INFO] 元数据库目录: {OUTPUT_DIR}")
    if not bearer:
        print("[WARN] bearer_token 为空，将跳过 packages-v2 API")

    def cb(i, total, pid, name, ok, status="ok"):
        tag = ("OK" if status == "ok" else ("SKIP" if status == "skipped" else "FAIL")).ljust(4)
        print(f"[{tag}] ({i}/{total}) {pid} {name}")

    success, failed, skipped = run_fetch(limit=limit, progress_callback=cb)
    print(f"[DONE] 成功={success}, 失败={failed}, 跳过(无差异)={skipped}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
