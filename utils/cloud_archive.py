"""云端归档同步 — 通过 GitHub data-archive 分支持久化存储

与 Stock_test 共享同一个 data-archive 分支，两个 App 的归档数据互通。

启动时：从 GitHub 拉取最新归档到本地（增量同步，只拉缺失文件）
保存时：save_archive 完成后异步推送到 GitHub（后台线程，不阻塞UI）
"""

import json
import base64
import logging
import threading
import time as _time
from pathlib import Path

logger = logging.getLogger(__name__)

ARCHIVE_DIR = Path(__file__).parent.parent / "data" / "archive"

_sync_lock = threading.Lock()
_initial_sync_done = False


def _get_config() -> dict:
    """从 secrets 读取 GitHub 配置"""
    try:
        import streamlit as st
        return {
            "token": st.secrets.get("GITHUB_TOKEN", ""),
            "repo": st.secrets.get("GITHUB_ARCHIVE_REPO", "TianLin0509/Stock_test"),
            "branch": st.secrets.get("GITHUB_ARCHIVE_BRANCH", "data-archive"),
        }
    except Exception:
        return {"token": "", "repo": "", "branch": "data-archive"}


def _headers(token: str) -> dict:
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }


def _ensure_branch(token: str, repo: str, branch: str) -> bool:
    """确保 data-archive 分支存在，不存在则从 main 创建"""
    import requests
    api = f"https://api.github.com/repos/{repo}"
    h = _headers(token)

    r = requests.get(f"{api}/branches/{branch}", headers=h, timeout=15)
    if r.status_code == 200:
        return True

    # 从 main 创建
    r_main = requests.get(f"{api}/git/ref/heads/main", headers=h, timeout=15)
    if r_main.status_code != 200:
        logger.warning("[cloud] 无法获取 main 分支: %s", r_main.text[:200])
        return False
    sha = r_main.json()["object"]["sha"]
    r_create = requests.post(
        f"{api}/git/refs", headers=h, timeout=15,
        json={"ref": f"refs/heads/{branch}", "sha": sha},
    )
    return r_create.status_code in (200, 201)


# ══════════════════════════════════════════════════════════════════════════════
# 启动时拉取：从 GitHub 增量同步归档到本地
# ══════════════════════════════════════════════════════════════════════════════

def pull_from_github():
    """从 data-archive 分支拉取所有归档文件（增量：只下载本地缺失的）"""
    import requests

    cfg = _get_config()
    token, repo, branch = cfg["token"], cfg["repo"], cfg["branch"]
    if not token:
        logger.debug("[cloud] GITHUB_TOKEN 未配置，跳过云端同步")
        return 0

    api = f"https://api.github.com/repos/{repo}"
    h = _headers(token)

    # 列出分支上 data/archive/ 目录的文件
    r = requests.get(
        f"{api}/contents/data/archive?ref={branch}",
        headers=h, timeout=20,
    )
    if r.status_code == 404:
        logger.debug("[cloud] data-archive 分支或目录不存在，跳过")
        return 0
    if r.status_code != 200:
        logger.warning("[cloud] 拉取文件列表失败: %s", r.status_code)
        return 0

    remote_files = r.json()
    if not isinstance(remote_files, list):
        return 0

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    local_files = {f.name for f in ARCHIVE_DIR.iterdir() if f.is_file()}

    downloaded = 0
    for item in remote_files:
        fname = item.get("name", "")
        if not fname.endswith((".json", ".jsonl")):
            continue
        if fname in local_files:
            continue  # 本地已有，跳过

        # 下载文件内容
        try:
            r_file = requests.get(item["download_url"], timeout=30)
            if r_file.status_code == 200:
                (ARCHIVE_DIR / fname).write_bytes(r_file.content)
                downloaded += 1
        except Exception as e:
            logger.debug("[cloud] 下载 %s 失败: %s", fname, e)

        if downloaded % 10 == 0 and downloaded > 0:
            _time.sleep(0.3)  # 避免限流

    if downloaded > 0:
        logger.info("[cloud] 从 GitHub 拉取了 %d 个归档文件", downloaded)

        # 重建索引缓存
        try:
            from utils.archive import _load_index_cache
            _load_index_cache()
        except Exception:
            pass

    return downloaded


