from __future__ import annotations

import json
import sys
from datetime import datetime
from html import escape
from pathlib import Path
import re


NAME_KEYS = ("股票名称", "stock_name", "name")
CODE_KEYS = ("代码", "ts_code", "stock_code", "symbol")
SCORE_KEYS = ("综合匹配度", "综合评分", "match_score", "score")
REPORT_KEYS = ("报告链接", "report_url", "url")


def get_top10_repo_dir(base_dir: Path) -> Path:
    repo_dir = base_dir / "Stock_top10"
    if repo_dir.exists():
        return repo_dir
    return base_dir.parent / "Stock_top10"


def ensure_top10_import_path(top10_repo_dir: Path) -> None:
    top10_root = str(top10_repo_dir)
    if top10_root not in sys.path:
        sys.path.insert(0, top10_root)


def latest_top10_result_file(top10_cache_dir: Path) -> Path | None:
    if not top10_cache_dir.exists():
        return None
    candidates = [
        path for path in top10_cache_dir.glob("*.json")
        if "deep_status" not in path.name.lower()
    ]
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def latest_top10_status_file(top10_cache_dir: Path) -> Path | None:
    if not top10_cache_dir.exists():
        return None
    candidates = list(top10_cache_dir.glob("*deep_status.json"))
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def normalize_report_link(url: str, base_url: str) -> str:
    text = (url or "").strip()
    if not text:
        return ""
    if text.startswith("/report/"):
        return f"{base_url}{text}"
    match = re.search(r"/report/([0-9a-fA-F-]{36})", text)
    if match:
        return f"{base_url}/report/{match.group(1)}"
    return text


def row_get(row: dict, *keys: str, default: object = "") -> object:
    for key in keys:
        if key in row and row.get(key) not in (None, ""):
            return row.get(key)
    return default


def format_money(value: object) -> str:
    if value in (None, ""):
        return "N/A"
    try:
        return f"{float(value):,.2f}"
    except Exception:
        return str(value)


def format_int(value: object) -> str:
    if value in (None, ""):
        return "0"
    try:
        return f"{int(value):,}"
    except Exception:
        return str(value)


def format_score_value(value: object) -> str:
    try:
        return f"{float(value):.2f}".rstrip("0").rstrip(".")
    except Exception:
        return str(value or "")


def _normalize_rank_rows(results: list[dict], raw_links: list[str], base_url: str, limit: int) -> list[dict]:
    rows: list[dict] = []
    for idx, item in enumerate(results[:limit], start=1):
        row = dict(item)
        row["rank"] = idx
        row["股票名称"] = row_get(row, *NAME_KEYS, default="")
        row["代码"] = row_get(row, *CODE_KEYS, default="")
        report_link = row_get(row, *REPORT_KEYS, default="")
        if not report_link:
            for value in row.values():
                text = str(value or "")
                if "/report/" in text:
                    report_link = text
                    break
        if not report_link and idx - 1 < len(raw_links):
            report_link = raw_links[idx - 1]
        row["报告链接"] = normalize_report_link(str(report_link), base_url)
        row["综合匹配度"] = row_get(row, *SCORE_KEYS, default="")
        rows.append(row)
    return rows


def get_latest_rank_snapshot(
    *,
    top10_cache_dir: Path,
    base_url: str,
    limit: int,
) -> dict | None:
    result_file = latest_top10_result_file(top10_cache_dir)
    if result_file is None:
        return None

    result_data = json.loads(result_file.read_text(encoding="utf-8"))
    status_data: dict = {}
    status_file = latest_top10_status_file(top10_cache_dir)
    if status_file is not None:
        status_data = json.loads(status_file.read_text(encoding="utf-8"))

    results = result_data.get("results") or []
    raw_links = status_data.get("top10_links") or []
    rows = _normalize_rank_rows(results, raw_links, base_url, limit)
    finished = status_data.get("finished") or datetime.fromtimestamp(
        result_file.stat().st_mtime
    ).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "model": result_data.get("model") or status_data.get("model") or "",
        "tokens_used": result_data.get("tokens_used") or status_data.get("tokens_used") or 0,
        "summary": result_data.get("summary") or "",
        "date": result_data.get("date") or "",
        "finished": finished,
        "scored_count": status_data.get("scored_count") or len(results),
        "status": status_data.get("status") or "done",
        "requested_limit": limit,
        "actual_count": len(rows),
        "rows": rows,
        "source_file": str(result_file),
        "status_file": str(status_file) if status_file else "",
    }


def build_rank_summary_text(snapshot: dict, *, label: str, path: str, base_url: str) -> str:
    rows = snapshot.get("rows") or []
    if not rows:
        return f"暂时没有可用的 {label} 结果。"

    lines = [
        f"{label} 最新结果",
        f"模型：{snapshot.get('model') or 'N/A'}",
        f"Token 消耗：{format_int(snapshot.get('tokens_used'))}",
        f"完成时间：{snapshot.get('finished') or 'N/A'}",
        f"结果数量：{format_int(snapshot.get('actual_count') or len(rows))}",
        f"查看详情：{base_url}{path}",
        "",
        "前 3 名：",
    ]
    for row in rows[:3]:
        lines.append(
            f"{row.get('rank')}. {row.get('股票名称') or 'N/A'} {row.get('代码') or ''} 分数 {format_score_value(row.get('综合匹配度'))}"
        )
    return "\n".join(lines)


