"""CLI Provider 调用封装 — Gemini CLI / Codex CLI / Claude Code CLI

关键设计：prompt 通过 stdin 传递给 CLI 工具，不依赖 bash。
之前用 bash -c 'cat file | cli' 方式在 PowerShell 环境下不稳定（PATH 丢失、路径不兼容）。
现在直接 subprocess.run(cmd, input=prompt_text)，跨平台可靠。

鲁棒化设计（2026-04-09）：
- 错误分类：区分可重试（rate_limit/timeout）vs 致命（policy/sandbox）错误
- 指数退避重试：首选模型最多重试 3 次
- 自动降级：Codex → Gemini → Claude 三级 fallback
- 环境隔离：每个 CLI 用干净的最小环境，避免变量污染
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time

logger = logging.getLogger(__name__)

# 代理配置（Gemini/Codex 需要访问国外服务）
_PROXY = "http://127.0.0.1:7890"
_PROXY_HOST = "127.0.0.1"
_PROXY_PORT = 7890
_proxy_checked = False
_proxy_ok = False
_proxy_lock = threading.Lock()


def _check_proxy_available() -> bool:
    """快速探测 Clash 代理端口是否可用（2s 超时）。结果缓存 60s，线程安全。"""
    global _proxy_checked, _proxy_ok
    with _proxy_lock:
        if _proxy_checked:
            return _proxy_ok
        import socket
        try:
            sock = socket.create_connection((_PROXY_HOST, _PROXY_PORT), timeout=2)
            sock.close()
            _proxy_ok = True
        except (OSError, socket.timeout):
            _proxy_ok = False
        _proxy_checked = True
        # 60s 后重新检测（daemon=True 不阻止进程退出）
        def _reset():
            global _proxy_checked
            with _proxy_lock:
                _proxy_checked = False
        t = threading.Timer(60, _reset)
        t.daemon = True
        t.start()
        return _proxy_ok


def _build_full_prompt(prompt: str, system: str = "") -> str:
    """将 system + user prompt 合并为单一文本（仅用于不支持原生 system prompt 的场景）"""
    parts = []
    if system:
        parts.append(f"【系统指令】\n{system}\n")
    parts.append(f"【用户请求】\n{prompt}")
    return "\n".join(parts)


def _write_system_file(system: str) -> str | None:
    """将 system prompt 写入临时文件，返回路径。调用方负责清理。"""
    if not system:
        return None
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8",
        prefix="lindang_system_",
    )
    f.write(system)
    f.close()
    return f.name


def _get_clean_env(provider: str) -> dict:
    """为 CLI 子进程构建干净环境，只保留必要变量，避免污染。

    核心原则：
    - 保留 PATH（找到 CLI 可执行文件）
    - 保留用户目录变量（CLI 认证文件在 ~/.claude/ 等）
    - 保留系统变量（Windows 运行时必需）
    - 不继承 CLAUDE_CODE_* 等 IDE 变量（避免 403 冲突）
    - 代理按需设置
    """
    KEEP = [
        # 系统必需
        'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT',
        # 用户目录（CLI 认证文件在这里）
        'HOME', 'USERPROFILE', 'HOMEPATH', 'HOMEDRIVE',
        'APPDATA', 'LOCALAPPDATA',
        # 临时目录
        'TEMP', 'TMP', 'TMPDIR',
        # 编码
        'LANG', 'LC_ALL', 'PYTHONUTF8',
        # Node.js（claude/codex CLI 是 node 程序）
        'NODE_PATH', 'NVM_DIR', 'FNM_DIR',
    ]
    env = {k: v for k, v in os.environ.items()
           if k in KEEP or k.startswith('npm_') or k.startswith('NVM_')}

    # 代理铁律：所有 CLI 必须经过 Clash 代理，不允许直连
    if not _check_proxy_available():
        raise RuntimeError(
            f"Clash 代理不可用({_PROXY_HOST}:{_PROXY_PORT})，"
            "请开启 Clash 后重试。所有 CLI 调用必须经过代理。"
        )
    env['HTTP_PROXY'] = _PROXY
    env['HTTPS_PROXY'] = _PROXY

    # Claude CLI 专用：跳过 hooks 避免冲突
    if provider == 'claude':
        env['CLAUDE_CODE_SKIP_HOOKS'] = '1'

    # Gemini CLI 专用：清除 DEBUG 变量，否则 headless 模式等待 debugger 连接导致死锁
    if provider == 'gemini':
        env.pop('DEBUG', None)

    return env


def _find_cli(name: str) -> str:
    """找到 CLI 可执行文件的完整路径。Windows 上 npm 安装的是 .cmd 文件。"""
    path = shutil.which(name)
    if path:
        return path
    # Windows fallback: 尝试 .cmd 后缀
    if os.name == 'nt':
        path = shutil.which(f"{name}.cmd")
        if path:
            return path
    return name  # 兜底返回原名，让 subprocess 报错


def call_gemini_cli(prompt: str, system: str = "",
                    timeout: int = 300) -> tuple[str, str | None]:
    """调用 Gemini CLI，返回 (response_text, error_msg)

    system prompt 走 GEMINI_SYSTEM_MD 临时文件（完全替换内置 system prompt），user prompt 走 stdin。
    """
    env = _get_clean_env("gemini")
    # GEMINI_SYSTEM_MD 设为文件路径会完全替换 Gemini 内置 system prompt
    system_file = _write_system_file(system)
    if system_file:
        env["GEMINI_SYSTEM_MD"] = system_file

    gemini_exe = _find_cli("gemini")
    # stdin 管道自动触发 headless 模式，不需要 -p（-p 是 --prompt 简写，会抢占下一个参数值）
    cmd = [gemini_exe, "--output-format", "json", "-y"]

    logger.info("[gemini_cli] calling %s, stdin %d chars, system_file %s",
                gemini_exe, len(prompt), "yes" if system_file else "no")
    try:
        result = subprocess.run(
            cmd, input=prompt,
            capture_output=True, text=True, timeout=timeout, env=env,
            encoding="utf-8", errors="replace",
            creationflags=0x08000000 if os.name == 'nt' else 0,  # CREATE_NO_WINDOW (Windows only)
        )

        if result.returncode != 0:
            stderr = result.stderr[:1000] if result.stderr else "unknown error"
            logger.warning("[gemini_cli] FAIL code=%d stderr=%s", result.returncode, stderr[:200])
            # 有时返回非零但 stdout 有内容
            if result.stdout and len(result.stdout.strip()) > 100:
                return result.stdout.strip(), None
            return "", f"Gemini CLI 退出码 {result.returncode}: {stderr}"

        stdout = result.stdout.strip()
        if not stdout:
            return "", "Gemini CLI 返回空输出"

        # 解析 JSON 输出（可能有非 JSON 前缀行）
        json_start = stdout.find("{")
        if json_start == -1:
            return stdout, None  # 纯文本回退

        try:
            # 用 raw_decode 精确解析第一个 JSON 对象（忽略尾部内容）
            decoder = json.JSONDecoder()
            data, _ = decoder.raw_decode(stdout, json_start)
            response = data.get("response") or data.get("content") or data.get("text") or ""
            if not response:
                # 最后手段：用整个 stdout
                return stdout[json_start:], None

            # 记录 token 统计
            models = data.get("stats", {}).get("models", {})
            if isinstance(models, dict):
                for model_name, model_stats in models.items():
                    if isinstance(model_stats, dict):
                        tokens = model_stats.get("tokens", {})
                        logger.info("[gemini_cli] model=%s input=%d output=%d",
                                   model_name, tokens.get("input", 0), tokens.get("candidates", 0))

            return response, None
        except json.JSONDecodeError:
            return stdout, None

    except subprocess.TimeoutExpired:
        logger.error("[gemini_cli] 超时 %ds，子进程已被终止", timeout)
        return "", f"Gemini CLI 超时（{timeout}s），请检查网络和代理"
    except Exception as e:
        return "", f"Gemini CLI 异常: {e}"
    finally:
        if system_file:
            try:
                os.unlink(system_file)
            except OSError:
                pass


def call_codex_cli(prompt: str, system: str = "",
                   timeout: int = 300) -> tuple[str, str | None]:
    """调用 Codex CLI，返回 (response_text, error_msg)

    system prompt 走 --system-prompt 参数，user prompt 走 stdin。
    """
    system_file = None  # 提前初始化，防止 _get_clean_env 异常时 finally 中 NameError
    env = _get_clean_env("codex")

    codex_exe = _find_cli("codex")
    cmd = [codex_exe, "exec", "-", "--skip-git-repo-check", "--json",
           "--full-auto"]
    if system:
        system_file = _write_system_file(system)
        cmd.extend(["-c", f"experimental_instructions_file={system_file}"])

    logger.info("[codex_cli] calling %s, stdin %d chars, system %s",
                codex_exe, len(prompt), "file" if system_file else "none")
    try:
        result = subprocess.run(
            cmd, input=prompt,
            capture_output=True, text=True, timeout=timeout, env=env,
            encoding="utf-8", errors="replace",
            creationflags=0x08000000 if os.name == 'nt' else 0,  # CREATE_NO_WINDOW (Windows only)
        )

        if result.returncode != 0:
            stderr = result.stderr[:1000] if result.stderr else "unknown error"
            logger.warning("[codex_cli] FAIL code=%d stderr=%s", result.returncode, stderr[:200])
            if result.stdout and len(result.stdout.strip()) > 100:
                return result.stdout.strip(), None
            return "", f"Codex CLI 退出码 {result.returncode}: {stderr}"

        stdout = result.stdout.strip()
        if not stdout:
            return "", "Codex CLI 返回空输出"

        # 解析 JSONL 输出，提取最终文本
        # 事件类型：item.completed(agent_message) 是主要响应，turn.completed 含 usage
        response_parts = []
        for line in stdout.split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                event_type = event.get("type", "")
                # 主要响应：item.completed 中的 agent_message
                if event_type == "item.completed":
                    item = event.get("item", {})
                    text = item.get("text", "")
                    if text:
                        response_parts.append(text)
                # 兼容旧版 message 事件（v0.116 以前）
                elif event_type == "message":
                    content = event.get("content", "")
                    if content:
                        response_parts.append(content)
                # token 统计（turn.completed 或内联 usage）
                usage = event.get("usage")
                if usage and isinstance(usage, dict):
                    logger.info("[codex_cli] tokens: input=%d output=%d cached=%d",
                               usage.get("input_tokens", 0),
                               usage.get("output_tokens", 0),
                               usage.get("cached_input_tokens", 0))
            except json.JSONDecodeError:
                logger.debug("[codex_cli] non-JSON line: %s", line[:80])

        response = "\n".join(response_parts)
        if not response:
            return "", "Codex CLI 输出中未找到响应文本"

        return response, None

    except subprocess.TimeoutExpired:
        logger.error("[codex_cli] 超时 %ds，子进程已被终止", timeout)
        return "", f"Codex CLI 超时（{timeout}s），请检查网络和代理"
    except Exception as e:
        return "", f"Codex CLI 异常: {e}"
    finally:
        if system_file:
            try:
                os.unlink(system_file)
            except OSError:
                pass


def call_claude_cli(prompt: str, system: str = "",
                    model: str = "opus",
                    timeout: int = 300) -> tuple[str, str | None]:
    """调用 Claude Code CLI（-p 非交互模式），返回 (response_text, error_msg)

    使用 MAX 订阅额度，支持 opus/sonnet/haiku 模型选择。
    长 prompt 通过临时文件 + cat 管道传递。
    """
    return _call_claude_internal(prompt, system, model, timeout)


def call_claude_cli_with_tools(prompt: str, system: str = "",
                               model: str = "sonnet",
                               allowed_tools: list[str] | None = None,
                               timeout: int = 600) -> tuple[str, str | None]:
    """调用 Claude Code CLI（-p 非交互模式 + 工具），返回 (response_text, error_msg)

    升级版：Claude 在推理过程中可以使用指定的工具（读文件、执行命令等）。
    适用于深度反思、复杂分析等需要 Claude 自主探索数据的场景。

    allowed_tools 示例：["Read", "Bash(python*)"]
    """
    return _call_claude_internal(prompt, system, model, timeout, allowed_tools)


def _call_claude_internal(prompt: str, system: str = "",
                          model: str = "opus",
                          timeout: int = 300,
                          allowed_tools: list[str] | None = None) -> tuple[str, str | None]:
    """Claude CLI 内部调用。system prompt 走 --append-system-prompt-file，user prompt 走 stdin。"""
    env = _get_clean_env("claude")

    # 模型映射
    model_map = {"opus": "opus", "sonnet": "sonnet", "haiku": "haiku"}
    claude_model = model_map.get(model, "opus")

    claude_exe = _find_cli("claude")
    cmd = [claude_exe, "-p", "--model", claude_model, "--bare"]

    # system prompt 走原生通道（文件），不塞 stdin
    system_file = _write_system_file(system)
    if system_file:
        cmd.extend(["--append-system-prompt-file", system_file])

    # allowedTools 每个工具独立参数（不用逗号拼接）
    if allowed_tools:
        for tool in allowed_tools:
            cmd.extend(["--allowedTools", tool])

    logger.info("[claude_cli] calling %s (%s%s), stdin %d chars, system_file %s",
                claude_exe, claude_model,
                f" +tools:{allowed_tools}" if allowed_tools else "",
                len(prompt), "yes" if system_file else "no")
    try:
        result = subprocess.run(
            cmd, input=prompt,
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
            creationflags=0x08000000 if os.name == 'nt' else 0,
            env=env,
        )

        if result.returncode != 0:
            stderr = result.stderr[:1000] if result.stderr else "unknown error"
            stdout_preview = result.stdout[:200] if result.stdout else ""
            logger.warning("[claude_cli] FAIL code=%d stderr=%s stdout=%s",
                           result.returncode, stderr[:300], stdout_preview[:200])
            if result.stdout and len(result.stdout.strip()) > 100:
                logger.info("[claude_cli] 退出码非零但有输出，尝试使用 stdout (%d chars)", len(result.stdout))
                return result.stdout.strip(), None
            return "", f"Claude CLI 退出码 {result.returncode}: {stderr}"

        stdout = result.stdout.strip()
        if not stdout:
            return "", "Claude CLI 返回空输出"

        logger.info("[claude_cli] model=%s output=%d chars", claude_model, len(stdout))
        return stdout, None

    except subprocess.TimeoutExpired:
        logger.error("[claude_cli] 超时 %ds，子进程已被终止", timeout)
        return "", f"Claude CLI 超时（{timeout}s），请检查网络和代理"
    except Exception as e:
        return "", f"Claude CLI 异常: {e}"
    finally:
        if system_file:
            try:
                os.unlink(system_file)
            except OSError:
                pass


# ─── 鲁棒化层：错误分类 + 重试 + Fallback ───────────────────────

def _is_retryable_error(err_msg: str) -> bool:
    """区分可重试 vs 致命错误，避免无意义重试浪费时间。

    致命错误（policy/sandbox/auth）直接跳到 fallback；
    可重试错误（rate_limit/timeout/网络）指数退避后重试。
    """
    err_lower = err_msg.lower()

    # 致命错误 — 重试也没用，直接降级
    fatal = ["policy", "sandbox", "permission denied", "401",
             "authentication", "not inside a trusted"]
    if any(p in err_lower for p in fatal):
        return False

    # 可重试错误 — 等一下可能恢复
    retryable = ["timeout", "超时", "rate_limit", "429", "503",
                  "connection", "temporarily", "re-connecting"]
    if any(p in err_lower for p in retryable):
        return True

    # 未知错误默认可重试（保守策略）
    return True


def call_cli_robust(
    prompt: str,
    system: str = "",
    primary: str = "codex",
    timeout: int = 300,
    max_retries: int = 3,
    fallback_chain: list[str] | None = None,
) -> tuple[str, str | None, str]:
    """鲁棒 CLI 调用：重试 + 自动降级。

    Args:
        prompt: 用户 prompt
        system: system prompt
        primary: 首选模型 ("codex" / "gemini" / "claude")
        timeout: 单次调用超时秒数
        max_retries: 首选模型的最大重试次数
        fallback_chain: 降级顺序，默认 ["gemini", "claude"]

    Returns:
        (result_text, error_or_none, actual_model_used)
        - error_or_none 为 None 表示成功
        - actual_model_used 标注实际使用的模型（含降级信息）
    """
    if fallback_chain is None:
        fallback_chain = ["gemini", "claude"]

    providers = {
        "codex": lambda p, s, t: call_codex_cli(p, system=s, timeout=t),
        "gemini": lambda p, s, t: call_gemini_cli(p, system=s, timeout=t),
        "claude": lambda p, s, t: call_claude_cli(p, system=s, model="sonnet", timeout=t),
    }

    chain = [primary] + [f for f in fallback_chain if f != primary]
    all_errors = []

    for model_name in chain:
        call_fn = providers.get(model_name)
        if not call_fn:
            logger.warning("[robust] 未知 provider: %s，跳过", model_name)
            continue

        retries = max_retries if model_name == primary else 1

        for attempt in range(retries):
            try:
                text, err = call_fn(prompt, system, timeout)
                if not err:
                    if model_name != primary:
                        logger.warning("[robust] %s 失败，%s 接力成功", primary, model_name)
                    return text, None, model_name

                all_errors.append(f"{model_name}(#{attempt+1}): {err[:150]}")

                if not _is_retryable_error(err):
                    logger.error("[%s] 致命错误，跳过重试: %s", model_name, err[:200])
                    break  # 跳到下一个 fallback

                if attempt < retries - 1:
                    wait = 2 ** attempt  # 1s, 2s, 4s 指数退避
                    logger.warning("[%s] 第%d次重试，等待%ds: %s",
                                   model_name, attempt + 1, wait, err[:100])
                    time.sleep(wait)

            except Exception as e:
                all_errors.append(f"{model_name}(#{attempt+1}): 异常 {e}")
                logger.error("[%s] 异常（第%d次）: %s", model_name, attempt + 1, e)
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)

    error_summary = " | ".join(all_errors[-5:])  # 只保留最近 5 条
    return "", f"全部失败: {error_summary}", "none"
