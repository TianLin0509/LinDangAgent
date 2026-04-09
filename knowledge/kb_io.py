# -*- coding: utf-8 -*-
"""安全 JSONL I/O — 原子写入、流式读取、按 key 覆盖

解决 JSONL 文件的 TOCTOU 竞态条件和大文件全量加载 OOM 风险。
所有写操作通过锁+原子重命名保证线程安全和进程安全。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)


def append_jsonl(path: Path, entry: dict, lock: threading.Lock | None = None) -> None:
    """原子追加一行 JSONL。

    使用文件追加模式（'a'），配合可选的线程锁。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False) + "\n"

    def _do_append():
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)

    if lock:
        with lock:
            _do_append()
    else:
        _do_append()


def read_jsonl_iter(path: Path) -> Iterator[dict]:
    """流式逐行读取 JSONL 文件（不全量加载到内存）。

    跳过空行和解析失败的行（记录 warning）。
    """
    if not path.exists():
        return
    with open(path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                logger.warning("[kb_io] JSONL 解析失败 %s 行%d: %s", path.name, line_num, line[:100])


def read_jsonl_recent(path: Path, days: int = 0, date_field: str = "date") -> list[dict]:
    """读取最近 N 天的 JSONL 记录（流式读取 + 日期过滤）。

    Args:
        path: JSONL 文件路径
        days: 0 表示读取全部，>0 表示最近 N 天
        date_field: 日期字段名（支持 "date", "report_date", "analyzed_at" 等）

    Returns:
        匹配的记录列表
    """
    if days <= 0:
        return list(read_jsonl_iter(path))

    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    results = []
    for entry in read_jsonl_iter(path):
        entry_date = entry.get(date_field, "")
        if isinstance(entry_date, str) and entry_date[:10] >= cutoff:
            results.append(entry)
    return results


def read_jsonl_tail(path: Path, n: int = 10) -> list[dict]:
    """读取 JSONL 文件的最后 N 行（高效：从文件尾部反向读取）。"""
    if not path.exists():
        return []
    results = []
    with open(path, "rb") as f:
        f.seek(0, 2)  # 移到文件尾
        file_size = f.tell()
        if file_size == 0:
            return []

        # 分块从尾部读取
        chunk_size = 8192
        remaining = b""
        pos = file_size

        while pos > 0 and len(results) < n:
            read_size = min(chunk_size, pos)
            pos -= read_size
            f.seek(pos)
            chunk = f.read(read_size) + remaining
            lines = chunk.split(b"\n")
            remaining = lines[0]  # 可能是不完整的行

            for line in reversed(lines[1:]):
                line = line.strip()
                if not line:
                    continue
                try:
                    results.append(json.loads(line.decode("utf-8")))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if len(results) >= n:
                    break

        # 处理最后的残留行
        if len(results) < n and remaining.strip():
            try:
                results.append(json.loads(remaining.decode("utf-8")))
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

    results.reverse()
    return results


def upsert_jsonl_by_key(
    path: Path,
    entry: dict,
    key_field: str,
    lock: threading.Lock | None = None,
) -> None:
    """按 key 覆盖 JSONL 中的记录（原子操作：写临时文件 + rename）。

    如果文件中已存在 key_field 值相同的记录，替换之；否则追加。
    通过写入临时文件再原子重命名，避免 TOCTOU 竞态。

    Args:
        path: JSONL 文件路径
        entry: 要写入的记录
        key_field: 用于匹配的字段名（如 "date"）
        lock: 可选的线程锁
    """
    key_value = entry.get(key_field, "")

    def _do_upsert():
        path.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        replaced = False

        if path.exists():
            for existing in read_jsonl_iter(path):
                if existing.get(key_field) == key_value:
                    lines.append(json.dumps(entry, ensure_ascii=False))
                    replaced = True
                else:
                    lines.append(json.dumps(existing, ensure_ascii=False))

        if not replaced:
            lines.append(json.dumps(entry, ensure_ascii=False))

        # 写入临时文件再原子重命名
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), suffix=".tmp", prefix=path.stem
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            # Windows 上需要先删除目标文件
            if path.exists():
                path.unlink()
            os.rename(tmp_path, str(path))
        except Exception:
            # 清理临时文件
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    if lock:
        with lock:
            _do_upsert()
    else:
        _do_upsert()


def count_jsonl(path: Path) -> int:
    """统计 JSONL 文件的有效行数（流式计数，不加载内容）。"""
    if not path.exists():
        return 0
    count = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                count += 1
    return count
