import json
import logging
import re
import sys
import uuid
from datetime import datetime
from html import escape
from hashlib import sha1
from pathlib import Path
from threading import Lock
from xml.etree import ElementTree as ET

import requests
import uvicorn
from fastapi import BackgroundTasks, FastAPI, Query, Request
from fastapi.responses import HTMLResponse, Response

from repositories.report_repo import get_report as load_report
from repositories.report_repo import init_db, save_report
from services.analysis_service import generate_report_bundle
from services.prebuilt_kline_service import (
    build_kline_prediction_report,
    ensure_research_dataset,
)
from services.top100_review_service import build_latest_top100_review
from data.tushare_client import resolve_stock
from utils.app_config import get_secret


TOKEN = get_secret("WECHAT_TOKEN", "StockLite2026")
APPID = get_secret("WECHAT_APPID", get_secret("APPID", "wx4e4d573b84971454"))
APPSECRET = get_secret("WECHAT_APPSECRET", get_secret("APPSECRET", "513440534b87550ef9c226646de7d201"))
TEMPLATE_ID = get_secret(
    "WECHAT_TEMPLATE_ID",
    get_secret("TEMPLATE_ID", "R7OvwS6JvBAcvpg7vlayZ-OPK6WKxODPerMSLMEIPFE"),
)
BASE_URL = get_secret("BASE_URL", "http://8.130.158.231")
MAX_WECHAT_TEXT_CHARS = int(get_secret("MAX_WECHAT_TEXT_CHARS", "600"))

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LOG_PATH = LOG_DIR / "wechat_server.log"
PROMPT_HTML_PATH = BASE_DIR / "storage" / "current_stock_prompt.html"

LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("wechat_server")
logger.setLevel(logging.INFO)
logger.handlers.clear()

file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(file_handler)
logger.propagate = False

app = FastAPI()
init_db()

INVALID_STOCK_WORDS = {
    "测试",
    "你好",
    "您好",
    "在吗",
    "哈哈",
    "hello",
    "hi",
    "test",
}

MESSAGE_DEDUP_WINDOW_SECONDS = 600
_processed_message_ids: dict[str, float] = {}
_processed_message_ids_lock = Lock()
TOP10_REPO_DIR = BASE_DIR / "Stock_top10"
if not TOP10_REPO_DIR.exists():
    TOP10_REPO_DIR = BASE_DIR.parent / "Stock_top10"
TOP10_CACHE_DIR = TOP10_REPO_DIR / "cache"
TOP10_DEFAULT_MODEL = get_secret("TOP10_MODEL_NAME", "🟣 豆包 · Seed 2.0 Pro")
BALANCE_COMMANDS = {
    "查询token余额",
    "查询余额",
    "token余额",
    "豆包余额",
    "查询豆包余额",
}
TOP10_QUERY_COMMANDS = {
    "top10",
}
TOP100_QUERY_COMMANDS = {
    "top100",
}
TOP100_REVIEW_QUERY_COMMANDS = {
    "top100复盘",
    "复盘top100",
    "top100review",
    "top100-review",
}
TOP100_REVIEW_GENERATE_COMMANDS = {
    "生成top100复盘",
    "top100复盘生成",
    "更新top100复盘",
    "刷新top100复盘",
}
TOP10_GENERATE_COMMANDS = {
    "生成top10",
    "top10生成",
    "更新top10",
    "刷新top10",
}
KLINE_PREDICT_PREFIXES = (
    "k线预测",
    "形态预测",
    "走势预测",
)


def verify_signature(signature: str, timestamp: str, nonce: str) -> bool:
    items = [TOKEN, timestamp, nonce]
    items.sort()
    digest = sha1("".join(items).encode("utf-8")).hexdigest()
    return digest == signature


def xml_text(root: ET.Element, tag: str, default: str = "") -> str:
    node = root.find(tag)
    return node.text if node is not None and node.text is not None else default


def is_valid_stock_input(content: str) -> bool:
    value = (content or "").strip()
    if len(value) < 2 or len(value) > 8:
        return False
    if value.lower() in INVALID_STOCK_WORDS:
        return False
    if re.fullmatch(r"[A-Za-z]+", value):
        return False
    return True


def normalize_text_command(content: str) -> str:
    return re.sub(r"\s+", "", (content or "").strip().lower())


def is_balance_query(content: str) -> bool:
    raw = (content or "").strip()
    normalized = normalize_text_command(raw)
    if normalized in {item.lower() for item in BALANCE_COMMANDS}:
        return True
    if "token" in normalized:
        return True
    if "token" in normalized and ("余额" in raw or "balance" in normalized):
        return True
    if "豆包" in raw and "余额" in raw:
        return True
    if "查询" in raw and "余额" in raw:
        return True
    return False


def is_top10_query(content: str) -> bool:
    normalized = normalize_text_command(content).replace("-", "")
    if normalized in TOP10_QUERY_COMMANDS or normalized == "查询top10":
        return True
    return normalized == "top10" and not is_top10_generate_command(content)


def is_top100_query(content: str) -> bool:
    normalized = normalize_text_command(content).replace("-", "")
    return normalized in TOP100_QUERY_COMMANDS or normalized == "查询top100"


def is_top100_review_query(content: str) -> bool:
    normalized = normalize_text_command(content).replace("-", "")
    return normalized in {item.replace("-", "") for item in TOP100_REVIEW_QUERY_COMMANDS}


def is_top100_review_generate_command(content: str) -> bool:
    normalized = normalize_text_command(content).replace("-", "")
    return normalized in {item.replace("-", "") for item in TOP100_REVIEW_GENERATE_COMMANDS}


def is_top10_generate_command(content: str) -> bool:
    normalized = normalize_text_command(content).replace("-", "")
    if normalized in TOP10_GENERATE_COMMANDS:
        return True
    raw = (content or "").strip()
    return "top10" in normalized and any(keyword in raw for keyword in ("生成", "更新", "刷新"))


def parse_kline_predict_command(content: str) -> str | None:
    raw = (content or "").strip()
    normalized = normalize_text_command(raw)
    for prefix in KLINE_PREDICT_PREFIXES:
        compact_prefix = prefix.replace(" ", "")
        if normalized.startswith(compact_prefix):
            stock_text = raw[len(prefix):].strip(" ：:;；,，")
            return stock_text or None

    natural_patterns = [
        r"^(?P<stock>.+?)\s*k线$",
        r"^(?P<stock>.+?)\s*走势$",
        r"^(?P<stock>.+?)\s*预测$",
        r"^预测\s*(?P<stock>.+)$",
        r"^分析\s*(?P<stock>.+?)\s*k线$",
        r"^分析\s*(?P<stock>.+?)\s*走势$",
        r"^看看\s*(?P<stock>.+?)\s*k线$",
    ]
    for pattern in natural_patterns:
        match = re.match(pattern, raw, flags=re.IGNORECASE)
        if match:
            stock_text = (match.group("stock") or "").strip(" ：:;；,，")
            return stock_text or None
    return None


