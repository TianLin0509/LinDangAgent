"""分析归档 — 保存完整分析结果供回测使用

每次分析完成时自动归档，带质量校验（截断/失败的分析不保存）。
归档文件: data/archive/{date}_{code6}_{user}.json
索引文件: data/archive/_index.jsonl (JSON Lines, pandas 一行加载)
"""

import json
import re
import threading
from datetime import datetime
from pathlib import Path

ARCHIVE_DIR = Path(__file__).parent.parent / "data" / "archive"
INDEX_FILE = ARCHIVE_DIR / "_index.jsonl"
_lock = threading.Lock()

# ── 内存索引缓存（启动时加载，增量更新）──────────────────────────────
_index_cache: list[dict] = []         # 全量索引条目
_index_by_code: dict[str, list[dict]] = {}  # stock_code → [entries]（按 ts 排序）
_index_loaded = False


def _load_index_cache():
    """加载 _index.jsonl 到内存，按 stock_code 建立倒排索引"""
    global _index_cache, _index_by_code, _index_loaded
    with _lock:
        _index_cache.clear()
        _index_by_code.clear()
        if not INDEX_FILE.exists():
            _index_loaded = True
            return
        for line in INDEX_FILE.read_text(encoding="utf-8").strip().split("\n"):
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            _index_cache.append(entry)
            code = entry.get("stock_code", "")
            if code:
                _index_by_code.setdefault(code, []).append(entry)
        _index_loaded = True


def _ensure_index_loaded():
    global _index_loaded
    if not _index_loaded:
        _load_index_cache()


def _add_to_index_cache(entry: dict):
    """增量更新内存索引"""
    _ensure_index_loaded()
    _index_cache.append(entry)
    code = entry.get("stock_code", "")
    if code:
        _index_by_code.setdefault(code, []).append(entry)

# ── 质量校验：确认分析输出完整，未被截断 ──────────────────────────────
#
# 核心规则：每项分析的最后一节必须是裁决/评分段。
# 只检查文本末尾区域（最后500字）是否包含裁决关键词，
# 防止中间段碰巧命中关键词导致截断内容被误判为完整。

# 每种分析类型的"裁决段标志"——必须在末尾500字内命中至少一个
_VERDICT_MARKERS = {
    "comprehensive": [
        r"<<<END_SCORES>>>",
        r"操作评级",
        r"三情景推演",
        r"综合研判",
    ],
    "expectation": [
        r"预期差裁决",
        r"综合评分[：:]\s*\d+\.?\d*\s*/\s*10",
        r"筛选结论[：:].*[✅❌⚠️]",
        r"核心理由",
    ],
    "trend": [
        r"技术面裁决",
        r"综合评分[：:]\s*\d+\.?\d*\s*/\s*10",
        r"筛选结论[：:].*[✅❌⚠️]",
        r"核心理由",
    ],
    "fundamentals": [
        r"基本面裁决",
        r"综合评分[：:]\s*\d+\.?\d*\s*/\s*10",
        r"筛选结论[：:].*[✅❌⚠️]",
        r"核心理由",
    ],
    "sentiment": [
        r"舆情裁决",
        r"情绪位置[：:]",
        r"操作指引[：:]",
        r"核心判断",
    ],
    "sector": [
        r"板块操作决策",
        r"当前板块位置的操作建议",
        r"板块退潮.*独立",
        r"最优选择",
    ],
    "holders": [
        r"综合评估",
        r"最关键的.*信号",
        r"股东风险等级[：:]",
        r"机构态度[：:]",
    ],
}


def _is_complete(key: str, text: str) -> bool:
    """检查分析文本是否完整（裁决段必须出现在末尾区域）

    规则：
    1. 最低字数 ≥ 500字（低于此肯定是截断）
    2. 不以失败标记开头
    3. 末尾500字内必须命中至少一个裁决关键词
    """
    if not text or len(text) < 500:
        return False
    # 失败标记
    if text.startswith("⚠️") and ("失败" in text[:50] or "异常" in text[:50]):
        return False

    # 在末尾500字中检查裁决标志
    tail = text[-500:]
    markers = _VERDICT_MARKERS.get(key, [])
    if not markers:
        # 未知分析类型，仅靠字数兜底
        return len(text) >= 1000

    return any(re.search(m, tail) for m in markers)


# ── 归档保存 ─────────────────────────────────────────────────────────

