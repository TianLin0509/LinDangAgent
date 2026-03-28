import json
import logging
import re
import uuid
from datetime import datetime
from html import escape
from pathlib import Path
from xml.etree import ElementTree as ET

import requests
import uvicorn
from fastapi import BackgroundTasks, FastAPI, Query, Request
from fastapi.responses import HTMLResponse, Response

from repositories.report_repo import get_report as load_report
from repositories.report_repo import init_db, save_report, DB_PATH
from services import rank_service, wechat_command_service, wechat_dispatch_service
from services.analysis_service import generate_report_bundle
from services.prebuilt_kline_service import (
    build_kline_prediction_report,
    ensure_research_dataset,
)
from services.top100_review_service import build_latest_top100_review
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
TOP10_REPO_DIR = rank_service.get_top10_repo_dir(BASE_DIR)
TOP10_CACHE_DIR = TOP10_REPO_DIR / "cache"
TOP10_DEFAULT_MODEL = get_secret(
    "TOP10_MODEL_NAME",
    "\U0001f7e3 \u8c46\u5305 \u00b7 Seed 2.0 Pro",  # 🟣 豆包 · Seed 2.0 Pro
)
MESSAGE_DEDUPLICATOR = wechat_command_service.MessageDeduplicator(window_seconds=600)

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


@app.on_event("startup")
async def _startup():
    """启动时注册知识库定时任务。"""
    try:
        from knowledge.scheduler import start_scheduler
        start_scheduler()
    except Exception as exc:
        logger.warning("knowledge scheduler startup failed: %r", exc)


@app.get("/api/health")
async def health_check():
    """健康检查端点 — 供负载均衡和监控使用"""
    from data.tushare_client import get_data_source, get_ts_error
    checks = {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "data_source": get_data_source(),
        "tushare_error": get_ts_error() or None,
        "db_path": str(DB_PATH) if DB_PATH.exists() else "missing",
    }
    return checks


def _fmt_money(value: object) -> str:
    return rank_service.format_money(value)


def _fmt_int(value: object) -> str:
    return rank_service.format_int(value)


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
            return "当前没有配置可用的豆包模型。"

        snapshot = get_token_balance_snapshot(model_name=doubao_model_name)
        providers = snapshot.get("providers") or []
        provider = providers[0] if providers else {}
        status = provider.get("status", "unknown")
        account = provider.get("account") or {}
        currency = account.get("currency") or "CNY"
        local_usage = snapshot.get("local_token_usage") or {}

        if status == "ok":
            return (
                "豆包账户余额信息\n"
                f"可用余额：{_fmt_money(account.get('available_balance'))} {currency}\n"
                f"可提现余额：{_fmt_money(account.get('available_balance_available'))} {currency}\n"
                f"冻结余额：{_fmt_money(account.get('available_balance_unavailable'))} {currency}\n"
                f"授信余额：{_fmt_money(account.get('credit_balance'))} {currency}\n"
                f"本地累计 Token：{_fmt_int(local_usage.get('total'))}\n"
                f"查询时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}"
            )

        if status == "credential_required":
            return (
                "豆包余额查询需要先配置火山引擎凭证。\n"
                "请检查环境变量 `VOLC_ACCESSKEY` 和 `VOLC_SECRETKEY` 是否已设置。"
            )

        message = provider.get("message") or "未知错误"
        return f"豆包余额查询失败：{message}"
    except Exception as exc:
        logger.exception("build_doubao_balance_reply failed: %s", exc)
        return f"豆包余额查询失败：{exc}"


def get_latest_rank_snapshot(limit: int) -> dict | None:
    return rank_service.get_latest_rank_snapshot(
        top10_cache_dir=TOP10_CACHE_DIR,
        base_url=BASE_URL,
        limit=limit,
    )


def build_top10_summary_text(snapshot: dict) -> str:
    return rank_service.build_rank_summary_text(snapshot, label="Top10", path="/top10/latest", base_url=BASE_URL)


def build_top100_summary_text(snapshot: dict) -> str:
    return rank_service.build_rank_summary_text(snapshot, label="Top100", path="/top100/latest", base_url=BASE_URL)


def build_top100_review_summary_text(review: dict) -> str:
    return rank_service.build_top100_review_summary_text(review, base_url=BASE_URL)


def render_top10_html(snapshot: dict) -> str:
    return rank_service.render_rank_html(snapshot, title="Top10 最新结果", heading="Top10 最新结果")


