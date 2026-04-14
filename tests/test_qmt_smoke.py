"""
QMT 金丝雀冒烟测试 —— 需 QMT 客户端已登录

运行: python tests/test_qmt_smoke.py
非 pytest 断言，打印人类可读报告，返回码 0=通过 / 1=失败。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    print("======== QMT 金丝雀冒烟测试 ========")

    # 1. is_alive
    from data import qmt_client
    alive = qmt_client.is_alive()
    print(f"[1/3] qmt_client.is_alive() -> {alive}")
    if not alive:
        print("    FAIL: QMT 未登录/不可用，后续测试跳过")
        return 1

    # 2. get_kline
    try:
        df = qmt_client.get_kline("000001", count=60)
        print(f"[2/3] get_kline('000001', count=60) -> rows={len(df)}, cols={list(df.columns)}")
        assert len(df) >= 40, f"期望 >=40 行，实际 {len(df)}"
        for c in ("open", "high", "low", "close", "volume"):
            assert c in df.columns, f"缺少列 {c}"
    except Exception as e:
        print(f"    FAIL get_kline: {type(e).__name__}: {e}")
        return 1

    # 3. get_price_df 走 QMT（验证 _data_source 切换）
    try:
        from data import tushare_client
        df2, err = tushare_client.get_price_df("000001.SZ", days=60)
        src = tushare_client._data_source
        print(f"[3/3] tushare_client.get_price_df('000001.SZ', days=60) -> rows={len(df2)}, _data_source={src}, err={err}")
        if src != "qmt":
            print(f"    FAIL: 预期 _data_source=qmt，实际 {src}（QMT 可能被降级）")
            return 1
        for c in ("日期", "开盘", "收盘", "成交量"):
            assert c in df2.columns, f"缺少中文列 {c}: 实际={list(df2.columns)}"
    except Exception as e:
        print(f"    FAIL get_price_df: {type(e).__name__}: {e}")
        return 1

    print("\nPASS: 金丝雀冒烟测试通过 - QMT 已成为 get_price_df 的最高优先级数据源")
    return 0


if __name__ == "__main__":
    sys.exit(main())