def sync_on_startup():
    """启动时双向同步（只执行一次，后台线程）
    1. 先从 GitHub 拉取缺失文件（云→本地）
    2. 再把本地有但云端没有的文件推上去（本地→云）
    """
    global _initial_sync_done
    if _initial_sync_done:
        return

    def _do_sync():
        global _initial_sync_done
        try:
            pull_from_github()
            _push_missing_to_cloud()
        except Exception as e:
            logger.debug("[cloud] 启动同步失败: %s", e)
        finally:
            _initial_sync_done = True

    t = threading.Thread(target=_do_sync, daemon=True)
    t.start()


def _push_missing_to_cloud():
    """把本地有但云端没有的归档文件推到 GitHub（补漏）"""
    import requests

    cfg = _get_config()
    token, repo, branch = cfg["token"], cfg["repo"], cfg["branch"]
    if not token:
        return

    api = f"https://api.github.com/repos/{repo}"
    h = _headers(token)

    # 获取云端文件列表
    try:
        r = requests.get(
            f"{api}/contents/data/archive?ref={branch}",
            headers=h, timeout=20,
        )
        if r.status_code != 200:
            return
        remote_names = {item["name"] for item in r.json() if isinstance(item, dict)}
    except Exception:
        return

    # 找到本地有但云端没有的
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    local_files = [f for f in ARCHIVE_DIR.iterdir()
                   if f.is_file() and f.suffix in (".json", ".jsonl")]
    missing = [f for f in local_files if f.name not in remote_names]

    if not missing:
        return

    logger.info("[cloud] 发现 %d 个本地文件未上传，开始补推...", len(missing))
    pushed = 0
    for f in missing:
        try:
            _push_single_file(f.name)
            pushed += 1
            if pushed % 5 == 0:
                _time.sleep(1)  # 避免 GitHub 限流
        except Exception as e:
            logger.debug("[cloud] 补推 %s 失败: %s", f.name, e)

    if pushed:
        logger.info("[cloud] 补推完成，成功 %d/%d", pushed, len(missing))


# ══════════════════════════════════════════════════════════════════════════════
# 保存时推送：分析完成后异步推送到 GitHub
# ══════════════════════════════════════════════════════════════════════════════

def push_file_async(filename: str):
    """异步推送单个归档文件到 GitHub（后台线程，不阻塞UI）"""
    t = threading.Thread(target=_push_single_file, args=(filename,), daemon=True)
    t.start()


def _push_single_file(filename: str, _retry: int = 0):
    """推送单个文件到 data-archive 分支（含重试）"""
    import requests

    cfg = _get_config()
    token, repo, branch = cfg["token"], cfg["repo"], cfg["branch"]
    if not token:
        return

    filepath = ARCHIVE_DIR / filename
    if not filepath.exists():
        return

    with _sync_lock:
        try:
            if not _ensure_branch(token, repo, branch):
                logger.warning("[cloud] 无法确保 data-archive 分支存在")
                return

            api = f"https://api.github.com/repos/{repo}"
            h = _headers(token)
            path_in_repo = f"data/archive/{filename}"
            content_b64 = base64.b64encode(filepath.read_bytes()).decode()

            # 检查文件是否已存在
            r_get = requests.get(
                f"{api}/contents/{path_in_repo}?ref={branch}",
                headers=h, timeout=15,
            )
            payload = {
                "message": f"archive: {filename}",
                "content": content_b64,
                "branch": branch,
            }
            if r_get.status_code == 200:
                existing_sha = r_get.json().get("sha", "")
                existing_content = r_get.json().get("content", "").replace("\n", "")
                if existing_content == content_b64:
                    return  # 内容未变
                payload["sha"] = existing_sha

            r_put = requests.put(
                f"{api}/contents/{path_in_repo}",
                headers=h, timeout=30,
                json=payload,
            )
            if r_put.status_code in (200, 201):
                logger.debug("[cloud] 已推送 %s", filename)
            elif r_put.status_code == 409 and _retry < 2:
                # SHA 冲突（并发更新）→ 重试
                _time.sleep(1)
                _push_single_file(filename, _retry=_retry + 1)
                return
            else:
                logger.warning("[cloud] 推送 %s 失败: %s", filename, r_put.status_code)

            # 同步推送索引文件
            _push_index_file(token, repo, branch)

        except requests.exceptions.ConnectionError:
            if _retry < 2:
                _time.sleep(2)
                _push_single_file(filename, _retry=_retry + 1)
            else:
                logger.warning("[cloud] 推送 %s 网络失败（已重试）", filename)
        except Exception as e:
            logger.debug("[cloud] 推送异常: %s", e)


