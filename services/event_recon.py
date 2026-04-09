# -*- coding: utf-8 -*-
"""战役侦察令 — 事件驱动的快速反应选股机制

流程：
  Phase 1: 双路侦察（Gemini + Codex 并行联网搜索，独立出受益标的名单）
  Phase 2: 题材正宗性验证（逐只验证业务关联+正宗度评级）
  Phase 3: 返回验证结果给 Claude 决策 Top5
  Phase 4: Top5 → 指挥部深度分析（由调用方触发）

军事类比：林彪收到"敌军在锦州集结"情报后的快速反应——
判断战场格局 → 锁定主攻方向 → 集中优势兵力
"""

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent

# 双路侦察模型（都有联网搜索能力）
GEMINI_MODEL = "🔮 Gemini CLI（免费）"
CODEX_MODEL = "🤖 Codex CLI（Plus）"

# ── Phase 1: 战略侦察 Prompt ────────────────────────────────────

RECON_SYSTEM = """# 任务
你在执行"战役侦察令"：针对某个事件，筛选A股最正宗的受益标的。
不只判断"相关"，更要判断：受益是否真实、路径是否清晰、兑现时间是否匹配交易窗口。

# Phase 1：事件拆解（不急着找股票）
1. 判断事件类型：政策/新品发布/涨价/供给收缩/技术突破/并购/出海/订单/监管变化
2. 拆解产业链传导路径：上游→中游→下游→配套
3. 写出每个环节的核心受益逻辑：谁直接受益、谁间接受益、谁只是概念映射

# Phase 2：广度侦察
联网搜索受益标的，初筛15-25只。
每只附一句话关联理由（≤20字）。

# 正宗度硬规则
- S级：关联业务营收占比≥30%，且处于产业链直接受益环节，且有公告/订单/客户关系实锤
- A级：营收占比10-30%，或占比高但间接受益，受益程度或兑现节奏仍待验证
- B级：营收占比<10%，或逻辑链≥3步，或仅有"规划/技术储备"无实际营收
- C级：纯概念炒作，无实质关联 → 标注【情绪映射】

# 防蹭热点过滤规则
- 董秘"积极布局/密切关注"但无具体产品/订单 → 最高B级
- 仅有专利但无产品落地 → B级
- 仅通过参股子公司关联 → B级（除非并表且营收占比显著）
- 互动易模糊回复 → 不作为A类证据

# 输出要求
严格按以下格式，每行一只股票，用 | 分隔：
股票名称 | 正宗度(S/A) | 受益逻辑（含关联业务+营收占比估算+证据等级）

只输出S和A级（15-25只），按正宗度从高到低排列。
不要输出港股、美股、新三板、ST股。不要输出表头行。"""

RECON_PROMPT_TEMPLATE = """事件/热点：{event_desc}

请先拆解事件的产业链传导路径，再联网搜索该事件的最新信息和影响，识别A股中最直接受益的标的。
搜索关键词建议："{event_desc} A股 受益股 概念股 龙头 产业链"

请严格按格式输出（每行：股票名称 | 正宗度 | 受益逻辑）："""

# ── Phase 2: 正宗性验证 Prompt ──────────────────────────────────

VERIFY_SYSTEM = """你是题材正宗性审核官（Phase 3验证）。对给定的股票，严格验证其与事件的业务关联。
你必须联网搜索该公司的主营业务、营收构成、产品线，然后做出判断。

正宗度硬规则：
- S级：关联业务营收占比≥30%+产业链直接受益+有公告/订单实锤
- A级：营收占比10-30%，或占比高但间接受益
- B级：营收占比<10%，或逻辑链≥3步，或仅有规划/储备
- C级：纯概念，无实质关联 → 标注【情绪映射，随时核按钮】

防蹭热点：董秘"积极布局"无实际订单→最高B；仅专利无产品→B；仅参股关联→B。
输出极简，严格按格式。"""

VERIFY_PROMPT_TEMPLATE = """事件：{event_desc}
股票：{stock_name}
初步受益逻辑：{benefit_logic}

请联网搜索"{stock_name} 主营业务 营收构成 {event_desc}"，验证其与事件的真实关联度。

输出格式（每项一行）：
正宗度: S/A/B/C
关联业务: 具体业务名称（非笼统板块名）
营收占比: 该业务占总营收约xx%（标注数据来源）
关联路径: 事件→公司受益的完整逻辑链（≤3步）
证据等级: A类(公告/订单实锤) / B类(研报/产业信息) / C类(传闻/概念)
受益方式: 收入弹性 / 利润弹性 / 估值映射 / 纯情绪映射
兑现周期: 0-1月(极短期) / 1-3月(中期) / 3-12月(长期)
身位: 龙头/核心/补涨/跟风
弹性: 高/中/低
结论: 一句话"""


