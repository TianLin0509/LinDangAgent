# -*- coding: utf-8 -*-
"""三路交叉代码审查 — Claude + Gemini + Codex 并行审查

解决 Codex CLI 沙箱限制问题：不让 Codex 自己读文件，
而是预先读取代码内容，通过 prompt 文本喂给它分析。

使用方式：
  python cli.py code-review services/war_room.py ai/prompts_report.py
  /s 审查 services/war_room.py
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent

REVIEW_SYSTEM = """你是一位资深 Python 代码审查专家。请审查以下代码，重点关注：
1. 变量引用错误（已删除/重命名的变量仍被引用）
2. 函数签名变更后调用方未同步更新
3. 类型注解与实际返回值不匹配
4. 新增的 provider/模型未在所有路由判断中注册
5. 数据流断裂（返回值结构变了但下游未更新）
6. import 了已删除的符号，或缺少必要的 import

只报告高置信度的真实 bug，每个问题给出：
- 文件名 + 行号（如能定位）
- 问题描述
- 修复建议

不要报风格问题、命名建议或可选优化。不要报 false positive。"""


def run_cross_review(
    files: list[str],
    focus: str = "",
    reviewers: list[str] | None = None,
) -> dict:
    """三路交叉代码审查。

    Args:
        files: 需要审查的文件路径列表（相对于 BASE_DIR 或绝对路径）
        focus: 审查重点描述
        reviewers: 审查者列表，默认 ["claude", "gemini", "codex"]

    Returns:
        {"Claude": "审查结果...", "Gemini": "...", "Codex": "..."}
    """
    if reviewers is None:
        reviewers = ["claude", "gemini", "codex"]

    # 1. 读取所有文件内容
    code_blocks = []
    total_chars = 0
    for f in files:
        path = Path(f) if Path(f).is_absolute() else BASE_DIR / f
        if not path.exists():
            code_blocks.append(f"### {f}\n⚠️ 文件不存在")
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        code_blocks.append(f"### {path.name} ({path})\n```python\n{content}\n```")
        total_chars += len(content)

    if not code_blocks:
        return {"error": "没有可审查的文件"}

    code_text = "\n\n".join(code_blocks)
    logger.info("[code_review] 审查 %d 个文件，共 %d 字符", len(files), total_chars)

    # 如果代码太长，截断保护
    max_chars = 80000
    if len(code_text) > max_chars:
        code_text = code_text[:max_chars] + "\n\n... (代码过长，已截断)"
        logger.warning("[code_review] 代码超过 %d 字符，已截断", max_chars)

    # system prompt = 审查规则 + 代码文本（走文件通道，无大小限制）
    # stdin prompt = 仅审查指令（<100字符，远低于 7000 字阈值）
    full_system = REVIEW_SYSTEM + "\n\n以下是需要审查的代码文件：\n\n" + code_text
    short_prompt = f"请审查以上代码{'，审查重点：' + focus if focus else ''}。列出发现的所有 bug（按严重度从高到低排序）："

    logger.info("[code_review] system %d 字符(走文件), stdin %d 字符(走管道)",
                len(full_system), len(short_prompt))

    # 2. 三路并行审查
    reviewer_fns = {
        "claude": _review_with_claude,
        "gemini": _review_with_gemini,
        "codex": _review_with_codex,
    }

    results = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {}
        for name in reviewers:
            fn = reviewer_fns.get(name)
            if fn:
                futures[pool.submit(fn, short_prompt, full_system)] = name.capitalize()

        for fut in as_completed(futures):
            reviewer_name = futures[fut]
            try:
                text = fut.result()
                results[reviewer_name] = text
                logger.info("[code_review] %s 审查完成 (%d 字符)", reviewer_name, len(text))
            except Exception as exc:
                results[reviewer_name] = f"审查失败: {exc}"
                logger.warning("[code_review] %s 审查异常: %r", reviewer_name, exc)

    return results


def _review_with_claude(prompt: str, system: str = "") -> str:
    """Claude Sonnet 审查（通过 Claude Code CLI），失败自动降级到 Gemini"""
    from ai.cli_providers import call_cli_robust
    text, err, model = call_cli_robust(
        prompt, system=system or REVIEW_SYSTEM, primary="claude",
        timeout=300, fallback_chain=["gemini", "codex"],
    )
    if err:
        return f"Claude 审查失败: {err}"
    prefix = f"[{model} fallback] " if model != "claude" else ""
    return prefix + text


def _review_with_gemini(prompt: str, system: str = "") -> str:
    """Gemini CLI 审查，失败自动降级到 Claude"""
    from ai.cli_providers import call_cli_robust
    text, err, model = call_cli_robust(
        prompt, system=system or REVIEW_SYSTEM, primary="gemini",
        timeout=300, fallback_chain=["claude", "codex"],
    )
    if err:
        return f"Gemini 审查失败: {err}"
    prefix = f"[{model} fallback] " if model != "gemini" else ""
    return prefix + text


def _review_with_codex(prompt: str, system: str = "") -> str:
    """Codex CLI 审查（代码通过 system prompt 文件传入），失败自动降级到 Gemini"""
    from ai.cli_providers import call_cli_robust
    text, err, model = call_cli_robust(
        prompt, system=system or REVIEW_SYSTEM, primary="codex",
        timeout=300, fallback_chain=["gemini", "claude"],
    )
    if err:
        return f"Codex 审查失败: {err}"
    prefix = f"[{model} fallback] " if model != "codex" else ""
    return prefix + text