def _push_index_file(token: str, repo: str, branch: str):
    """推送 _index.jsonl 到 GitHub"""
    import requests

    index_path = ARCHIVE_DIR / "_index.jsonl"
    if not index_path.exists():
        return

    api = f"https://api.github.com/repos/{repo}"
    h = _headers(token)
    path_in_repo = "data/archive/_index.jsonl"
    content_b64 = base64.b64encode(index_path.read_bytes()).decode()

    r_get = requests.get(
        f"{api}/contents/{path_in_repo}?ref={branch}",
        headers=h, timeout=15,
    )
    payload = {
        "message": "update index",
        "content": content_b64,
        "branch": branch,
    }
    if r_get.status_code == 200:
        payload["sha"] = r_get.json().get("sha", "")

    requests.put(
        f"{api}/contents/{path_in_repo}",
        headers=h, timeout=30,
        json=payload,
    )


# ══════════════════════════════════════════════════════════════════════════════
# user_data 同步（用户数据持久化）
# ══════════════════════════════════════════════════════════════════════════════

USER_DATA_DIR = Path(__file__).parent.parent / "user_data"


def push_user_data_async(username: str):
    """异步推送用户数据到 GitHub"""
    t = threading.Thread(target=_push_user_file, args=(username,), daemon=True)
    t.start()


def _push_user_file(username: str):
    """推送单个用户JSON到 data-archive 分支"""
    import requests

    cfg = _get_config()
    token, repo, branch = cfg["token"], cfg["repo"], cfg["branch"]
    if not token:
        return

    filepath = USER_DATA_DIR / f"{username}.json"
    if not filepath.exists():
        return

    with _sync_lock:
        try:
            if not _ensure_branch(token, repo, branch):
                return

            api = f"https://api.github.com/repos/{repo}"
            h = _headers(token)
            path_in_repo = f"user_data/{username}.json"
            content_b64 = base64.b64encode(filepath.read_bytes()).decode()

            r_get = requests.get(
                f"{api}/contents/{path_in_repo}?ref={branch}",
                headers=h, timeout=15,
            )
            payload = {
                "message": f"user: {username}",
                "content": content_b64,
                "branch": branch,
            }
            if r_get.status_code == 200:
                existing_content = r_get.json().get("content", "").replace("\n", "")
                if existing_content == content_b64:
                    return
                payload["sha"] = r_get.json().get("sha", "")

            requests.put(
                f"{api}/contents/{path_in_repo}",
                headers=h, timeout=30,
                json=payload,
            )
        except Exception as e:
            logger.debug("[cloud] 用户数据推送异常: %s", e)


# ══════════════════════════════════════════════════════════════════════════════
# 玄学炒股缓存同步
# ══════════════════════════════════════════════════════════════════════════════

MYSTIC_DIR = Path(__file__).parent.parent / "data" / "mystic"