def _call_model(prompt: str, system: str, model_name: str) -> str:
    """调用单个模型（复用 war_room 的调用逻辑）。"""
    from ai.client import call_ai, call_ai_stream, get_ai_client

    client, cfg, err = get_ai_client(model_name)
    if err and not cfg:
        return f"⚠️ 模型不可用：{err}"

    if cfg.get("provider") in ("gemini_cli", "codex_cli", "claude_cli"):
        stream = call_ai_stream(client, cfg, prompt, system=system, max_tokens=8000)
        for _ in stream:
            pass
        return stream.full_text

    text, call_err = call_ai(client, cfg, prompt, system=system, max_tokens=8000)
    if call_err:
        return f"⚠️ 调用失败：{call_err}"
    return text


def _parse_recon_output(text: str) -> list[dict]:
    """解析侦察输出，提取股票名称+正宗度+受益逻辑。"""
    results = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("股票名称"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 3:
            name = re.sub(r"^\d+[\.\)、]\s*", "", parts[0]).strip()
            if not name or len(name) < 2:
                continue
            grade = parts[1].strip().upper()
            if grade not in ("S", "A", "B"):
                grade = "A"
            results.append({
                "name": name,
                "grade": grade,
                "logic": parts[2].strip(),
            })
        elif len(parts) == 2:
            name = re.sub(r"^\d+[\.\)、]\s*", "", parts[0]).strip()
            if name and len(name) >= 2:
                results.append({
                    "name": name,
                    "grade": "A",
                    "logic": parts[1].strip(),
                })
    return results


def _parse_verify_output(text: str) -> dict:
    """解析正宗性验证输出（v3.0扩展字段）。"""
    result = {
        "grade": "C", "business": "", "revenue_pct": "", "benefit_path": "",
        "evidence_level": "", "benefit_type": "", "realize_cycle": "",
        "stance": "", "elasticity": "", "conclusion": "",
    }
    field_map = {
        "正宗度": ("grade", True),
        "关联业务": ("business", False),
        "营收占比": ("revenue_pct", False),
        "关联路径": ("benefit_path", False),
        "证据等级": ("evidence_level", False),
        "受益方式": ("benefit_type", False),
        "兑现周期": ("realize_cycle", False),
        "身位": ("stance", False),
        "弹性": ("elasticity", False),
        "结论": ("conclusion", False),
        # 兼容旧格式
        "业务关联": ("business", False),
    }
    for line in text.strip().splitlines():
        line = line.strip()
        for prefix, (key, is_grade) in field_map.items():
            if line.startswith(prefix):
                if is_grade:
                    m = re.search(r"[：:]\s*([SABC])", line, re.IGNORECASE)
                    if m:
                        result[key] = m.group(1).upper()
                else:
                    result[key] = re.sub(rf"^{prefix}[：:]\s*", "", line)
                break
    return result


# ── 主流程 ──────────────────────────────────────────────────────

def run_event_recon(
    event_desc: str,
    progress_callback=None,
) -> dict:
    """战役侦察令 Phase 1-2：双路侦察 + 正宗性验证。

    返回验证后的候选清单（供 Claude 决策 Top5）。
    """
    def _log(msg: str):
        logger.info("[event_recon] %s", msg)
        if progress_callback:
            progress_callback(msg)

    _log(f"⚔️ 战役侦察令启动：{event_desc}")

    # ── Phase 1: 双路侦察（Gemini + Codex 并行）──────────────────
    _log("📡 Phase 1: 双路侦察兵出发（Gemini + Codex 并行联网搜索）...")

    recon_prompt = RECON_PROMPT_TEMPLATE.format(event_desc=event_desc)
    gemini_result = [None]
    codex_result = [None]

    def _gemini_recon():
        gemini_result[0] = _call_model(recon_prompt, RECON_SYSTEM, GEMINI_MODEL)

    def _codex_recon():
        codex_result[0] = _call_model(recon_prompt, RECON_SYSTEM, CODEX_MODEL)

    with ThreadPoolExecutor(max_workers=2) as pool:
        pool.submit(_gemini_recon)
        pool.submit(_codex_recon)

    # 解析两路结果
    gemini_stocks = _parse_recon_output(gemini_result[0] or "")
    codex_stocks = _parse_recon_output(codex_result[0] or "")

    _log(f"  Gemini 找到 {len(gemini_stocks)} 只，Codex 找到 {len(codex_stocks)} 只")

    # 合并策略：交集=共识（高可信），独有=分歧
    gemini_names = {s["name"] for s in gemini_stocks}
    codex_names = {s["name"] for s in codex_stocks}
    consensus_names = gemini_names & codex_names
    all_names_seen = set()
    merged = []

    # 共识标的优先
    for s in gemini_stocks:
        if s["name"] in consensus_names and s["name"] not in all_names_seen:
            # 合并两路的逻辑，取更长的
            codex_logic = next((c["logic"] for c in codex_stocks if c["name"] == s["name"]), "")
            logic = s["logic"] if len(s["logic"]) >= len(codex_logic) else codex_logic
            merged.append({**s, "logic": logic, "source": "gemini+codex"})
            all_names_seen.add(s["name"])

    # 独有标的
    for s in gemini_stocks:
        if s["name"] not in all_names_seen:
            merged.append({**s, "source": "gemini"})
            all_names_seen.add(s["name"])
    for s in codex_stocks:
        if s["name"] not in all_names_seen:
            merged.append({**s, "source": "codex"})
            all_names_seen.add(s["name"])

    _log(f"  合并后 {len(merged)} 只（共识 {len(consensus_names)} 只）")

    if not merged:
        _log("❌ 双路侦察均未找到相关标的")
        return {"event": event_desc, "status": "no_candidates", "verified_candidates": []}

    # ── Phase 2: 题材正宗性验证（并行）─────────────────────────
    _log(f"🔍 Phase 2: 验证 {len(merged)} 只候选的题材正宗性...")

    verified = []
    completed = 0

    def _verify_one(stock: dict) -> dict:
        prompt = VERIFY_PROMPT_TEMPLATE.format(
            event_desc=event_desc,
            stock_name=stock["name"],
            benefit_logic=stock["logic"],
        )
        text = _call_model(prompt, VERIFY_SYSTEM, GEMINI_MODEL)
        parsed = _parse_verify_output(text)
        return {**stock, **parsed}

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_verify_one, s): s["name"] for s in merged}
        for future in as_completed(futures):
            name = futures[future]
            completed += 1
            try:
                result = future.result()
                verified.append(result)
                grade = result.get("grade", "?")
                _log(f"  [{completed}/{len(merged)}] {name} → {grade}级 {result.get('conclusion', '')[:50]}")
            except Exception as exc:
                _log(f"  [{completed}/{len(merged)}] {name} → 验证失败：{exc}")

    # 按正宗度排序：S > A > B > C
    grade_order = {"S": 0, "A": 1, "B": 2, "C": 3}
    verified.sort(key=lambda x: (grade_order.get(x.get("grade", "C"), 9),
                                  0 if x.get("source") == "gemini+codex" else 1))

    # 分级统计
    s_count = sum(1 for v in verified if v.get("grade") == "S")
    a_count = sum(1 for v in verified if v.get("grade") == "A")
    b_count = sum(1 for v in verified if v.get("grade") == "B")
    c_count = sum(1 for v in verified if v.get("grade") == "C")

    # 只保留 S/A 级
    qualified = [v for v in verified if v.get("grade") in ("S", "A")]

    _log(f"✅ 验证完成：S级{s_count}只 A级{a_count}只 B级{b_count}只 C级{c_count}只")
    _log(f"  → 保留 {len(qualified)} 只正宗标的，淘汰 {b_count + c_count} 只蹭概念")

    # 解析股票代码
    for stock in qualified:
        try:
            from data.tushare_client import resolve_stock
            ts_code, resolved_name, _ = resolve_stock(stock["name"])
            stock["ts_code"] = ts_code
            stock["resolved_name"] = resolved_name
            stock["code6"] = ts_code.split(".")[0] if ts_code else ""
        except Exception:
            stock["ts_code"] = ""
            stock["resolved_name"] = stock["name"]
            stock["code6"] = ""

    result = {
        "event": event_desc,
        "recon_date": datetime.now().strftime("%Y-%m-%d"),
        "status": "ok",
        "recon_summary": {
            "gemini_found": len(gemini_stocks),
            "codex_found": len(codex_stocks),
            "consensus": len(consensus_names),
            "merged_total": len(merged),
            "s_grade": s_count,
            "a_grade": a_count,
            "eliminated": b_count + c_count,
        },
        "verified_candidates": [
            {
                "name": v.get("resolved_name", v["name"]),
                "code": v.get("code6", ""),
                "ts_code": v.get("ts_code", ""),
                "grade": v.get("grade", "?"),
                "logic": v.get("logic", ""),
                "business": v.get("business", ""),
                "revenue_pct": v.get("revenue_pct", ""),
                "benefit_path": v.get("benefit_path", ""),
                "evidence_level": v.get("evidence_level", ""),
                "benefit_type": v.get("benefit_type", ""),
                "realize_cycle": v.get("realize_cycle", ""),
                "stance": v.get("stance", ""),
                "elasticity": v.get("elasticity", ""),
                "conclusion": v.get("conclusion", ""),
                "source": v.get("source", ""),
            }
            for v in qualified
        ],
    }

    # 保存结果到文件
    recon_dir = BASE_DIR / "storage" / "event_recon"
    recon_dir.mkdir(parents=True, exist_ok=True)
    safe_event = re.sub(r"[^\w\u4e00-\u9fff]", "_", event_desc)[:20]
    recon_file = recon_dir / f"{datetime.now().strftime('%Y%m%d_%H%M')}_{safe_event}.json"
    recon_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"📄 侦察结果已保存：{recon_file}")

    # 发送侦察结果邮件
    try:
        _send_recon_email(result)
        _log("📧 侦察结果邮件已发送")
    except Exception as exc:
        _log(f"⚠️ 邮件发送失败：{exc}")

    return result


