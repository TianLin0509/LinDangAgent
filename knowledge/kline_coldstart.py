"""K线盘感冷启动 — 用历史数据批量回测填充 kline_diary.db

用法：
  python -m knowledge.kline_coldstart           # 默认50只×1年
  python -m knowledge.kline_coldstart --stocks 20 --days 180
"""

import json
import logging
import uuid
from datetime import datetime, timedelta

import pandas as pd

logger = logging.getLogger(__name__)

# 代表性股票池（覆盖主要板块）
DEFAULT_STOCK_POOL = [
    ("600519.SH", "贵州茅台"), ("000858.SZ", "五粮液"),
    ("600036.SH", "招商银行"), ("601166.SH", "兴业银行"),
    ("000001.SZ", "平安银行"), ("601318.SH", "中国平安"),
    ("300750.SZ", "宁德时代"), ("002594.SZ", "比亚迪"),
    ("601012.SH", "隆基绿能"), ("300274.SZ", "阳光电源"),
    ("688981.SH", "中芯国际"), ("002049.SZ", "紫光国微"),
    ("600900.SH", "长江电力"), ("601888.SH", "中国中免"),
    ("000568.SZ", "泸州老窖"), ("002714.SZ", "牧原股份"),
    ("300059.SZ", "东方财富"), ("601899.SH", "紫金矿业"),
    ("600030.SH", "中信证券"), ("601088.SH", "中国神华"),
    ("300760.SZ", "迈瑞医疗"), ("000651.SZ", "格力电器"),
    ("600585.SH", "海螺水泥"), ("002475.SZ", "立讯精密"),
    ("600436.SH", "片仔癀"),   ("603288.SH", "海天味业"),
    ("300015.SZ", "爱尔眼科"), ("002304.SZ", "洋河股份"),
    ("601398.SH", "工商银行"), ("600276.SH", "恒瑞医药"),
    ("000333.SZ", "美的集团"), ("002415.SZ", "海康威视"),
    ("601668.SH", "中国建筑"), ("600809.SH", "山西汾酒"),
    ("002352.SZ", "顺丰控股"), ("300142.SZ", "沃森生物"),
    ("603986.SH", "兆易创新"), ("688012.SH", "中微公司"),
    ("300124.SZ", "汇川技术"), ("002371.SZ", "北方华创"),
    ("600887.SH", "伊利股份"), ("601225.SH", "陕西煤业"),
    ("002027.SZ", "分众传媒"), ("000625.SZ", "长安汽车"),
    ("601919.SH", "中远海控"), ("300033.SZ", "同花顺"),
    ("002230.SZ", "科大飞"),   ("600809.SH", "山西汾酒"),
    ("601985.SH", "中国核电"), ("300661.SZ", "圣邦股份"),
]


