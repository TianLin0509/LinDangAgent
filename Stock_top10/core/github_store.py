"""GitHub 持久化存储 — 将分析结果 JSON 存/取到 GitHub 统一数据仓库

所有项目共享同一个 repo（Stock_test）的 data-archive 分支，路径 cache/top10/。
未来其他项目可用 cache/xxx/ 各自的子目录，互不干扰。

secrets.toml 配置：
  GITHUB_TOKEN = "..."
  GITHUB_REPO = "TianLin0509/Stock_test"        # 统一数据仓库
  GITHUB_CACHE_BRANCH = "data-archive"           # 存储分支
"""

import base64
import json
import logging
import requests
from core.secrets_compat import _get_secret

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
_CACHE_PREFIX = "cache/top10"  # 所有 Top10 结果存在这个路径下


def _get_config() -> tuple[str, str, str]:
    """返回 (token, repo, branch)"""
    token = _get_secret("GITHUB_TOKEN", "")
    repo = _get_secret("GITHUB_REPO", "")
    branch = _get_secret("GITHUB_CACHE_BRANCH", "data-archive")
    return token, repo, branch


def _headers(token: str) -> dict:
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }


def is_enabled() -> bool:
    token, repo, _ = _get_config()
    return bool(token and repo)


def _ensure_branch(token: str, repo: str, branch: str) -> bool:
    """确保目标分支存在，不存在则基于 main 创建"""
    headers = _headers(token)
    api = f"{_GITHUB_API}/repos/{repo}"

    r = requests.get(f"{api}/branches/{branch}", headers=headers, timeout=10)
    if r.status_code == 200:
        return True

    # 获取 main SHA 创建新分支
    r_main = requests.get(f"{api}/git/ref/heads/main", headers=headers, timeout=10)
    if r_main.status_code != 200:
        return False
    sha = r_main.json()["object"]["sha"]
    r_create = requests.post(
        f"{api}/git/refs", headers=headers, timeout=10,
        json={"ref": f"refs/heads/{branch}", "sha": sha},
    )
    return r_create.status_code in (200, 201)


# ══════════════════════════════════════════════════════════════════════════════
# 上传（写入）
# ══════════════════════════════════════════════════════════════════════════════

def push_file(filename: str, content_bytes: bytes, commit_msg: str = "") -> bool:
    """上传到 GitHub repo 的 {_CACHE_PREFIX}/{filename}（data-archive 分支）"""
    token, repo, branch = _get_config()
    if not token or not repo:
        return False

    # 确保分支存在
    if not _ensure_branch(token, repo, branch):
        logger.warning("[github_store] 分支 %s 不存在且创建失败", branch)
        return False

    path = f"{_CACHE_PREFIX}/{filename}"
    url = f"{_GITHUB_API}/repos/{repo}/contents/{path}"
    headers = _headers(token)

    if not commit_msg:
        commit_msg = f"auto: update {filename}"

    # 查是否已存在（获取 sha）
    sha = None
    try:
        resp = requests.get(url, headers=headers, timeout=10,
                            params={"ref": branch})
        if resp.status_code == 200:
            sha = resp.json().get("sha")
    except Exception:
        pass

    payload = {
        "message": commit_msg,
        "content": base64.b64encode(content_bytes).decode("ascii"),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha

    try:
        resp = requests.put(url, headers=headers, json=payload, timeout=30)
        if resp.status_code in (200, 201):
            logger.info("[github_store] ✅ 已推送 %s → %s:%s", path, repo, branch)
            return True
        else:
            logger.warning("[github_store] 推送失败 %s: %d %s",
                           path, resp.status_code, resp.text[:200])
            return False
    except Exception as e:
        logger.warning("[github_store] 推送异常 %s: %s", path, e)
        return False


def push_json(filename: str, data: dict) -> bool:
    """将 dict 序列化为 JSON 后上传"""
    content = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    return push_file(filename, content.encode("utf-8"),
                     commit_msg=f"top10: {filename}")


# ══════════════════════════════════════════════════════════════════════════════
# 下载（读取）
# ══════════════════════════════════════════════════════════════════════════════

def pull_file(filename: str) -> bytes | None:
    """从 GitHub 下载 {_CACHE_PREFIX}/{filename}"""
    token, repo, branch = _get_config()
    if not token or not repo:
        return None

    path = f"{_CACHE_PREFIX}/{filename}"
    url = f"{_GITHUB_API}/repos/{repo}/contents/{path}"

    try:
        resp = requests.get(url, headers=_headers(token), timeout=15,
                            params={"ref": branch})
        if resp.status_code == 200:
            content_b64 = resp.json().get("content", "")
            return base64.b64decode(content_b64)
        elif resp.status_code == 404:
            return None
        else:
            logger.debug("[github_store] 读取失败 %s: %d", path, resp.status_code)
            return None
    except Exception as e:
        logger.debug("[github_store] 读取异常 %s: %s", path, e)
        return None


def pull_json(filename: str) -> dict | None:
    """从 GitHub 下载 JSON 并解析"""
    raw = pull_file(filename)
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# 列表（查询今日有哪些缓存文件）
# ══════════════════════════════════════════════════════════════════════════════

def list_today_files(date_str: str) -> list[str]:
    """列出 {_CACHE_PREFIX}/ 下以 date_str 开头的 .json 文件名"""
    token, repo, branch = _get_config()
    if not token or not repo:
        return []

    url = f"{_GITHUB_API}/repos/{repo}/contents/{_CACHE_PREFIX}"

    try:
        resp = requests.get(url, headers=_headers(token), timeout=15,
                            params={"ref": branch})
        if resp.status_code != 200:
            return []
        files = resp.json()
        return [
            f["name"] for f in files
            if isinstance(f, dict)
            and f.get("name", "").startswith(date_str)
            and f.get("name", "").endswith(".json")
            and "deep_status" not in f.get("name", "")
        ]
    except Exception as e:
        logger.debug("[github_store] 列目录失败: %s", e)
        return []