def _send_recon_email(result: dict):
    """发送战役侦察令 Phase 1-2 结果邮件。"""
    try:
        from utils.email_sender import send_text_email, smtp_configured
    except ImportError:
        return

    if not smtp_configured():
        return

    event = result.get("event", "未知事件")
    recon_date = result.get("recon_date", "")
    summary = result.get("recon_summary", {})
    candidates = result.get("verified_candidates", [])

    lines = [
        f"四野指挥部·战役侦察令",
        f"{'=' * 40}",
        f"事件：{event}",
        f"日期：{recon_date}",
        f"",
        f"【侦察统计】",
        f"  Gemini 找到: {summary.get('gemini_found', 0)} 只",
        f"  Codex 找到: {summary.get('codex_found', 0)} 只",
        f"  双路共识: {summary.get('consensus', 0)} 只",
        f"  合并候选: {summary.get('merged_total', 0)} 只",
        f"  S级正宗: {summary.get('s_grade', 0)} 只",
        f"  A级正宗: {summary.get('a_grade', 0)} 只",
        f"  淘汰(蹭概念): {summary.get('eliminated', 0)} 只",
        f"",
        f"{'=' * 40}",
        f"【正宗标的清单（S/A级）】",
        f"",
    ]

    for i, c in enumerate(candidates, 1):
        source_mark = "★" if c.get("source") == "gemini+codex" else " "
        lines.append(
            f"#{i:2d} {source_mark} [{c.get('grade', '?')}级] {c.get('name', '?')}（{c.get('code', '')}）"
        )
        lines.append(f"     身位: {c.get('stance', '?')} | 弹性: {c.get('elasticity', '?')} | 兑现: {c.get('realize_cycle', '?')}")
        lines.append(f"     业务: {c.get('business', '?')} | 营收占比: {c.get('revenue_pct', '?')}")
        lines.append(f"     证据: {c.get('evidence_level', '?')} | 受益方式: {c.get('benefit_type', '?')}")
        lines.append(f"     结论: {c.get('conclusion', '?')}")
        lines.append("")

    lines.append(f"{'=' * 40}")
    lines.append("★ = Gemini+Codex 双路共识标的（高可信度）")
    lines.append("")
    lines.append("等待司令员（Claude）拍板 Top5 后启动指挥部深度分析。")
    lines.append("LinDangAgent 战役侦察系统")

    subject = f"【战役侦察令】{event} — S级{summary.get('s_grade', 0)}只 A级{summary.get('a_grade', 0)}只"
    send_text_email(subject, "\n".join(lines))
