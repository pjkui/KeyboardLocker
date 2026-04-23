# -*- coding: utf-8 -*-
"""
GitHub Release 更新检查
---------------------------------
- 通过 GitHub Releases API 查询最新版本（latest 接口，自动跳过 draft / pre-release）
- 与本地 __version__ 比较，宽松 SemVer 解析（v1.2.3 / 1.2.3 / 1.2.3-beta）
- 纯 urllib 实现，不引入第三方依赖
- 5 秒超时，失败静默
"""

import json
import re
import socket
import urllib.request
import urllib.error

REPO_OWNER = "pjkui"
REPO_NAME = "KeyboardLocker"
API_URL = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO_OWNER}/{REPO_NAME}/releases"

USER_AGENT = "KeyboardLocker-UpdateChecker"
HTTP_TIMEOUT = 5  # 秒


# ---------------- 版本号解析 ----------------
_VERSION_RE = re.compile(
    r"^\s*v?(\d+)\.(\d+)\.(\d+)(?:[.\-+]?([A-Za-z0-9.\-]+))?\s*$"
)


def parse_version(s):
    """
    把 'v1.2.3' / '1.2.3' / '1.2.3-beta.1' 解析为可比较的元组。
    pre-release 视为比正式版更小（1.2.3-beta < 1.2.3）。
    无法解析时返回 None。
    """
    if not s:
        return None
    m = _VERSION_RE.match(s)
    if not m:
        return None
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    pre = m.group(4) or ""
    # 没有 pre 后缀的版本视为更"大"，所以在比较元组里用 (1, ...)；有则用 (0, pre_str)
    pre_key = (1,) if pre == "" else (0, pre.lower())
    return (major, minor, patch, pre_key)


def is_newer(remote, local):
    """remote > local 返回 True；解析失败时安全返回 False（不打扰用户）。"""
    rv = parse_version(remote)
    lv = parse_version(local)
    if rv is None or lv is None:
        return False
    return rv > lv


# ---------------- GitHub API 调用 ----------------
def fetch_latest_release(timeout=HTTP_TIMEOUT):
    """
    调用 GitHub API 获取最新 Release。
    返回 dict 形如：
      {"tag": "v1.2.3", "name": "...", "url": "https://...", "body": "..."}
    失败返回 None（网络问题、限流、无 release 等都视为失败，静默）。
    """
    req = urllib.request.Request(
        API_URL,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, urllib.error.HTTPError, socket.timeout, TimeoutError, ValueError, OSError):
        return None

    tag = data.get("tag_name") or ""
    if not tag:
        return None
    return {
        "tag":  tag,
        "name": data.get("name") or tag,
        "url":  data.get("html_url") or RELEASES_URL,
        "body": (data.get("body") or "").strip(),
    }


# ---------------- 上层入口 ----------------
def check_update(local_version):
    """
    检查是否有新版本。返回 dict：
      {"has_update": bool,
       "local": "1.0.1",
       "latest_tag": "v1.0.2" 或 None,
       "release": <fetch_latest_release 返回的 dict 或 None>,
       "error": None 或 简短错误描述}
    """
    info = fetch_latest_release()
    if info is None:
        return {
            "has_update": False,
            "local": local_version,
            "latest_tag": None,
            "release": None,
            "error": "无法访问 GitHub Release（网络或限流）",
        }

    has = is_newer(info["tag"], local_version)
    return {
        "has_update": has,
        "local": local_version,
        "latest_tag": info["tag"],
        "release": info,
        "error": None,
    }
