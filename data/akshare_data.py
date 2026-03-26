"""akshare 补充数据 — 分析师一致预期"""

import logging

logger = logging.getLogger(__name__)

try:
    import akshare as ak
except ImportError:
    ak = None


def get_analyst_consensus(stock_code: str) -> dict | None:
    """获取分析师一致预期（盈利预测）

    Args:
        stock_code: 6位数字代码（如 '000001'）

    Returns:
        dict with keys like 'year', 'eps', 'revenue', 'net_profit' or None
    """
    if ak is None:
        return None

    try:
        df = ak.stock_profit_forecast_em(symbol=stock_code)
        if df is not None and not df.empty:
            return {
                "source": "eastmoney",
                "data": df.head(5).to_dict(orient="records"),
                "text": df.head(5).to_string(index=False),
            }
    except Exception as e:
        logger.debug("[get_analyst_consensus] stock_profit_forecast_em 失败: %s", e)

    try:
        df = ak.stock_profit_forecast(symbol=stock_code)
        if df is not None and not df.empty:
            return {
                "source": "eastmoney_v2",
                "data": df.head(5).to_dict(orient="records"),
                "text": df.head(5).to_string(index=False),
            }
    except Exception as e:
        logger.debug("[get_analyst_consensus] stock_profit_forecast 失败: %s", e)

    return None