def save_archive(session_state: dict):
    """将当前完整分析结果归档保存（仅保存通过质量校验的分析）"""
    stock_name = session_state.get("stock_name", "")
    stock_code = session_state.get("stock_code", "")
    if not stock_name or not stock_code:
        return None

    analyses = session_state.get("analyses", {})

    # 只归档通过质量校验的分析
    valid_keys = [k for k in ["comprehensive", "expectation", "trend", "fundamentals",
                               "sentiment", "sector", "holders"]
                  if analyses.get(k) and _is_complete(k, analyses[k])]
    if not valid_keys:
        return None

    now = datetime.now()
    code6 = stock_code.split(".")[0] if "." in stock_code else stock_code
    username = session_state.get("current_user", "anonymous")

    # 同一天 + 同一股票 + 同一用户 → 更新而非新建
    archive_key = f"{code6}_{username}"
    last_ts = session_state.get("_last_archive", {}).get(archive_key, "")
    if last_ts:
        try:
            last_dt = datetime.fromisoformat(last_ts)
            if last_dt.date() == now.date():
                return _update_archive(session_state, valid_keys, archive_key)
        except (ValueError, TypeError):
            pass

    # 股价快照
    info = dict(session_state.get("stock_info", {}))
    import pandas as pd
    price_df = session_state.get("price_df", pd.DataFrame())
    price_snapshot = {}
    if not price_df.empty:
        try:
            latest = price_df.iloc[-1]
            def _safe_float(val, default=0):
                try: return float(val) if val is not None else default
                except (ValueError, TypeError): return default
            price_snapshot = {
                "date": str(latest.get("日期", "")),
                "open": _safe_float(latest.get("开盘")),
                "high": _safe_float(latest.get("最高")),
                "low": _safe_float(latest.get("最低")),
                "close": _safe_float(latest.get("收盘")),
                "volume": _safe_float(latest.get("成交量")),
                "pct_chg": _safe_float(latest.get("涨跌幅")),
            }
        except Exception:
            pass  # 价格快照非关键，失败不影响归档

    # MoE结果（也做质量校验）
    moe = session_state.get("moe_results", {})
    moe_data = None
    if moe.get("done"):
        ceo = moe.get("ceo", "")
        # CEO裁决必须包含操作评级才算完整
        if ceo and re.search(r"操作评级|强烈买入|买入|谨慎介入|持有观察|减持|回避", ceo):
            moe_data = {
                "roles": dict(moe.get("roles", {})),
                "ceo": ceo,
            }

    # 完整归档记录
    record = {
        "archive_ts": now.isoformat(timespec="seconds"),
        "archive_date": now.strftime("%Y-%m-%d"),
        "username": username,
        "model": session_state.get("selected_model", ""),
        "stock_code": stock_code,
        "stock_name": stock_name,
        "stock_info": info,
        "price_snapshot": price_snapshot,
        "analyses": {k: analyses[k] for k in valid_keys},
        "analyses_validated": valid_keys,
        "moe_results": moe_data,
        "report_summary": session_state.get("report_summary", ""),
        "blue_team_report": session_state.get("blue_team_report", ""),
        "final_verdict": session_state.get("final_verdict", ""),
        "final_scores": session_state.get("final_scores"),
    }

    # 写入归档文件
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{now.strftime('%Y%m%d_%H%M%S')}_{code6}_{username}.json"
    filepath = ARCHIVE_DIR / filename

    with _lock:
        filepath.write_text(
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _append_index(filename, record, info, price_snapshot, valid_keys, moe_data)

    # 记录归档时间，防重复
    if "_last_archive" not in session_state:
        session_state["_last_archive"] = {}
    session_state["_last_archive"][archive_key] = now.isoformat()
    session_state["_last_archive_file"] = filename

    return filename


def _update_archive(session_state, new_valid_keys, archive_key):
    """更新最近一次归档（同一股票5分钟内有新分析完成时）"""
    last_file = session_state.get("_last_archive_file", "")
    if not last_file:
        return None

    filepath = ARCHIVE_DIR / last_file
    if not filepath.exists():
        return None

    with _lock:
        try:
            record = json.loads(filepath.read_text(encoding="utf-8"))
        except Exception:
            return None

        analyses = session_state.get("analyses", {})
        for k in new_valid_keys:
            record["analyses"][k] = analyses[k]
            if k not in record.get("analyses_validated", []):
                record["analyses_validated"].append(k)

        # 更新MoE
        moe = session_state.get("moe_results", {})
        if moe.get("done") and not record.get("moe_results"):
            ceo = moe.get("ceo", "")
            if ceo and re.search(r"操作评级|强烈买入|买入|谨慎介入|持有观察|减持|回避", ceo):
                record["moe_results"] = {
                    "roles": dict(moe.get("roles", {})),
                    "ceo": ceo,
                }

        record["archive_ts"] = datetime.now().isoformat(timespec="seconds")
        filepath.write_text(
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return last_file


def _append_index(filename, record, info, price_snapshot, valid_keys, moe_data):
    """追加索引行（同时更新内存缓存）"""
    index_entry = {
        "file": filename,
        "ts": record["archive_ts"],
        "date": record["archive_date"],
        "username": record["username"],
        "model": record["model"],
        "stock_code": record["stock_code"],
        "stock_name": record["stock_name"],
        "close": price_snapshot.get("close", 0),
        "pe_ttm": info.get("市盈率TTM", ""),
        "pb": info.get("市净率PB", ""),
        "analyses_done": valid_keys,
        "has_moe": moe_data is not None,
    }
    with open(INDEX_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(index_entry, ensure_ascii=False) + "\n")
    _add_to_index_cache(index_entry)


# ── 读取接口 ─────────────────────────────────────────────────────────

def load_index():
    """加载索引为 pandas DataFrame（使用内存缓存）"""
    import pandas as pd
    _ensure_index_loaded()
    return pd.DataFrame(_index_cache) if _index_cache else pd.DataFrame()


def load_archive(filename: str) -> dict:
    """加载单个归档文件"""
    path = ARCHIVE_DIR / filename
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def cleanup_expired(days: int = 30):
    """清理超过 days 天的归档文件，保留索引记录（标记 expired）"""
    import time as _time
    if not ARCHIVE_DIR.exists():
        return 0
    cutoff_ts = _time.time() - days * 86400
    removed = 0
    for f in ARCHIVE_DIR.glob("*.json"):
        if f.name.startswith("_"):
            continue
        try:
            if f.stat().st_mtime < cutoff_ts:
                f.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def get_archive_stats() -> dict:
    """归档统计"""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    files = [f for f in ARCHIVE_DIR.glob("*.json")]
    total_size = sum(f.stat().st_size for f in files) if files else 0
    return {
        "count": len(files),
        "size_mb": round(total_size / 1024 / 1024, 2),
    }


# ── 缓存查询（替代 shared_cache）─────────────────────────────────────

def _get_cutoff() -> float:
    """获取当前周期的起始时间戳（以每天 ARCHIVE_CUTOFF_HOUR 为分界）"""
    from config import ARCHIVE_CUTOFF_HOUR
    now = datetime.now()
    today_7pm = now.replace(hour=ARCHIVE_CUTOFF_HOUR, minute=0, second=0, microsecond=0)
    if now >= today_7pm:
        return today_7pm.timestamp()
    else:
        from datetime import timedelta
        return (today_7pm - timedelta(days=1)).timestamp()


def find_recent(stock_code: str) -> dict | None:
    """查找该股票在当前周期内（最近19:00后）的最新归档，返回索引条目或 None
    使用内存索引 O(k) 查找（k = 该股票归档数），而非 O(n) 全量扫描。
    """
    _ensure_index_loaded()
    entries = _index_by_code.get(stock_code, [])
    if not entries:
        return None
    cutoff = _get_cutoff()
    best = None
    for entry in entries:
        try:
            ts = datetime.fromisoformat(entry["ts"]).timestamp()
        except (ValueError, KeyError):
            continue
        if ts >= cutoff and (best is None or ts > best["_ts"]):
            best = {**entry, "_ts": ts}
    if best:
        best.pop("_ts", None)
    return best


def find_today_others(stock_code: str, exclude_user: str = "") -> list[dict]:
    """查找当前周期内（最近19:00后）该股票其他用户的归档"""
    _ensure_index_loaded()
    entries = _index_by_code.get(stock_code, [])
    if not entries:
        return []
    cutoff = _get_cutoff()
    results = []
    for entry in entries:
        try:
            ts = datetime.fromisoformat(entry["ts"]).timestamp()
        except (ValueError, KeyError):
            continue
        if ts < cutoff:
            continue
        if exclude_user and entry.get("username") == exclude_user:
            continue
        results.append(entry)
    results.sort(key=lambda x: x.get("ts", ""), reverse=True)
    return results


def save_standalone(stock_code: str, stock_name: str, model_name: str,
                    username: str, analyses: dict,
                    moe_results: dict = None, stock_info: dict = None):
    """独立保存归档（不依赖 session_state，供 Top10 deep_runner 使用）"""
    if not analyses:
        return None
    valid_keys = [k for k in ["comprehensive", "expectation", "trend", "fundamentals",
                               "sentiment", "sector", "holders"]
                  if analyses.get(k) and _is_complete(k, analyses[k])]
    if not valid_keys:
        return None

    now = datetime.now()
    code6 = stock_code.split(".")[0] if "." in stock_code else stock_code
    info = dict(stock_info) if stock_info else {}

    moe_data = None
    if moe_results and moe_results.get("done"):
        ceo = moe_results.get("ceo", "")
        if ceo and re.search(r"操作评级|强烈买入|买入|谨慎介入|持有观察|减持|回避", ceo):
            moe_data = {"roles": dict(moe_results.get("roles", {})), "ceo": ceo}

    record = {
        "archive_ts": now.isoformat(timespec="seconds"),
        "archive_date": now.strftime("%Y-%m-%d"),
        "username": username,
        "model": model_name,
        "stock_code": stock_code,
        "stock_name": stock_name,
        "stock_info": info,
        "price_snapshot": {},
        "analyses": {k: analyses[k] for k in valid_keys},
        "analyses_validated": valid_keys,
        "moe_results": moe_data,
    }

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{now.strftime('%Y%m%d_%H%M%S')}_{code6}_{username}.json"
    filepath = ARCHIVE_DIR / filename

    with _lock:
        filepath.write_text(
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _append_index(filename, record, info, {}, valid_keys, moe_data)

    return filename