def render_top100_html(snapshot: dict) -> str:
    return rank_service.render_rank_html(snapshot, title="Top100 最新结果", heading="Top100 最新结果")


def render_top100_review_html(review: dict) -> str:
    return rank_service.render_top100_review_html(review)


def get_top10_generation_status() -> dict | None:
    try:
        rank_service.ensure_top10_import_path(TOP10_REPO_DIR)
        from top10.deep_runner import get_deep_status

        return get_deep_status()
    except Exception:
        logger.exception("get_top10_generation_status failed")
        return None


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
    logger.info("send_template_message result openid=%s template_id=%s result=%s", openid, template_id, result)
    return result


def send_custom_message(openid: str, content: str) -> list[dict]:
    access_token = get_access_token()
    api_url = (
        "https://api.weixin.qq.com/cgi-bin/message/custom/send"
        f"?access_token={access_token}"
    )

    results: list[dict] = []
    chunks = wechat_command_service.split_text_content(content, MAX_WECHAT_TEXT_CHARS)
    logger.info("send_custom_message start openid=%s chunks=%s content_length=%s", openid, len(chunks), len(content))
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

    score_match = re.search(r"(评分|综合评分|匹配度)[:：]\s*([^\n]{1,30})", text, re.IGNORECASE)
    theme_match = re.search(r"(主题|核心逻辑|投资主线)[:：]\s*([^\n]{1,50})", text, re.IGNORECASE)
    tactics_match = re.search(r"(策略|建议|操作建议)[:：]\s*([^\n]{1,50})", text, re.IGNORECASE)

    score_val = score_match.group(2).strip() if score_match else "见完整报告"
    theme_val = theme_match.group(2).strip() if theme_match else "见完整报告"
    tactics_val = tactics_match.group(2).strip() if tactics_match else "见完整报告"
    return score_val, theme_val, tactics_val


