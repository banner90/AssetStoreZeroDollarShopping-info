# 从 purchases_snapshot.json 读取已购列表，抓取每个 package 的详情信息
# 复用主项目的认证方式：Cookie（assetstore）和 Bearer（packages-v2）
#
# 说明：Unity Package Manager 右侧详情（Overview、版本、大小等）来自
# packages-v2.unity.cn/api/product/{packageId}，需 Bearer 认证。
# 抓包确认的路径：/api/product/{ID}，返回 JSON。

import json
import re
import sys
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


def try_assetstore_search(cookie: str, query: str) -> Optional[Dict]:
    """通过 Asset Store 搜索获取包信息"""
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
        resp = session.get(url, params={"q": query[:80], "rows": 5}, timeout=15)
        if resp.status_code != 200:
            return None
        data = resp.json()
        results = data.get("results") or data.get("items") or []
        if results:
            return {"searchResults": results[:3], "query": query}
    except Exception:
        pass
    return None


def try_assetstore_html(cookie: str, package_id: int, display_name: str) -> Optional[Dict]:
    """尝试从 Asset Store 页面 HTML 中解析 __NEXT_DATA__ 或描述文本"""
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
    # 尝试用 packageId 直接访问（部分站点支持 /packages/xxx/xxx-{id}）
    slug = re.sub(r"[^a-z0-9]+", "-", display_name.lower())[:50].strip("-")
    urls = [
        f"https://assetstore.unity.com/packages/-/-{package_id}",
        f"https://assetstore.unity.com/packages/0/{package_id}",
    ]
    for url in urls:
        try:
            resp = session.get(url, timeout=10, allow_redirects=True)
            if resp.status_code != 200:
                continue
            html = resp.text
            # 尝试解析 __NEXT_DATA__
            m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.+?)</script>', html)
            if m:
                try:
                    data = json.loads(m.group(1))
                    return data
                except Exception:
                    pass
            # 尝试解析其他内联 JSON
            m = re.search(r'"description"\s*:\s*"([^"]*(?:\\.[^"]*)*)"', html)
            if m:
                desc = m.group(1).encode().decode("unicode_escape")
                return {"description": desc, "source": "html_parse"}
        except Exception:
            continue
    return None


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
                result["detail"] = detail
                result["source"] = f"{base}/-/api/packages"
                break
        if result["detail"]:
            return result

    # 2. 尝试 assetstore.unity.com API
    detail = try_assetstore_api(cookie, pid)
    if detail:
        result["detail"] = detail
        result["source"] = "assetstore.unity.com api"
        return result

    # 3. 尝试 Asset Store 搜索（按 displayName）
    detail = try_assetstore_search(cookie, display_name)
    if detail:
        result["detail"] = detail
        result["source"] = "assetstore.unity.com search"
        return result

    # 4. 尝试从 HTML 页面解析
    detail = try_assetstore_html(cookie, pid, display_name)
    if detail:
        result["detail"] = detail
        result["source"] = "assetstore.unity.com html"
        return result

    result["error"] = "all sources failed"
    return result


def run_fetch(
    limit: int = 0,
    progress_callback: Optional[Callable[[int, int, Any, str, bool], None]] = None,
) -> tuple[int, int]:
    """执行抓取。progress_callback(current, total, package_id, display_name, success)"""
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
    success, failed = 0, 0
    total = len(purchases)
    for i, item in enumerate(purchases, 1):
        pid = item.get("packageId", "?")
        name = str(item.get("displayName") or "?")
        result = fetch_one_package(item, bearer, cookie, config, timeout)
        out_file = OUTPUT_DIR / f"{pid}.json"
        out_file.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        ok = bool(result.get("detail"))
        if ok:
            success += 1
        else:
            failed += 1
        if progress_callback:
            progress_callback(i, total, pid, name, ok)
        time.sleep(0.5)
    return success, failed


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="从 purchases_snapshot 抓取包详情")
    parser.add_argument("--limit", type=int, default=0, help="限制抓取数量，0=全部")
    args = parser.parse_args()

    print("Package Info Fetcher - 从 purchases_snapshot 抓取包详情")
    config = load_config()
    bearer = (config.get("bearer_token") or "").strip()
    cookie = load_cookie()
    if not cookie:
        print("[ERROR] cookie.txt 为空，请先配置 cookie")
        return 1
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

    def cb(i, total, pid, name, ok):
        status = "OK" if ok else "SKIP"
        print(f"[{status}] ({i}/{total}) {pid} {name}")

    success, failed = run_fetch(limit=limit, progress_callback=cb)
    print(f"[DONE] 成功={success}, 失败={failed}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