def _fmt_money(value: object) -> str:
    if value in (None, ""):
        return "未知"
    try:
        return f"{float(value):,.2f}"
    except Exception:
        return str(value)


def _fmt_int(value: object) -> str:
    if value in (None, ""):
        return "0"
    try:
        return f"{int(value):,}"
    except Exception:
        return str(value)


def build_doubao_balance_reply() -> str:
    try:
        from config import MODEL_CONFIGS
        from services.token_balance_service import get_token_balance_snapshot

        doubao_model_name = None
        for current_model_name, cfg in MODEL_CONFIGS.items():
            if cfg.get("provider") != "doubao":
                continue
            doubao_model_name = current_model_name
            if "pro" in str(cfg.get("model", "")).lower():
                break

        if not doubao_model_name:
            return "未找到豆包模型配置，请先检查服务端模型设置。"

        snapshot = get_token_balance_snapshot(model_name=doubao_model_name)
        providers = snapshot.get("providers") or []
        provider = providers[0] if providers else {}
        status = provider.get("status", "unknown")
        account = provider.get("account") or {}
        currency = account.get("currency") or "CNY"
        local_usage = snapshot.get("local_token_usage") or {}

        if status == "ok":
            return (
                "豆包当前余额如下：\n"
                f"可用余额：{_fmt_money(account.get('available_balance'))} {currency}\n"
                f"可用现金：{_fmt_money(account.get('available_balance_available'))} {currency}\n"
                f"冻结/不可用：{_fmt_money(account.get('available_balance_unavailable'))} {currency}\n"
                f"授信余额：{_fmt_money(account.get('credit_balance'))} {currency}\n"
                f"本地累计Token：{_fmt_int(local_usage.get('total'))}\n"
                f"查询时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}"
            )

        if status == "credential_required":
            return (
                "豆包余额查询暂未启用。\n"
                "原因：服务端还没有配置火山引擎计费 AK/SK。\n"
                "需要配置 `VOLC_ACCESSKEY` 和 `VOLC_SECRETKEY` 后，公众号里才能直接返回实时余额。"
            )

        message = provider.get("message") or "未知错误"
        return f"豆包余额查询失败：{message}"
    except Exception as exc:
        logger.exception("build_doubao_balance_reply failed: %s", exc)
        return f"豆包余额查询失败：{exc}"


def _ensure_top10_import_path() -> None:
    top10_root = str(TOP10_REPO_DIR)
    if top10_root not in sys.path:
        sys.path.insert(0, top10_root)