def render_report_html(markdown_text: str) -> str:
    markdown_json = json.dumps(markdown_text, ensure_ascii=False)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
  <title>LinDangAgent AI Report</title>
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
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background:
        radial-gradient(circle at top right, rgba(11, 107, 105, 0.10), transparent 22%),
        linear-gradient(180deg, #f4f8fc 0%, var(--bg) 100%);
      color: var(--text);
      font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
      line-height: 1.75;
    }}
    .wrap {{ max-width: 920px; margin: 0 auto; padding: 24px 16px 48px; }}
    .card {{
      background: var(--card);
      border-radius: 24px;
      box-shadow: 0 18px 50px rgba(15, 23, 42, 0.10);
      padding: 28px 24px 34px;
      border: 1px solid rgba(217, 226, 236, 0.95);
    }}
    .eyebrow {{ color: var(--accent); font-size: 12px; font-weight: 700; letter-spacing: 0.08em; margin-bottom: 10px; text-transform: uppercase; }}
    #content, #content * {{ color: inherit; }}
    h1, h2, h3 {{ line-height: 1.35; margin-top: 1.4em; margin-bottom: 0.6em; }}
    h1 {{ font-size: 34px; margin-top: 0; margin-bottom: 18px; }}
    h2 {{ font-size: 22px; padding: 12px 14px; border-radius: 14px; background: var(--panel); border: 1px solid var(--line); }}
    h3 {{ font-size: 18px; }}
    p, li, ul, ol {{ font-size: 16px; }}
    ul, ol {{ padding-left: 24px; }}
    li {{ margin: 10px 0; }}
    code {{ background: #eef4ff; color: #174ea6; padding: 2px 8px; border-radius: 6px; font-size: 0.92em; }}
    pre {{ background: #162033; color: #f8fbff; padding: 14px; border-radius: 12px; overflow-x: auto; }}
    pre code {{ background: transparent; color: inherit; padding: 0; }}
    blockquote {{ margin: 1em 0; padding: 0.8em 1em; background: var(--accent-soft); border-left: 4px solid #67b7ab; color: var(--muted); border-radius: 10px; }}
    table {{ width: max-content; min-width: 100%; border-collapse: collapse; margin: 0; white-space: nowrap; }}
    th, td {{ border: 1px solid var(--line); padding: 10px 12px; text-align: left; font-size: 14px; vertical-align: top; }}
    th {{ background: #f8fafc; }}
    .table-scroll {{ overflow-x: auto; width: 100%; margin: 14px 0; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <div class="eyebrow">LinDangAgent AI Report</div>
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

        abstract_text = bundle.summary or "分析已完成，请查看完整报告。"
        score_val, theme_val, tactics_val = extract_template_fields(
            summary_text=abstract_text,
            report_text=bundle.combined_markdown,
        )
        template_result = send_template_message(
            openid=openid,
            template_id=TEMPLATE_ID,
            url=f"{BASE_URL}/report/{report_id}",
            data={
                "stock": {"value": bundle.stock_name or stock_name, "color": "#173177"},
                "score": {"value": score_val, "color": "#FF0000"},
                "theme": {"value": theme_val, "color": "#173177"},
                "tactics": {"value": tactics_val, "color": "#173177"},
                "time": {"value": datetime.now().strftime("%Y-%m-%d %H:%M"), "color": "#173177"},
                "remark": {
                    "value": "\n点击模板消息可查看完整研报。",
                    "color": "#888888",
                },
            },
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
        logger.exception("run_real_ai_analysis failed openid=%s stock=%s error=%r", openid, stock_name, exc)
        error_message = str(exc).lower()
        user_message = f"生成 {stock_name} 的分析报告失败：{exc}"
        if "api key" in error_message:
            user_message = "分析服务凭证未配置完整，请检查模型 API Key。"
        elif "quota" in error_message or "insufficient" in error_message:
            user_message = "分析额度不足，请补充可用配额后再试。"
        elif "timeout" in error_message or "network" in error_message:
            user_message = "网络请求超时，本次分析未完成，请稍后重试。"
        try:
            send_custom_message(openid, user_message)
        except Exception as send_exc:
            logger.exception("send error message failed openid=%s stock=%s error=%r", openid, stock_name, send_exc)


def run_kline_prediction_analysis(openid: str, stock_name: str) -> None:
    logger.info("run_kline_prediction_analysis start openid=%s stock=%s", openid, stock_name)
    try:
        ensure_research_dataset()
        result = build_kline_prediction_report(stock_name)
        report_id = str(uuid.uuid4())
        markdown_path = save_report(
            report_id=report_id,
            openid=openid,
            stock_name=result["stock_name"],
            stock_code=result["ts_code"],
            summary=result["summary"],
            markdown_text=result["markdown"],
        )
        summary_text = (
            f"K线预测已完成：{result['stock_name']}({result['ts_code']})\n"
            f"预测周期：{result['snapshot']['horizon']} 个交易日\n"
            f"上涨概率：{result['snapshot']['up_probability']:.2f}%\n"
            f"形态摘要：{result['snapshot'].get('pattern_summary', result['snapshot']['pattern_key'])}\n"
            f"查看报告：{BASE_URL}/report/{report_id}"
        )
        send_custom_message(openid, summary_text)
        logger.info(
            "run_kline_prediction_analysis success openid=%s stock=%s report_id=%s markdown_path=%s",
            openid,
            stock_name,
            report_id,
            markdown_path,
        )
    except Exception as exc:
        logger.exception("run_kline_prediction_analysis failed openid=%s stock=%s error=%r", openid, stock_name, exc)
        try:
            send_custom_message(openid, f"K线预测失败：{exc}\n请发送例如“K线预测 600519”重新尝试。")
        except Exception:
            logger.exception("send kline error message failed openid=%s", openid)


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
            send_custom_message(openid, f"Top100 复盘生成失败：{exc}")
        except Exception:
            logger.exception("send top100 review error message failed openid=%s", openid)


def run_sentiment_radar_and_notify(openid: str) -> None:
    logger.info("run_sentiment_radar start openid=%s", openid)
    try:
        from services.sentiment_radar import build_radar_summary_text, run_sentiment_radar

        result = run_sentiment_radar(model_name=TOP10_DEFAULT_MODEL)
        summary = build_radar_summary_text(result)
        summary += f"\n\n详情：{BASE_URL}/sentiment/latest"
        send_custom_message(openid, summary)
    except Exception as exc:
        logger.exception("run_sentiment_radar failed openid=%s error=%r", openid, exc)
        try:
            send_custom_message(openid, f"舆情雷达生成失败：{exc}")
        except Exception:
            logger.exception("send sentiment error message failed openid=%s", openid)


def run_top10_generation_and_notify(openid: str) -> None:
    logger.info("run_top10_generation start openid=%s", openid)
    try:
        rank_service.ensure_top10_import_path(TOP10_REPO_DIR)
        from top10.deep_runner import get_deep_status, is_deep_running, run_deep_top10

        status = get_deep_status() or {}
        if is_deep_running() or status.get("status") == "running":
            send_custom_message(openid, f"Top10 任务已在运行中，请稍后查看 {BASE_URL}/top10/latest")
            return

        run_deep_top10(model_name=TOP10_DEFAULT_MODEL, candidate_count=100, username=openid)
        status = get_deep_status() or {}
        if status.get("status") != "done":
            send_custom_message(openid, f"生成 Top10 失败：{status.get('error') or '未知错误'}")
            return

        snapshot = get_latest_rank_snapshot(limit=10)
        if not snapshot:
            send_custom_message(openid, "Top10 已执行完成，但暂时没有读取到结果文件。")
            return
        send_custom_message(openid, build_top10_summary_text(snapshot))
    except Exception as exc:
        logger.exception("run_top10_generation_and_notify failed openid=%s error=%r", openid, exc)
        try:
            send_custom_message(openid, f"生成 Top10 失败：{exc}")
        except Exception:
            logger.exception("send top10 error message failed openid=%s", openid)


@app.get("/wechat")
def wechat_verify(
    signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
):
    ok = wechat_command_service.verify_signature(TOKEN, signature, timestamp, nonce)
    logger.info("GET /wechat verify ok=%s timestamp=%s nonce=%s echostr_length=%s", ok, timestamp, nonce, len(echostr))
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
    ok = wechat_command_service.verify_signature(TOKEN, signature, timestamp, nonce)
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

    to_user = wechat_command_service.xml_text(root, "FromUserName")
    from_user = wechat_command_service.xml_text(root, "ToUserName")
    msg_type = wechat_command_service.xml_text(root, "MsgType")
    user_content = wechat_command_service.xml_text(root, "Content")
    msg_id = wechat_command_service.xml_text(root, "MsgId")
    now_ts = datetime.now().timestamp()

    logger.info(
        "POST /wechat parsed from_user=%s to_user=%s msg_type=%s msg_id=%s content=%s",
        to_user,
        from_user,
        msg_type,
        msg_id,
        user_content,
    )

    if msg_type == "text" and wechat_command_service.is_balance_query(user_content):
        reply_xml = wechat_command_service.build_text_reply(
            to_user=to_user,
            from_user=from_user,
            content=build_doubao_balance_reply(),
            timestamp=timestamp,
        )
        return Response(content=reply_xml, media_type="application/xml")

    if msg_type == "text":
        def _top10_summary() -> str | None:
            snapshot = get_latest_rank_snapshot(limit=10)
            return build_top10_summary_text(snapshot) if snapshot else None

        def _top100_summary() -> str | None:
            snapshot = get_latest_rank_snapshot(limit=100)
            return build_top100_summary_text(snapshot) if snapshot else None

        def _top100_review_summary() -> str:
            try:
                review = build_latest_top100_review()
                return build_top100_review_summary_text(review)
            except Exception as exc:
                logger.exception("POST /wechat top100 review failed openid=%s msg_id=%s error=%r", to_user, msg_id, exc)
                return f"Top100 复盘读取失败：{exc}"

        def _sentiment_summary() -> str:
            try:
                from services.sentiment_radar import build_radar_summary_text, get_latest_radar
                radar = get_latest_radar()
                text = build_radar_summary_text(radar)
                if radar:
                    text += f"\n\n详情：{BASE_URL}/sentiment/latest"
                return text
            except Exception as exc:
                return f"舆情雷达读取失败：{exc}"

        dispatch = wechat_dispatch_service.dispatch_text_message(
            user_content=user_content,
            msg_id=msg_id,
            to_user=to_user,
            now_ts=now_ts,
            base_url=BASE_URL,
            deduplicator=MESSAGE_DEDUPLICATOR,
            top10_snapshot_getter=_top10_summary,
            top100_snapshot_getter=_top100_summary,
            top100_review_summary_getter=_top100_review_summary,
            top10_generation_status_getter=get_top10_generation_status,
            sentiment_radar_getter=_sentiment_summary,
        )
        if dispatch.action == "run_real_ai_analysis":
            background_tasks.add_task(run_real_ai_analysis, to_user, dispatch.action_arg)
        elif dispatch.action == "run_kline_prediction_analysis":
            background_tasks.add_task(run_kline_prediction_analysis, to_user, dispatch.action_arg)
        elif dispatch.action == "run_top10_generation_and_notify":
            background_tasks.add_task(run_top10_generation_and_notify, to_user)
        elif dispatch.action == "run_top100_review_generation_and_notify":
            background_tasks.add_task(run_top100_review_generation_and_notify, to_user)
        elif dispatch.action == "run_sentiment_radar_and_notify":
            background_tasks.add_task(run_sentiment_radar_and_notify, to_user)
        reply_content = dispatch.reply_content
    else:
        reply_content = f"暂不支持消息类型：{msg_type or 'unknown'}"

    reply_xml = wechat_command_service.build_text_reply(
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
        return HTMLResponse("<h1>报告不存在</h1>", status_code=404)
    return HTMLResponse(render_report_html(report["markdown_text"]))


@app.get("/sentiment/latest")
def get_sentiment_page():
    logger.info("GET /sentiment/latest")
    try:
        from services.sentiment_radar import get_latest_radar, render_radar_html
        radar = get_latest_radar()
        if radar is None:
            return HTMLResponse("<h1>暂时没有可用的舆情雷达结果</h1><p>发送"生成舆情"来生成。</p>", status_code=404)
        return HTMLResponse(render_radar_html(radar))
    except Exception as exc:
        return HTMLResponse(f"<h1>舆情雷达加载失败</h1><p>{escape(str(exc))}</p>", status_code=500)


@app.get("/top10/latest")
def get_top10_page():
    logger.info("GET /top10/latest")
    snapshot = get_latest_rank_snapshot(limit=10)
    if snapshot is None:
        return HTMLResponse("<h1>暂时没有可用的 Top10 结果</h1>", status_code=404)
    return HTMLResponse(render_top10_html(snapshot))


@app.get("/top100/latest")
def get_top100_page():
    logger.info("GET /top100/latest")
    snapshot = get_latest_rank_snapshot(limit=100)
    if snapshot is None:
        return HTMLResponse("<h1>暂时没有可用的 Top100 结果</h1>", status_code=404)
    return HTMLResponse(render_top100_html(snapshot))


@app.get("/top100/review/latest")
def get_top100_review_page():
    logger.info("GET /top100/review/latest")
    try:
        review = build_latest_top100_review()
    except Exception as exc:
        return HTMLResponse(f"<h1>暂时没有可用的 Top100 复盘</h1><p>{escape(str(exc))}</p>", status_code=404)
    return HTMLResponse(render_top100_review_html(review))


@app.get("/prompt/current")
def get_current_prompt_page():
    logger.info("GET /prompt/current")
    if not PROMPT_HTML_PATH.exists():
        return HTMLResponse("<h1>当前没有可用的 Prompt 页面</h1>", status_code=404)
    return HTMLResponse(PROMPT_HTML_PATH.read_text(encoding="utf-8"))


@app.get("/api/token-balance")
def get_token_balance(model_name: str | None = Query(default=None)):
    logger.info("GET /api/token-balance model_name=%s", model_name)
    try:
        from services.token_balance_service import get_token_balance_snapshot

        return get_token_balance_snapshot(model_name=model_name)
    except Exception as exc:
        logger.exception("token balance endpoint failed to initialize: %s", exc)
        return {"status": "error", "message": f"token balance service unavailable: {exc}"}


@app.get("/api/knowledge/stats")
def get_knowledge_stats():
    """知识库状态概览。"""
    try:
        from knowledge.analyst_scorecard import load_scorecard
        from knowledge.outcome_tracker import get_accuracy_summary, get_top100_accuracy
        from knowledge.regime_detector import get_current_regime

        return {
            "regime": get_current_regime(),
            "accuracy": get_accuracy_summary(days=90),
            "top100_accuracy": get_top100_accuracy(days=90),
            "scorecard_summary": {
                k: v for k, v in load_scorecard().items()
                if k in ("sample_count", "directional_count", "overall", "last_updated")
            },
        }
    except Exception as exc:
        return {"error": str(exc)}


@app.post("/api/knowledge/update")
def trigger_knowledge_update():
    """手动触发知识库更新。"""
    try:
        from knowledge.scheduler import run_knowledge_update
        results = run_knowledge_update()
        return {"status": "ok", "results": results}
    except Exception as exc:
        logger.exception("knowledge update failed: %r", exc)
        return {"status": "error", "message": str(exc)}


if __name__ == "__main__":
    logger.info("wechat server starting on 127.0.0.1:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000)
