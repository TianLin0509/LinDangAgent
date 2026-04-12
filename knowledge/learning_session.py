# -*- coding: utf-8 -*-
"""统一学习引擎 — Session 状态管理。

Session 生命周期：
  Stage 1 backtest → Stage 2 reflect → Stage 3 validate → Stage 4 adopt/reject

每个 stage 结束后产出 HTML 报告，等待用户审查后手动触发下一 stage。
Stage 1 支持断点续跑（写 exams.json + 追加 results.jsonl）。

目录结构：
  data/knowledge/learning_sessions/<session_id>/
    state.json                 # session 当前状态
    stage1_backtest/
      exams.json               # 初始选题
      results.jsonl            # 已完成的回测（逐行追加，断点续跑）
      stats.json               # 全部跑完后的汇总
      holdout.json             # 验证集 exams
      summary.html             # 报告
    stage2_reflect/
      proposals.json, audit.json, adopted.json, summary.html
    stage3_validate/
      diff.json, old_results.jsonl, new_results.jsonl, criteria.json, summary.html
    stage4_final/
      decision.json
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from knowledge.kb_config import KNOWLEDGE_DIR

logger = logging.getLogger(__name__)

SESSIONS_DIR = KNOWLEDGE_DIR / "learning_sessions"

# Stage 状态枚举
STATE_PENDING = "pending"
STATE_IN_PROGRESS = "in_progress"
STATE_DONE = "done"
STATE_ADOPTED = "adopted"
STATE_REJECTED = "rejected"


def _sessions_root() -> Path:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    return SESSIONS_DIR


def new_session_id() -> str:
    """生成新 session ID。"""
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")


def session_dir(session_id: str) -> Path:
    """获取 session 目录。"""
    return _sessions_root() / session_id


def stage_dir(session_id: str, stage: str) -> Path:
    """获取 stage 子目录（stage: backtest/reflect/validate/final）。"""
    d = session_dir(session_id) / f"stage_{stage}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def create_session(mode: str, count: int, delay_between: int = 30) -> str:
    """创建新 session 并初始化 state。返回 session_id。"""
    sid = new_session_id()
    sdir = session_dir(sid)
    sdir.mkdir(parents=True, exist_ok=True)

    state = {
        "session_id": sid,
        "mode": mode,
        "count": count,
        "delay_between": delay_between,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "stages": {
            "backtest": STATE_PENDING,
            "reflect": STATE_PENDING,
            "validate": STATE_PENDING,
            "final": STATE_PENDING,
        },
    }
    _save_state(sid, state)
    logger.info("[learn] created session %s (mode=%s, count=%d)", sid, mode, count)
    return sid


def load_state(session_id: str) -> dict | None:
    """加载 session state。"""
    path = session_dir(session_id) / "state.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _save_state(session_id: str, state: dict):
    """保存 session state。"""
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    path = session_dir(session_id) / "state.json"
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def update_stage(session_id: str, stage: str, status: str):
    """更新某 stage 的状态。"""
    state = load_state(session_id)
    if not state:
        raise ValueError(f"Session {session_id} 不存在")
    state["stages"][stage] = status
    _save_state(session_id, state)


def list_sessions(limit: int = 20) -> list[dict]:
    """列出所有 sessions。"""
    root = _sessions_root()
    entries = []
    for sdir in sorted(root.iterdir(), reverse=True):
        if not sdir.is_dir():
            continue
        state_path = sdir / "state.json"
        if not state_path.exists():
            continue
        try:
            entries.append(json.loads(state_path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return entries[:limit]


# ── Stage 1 持久化 ─────────────────────────────────────────────────

def save_exams(session_id: str, exams: list[dict], holdout_exams: list[dict]):
    """保存 Stage 1 初始选题（训练集 + 验证集）。"""
    d = stage_dir(session_id, "backtest")
    (d / "exams.json").write_text(
        json.dumps(exams, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (d / "holdout.json").write_text(
        json.dumps(holdout_exams, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_exams(session_id: str) -> tuple[list[dict], list[dict]]:
    """加载 Stage 1 exams + holdout。"""
    d = stage_dir(session_id, "backtest")
    exams_path = d / "exams.json"
    holdout_path = d / "holdout.json"
    exams = json.loads(exams_path.read_text(encoding="utf-8")) if exams_path.exists() else []
    holdout = json.loads(holdout_path.read_text(encoding="utf-8")) if holdout_path.exists() else []
    return exams, holdout


def append_result(session_id: str, result: dict):
    """追加一条回测结果到 results.jsonl（断点续跑关键）。"""
    d = stage_dir(session_id, "backtest")
    with open(d / "results.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")


def load_completed_results(session_id: str) -> list[dict]:
    """加载已完成的回测结果（用于断点续跑）。"""
    path = stage_dir(session_id, "backtest") / "results.jsonl"
    if not path.exists():
        return []
    results = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return results


def completed_codes(session_id: str) -> set[str]:
    """已完成回测的股票代码集合（用于断点续跑时跳过）。"""
    return {r.get("ts_code", "") for r in load_completed_results(session_id)}


def save_backtest_stats(session_id: str, stats: dict):
    """保存 Stage 1 汇总统计。"""
    d = stage_dir(session_id, "backtest")
    (d / "stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


def load_backtest_stats(session_id: str) -> dict | None:
    path = stage_dir(session_id, "backtest") / "stats.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ── Stage 2 持久化 ─────────────────────────────────────────────────

def save_proposals(session_id: str, proposals: list[dict]):
    d = stage_dir(session_id, "reflect")
    (d / "proposals.json").write_text(
        json.dumps(proposals, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_proposals(session_id: str) -> list[dict]:
    path = stage_dir(session_id, "reflect") / "proposals.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def save_audit(session_id: str, audit: dict):
    d = stage_dir(session_id, "reflect")
    (d / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_audit(session_id: str) -> dict:
    path = stage_dir(session_id, "reflect") / "audit.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


# ── Stage 3 持久化 ─────────────────────────────────────────────────

def save_validation(session_id: str, validation: dict):
    d = stage_dir(session_id, "validate")
    (d / "validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


def load_validation(session_id: str) -> dict:
    path = stage_dir(session_id, "validate") / "validation.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_diff(session_id: str, diff: list[dict], prompt_proposals: list[dict]):
    d = stage_dir(session_id, "validate")
    (d / "diff.json").write_text(
        json.dumps(diff, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (d / "prompt_proposals.json").write_text(
        json.dumps(prompt_proposals, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_diff(session_id: str) -> tuple[list[dict], list[dict]]:
    d = stage_dir(session_id, "validate")
    diff_path = d / "diff.json"
    prompt_path = d / "prompt_proposals.json"
    diff = json.loads(diff_path.read_text(encoding="utf-8")) if diff_path.exists() else []
    prompts = json.loads(prompt_path.read_text(encoding="utf-8")) if prompt_path.exists() else []
    return diff, prompts


def append_validation_result(session_id: str, result: dict, config: str):
    """追加验证结果到 old_results.jsonl 或 new_results.jsonl。"""
    d = stage_dir(session_id, "validate")
    filename = f"{config}_results.jsonl"  # old_results.jsonl / new_results.jsonl
    with open(d / filename, "a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")


def load_validation_results(session_id: str, config: str) -> list[dict]:
    path = stage_dir(session_id, "validate") / f"{config}_results.jsonl"
    if not path.exists():
        return []
    results = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return results


def validation_completed_codes(session_id: str, config: str) -> set[str]:
    return {r.get("ts_code", "") for r in load_validation_results(session_id, config)}


# ── Stage 4 持久化 ─────────────────────────────────────────────────

def save_decision(session_id: str, decision: str, reason: str = ""):
    """保存最终 adopt/reject 决定。"""
    d = stage_dir(session_id, "final")
    (d / "decision.json").write_text(
        json.dumps({
            "decision": decision,
            "reason": reason,
            "decided_at": datetime.now().isoformat(timespec="seconds"),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_decision(session_id: str) -> dict | None:
    path = stage_dir(session_id, "final") / "decision.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