def _latest_top10_result_file() -> Path | None:
    if not TOP10_CACHE_DIR.exists():
        return None
    candidates = [
        path
        for path in TOP10_CACHE_DIR.glob("*.json")
        if "deep_status" not in path.name.lower()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _latest_top10_status_file() -> Path | None:
    if not TOP10_CACHE_DIR.exists():
        return None
    candidates = list(TOP10_CACHE_DIR.glob("*deep_status.json"))
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _normalize_report_link(url: str) -> str:
    text = (url or "").strip()
    if not text:
        return ""
    match = re.search(r"/report/([0-9a-fA-F-]{36})", text)
    if match:
        return f"{BASE_URL}/report/{match.group(1)}"
    return text


def get_latest_top10_snapshot() -> dict | None:
    return get_latest_rank_snapshot(limit=10)


def get_latest_rank_snapshot(limit: int) -> dict | None:
    result_file = _latest_top10_result_file()
    if result_file is None:
        return None

    result_data = json.loads(result_file.read_text(encoding="utf-8"))
    status_data: dict = {}
    status_file = _latest_top10_status_file()
    if status_file is not None:
        try:
            status_data = json.loads(status_file.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("load top10 status failed path=%s", status_file)

    results = result_data.get("results") or []
    top_rows = []
    raw_links = status_data.get("top10_links") or []
    for idx, item in enumerate(results[:limit], start=1):
        row = dict(item)
        row["排名"] = idx
        row["报告链接"] = _normalize_report_link(
            str(row.get("报告链接") or (raw_links[idx - 1] if idx - 1 < len(raw_links) else ""))
        )
        top_rows.append(row)

    finished = status_data.get("finished") or datetime.fromtimestamp(
        result_file.stat().st_mtime
    ).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "model": result_data.get("model") or status_data.get("model") or TOP10_DEFAULT_MODEL,
        "tokens_used": result_data.get("tokens_used") or status_data.get("tokens_used") or 0,
        "summary": result_data.get("summary") or "",
        "date": result_data.get("date") or "",
        "finished": finished,
        "scored_count": status_data.get("scored_count") or len(results),
        "status": status_data.get("status") or "done",
        "requested_limit": limit,
        "actual_count": len(top_rows),
        "rows": top_rows,
        "source_file": str(result_file),
        "status_file": str(status_file) if status_file else "",
    }


def build_top10_summary_text(snapshot: dict) -> str:
    return build_rank_summary_text(snapshot, label="Top10", path="/top10/latest")


def build_top100_summary_text(snapshot: dict) -> str:
    return build_rank_summary_text(snapshot, label="Top100", path="/top100/latest")


def build_top100_review_summary_text(review: dict) -> str:
    rows = review.get("rows") or []
    if not rows:
        return "当前还没有可用的 Top100 复盘结果。"

    lines = [
        "最新 Top100 复盘结果已准备好。",
        f"榜单生成时间：{review.get('generated_at') or '未知'}",
        f"对比交易日：{review.get('compare_trade_date') or '未知'}",
        f"分析模型：{review.get('model') or '未知'}",
        f"Token：{_fmt_int(review.get('tokens_used'))}",
        f"结果数量：{_fmt_int(len(rows))}",
        f"查看详情：{BASE_URL}/top100/review/latest",
        "",
        "前 3 名表现：",
    ]
    for row in rows[:3]:
        daily_pct = (
            f"{float(row.get('daily_pct_chg')):.2f}%"
            if row.get("daily_pct_chg") is not None
            else "N/A"
        )
        open_buy_pct = (
            f"{float(row.get('open_buy_pct')):.2f}%"
            if row.get("open_buy_pct") is not None
            else "N/A"
        )
        lines.append(
            f"{row.get('rank')}. {row.get('stock_name', '未知')} "
            f"当日涨跌幅 {daily_pct} 开盘买入 {open_buy_pct}"
        )
    return "\n".join(lines)


def run_top100_review_generation_and_notify(openid: str) -> None:
    logger.info("run_top100_review_generation start openid=%s", openid)
    try:
        review = build_latest_top100_review()
        send_custom_message(openid, build_top100_review_summary_text(review))
        logger.info(
            "run_top100_review_generation success openid=%s compare_trade_date=%s markdown_path=%s",
            openid,
            review.get("compare_trade_date"),
            review.get("markdown_path"),
        )
    except Exception as exc:
        logger.exception("run_top100_review_generation failed openid=%s error=%r", openid, exc)
        try:
            send_custom_message(openid, f"Top100复盘生成失败：{exc}")
        except Exception:
            logger.exception("send top100 review error message failed openid=%s", openid)


def build_rank_summary_text(snapshot: dict, *, label: str, path: str) -> str:
    rows = snapshot.get("rows") or []
    actual_count = snapshot.get("actual_count") or len(rows)
    if not rows:
        return f"当前还没有可用的 {label} 结果。"

    lines = [
        f"最新 {label} 结果已准备好。",
        f"模型：{snapshot.get('model') or '未知'}",
        f"Token：{_fmt_int(snapshot.get('tokens_used'))}",
        f"生成时间：{snapshot.get('finished') or '未知'}",
        f"结果数量：{_fmt_int(actual_count)}",
        f"查看详情：{BASE_URL}{path}",
        "",
        "前3名：",
    ]
    for row in rows[:3]:
        lines.append(
            f"{row.get('排名')}. {row.get('股票名称', '未知')} "
            f"{row.get('综合匹配度', 0)}分"
        )
    return "\n".join(lines)


def render_top10_html(snapshot: dict) -> str:
    return render_rank_html(snapshot, title="Top10 模式匹配榜", heading="最新 Top10 模式匹配榜")


def render_top100_html(snapshot: dict) -> str:
    return render_rank_html(snapshot, title="Top100 模式匹配榜", heading="最新 Top100 模式匹配榜")


def render_top100_review_html(review: dict) -> str:
    rows = review.get("rows") or []
    table_rows = []
    for row in rows:
        daily_pct = (
            f"{float(row.get('daily_pct_chg')):.2f}%"
            if row.get("daily_pct_chg") is not None
            else "N/A"
        )
        open_buy_pct = (
            f"{float(row.get('open_buy_pct')):.2f}%"
            if row.get("open_buy_pct") is not None
            else "N/A"
        )
        market_pct = (
            f"{float(row.get('market_pct_chg')):.2f}%"
            if row.get("market_pct_chg") is not None
            else "N/A"
        )
        table_rows.append(
            "<tr>"
            f"<td>{escape(str(row.get('rank', '')))}</td>"
            f"<td>{escape(str(row.get('stock_name', '')))}</td>"
            f"<td>{escape(str(row.get('ts_code', '')))}</td>"
            f"<td>{escape(str(row.get('match_score', '')))}</td>"
            f"<td>{escape(str(row.get('short_term', '')))}</td>"
            f"<td>{escape(daily_pct)}</td>"
            f"<td>{escape(open_buy_pct)}</td>"
            f"<td>{escape(market_pct)}</td>"
            "</tr>"
        )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
  <title>Top100 复盘结果</title>
  <style>
    :root {{
      --bg: #f4f7fb;
      --card: #ffffff;
      --text: #172033;
      --muted: #667085;
      --accent: #1d4ed8;
      --line: #dbe4ee;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
      background:
        radial-gradient(circle at top right, rgba(29,78,216,0.10), transparent 28%),
        linear-gradient(180deg, #eef5ff 0%, var(--bg) 45%, #f8fafc 100%);
      color: var(--text);
    }}
    .wrap {{ max-width: 1100px; margin: 0 auto; padding: 20px 12px 40px; }}
    .hero, .card {{
      background: rgba(255,255,255,0.94);
      border: 1px solid rgba(255,255,255,0.8);
      border-radius: 20px;
      box-shadow: 0 14px 40px rgba(15, 23, 42, 0.08);
    }}
    .hero {{ padding: 22px 18px; margin-bottom: 14px; }}
    .eyebrow {{ color: var(--accent); font-size: 13px; font-weight: 700; letter-spacing: 0.08em; }}
    h1 {{ margin: 8px 0 12px; font-size: 30px; line-height: 1.2; }}
    .meta {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 10px;
      margin-top: 16px;
    }}
    .meta-item {{
      background: #f8fbff;
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 12px;
    }}
    .meta-label {{ color: var(--muted); font-size: 12px; margin-bottom: 6px; }}
    .meta-value {{ font-size: 18px; font-weight: 700; }}
    .card {{ padding: 18px 14px; }}
    h2 {{ font-size: 22px; margin: 0 0 14px; }}
    .table-scroll {{ overflow-x: auto; width: 100%; }}
    table {{ width: max-content; min-width: 100%; border-collapse: collapse; white-space: nowrap; }}
    th, td {{ border: 1px solid var(--line); padding: 10px 12px; text-align: left; font-size: 14px; vertical-align: top; }}
    th {{ background: #f8fafc; }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="eyebrow">TOP100 REVIEW</div>
      <h1>最新 Top100 复盘结果</h1>
      <div class="meta">
        <div class="meta-item">
          <div class="meta-label">榜单生成时间</div>
          <div class="meta-value">{escape(str(review.get("generated_at") or "未知"))}</div>
        </div>
        <div class="meta-item">
          <div class="meta-label">对比交易日</div>
          <div class="meta-value">{escape(str(review.get("compare_trade_date") or "未知"))}</div>
        </div>
        <div class="meta-item">
          <div class="meta-label">分析模型</div>
          <div class="meta-value">{escape(str(review.get("model") or "未知"))}</div>
        </div>
        <div class="meta-item">
          <div class="meta-label">Token 消耗</div>
          <div class="meta-value">{escape(_fmt_int(review.get("tokens_used")))}</div>
        </div>
      </div>
    </section>
    <section class="card">
      <h2>Top100 次日表现复盘</h2>
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>排名</th>
              <th>股票名</th>
              <th>代码</th>
              <th>综合匹配度</th>
              <th>短线建议</th>
              <th>当天涨跌幅</th>
              <th>开盘买入策略</th>
              <th>上证指数涨跌幅</th>
            </tr>
          </thead>
          <tbody>
            {''.join(table_rows) or '<tr><td colspan="8">暂无结果</td></tr>'}
          </tbody>
        </table>
      </div>
    </section>
  </div>
</body>
</html>"""


def _format_score_value(value: object) -> str:
    try:
        return f"{float(value):.2f}".rstrip("0").rstrip(".")
    except Exception:
        return str(value)


def _row_get(row: dict, *keys: str, default: object = "") -> object:
    for key in keys:
        if key in row:
            return row.get(key)
    return default


def explain_match_score(row: dict) -> str:
    score = _row_get(row, "\u7efc\u5408\u5339\u914d\u5ea6", default=None)
    score_text = _format_score_value(score)
    try:
        score_value = float(score)
    except Exception:
        return score_text

    if score_value != 0:
        return score_text

    analysis_text = "\n".join(
        str(_row_get(row, key, default="") or "")
        for key in ("\u6838\u5fc3\u6458\u8981", "\u6458\u8981", "AI分析")
    )
    if "\u57fa\u672c\u9762\u4e00\u7968\u5426\u51b3" in analysis_text or "\u57fa\u672c\u9762\u5426\u51b3" in analysis_text:
        return f"{score_text}（基本面否决）"
    if "\u4e00\u7968\u5426\u51b3" in analysis_text:
        return f"{score_text}（一票否决）"
    if "\u6728\u6876\u4fee\u6b63" in analysis_text:
        return f"{score_text}（木桶修正）"
    if "\u56de\u907f" in analysis_text or str(_row_get(row, "\u64cd\u4f5c\u8bc4\u7ea7", default="") or "") == "\u56de\u907f":
        return f"{score_text}（回避）"
    return f"{score_text}（低匹配）"


def render_rank_html(snapshot: dict, *, title: str, heading: str) -> str:
    rows = snapshot.get("rows") or []
    summary_html = ""
    summary_text = (snapshot.get("summary") or "").strip()
    if summary_text:
        summary_html = "".join(
            f"<p>{escape(line)}</p>" for line in summary_text.splitlines() if line.strip()
        )

    table_rows = []
    for row in rows:
        report_link = row.get("报告链接") or ""
        report_html = (
            f'<a href="{escape(report_link)}" target="_blank" rel="noreferrer">查看研报</a>'
            if report_link
            else "-"
        )
        table_rows.append(
            "<tr>"
            f"<td>{escape(str(row.get('排名', '')))}</td>"
            f"<td>{report_html}</td>"
            f"<td>{escape(str(row.get('股票名称', '')))}</td>"
            f"<td>{escape(str(row.get('代码', '')))}</td>"
            f"<td>{escape(str(row.get('行业', '')))}</td>"
            f"<td>{escape(explain_match_score(row))}</td>"
            f"<td>{escape(str(row.get('短线建议', row.get('操作评级', ''))))}</td>"
            f"<td>{escape(str(row.get('中期建议', '')))}</td>"
            f"<td>{escape(str(row.get('核心摘要', row.get('摘要', ''))))}</td>"
            "</tr>"
        )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
  <title>{escape(title)}</title>
  <style>
    :root {{
      --bg: #f4f7fb;
      --card: #ffffff;
      --text: #172033;
      --muted: #667085;
      --accent: #0f766e;
      --line: #dbe4ee;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
      background:
        radial-gradient(circle at top right, rgba(15,118,110,0.10), transparent 28%),
        linear-gradient(180deg, #eef5ff 0%, var(--bg) 45%, #f8fafc 100%);
      color: var(--text);
    }}
    .wrap {{ max-width: 1100px; margin: 0 auto; padding: 20px 12px 40px; }}
    .hero, .card {{
      background: rgba(255,255,255,0.94);
      border: 1px solid rgba(255,255,255,0.8);
      border-radius: 20px;
      box-shadow: 0 14px 40px rgba(15, 23, 42, 0.08);
    }}
    .hero {{ padding: 22px 18px; margin-bottom: 14px; }}
    .eyebrow {{ color: var(--accent); font-size: 13px; font-weight: 700; letter-spacing: 0.08em; }}
    h1 {{ margin: 8px 0 12px; font-size: 30px; line-height: 1.2; }}
    .meta {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 10px;
      margin-top: 16px;
    }}
    .meta-item {{
      background: #f8fbff;
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 12px;
    }}
    .meta-label {{ color: var(--muted); font-size: 12px; margin-bottom: 6px; }}
    .meta-value {{ font-size: 18px; font-weight: 700; }}
    .card {{ padding: 18px 14px; }}
    h2 {{ font-size: 22px; margin: 0 0 14px; }}
    .summary p {{ margin: 0 0 8px; color: #334155; }}
    .table-scroll {{ overflow-x: auto; width: 100%; }}
    table {{ width: max-content; min-width: 100%; border-collapse: collapse; white-space: nowrap; }}
    th, td {{ border: 1px solid var(--line); padding: 10px 12px; text-align: left; font-size: 14px; vertical-align: top; }}
    th {{ background: #f8fafc; }}
    a {{ color: #2563eb; text-decoration: none; }}
    @media (max-width: 640px) {{
      .wrap {{ padding: 12px 8px 24px; }}
      .hero, .card {{ border-radius: 14px; }}
      h1 {{ font-size: 24px; }}
      th, td {{ font-size: 13px; padding: 8px 10px; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="eyebrow">VALUE SPECULATION TOP10</div>
      <h1>{escape(heading)}</h1>
      <div class="meta">
        <div class="meta-item">
          <div class="meta-label">分析模型</div>
          <div class="meta-value">{escape(str(snapshot.get("model") or "未知"))}</div>
        </div>
        <div class="meta-item">
          <div class="meta-label">本次 Token 消耗</div>
          <div class="meta-value">{escape(_fmt_int(snapshot.get("tokens_used")))}</div>
        </div>
        <div class="meta-item">
          <div class="meta-label">生成时间</div>
          <div class="meta-value">{escape(str(snapshot.get("finished") or "未知"))}</div>
        </div>
        <div class="meta-item">
          <div class="meta-label">结果数量</div>
          <div class="meta-value">{escape(_fmt_int(snapshot.get("actual_count") or snapshot.get("scored_count")))}</div>
        </div>
      </div>
    </section>
    <section class="card">
      <h2>策略总结</h2>
      <div class="summary">{summary_html or "<p>暂无总结。</p>"}</div>
    </section>
    <section class="card" style="margin-top: 14px;">
      <h2>Top10 明细</h2>
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>排名</th>
              <th>查看报告</th>
              <th>股票</th>
              <th>代码</th>
              <th>行业</th>
              <th>综合匹配度</th>
              <th>短线建议</th>
              <th>中期建议</th>
              <th>核心摘要</th>
            </tr>
          </thead>
          <tbody>
            {''.join(table_rows) or '<tr><td colspan="9">暂无结果</td></tr>'}
          </tbody>
        </table>
      </div>
    </section>
  </div>
</body>
</html>"""


def get_top10_generation_status() -> dict | None:
    try:
        _ensure_top10_import_path()
        from top10.deep_runner import get_deep_status

        return get_deep_status()
    except Exception:
        logger.exception("get_top10_generation_status failed")
        return None


def run_top10_generation_and_notify(openid: str) -> None:
    logger.info("run_top10_generation start openid=%s", openid)
    try:
        _ensure_top10_import_path()
        from top10.deep_runner import get_deep_status, is_deep_running, run_deep_top10

        status = get_deep_status() or {}
        if is_deep_running() or status.get("status") == "running":
            send_custom_message(
                openid,
                f"Top10 任务正在生成中，请稍候查看：{BASE_URL}/top10/latest",
            )
            return

        run_deep_top10(model_name=TOP10_DEFAULT_MODEL, candidate_count=100, username=openid)
        status = get_deep_status() or {}
        if status.get("status") != "done":
            error_message = status.get("error") or "未知错误"
            send_custom_message(openid, f"生成 Top10 失败：{error_message}")
            return

        snapshot = get_latest_top10_snapshot()
        if not snapshot:
            send_custom_message(openid, "Top10 已生成完成，但暂未读取到结果文件，请稍后再试。")
            return

        send_custom_message(openid, build_top10_summary_text(snapshot))
    except Exception as exc:
        logger.exception("run_top10_generation_and_notify failed openid=%s error=%r", openid, exc)
        try:
            send_custom_message(openid, f"生成 Top10 失败：{exc}")
        except Exception:
            logger.exception("send top10 error message failed openid=%s", openid)


def build_text_reply(to_user: str, from_user: str, content: str, timestamp: str) -> str:
    return f"""<xml>
<ToUserName><![CDATA[{to_user}]]></ToUserName>
<FromUserName><![CDATA[{from_user}]]></FromUserName>
<CreateTime>{timestamp}</CreateTime>
<MsgType><![CDATA[text]]></MsgType>
<Content><![CDATA[{content}]]></Content>
</xml>"""


def is_duplicate_message(message_id: str, now_ts: float) -> bool:
    if not message_id:
        return False

    with _processed_message_ids_lock:
        expired = [
            key
            for key, seen_at in _processed_message_ids.items()
            if now_ts - seen_at > MESSAGE_DEDUP_WINDOW_SECONDS
        ]
        for key in expired:
            _processed_message_ids.pop(key, None)

        if message_id in _processed_message_ids:
            return True

        _processed_message_ids[message_id] = now_ts
        return False


def precheck_stock_input(content: str) -> tuple[bool, str | None]:
    raw = (content or "").strip()
    if not raw:
        return False, "输入内容为空，请输入股票名称或代码。"

    try:
        ts_code, _, resolve_warn = resolve_stock(raw)
    except Exception as exc:
        logger.exception("precheck_stock_input failed content=%s error=%r", raw, exc)
        return True, None

    if ts_code:
        return True, None

    if resolve_warn and ("未识别到股票" in resolve_warn or "未识别到有效股票代码" in resolve_warn):
        return False, f"抱歉，暂未识别到【{raw}】对应的股票，请检查输入是否准确。"

    return False, f"抱歉，暂未识别到【{raw}】对应的股票，请检查输入是否准确。"


def split_text_content(content: str, max_chars: int = MAX_WECHAT_TEXT_CHARS) -> list[str]:
    if len(content) <= max_chars:
        return [content]

    chunks: list[str] = []
    current = ""

    for block in content.split("\n\n"):
        candidate = block if not current else current + "\n\n" + block
        if len(candidate) <= max_chars:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = ""

        while len(block) > max_chars:
            chunks.append(block[:max_chars])
            block = block[max_chars:]
        current = block

    if current:
        chunks.append(current)

    return chunks


def get_access_token() -> str:
    url = (
        "https://api.weixin.qq.com/cgi-bin/token"
        f"?grant_type=client_credential&appid={APPID}&secret={APPSECRET}"
    )
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    data = response.json()
    logger.info("get_access_token success")
    return data["access_token"]


def send_template_message(openid: str, template_id: str, url: str, data: dict) -> dict:
    access_token = get_access_token()
    api_url = (
        "https://api.weixin.qq.com/cgi-bin/message/template/send"
        f"?access_token={access_token}"
    )
    payload = {
        "touser": openid,
        "template_id": template_id,
        "url": url,
        "data": data,
    }
    response = requests.post(
        api_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        timeout=30,
    )
    response.raise_for_status()
    result = response.json()
    logger.info(
        "send_template_message result openid=%s template_id=%s result=%s",
        openid,
        template_id,
        result,
    )
    return result


def send_custom_message(openid: str, content: str) -> list[dict]:
    access_token = get_access_token()
    api_url = (
        "https://api.weixin.qq.com/cgi-bin/message/custom/send"
        f"?access_token={access_token}"
    )

    results: list[dict] = []
    chunks = split_text_content(content)
    logger.info(
        "send_custom_message start openid=%s chunks=%s content_length=%s",
        openid,
        len(chunks),
        len(content),
    )
    for chunk in chunks:
        payload = {
            "touser": openid,
            "msgtype": "text",
            "text": {"content": chunk},
        }
        response = requests.post(
            api_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
        results.append(result)
        logger.info("send_custom_message chunk_result openid=%s result=%s", openid, result)

    return results


def extract_template_fields(summary_text: str, report_text: str) -> tuple[str, str, str]:
    text = "\n".join(filter(None, [summary_text, report_text]))

    try:
        score_match = re.search(
            r"(综合匹配度[:：]?\s*[0-9]{1,3}\s*分(?:\s*[\(（][^)）]{1,20}[\)）])?|"
            r"评分[:：]?\s*[^\n]{1,30}|"
            r"操作评级[:：]?\s*[^\n]{1,30})",
            text,
            re.IGNORECASE,
        )
        score_val = score_match.group(1).strip() if score_match else "评分提取中"
    except Exception as exc:
        logger.exception("extract score failed error=%r", exc)
        score_val = "评分提取中"

    try:
        theme_match = re.search(
            r"(核心炒作题材(?:定性)?[:：]?\s*[^\n]{1,50}|核心风口[:：]?\s*[^\n]{1,50})",
            text,
            re.IGNORECASE,
        )
        theme_val = theme_match.group(1).strip() if theme_match else "题材识别中"
    except Exception as exc:
        logger.exception("extract theme failed error=%r", exc)
        theme_val = "题材识别中"

    try:
        tactics_match = re.search(
            r"(最优介入战术[:：]?\s*[^\n]{1,50}|最优介入姿势[:：]?\s*[^\n]{1,50}|"
            r"操作评级[:：]?\s*[^\n]{1,30})",
            text,
            re.IGNORECASE,
        )
        tactics_val = tactics_match.group(1).strip() if tactics_match else "战术评估中"
    except Exception as exc:
        logger.exception("extract tactics failed error=%r", exc)
        tactics_val = "战术评估中"

    return score_val, theme_val, tactics_val


def render_report_html(markdown_text: str) -> str:
    markdown_json = json.dumps(markdown_text, ensure_ascii=False)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
  <title>Stock Lite AI Report</title>
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
  <style>
    :root {{
      --bg: #eef2f7;
      --card: #ffffff;
      --text: #18202f;
      --muted: #52607a;
      --line: #d9e2ec;
      --accent: #0b6b69;
      --accent-soft: #e6f4f1;
      --panel: #f8fbff;
      --link: #1d4ed8;
    }}
    * {{
      box-sizing: border-box;
    }}
    body {{
      margin: 0;
      background:
        radial-gradient(circle at top right, rgba(11, 107, 105, 0.10), transparent 22%),
        linear-gradient(180deg, #f4f8fc 0%, var(--bg) 100%);
      color: var(--text);
      font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
      line-height: 1.75;
    }}
    .wrap {{
      max-width: 920px;
      margin: 0 auto;
      padding: 24px 16px 48px;
    }}
    .card {{
      background: var(--card);
      border-radius: 24px;
      box-shadow: 0 18px 50px rgba(15, 23, 42, 0.10);
      padding: 28px 24px 34px;
      border: 1px solid rgba(217, 226, 236, 0.95);
    }}
    .eyebrow {{
      color: var(--accent);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.08em;
      margin-bottom: 10px;
      text-transform: uppercase;
    }}
    #content, #content * {{
      color: inherit;
    }}
    h1, h2, h3 {{
      line-height: 1.35;
      margin-top: 1.4em;
      margin-bottom: 0.6em;
    }}
    h1 {{
      font-size: 34px;
      margin-top: 0;
      margin-bottom: 18px;
    }}
    h2 {{
      font-size: 22px;
      padding: 12px 14px;
      border-radius: 14px;
      background: var(--panel);
      border: 1px solid var(--line);
    }}
    h3 {{
      font-size: 18px;
    }}
    p, li, ul, ol {{
      font-size: 16px;
    }}
    ul, ol {{
      padding-left: 24px;
    }}
    li {{
      margin: 10px 0;
    }}
    strong {{
      color: var(--text);
    }}
    code {{
      background: #eef4ff;
      color: #174ea6;
      padding: 2px 8px;
      border-radius: 6px;
      font-size: 0.92em;
    }}
    pre {{
      background: #162033;
      color: #f8fbff;
      padding: 14px;
      border-radius: 12px;
      overflow-x: auto;
    }}
    pre code {{
      background: transparent;
      color: inherit;
      padding: 0;
    }}
    blockquote {{
      margin: 1em 0;
      padding: 0.8em 1em;
      background: var(--accent-soft);
      border-left: 4px solid #67b7ab;
      color: var(--muted);
      border-radius: 10px;
    }}
    table {{
      width: max-content;
      min-width: 100%;
      border-collapse: collapse;
      margin: 0;
      white-space: nowrap;
    }}
    th, td {{
      border: 1px solid var(--line);
      padding: 10px 12px;
      text-align: left;
      font-size: 14px;
      vertical-align: top;
      background: #fff;
    }}
    th {{
      background: var(--panel);
    }}
    a {{
      color: var(--link);
      text-decoration: none;
      word-break: break-all;
    }}
    a:hover {{
      text-decoration: underline;
    }}
    .table-scroll {{
      width: 100%;
      overflow-x: auto;
      overflow-y: hidden;
      margin: 16px 0;
      -webkit-overflow-scrolling: touch;
      touch-action: pan-x;
    }}
    .table-scroll::-webkit-scrollbar {{
      height: 6px;
    }}
    .table-scroll::-webkit-scrollbar-thumb {{
      background: #cbd5e1;
      border-radius: 999px;
    }}
    @media (max-width: 640px) {{
      .wrap {{
        padding: 12px 8px 28px;
      }}
      .card {{
        padding: 18px 14px 24px;
        border-radius: 16px;
      }}
      h1 {{
        font-size: 26px;
      }}
      h2 {{
        font-size: 18px;
        padding: 10px 12px;
      }}
      p, li, ul, ol {{
        font-size: 15px;
      }}
      th, td {{
        font-size: 13px;
        padding: 8px 10px;
      }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <div class="eyebrow">Stock Lite AI Report</div>
      <div id="content"></div>
    </div>
  </div>
  <script>
    const markdown = {markdown_json};
    const content = document.getElementById("content");
    content.innerHTML = marked.parse(markdown);
    content.querySelectorAll("table").forEach((table) => {{
      const wrapper = document.createElement("div");
      wrapper.className = "table-scroll";
      table.parentNode.insertBefore(wrapper, table);
      wrapper.appendChild(table);
    }});
  </script>
</body>
</html>"""


def run_real_ai_analysis(openid: str, stock_name: str) -> None:
    logger.info("run_real_ai_analysis start openid=%s stock=%s", openid, stock_name)
    try:
        bundle = generate_report_bundle(stock_name=stock_name, username=openid)
        report_id = str(uuid.uuid4())
        markdown_path = save_report(
            report_id=report_id,
            openid=openid,
            stock_name=bundle.stock_name,
            stock_code=bundle.stock_code,
            summary=bundle.summary,
            markdown_text=bundle.combined_markdown,
        )

        abstract_text = bundle.summary or "摘要提取中，请点击卡片查看完整深度研报。"
        score_val, theme_val, tactics_val = extract_template_fields(
            summary_text=abstract_text,
            report_text=bundle.combined_markdown,
        )
        template_data = {
            "stock": {"value": bundle.stock_name or stock_name, "color": "#173177"},
            "score": {"value": score_val, "color": "#FF0000"},
            "theme": {"value": theme_val, "color": "#173177"},
            "tactics": {"value": tactics_val, "color": "#173177"},
            "time": {
                "value": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "color": "#173177",
            },
            "remark": {
                "value": "\n👉 测算完成！您的《中线价值投机全景报告》已生成，请点击本卡片查看排版精美的深度推演网页。\n",
                "color": "#888888",
            },
        }
        template_result = send_template_message(
            openid=openid,
            template_id=TEMPLATE_ID,
            url=f"{BASE_URL}/report/{report_id}",
            data=template_data,
        )
        logger.info(
            "run_real_ai_analysis success openid=%s stock=%s report_id=%s markdown_path=%s template_result=%s",
            openid,
            stock_name,
            report_id,
            markdown_path,
            template_result,
        )
    except Exception as exc:
        logger.exception(
            "run_real_ai_analysis failed openid=%s stock=%s error=%r",
            openid,
            stock_name,
            exc,
        )
        error_text = f"抱歉，未匹配到名为【{stock_name}】的股票数据，请检查输入是否准确。"
        error_message = str(exc)
        error_code = "unknown_error"
        user_message = error_text
        if "api key" in error_message.lower() or "尚未配置" in error_message:
            error_code = "ai_key_missing"
            user_message = (
                "抱歉，当前分析服务尚未完成模型密钥配置，暂时不能生成研报。\n"
                "系统已经记录这次失败，请稍后再试。"
            )
        elif "未识别到股票" in error_message or "无法识别股票" in error_message:
            error_code = "stock_not_found"
            user_message = f"抱歉，暂未识别到【{stock_name}】对应的股票，请检查输入是否准确。"
        elif "quota" in error_message.lower() or "insufficient" in error_message.lower():
            error_code = "ai_quota_error"
            user_message = "抱歉，当前模型额度不足，暂时无法生成研报，请稍后再试。"
        elif "连接" in error_message or "timeout" in error_message.lower() or "network" in error_message.lower():
            error_code = "network_error"
            user_message = "抱歉，当前网络或数据源暂时不稳定，本次分析未完成，请稍后重试。"
        logger.error(
            "run_real_ai_analysis classified_error openid=%s stock=%s error_code=%s error_message=%s",
            openid,
            stock_name,
            error_code,
            error_message,
        )
        try:
            send_custom_message(openid, user_message)
        except Exception as send_exc:
            logger.exception(
                "send error message failed openid=%s stock=%s error=%r",
                openid,
                stock_name,
                send_exc,
            )


def run_kline_prediction_analysis(openid: str, stock_name: str) -> None:
    logger.info("run_kline_prediction_analysis start openid=%s stock=%s", openid, stock_name)
    try:
        logger.info("run_kline_prediction_analysis stage=ensure_dataset openid=%s stock=%s", openid, stock_name)
        ensure_research_dataset()
        logger.info("run_kline_prediction_analysis stage=build_report openid=%s stock=%s", openid, stock_name)
        result = build_kline_prediction_report(stock_name)
        logger.info(
            "run_kline_prediction_analysis stage=build_report_done openid=%s stock=%s resolved=%s code=%s",
            openid,
            stock_name,
            result.get("stock_name"),
            result.get("ts_code"),
        )
        report_id = str(uuid.uuid4())
        logger.info("run_kline_prediction_analysis stage=save_report openid=%s stock=%s report_id=%s", openid, stock_name, report_id)
        markdown_path = save_report(
            report_id=report_id,
            openid=openid,
            stock_name=result["stock_name"],
            stock_code=result["ts_code"],
            summary=result["summary"],
            markdown_text=result["markdown"],
        )
        logger.info(
            "run_kline_prediction_analysis stage=save_report_done openid=%s stock=%s report_id=%s markdown_path=%s",
            openid,
            stock_name,
            report_id,
            markdown_path,
        )
        summary_text = (
            f"K线形态预测已完成：{result['stock_name']}({result['ts_code']})\n"
            f"未来{result['snapshot']['horizon']}日上涨概率：{result['snapshot']['up_probability']:.2f}%\n"
            f"当前形态：{result['snapshot'].get('pattern_summary', result['snapshot']['pattern_key'])}\n"
            f"详情：{BASE_URL}/report/{report_id}"
        )
        logger.info("run_kline_prediction_analysis stage=send_message openid=%s stock=%s report_id=%s", openid, stock_name, report_id)
        send_custom_message(openid, summary_text)
        logger.info("run_kline_prediction_analysis stage=send_message_done openid=%s stock=%s report_id=%s", openid, stock_name, report_id)
        logger.info(
            "run_kline_prediction_analysis success openid=%s stock=%s report_id=%s markdown_path=%s",
            openid,
            stock_name,
            report_id,
            markdown_path,
        )
    except Exception as exc:
        logger.exception(
            "run_kline_prediction_analysis failed openid=%s stock=%s error=%r",
            openid,
            stock_name,
            exc,
        )
        try:
            send_custom_message(
                openid,
                f"K线预测失败：{exc}\n可尝试发送“k线预测 600519”或稍后重试。",
            )
        except Exception:
            logger.exception("send kline error message failed openid=%s", openid)


@app.get("/wechat")
def wechat_verify(
    signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
):
    ok = verify_signature(signature, timestamp, nonce)
    logger.info(
        "GET /wechat verify ok=%s timestamp=%s nonce=%s echostr_length=%s",
        ok,
        timestamp,
        nonce,
        len(echostr),
    )
    if ok:
        return Response(content=echostr, media_type="text/plain")
    return Response(content="error", media_type="text/plain")


@app.post("/wechat")
async def wechat_message(
    request: Request,
    background_tasks: BackgroundTasks,
    signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
):
    ok = verify_signature(signature, timestamp, nonce)
    logger.info(
        "POST /wechat received client=%s ok=%s timestamp=%s nonce=%s",
        request.client.host if request.client else "unknown",
        ok,
        timestamp,
        nonce,
    )
    if not ok:
        logger.warning("POST /wechat invalid signature")
        return Response(content="error", media_type="text/plain")

    body = await request.body()
    raw_body = body.decode("utf-8", errors="replace")
    logger.info("POST /wechat raw_body=%s", raw_body[:2000])

    if not body:
        logger.warning("POST /wechat empty body")
        return Response(content="", media_type="application/xml")

    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        logger.exception("POST /wechat xml parse failed error=%r raw_body=%s", exc, raw_body[:2000])
        return Response(content="error", media_type="text/plain")

    to_user = xml_text(root, "FromUserName")
    from_user = xml_text(root, "ToUserName")
    msg_type = xml_text(root, "MsgType")
    user_content = xml_text(root, "Content")
    msg_id = xml_text(root, "MsgId")
    now_ts = datetime.now().timestamp()

    logger.info(
        "POST /wechat parsed from_user=%s to_user=%s msg_type=%s msg_id=%s content=%s",
        to_user,
        from_user,
        msg_type,
        msg_id,
        user_content,
    )

    if msg_type == "text" and is_balance_query(user_content):
        logger.info("POST /wechat token balance query openid=%s msg_id=%s", to_user, msg_id)
        reply_xml = build_text_reply(
            to_user=to_user,
            from_user=from_user,
            content=build_doubao_balance_reply(),
            timestamp=timestamp,
        )
        logger.info("POST /wechat balance reply_sent openid=%s", to_user)
        return Response(content=reply_xml, media_type="application/xml")

    if msg_type == "text":
        duplicate = is_duplicate_message(msg_id, now_ts)
        kline_stock_name = parse_kline_predict_command(user_content)
        if duplicate:
            logger.info("POST /wechat duplicate message ignored msg_id=%s content=%s", msg_id, user_content)
            reply_content = (
                f"已收到【{user_content}】请求，正在为您深度推演，"
                "预估需要1分钟输出详细分析报告，请稍候"
            )
        elif kline_stock_name is not None:
            if not kline_stock_name:
                reply_content = "请输入“k线预测 股票名或代码”，例如：k线预测 贵州茅台"
            else:
                stock_ok, stock_error = precheck_stock_input(kline_stock_name)
                if not stock_ok:
                    logger.info(
                        "POST /wechat kline precheck rejected openid=%s msg_id=%s content=%s",
                        to_user,
                        msg_id,
                        kline_stock_name,
                    )
                    reply_content = stock_error or f"抱歉，暂未识别到【{kline_stock_name}】对应的股票，请检查输入是否准确。"
                else:
                    background_tasks.add_task(run_kline_prediction_analysis, to_user, kline_stock_name)
                    logger.info(
                        "POST /wechat kline task queued openid=%s msg_id=%s content=%s",
                        to_user,
                        msg_id,
                        kline_stock_name,
                    )
                    reply_content = (
                        f"已收到【K线预测 {kline_stock_name}】请求，正在匹配历史形态、相似案例和胜率。\n"
                        "稍后会通过公众号消息返回详细推导结果链接。"
                    )
        elif is_top10_query(user_content):
            snapshot = get_latest_rank_snapshot(limit=10)
            if snapshot is None:
                reply_content = "当前还没有可用的 Top10 结果，请发送“生成Top10”先生成一轮。"
            else:
                reply_content = build_top10_summary_text(snapshot)
            logger.info("POST /wechat top10 latest openid=%s msg_id=%s", to_user, msg_id)
        elif is_top100_query(user_content):
            snapshot = get_latest_rank_snapshot(limit=100)
            if snapshot is None:
                reply_content = "当前还没有可用的 Top100 结果，请先发送“生成Top10”生成最新榜单。"
            else:
                reply_content = build_top100_summary_text(snapshot)
            logger.info("POST /wechat top100 latest openid=%s msg_id=%s", to_user, msg_id)
        elif is_top100_review_query(user_content):
            try:
                review = build_latest_top100_review()
                reply_content = build_top100_review_summary_text(review)
            except Exception as exc:
                logger.exception(
                    "POST /wechat top100 review failed openid=%s msg_id=%s error=%r",
                    to_user,
                    msg_id,
                    exc,
                )
                reply_content = f"Top100复盘结果暂不可用：{exc}"
            logger.info("POST /wechat top100 review latest openid=%s msg_id=%s", to_user, msg_id)
        elif is_top100_review_generate_command(user_content):
            background_tasks.add_task(run_top100_review_generation_and_notify, to_user)
            reply_content = (
                "已收到【Top100复盘生成】请求，正在为您生成最新复盘结果。\n"
                f"完成后会通过公众号消息通知您，也可稍后打开：{BASE_URL}/top100/review/latest"
            )
            logger.info("POST /wechat top100 review generate openid=%s msg_id=%s", to_user, msg_id)
        elif is_top10_generate_command(user_content):
            status = get_top10_generation_status() or {}
            if status.get("status") == "running":
                reply_content = f"Top10 正在生成中，请稍候查看：{BASE_URL}/top10/latest"
            else:
                background_tasks.add_task(run_top10_generation_and_notify, to_user)
                reply_content = (
                    "已收到【生成Top10】请求，正在为您生成最新榜单。\n"
                    f"完成后会通过公众号消息通知您，也可稍后打开：{BASE_URL}/top10/latest"
                )
            logger.info("POST /wechat top10 generate openid=%s msg_id=%s", to_user, msg_id)
        elif not is_valid_stock_input(user_content):
            logger.info("POST /wechat rejected invalid stock input content=%s", user_content)
            reply_content = "输入格式有误。请输入准确的股票名称或代码（例如：贵州茅台 或 600519），或发送“k线预测 股票名/代码”。"
        else:
            stock_ok, stock_error = precheck_stock_input(user_content)
            if not stock_ok:
                logger.info(
                    "POST /wechat stock precheck rejected openid=%s msg_id=%s content=%s",
                    to_user,
                    msg_id,
                    user_content,
                )
                reply_content = stock_error or f"抱歉，暂未识别到【{user_content}】对应的股票，请检查输入是否准确。"
            else:
                background_tasks.add_task(run_real_ai_analysis, to_user, user_content)
                logger.info("POST /wechat task queued openid=%s msg_id=%s content=%s", to_user, msg_id, user_content)
                reply_content = (
                    f"已收到【{user_content}】请求，正在为您深度推演，"
                    "预估需要1分钟输出详细分析报告，请稍候"
                )
    else:
        reply_content = f"已收到你的{msg_type or '未知类型'}消息，暂时仅支持文本分析请求。"

    reply_xml = build_text_reply(
        to_user=to_user,
        from_user=from_user,
        content=reply_content,
        timestamp=timestamp,
    )
    logger.info("POST /wechat reply_sent openid=%s msg_type=%s", to_user, msg_type)
    return Response(content=reply_xml, media_type="application/xml")


@app.get("/report/{report_id}")
def get_report_page(report_id: str):
    logger.info("GET /report/%s", report_id)
    report = load_report(report_id)
    if report is None:
        logger.warning("GET /report/%s not_found", report_id)
        return HTMLResponse("<h1>报告不存在或已丢失</h1>", status_code=404)
    return HTMLResponse(render_report_html(report["markdown_text"]))


@app.get("/top10/latest")
def get_top10_page():
    logger.info("GET /top10/latest")
    snapshot = get_latest_rank_snapshot(limit=10)
    if snapshot is None:
        logger.warning("GET /top10/latest no_snapshot")
        return HTMLResponse("<h1>当前还没有可用的 Top10 结果</h1>", status_code=404)
    return HTMLResponse(render_top10_html(snapshot))


@app.get("/top100/latest")
def get_top100_page():
    logger.info("GET /top100/latest")
    snapshot = get_latest_rank_snapshot(limit=100)
    if snapshot is None:
        logger.warning("GET /top100/latest no_snapshot")
        return HTMLResponse("<h1>当前还没有可用的 Top100 结果</h1>", status_code=404)
    return HTMLResponse(render_top100_html(snapshot))


@app.get("/top100/review/latest")
def get_top100_review_page():
    logger.info("GET /top100/review/latest")
    try:
        review = build_latest_top100_review()
    except Exception as exc:
        logger.exception("GET /top100/review/latest failed error=%r", exc)
        return HTMLResponse(f"<h1>当前还没有可用的 Top100 复盘结果</h1><p>{escape(str(exc))}</p>", status_code=404)
    return HTMLResponse(render_top100_review_html(review))


@app.get("/prompt/current")
def get_current_prompt_page():
    logger.info("GET /prompt/current")
    if not PROMPT_HTML_PATH.exists():
        logger.warning("GET /prompt/current missing_file=%s", PROMPT_HTML_PATH)
        return HTMLResponse("<h1>当前 Prompt 页面尚未生成</h1>", status_code=404)
    return HTMLResponse(PROMPT_HTML_PATH.read_text(encoding="utf-8"))


@app.get("/api/token-balance")
def get_token_balance(model_name: str | None = Query(default=None)):
    """Return realtime provider balance snapshots."""
    logger.info("GET /api/token-balance model_name=%s", model_name)
    try:
        from services.token_balance_service import get_token_balance_snapshot

        return get_token_balance_snapshot(model_name=model_name)
    except Exception as exc:
        logger.exception("token balance endpoint failed to initialize: %s", exc)
        return {
            "status": "error",
            "message": f"token balance service unavailable: {exc}",
        }


if __name__ == "__main__":
    logger.info("wechat server starting on 127.0.0.1:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000)