def push_mystic_async(today_key: str):
    """异步推送玄学缓存到 GitHub"""
    t = threading.Thread(target=_push_mystic_file, args=(today_key,), daemon=True)
    t.start()


def _push_mystic_file(today_key: str):
    """推送单个玄学缓存到 data-archive 分支"""
    import requests

    cfg = _get_config()
    token, repo, branch = cfg["token"], cfg["repo"], cfg["branch"]
    if not token:
        return

    filepath = MYSTIC_DIR / f"{today_key}.json"
    if not filepath.exists():
        return

    with _sync_lock:
        try:
            if not _ensure_branch(token, repo, branch):
                return

            api = f"https://api.github.com/repos/{repo}"
            h = _headers(token)
            path_in_repo = f"data/mystic/{today_key}.json"
            content_b64 = base64.b64encode(filepath.read_bytes()).decode()

            r_get = requests.get(
                f"{api}/contents/{path_in_repo}?ref={branch}",
                headers=h, timeout=15,
            )
            payload = {
                "message": f"mystic: {today_key}",
                "content": content_b64,
                "branch": branch,
            }
            if r_get.status_code == 200:
                existing_content = r_get.json().get("content", "").replace("\n", "")
                if existing_content == content_b64:
                    return
                payload["sha"] = r_get.json().get("sha", "")

            r_put = requests.put(
                f"{api}/contents/{path_in_repo}",
                headers=h, timeout=30,
                json=payload,
            )
            if r_put.status_code in (200, 201):
                logger.debug("[cloud] 已推送玄学缓存 %s", today_key)
            else:
                logger.warning("[cloud] 玄学缓存推送失败: %s", r_put.status_code)
        except Exception as e:
            logger.debug("[cloud] 玄学缓存推送异常: %s", e)


def pull_mystic(today_key: str) -> bool:
    """从 GitHub 拉取今日玄学缓存（本地未命中时调用）"""
    import requests

    cfg = _get_config()
    token, repo, branch = cfg["token"], cfg["repo"], cfg["branch"]
    if not token:
        return False

    try:
        api = f"https://api.github.com/repos/{repo}"
        h = _headers(token)
        path_in_repo = f"data/mystic/{today_key}.json"

        r = requests.get(
            f"{api}/contents/{path_in_repo}?ref={branch}",
            headers=h, timeout=15,
        )
        if r.status_code != 200:
            return False

        content = base64.b64decode(r.json()["content"]).decode("utf-8")
        MYSTIC_DIR.mkdir(parents=True, exist_ok=True)
        (MYSTIC_DIR / f"{today_key}.json").write_text(content, encoding="utf-8")
        logger.debug("[cloud] 从 GitHub 恢复玄学缓存: %s", today_key)
        return True
    except Exception as e:
        logger.debug("[cloud] 拉取玄学缓存失败: %s", e)
        return False


# ══════════════════════════════════════════════════════════════════════════════
# user_data 同步（用户数据持久化）
# ══════════════════════════════════════════════════════════════════════════════


def pull_user_data(username: str):
    """从 GitHub 拉取用户数据（启动时 load_user 未找到本地文件时调用）"""
    import requests

    cfg = _get_config()
    token, repo, branch = cfg["token"], cfg["repo"], cfg["branch"]
    if not token:
        return False

    try:
        api = f"https://api.github.com/repos/{repo}"
        h = _headers(token)
        path_in_repo = f"user_data/{username}.json"

        r = requests.get(
            f"{api}/contents/{path_in_repo}?ref={branch}",
            headers=h, timeout=15,
        )
        if r.status_code != 200:
            return False

        content = base64.b64decode(r.json()["content"]).decode("utf-8")
        USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        (USER_DATA_DIR / f"{username}.json").write_text(content, encoding="utf-8")
        logger.debug("[cloud] 从 GitHub 恢复用户数据: %s", username)
        return True
    except Exception as e:
        logger.debug("[cloud] 拉取用户数据失败: %s", e)
        return False
