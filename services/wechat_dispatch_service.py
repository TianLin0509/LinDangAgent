from __future__ import annotations

from dataclasses import dataclass

from services.wechat_command_service import (
    MessageDeduplicator,
    is_top10_generate_command,
    is_top10_query,
    is_top100_query,
    is_top100_review_generate_command,
    is_top100_review_query,
    is_valid_stock_input,
    parse_kline_predict_command,
    precheck_stock_input,
)


@dataclass(frozen=True)
class DispatchResult:
    reply_content: str
    action: str | None = None
    action_arg: str | None = None


def dispatch_text_message(
    *,
    user_content: str,
    msg_id: str,
    to_user: str,
    now_ts: float,
    base_url: str,
    deduplicator: MessageDeduplicator,
    top10_snapshot_getter,
    top100_snapshot_getter,
    top100_review_summary_getter,
    top10_generation_status_getter,
) -> DispatchResult:
    duplicate = deduplicator.is_duplicate(msg_id, now_ts)
    kline_stock_name = parse_kline_predict_command(user_content)

    if duplicate:
        return DispatchResult("这条消息已经处理过了，请不要重复发送；如果没收到结果，稍等一会儿我会继续推送。")

    if kline_stock_name is not None:
        if not kline_stock_name:
            return DispatchResult("请在“K线预测”后面带上股票名称或代码，例如：K线预测 600519")
        stock_ok, stock_error = precheck_stock_input(kline_stock_name)
        if not stock_ok:
            return DispatchResult(stock_error or "股票输入有误，请重新发送。")
        return DispatchResult(
            reply_content=f"已收到 {kline_stock_name} 的K线预测请求，正在匹配历史形态并生成分析结果。",
            action="run_kline_prediction_analysis",
            action_arg=kline_stock_name,
        )

    if is_top10_query(user_content):
        snapshot = top10_snapshot_getter()
        if snapshot is None:
            return DispatchResult("暂时还没有可用的 Top10 结果，可以先发送“生成top10”。")
        return DispatchResult(snapshot)

    if is_top100_query(user_content):
        snapshot = top100_snapshot_getter()
        if snapshot is None:
            return DispatchResult("暂时还没有可用的 Top100 结果，请稍后再试。")
        return DispatchResult(snapshot)

    if is_top100_review_query(user_content):
        return DispatchResult(top100_review_summary_getter())

    if is_top100_review_generate_command(user_content):
        return DispatchResult(
            reply_content=f"已开始生成 Top100 复盘，完成后会继续通知你，也可以稍后访问 {base_url}/top100/review/latest",
            action="run_top100_review_generation_and_notify",
            action_arg=to_user,
        )

    if is_top10_generate_command(user_content):
        status = top10_generation_status_getter() or {}
        if status.get("status") == "running":
            return DispatchResult(f"Top10 任务已经在运行中，稍后查看 {base_url}/top10/latest")
        return DispatchResult(
            reply_content=f"已开始生成 Top10，完成后会继续通知你，也可以稍后访问 {base_url}/top10/latest",
            action="run_top10_generation_and_notify",
            action_arg=to_user,
        )

    if not is_valid_stock_input(user_content):
        return DispatchResult(
            "请输入股票名称或代码。我支持单股分析、K线预测、Top10、Top100 和 Top100复盘，例如：贵州茅台、600519、K线预测 000001、top10。"
        )

    stock_ok, stock_error = precheck_stock_input(user_content)
    if not stock_ok:
        return DispatchResult(stock_error or "股票输入有误，请重新发送。")

    return DispatchResult(
        reply_content=f"已收到 {user_content} 的分析请求，正在生成公众号研报，请稍候。",
        action="run_real_ai_analysis",
        action_arg=user_content,
    )