def run_coldstart(max_stocks: int = 50, lookback_days: int = 365,
                  min_kline_rows: int = 60) -> dict:
    """批量历史回测填充 kline_diary.db。

    对每只股票的每个交易日（跳过最近10天），识别K线形态并记录观察，
    然后用 T+5 实际涨跌回填验证结果。
    """
    import os
    for k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
        os.environ.pop(k, None)
    os.environ["NO_PROXY"] = "*"

    from knowledge.kline_patterns import detect_all_patterns, classify_position, classify_volume_state
    from knowledge.kb_db import get_manager
    from knowledge.kline_diary import _generate_prediction, \
        backtest_pending, rebuild_pattern_stats, discover_combo_patterns

    stocks = DEFAULT_STOCK_POOL[:max_stocks]
    total_obs = 0
    total_verified = 0
    errors = 0

    logger.info("[coldstart] Starting with %d stocks, %d days lookback", len(stocks), lookback_days)

    for idx, (ts_code, stock_name) in enumerate(stocks, 1):
        print(f"[{idx}/{len(stocks)}] {stock_name}({ts_code})...", end=" ", flush=True)

        # 获取 K 线数据
        try:
            from data.fallback import bs_get_price_df
            df, err = bs_get_price_df(ts_code, days=lookback_days + 30)
            if err or df is None or len(df) < min_kline_rows:
                print(f"SKIP (数据不足: {len(df) if df is not None else 0}行)")
                continue

            # kline_patterns 需要英文列名：open/high/low/close/vol
            col_map = {"日期": "date", "开盘": "open", "最高": "high",
                       "最低": "low", "收盘": "close", "成交量": "vol",
                       "成交额": "amount", "涨跌幅": "pct_chg"}
            df = df.rename(columns=col_map)
            for c in ["open", "high", "low", "close", "vol"]:
                if c in df.columns:
                    df[c] = pd.to_numeric(df[c], errors="coerce")
            df = df.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)

        except Exception as e:
            print(f"FAIL ({e})")
            errors += 1
            continue

        # 市场环境（简化：全部标记 shock）
        regime = "shock"
        stock_obs = 0

        # 滑窗：从第60行开始，到倒数第10行（留10天验证）
        for i in range(min_kline_rows, len(df) - 10):
            window = df.iloc[max(0, i - min_kline_rows):i + 1].copy()
            if len(window) < 30:
                continue

            # 只处理每5个交易日一次（降低数据量）
            if (i - min_kline_rows) % 5 != 0:
                continue

            obs_date = str(window.iloc[-1]["date"]).replace("-", "")[:8]

            # 识别形态（kline_patterns 需要英文列名：open/high/low/close/vol）
            try:
                patterns = detect_all_patterns(window)
                if not patterns:
                    continue

                position = classify_position(window)
                volume_state = classify_volume_state(window)
                pattern_ids = [p.pattern_id for p in patterns]

                # 预测
                prediction, confidence = _generate_prediction(pattern_ids, regime, position)

                # 计算 T+5 实际收益
                future_idx = min(i + 5, len(df) - 1)
                current_close = float(window.iloc[-1]["close"])
                future_close = float(df.iloc[future_idx]["close"])
                actual_return = round((future_close - current_close) / current_close * 100, 2) if current_close > 0 else 0

                # 判断命中
                hit = None
                if prediction == "bullish" and actual_return > 0:
                    hit = 1
                elif prediction == "bearish" and actual_return < 0:
                    hit = 1
                elif prediction == "neutral" and abs(actual_return) < 2:
                    hit = 1
                elif prediction in ("bullish", "bearish", "neutral"):
                    hit = 0

                # 写入 kline_diary.db
                obs_id = str(uuid.uuid4())[:8]
                mgr = get_manager()
                with mgr.write("kline_diary") as conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO kline_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (obs_id, obs_date, ts_code, stock_name,
                         json.dumps(pattern_ids), regime, position, volume_state,
                         prediction, confidence,
                         actual_return, hit,
                         datetime.now().isoformat(timespec="seconds")),
                    )

                stock_obs += 1
                total_obs += 1
                if hit is not None:
                    total_verified += 1

            except Exception as exc:
                logger.debug("[coldstart] stock process failed: %r", exc)
                continue

        print(f"{stock_obs} 条观察")

    # 重算统计
    print(f"\n总计: {total_obs} 条观察, {total_verified} 条已验证")
    print("重算形态统计...", end=" ", flush=True)
    try:
        rebuild_pattern_stats()
        print("OK")
    except Exception as e:
        print(f"FAIL: {e}")

    print("发现高胜率组合...", end=" ", flush=True)
    try:
        combos = discover_combo_patterns()
        print(f"发现 {combos} 个组合")
    except Exception as e:
        print(f"FAIL: {e}")

    result = {
        "status": "ok",
        "total_observations": total_obs,
        "total_verified": total_verified,
        "stocks_processed": len(stocks) - errors,
        "errors": errors,
    }
    print(f"\n冷启动完成: {json.dumps(result, ensure_ascii=False)}")
    return result


if __name__ == "__main__":
    import sys
    max_stocks = 50
    days = 365
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--stocks" and i + 2 <= len(sys.argv):
            max_stocks = int(sys.argv[i + 2])
        elif arg == "--days" and i + 2 <= len(sys.argv):
            days = int(sys.argv[i + 2])
    run_coldstart(max_stocks=max_stocks, lookback_days=days)