def build_top100_review_summary_text(review: dict, *, base_url: str) -> str:
    rows = review.get("rows") or []
    if not rows:
        return "暂时没有可用的 Top100 复盘结果。"

    lines = [
        "Top100 最新复盘",
        f"生成时间：{review.get('generated_at') or 'N/A'}",
        f"对比交易日：{review.get('compare_trade_date') or 'N/A'}",
        f"模型：{review.get('model') or 'N/A'}",
        f"Token 消耗：{format_int(review.get('tokens_used'))}",
        f"查看详情：{base_url}/top100/review/latest",
        "",
        "前 3 名：",
    ]
    for row in rows[:3]:
        daily_pct = row.get("daily_pct_chg")
        open_buy_pct = row.get("open_buy_pct")
        daily_text = "N/A" if daily_pct is None else f"{float(daily_pct):.2f}%"
        open_buy_text = "N/A" if open_buy_pct is None else f"{float(open_buy_pct):.2f}%"
        lines.append(
            f"{row.get('rank')}. {row.get('stock_name') or 'N/A'} {row.get('ts_code') or ''} 当日涨跌 {daily_text} 开盘买入 {open_buy_text}"
        )
    return "\n".join(lines)


def _render_html_page(title: str, heading: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    :root {{
      --bg: #eef3f8;
      --card: #ffffff;
      --line: #d9e2ec;
      --text: #17212f;
      --muted: #52606d;
      --accent: #0f766e;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
      background: linear-gradient(180deg, #f8fbff 0%, var(--bg) 100%);
      color: var(--text);
    }}
    .wrap {{ max-width: 1100px; margin: 0 auto; padding: 24px 16px 40px; }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 24px;
      box-shadow: 0 18px 40px rgba(15, 23, 42, 0.08);
      padding: 24px;
    }}
    h1 {{ margin: 0 0 16px; font-size: 30px; }}
    .meta {{ color: var(--muted); margin-bottom: 16px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ border: 1px solid var(--line); padding: 10px 12px; text-align: left; vertical-align: top; font-size: 14px; }}
    th {{ background: #f8fafc; }}
    a {{ color: var(--accent); text-decoration: none; }}
    .summary {{ white-space: pre-wrap; background: #f8fbfd; border: 1px solid var(--line); border-radius: 14px; padding: 14px; margin: 16px 0; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>{escape(heading)}</h1>
      {body}
    </div>
  </div>
</body>
</html>"""


def render_rank_html(snapshot: dict, *, title: str, heading: str) -> str:
    rows = snapshot.get("rows") or []
    summary = escape(snapshot.get("summary") or "")
    meta = (
        f"<div class='meta'>模型：{escape(str(snapshot.get('model') or 'N/A'))} | "
        f"完成时间：{escape(str(snapshot.get('finished') or 'N/A'))} | "
        f"数量：{escape(str(snapshot.get('actual_count') or len(rows)))}</div>"
    )
    summary_html = f"<div class='summary'>{summary}</div>" if summary else ""
    table_rows = []
    for row in rows:
        link = row.get("报告链接") or ""
        link_html = f"<a href='{escape(link)}' target='_blank'>查看报告</a>" if link else "-"
        table_rows.append(
            "<tr>"
            f"<td>{escape(str(row.get('rank') or ''))}</td>"
            f"<td>{escape(str(row.get('股票名称') or ''))}</td>"
            f"<td>{escape(str(row.get('代码') or ''))}</td>"
            f"<td>{escape(format_score_value(row.get('综合匹配度')))}</td>"
            f"<td>{link_html}</td>"
            "</tr>"
        )
    body = (
        meta
        + summary_html
        + "<table><thead><tr><th>排名</th><th>股票</th><th>代码</th><th>分数</th><th>报告</th></tr></thead><tbody>"
        + "".join(table_rows)
        + "</tbody></table>"
    )
    return _render_html_page(title, heading, body)


def render_top100_review_html(review: dict) -> str:
    rows = review.get("rows") or []
    meta = (
        f"<div class='meta'>生成时间：{escape(str(review.get('generated_at') or 'N/A'))} | "
        f"交易日：{escape(str(review.get('compare_trade_date') or 'N/A'))} | "
        f"模型：{escape(str(review.get('model') or 'N/A'))}</div>"
    )
    table_rows = []
    for row in rows:
        daily = row.get("daily_pct_chg")
        open_buy = row.get("open_buy_pct")
        market = row.get("market_pct_chg")
        table_rows.append(
            "<tr>"
            f"<td>{escape(str(row.get('rank') or ''))}</td>"
            f"<td>{escape(str(row.get('stock_name') or ''))}</td>"
            f"<td>{escape(str(row.get('ts_code') or ''))}</td>"
            f"<td>{escape(format_score_value(row.get('match_score')))}</td>"
            f"<td>{escape(str(row.get('short_term') or '-'))}</td>"
            f"<td>{escape('N/A' if daily is None else f'{float(daily):.2f}%')}</td>"
            f"<td>{escape('N/A' if open_buy is None else f'{float(open_buy):.2f}%')}</td>"
            f"<td>{escape('N/A' if market is None else f'{float(market):.2f}%')}</td>"
            "</tr>"
        )
    body = (
        meta
        + "<table><thead><tr><th>排名</th><th>股票</th><th>代码</th><th>匹配分</th><th>短线建议</th><th>当日涨跌</th><th>开盘买入</th><th>大盘涨跌</th></tr></thead><tbody>"
        + "".join(table_rows)
        + "</tbody></table>"
    )
    return _render_html_page("Top100 复盘", "Top100 最新复盘", body)
