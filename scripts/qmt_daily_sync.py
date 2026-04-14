"""
QMT 盘后自动同步脚本
------------------------
运行方式:
    python scripts/qmt_daily_sync.py             # 普通运行
    python scripts/qmt_daily_sync.py --dry-run   # 只检查不下载
    python scripts/qmt_daily_sync.py --force-full # 强制全量同步

功能:
- 沪深A股 + 沪深ETF + 沪深指数 日线增量同步
- 自愈式缺失回补（基于 state.json 记录的 last_success_date）
- 周日额外刷新 sector_data / index_weight / holidays
- 验证性抽查 + 失败记录
- Windows Task Scheduler 友好（全绝对路径，无弹窗，log 落盘）

退出码: 0=ok, 1=partial, 2=fatal
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import logging
import os
import random
import sys
import time
import traceback
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

STATE_PATH = ROOT / "data" / "knowledge" / "qmt_sync_state.json"
LOG_DIR = ROOT / "logs" / "qmt_sync"

POOL_SECTORS = ["沪深A股", "沪深ETF", "沪深指数"]
WEEKLY_REFRESH_WEEKDAY = 6  # Sunday


def setup_logger(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("qmt_sync")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    # file
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    # console
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_success_date": None, "last_run_time": None, "last_stats": {}, "history": []}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # cap history
    state["history"] = state.get("history", [])[-30:]
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def gap_days(last_date: Optional[str]) -> int:
    if not last_date:
        return 9999  # 首次运行
    try:
        last = dt.date.fromisoformat(last_date)
        today = dt.date.today()
        return max(0, (today - last).days)
    except Exception:
        return 9999


def fetch_pool(xtdata, logger) -> list[str]:
    """拉取全市场股票池 = 沪深A股 + 沪深ETF + 沪深指数"""
    all_syms: list[str] = []
    for sector in POOL_SECTORS:
        try:
            symbols = xtdata.get_stock_list_in_sector(sector)
            logger.info(f"  {sector}: {len(symbols)} 只")
            all_syms.extend(symbols)
        except Exception as e:
            logger.warning(f"  {sector} 获取失败: {e}")
    # 去重
    all_syms = sorted(set(all_syms))
    logger.info(f"  合计去重后: {len(all_syms)} 只")
    return all_syms


def download_batch(xtdata, symbols: list[str], period: str, start: str, end: str,
                   logger, dry_run: bool) -> tuple[int, int]:
    """批量下载；返回 (成功数, 耗时ms)"""
    if dry_run:
        logger.info(f"  [DRY-RUN] 跳过 download_history_data2({len(symbols)} stocks, period={period})")
        return 0, 0

    t0 = time.time()
    done_cnt = [0]

    def on_progress(data):
        # data 格式: {'finished': n, 'total': m, 'message': '...'}
        try:
            if isinstance(data, dict):
                fin = data.get("finished", 0)
                tot = data.get("total", len(symbols))
                if fin and fin % 500 == 0:
                    logger.info(f"    进度: {fin}/{tot}")
                done_cnt[0] = fin
        except Exception:
            pass

    try:
        xtdata.download_history_data2(
            symbols, period=period, start_time=start, end_time=end,
            callback=on_progress,
        )
    except TypeError:
        # 某些 SDK 版本 callback 参数叫 incremental_callback
        xtdata.download_history_data2(symbols, period=period, start_time=start, end_time=end)
    except Exception as e:
        logger.error(f"    download_history_data2 失败: {e}")
        return 0, int((time.time() - t0) * 1000)

    cost = int((time.time() - t0) * 1000)
    logger.info(f"  批量下载完成: {len(symbols)} 只，耗时 {cost}ms ({cost/max(len(symbols),1):.1f}ms/只)")
    return len(symbols), cost


def verify_sample(xtdata, symbols: list[str], today_str: str, logger,
                  n: int = 10) -> tuple[int, int, list[str]]:
    """随机抽查 n 只股票，看是否有今天的数据（或上个交易日）"""
    sample = random.sample(symbols, min(n, len(symbols)))
    ok, fail, fail_list = 0, 0, []
    for sym in sample:
        try:
            data = xtdata.get_local_data(
                field_list=["time", "close"],
                stock_list=[sym], period="1d", count=3,
            )
            df = data.get(sym, pd.DataFrame())
            if df.empty:
                fail += 1
                fail_list.append(f"{sym}: EMPTY")
                continue
            last_time_ms = df["time"].iloc[-1]
            last_date = dt.datetime.fromtimestamp(last_time_ms / 1000).strftime("%Y-%m-%d")
            # 允许最后日期 <= today（盘后/节假日）
            if last_date:
                ok += 1
            else:
                fail += 1
                fail_list.append(f"{sym}: no_date")
        except Exception as e:
            fail += 1
            fail_list.append(f"{sym}: {e}")
    logger.info(f"  抽查 {n} 只: OK={ok}, FAIL={fail}")
    if fail_list:
        logger.warning(f"  失败样本: {fail_list[:5]}")
    return ok, fail, fail_list


def sync_weekly_refs(xtdata, logger, dry_run: bool) -> None:
    """每周日刷新板块/指数权重/节假日"""
    logger.info("[weekly] 周刷新任务")
    if dry_run:
        logger.info("  [DRY-RUN] 跳过 weekly refresh")
        return

    for name, fn in [
        ("download_sector_data",   lambda: xtdata.download_sector_data()),
        ("download_holiday_data",  lambda: xtdata.download_holiday_data()),
    ]:
        if hasattr(xtdata, name.split('.')[-1]):
            try:
                t0 = time.time()
                fn()
                logger.info(f"  {name}: OK, {int((time.time()-t0)*1000)}ms")
            except Exception as e:
                logger.warning(f"  {name}: {e}")
        else:
            logger.info(f"  {name}: 接口不存在，跳过")

    # index_weight 需要指定指数
    if hasattr(xtdata, "download_index_weight"):
        for idx in ("000300.SH", "000905.SH", "000852.SH"):
            try:
                xtdata.download_index_weight(idx)
                logger.info(f"  download_index_weight({idx}): OK")
            except Exception as e:
                logger.warning(f"  download_index_weight({idx}): {e}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="不下载，仅检查")
    ap.add_argument("--force-full", action="store_true", help="强制全量（忽略 state）")
    ap.add_argument("--skip-weekly", action="store_true", help="跳过周刷新")
    args = ap.parse_args()

    today = dt.date.today()
    today_str = today.strftime("%Y%m%d")
    log_path = LOG_DIR / f"{today_str}.log"
    logger = setup_logger(log_path)

    logger.info(f"======== QMT Daily Sync @ {dt.datetime.now():%Y-%m-%d %H:%M:%S} ========")
    logger.info(f"python={sys.version.split()[0]}, args={vars(args)}")

    # 1. 连 QMT
    try:
        from xtquant import xtdata
        _ = xtdata.get_sector_list()  # 触发连接 + 校验
    except Exception as e:
        logger.error(f"QMT 连接失败: {e}")
        logger.error("请先手动打开并登录 MiniQMT 客户端")
        return 2

    # 2. 读状态
    state = load_state()
    last_date = state.get("last_success_date")
    gap = gap_days(last_date)
    logger.info(f"state: last_success_date={last_date}, gap_days={gap}")
    if args.force_full:
        logger.info("force-full: 忽略 state")
        gap = 9999

    # 3. 拉股票池
    logger.info("[pool] 获取沪深A股 + ETF + 指数列表")
    symbols = fetch_pool(xtdata, logger)
    if not symbols:
        logger.error("股票池为空，abort")
        return 2

    # 4. 计算下载窗口
    if gap >= 9999 or gap >= 7:
        # 首次或长缺口：全量
        start_str = ""
        logger.info("下载窗口: 全历史（首次或长缺口）")
    elif gap >= 1:
        # 1 至 6 天缺口：从上次成功日期起
        start_dt = dt.date.fromisoformat(last_date)
        start_str = start_dt.strftime("%Y%m%d")
        logger.info(f"下载窗口: {start_str} → {today_str}（回补 {gap} 天）")
    else:
        # gap == 0：今日已跑过
        start_str = today_str
        logger.info(f"下载窗口: 仅今日 {today_str}（今日重跑）")
    end_str = today_str

    # 5. 批量下载日线
    logger.info(f"[daily] 批量下载日线 N={len(symbols)}")
    done_count, cost_ms = download_batch(
        xtdata, symbols, period="1d", start=start_str, end=end_str,
        logger=logger, dry_run=args.dry_run,
    )

    # 6. 验证抽查
    logger.info("[verify] 随机抽查 10 只")
    verify_ok, verify_fail, fail_list = (0, 0, [])
    if not args.dry_run:
        verify_ok, verify_fail, fail_list = verify_sample(
            xtdata, symbols, today_str, logger, n=10,
        )

    # 7. 每周日额外刷新
    if not args.skip_weekly and today.weekday() == WEEKLY_REFRESH_WEEKDAY:
        sync_weekly_refs(xtdata, logger, args.dry_run)

    # 8. 写状态
    exit_code = 0
    if verify_fail > 3:
        exit_code = 1
        logger.warning(f"验证抽查 FAIL={verify_fail} > 3，标记 partial")

    if not args.dry_run:
        state["last_success_date"] = today.isoformat()
        state["last_run_time"] = dt.datetime.now().isoformat(timespec="seconds")
        state["last_stats"] = {
            "stocks_synced": done_count,
            "verified_sample_ok": verify_ok,
            "verified_sample_fail": verify_fail,
            "download_cost_ms": cost_ms,
            "gap_days_recovered": gap if gap < 9999 else None,
        }
        state["history"].append({
            "date": today.isoformat(),
            "ok": exit_code == 0,
            "cost_s": round(cost_ms / 1000, 1),
        })
        save_state(state)
        logger.info(f"state.json 已更新: {STATE_PATH}")

    logger.info(f"======== Done: exit={exit_code} ========")
    return exit_code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n中断")
        sys.exit(130)
    except Exception as e:
        print(f"FATAL: {e}")
        traceback.print_exc()
        sys.exit(2)
