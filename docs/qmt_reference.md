# QMT / xtquant Reference (AI-oriented, auto-generated)

*Last verified: 2026-04-13 by `tests/qmt_probe.py`*

This file is **auto-generated from real API calls**. Do not hand-edit — changes will be overwritten on next probe run.
If you need to add an API, extend `tests/qmt_probe.py` with a new probe and re-run.

---

## xtdata 可导入
**Signature:** `import xtquant.xtdata`
**Status (verified 2026-04-13):** OK, 0ms
**Returns summary:** imported OK
**Verified sample:**
```
xtdata module path: C:\Users\lintian\AppData\Local\Programs\Python\Python312\Lib\site-packages\xtquant\xtdata.py
```

## 日线 x 60 根
**Signature:** `get_market_data_ex(..., period='1d', count=60)`
**Status (verified 2026-04-13):** OK, 56ms
**Returns summary:** rows=60, cols=['time', 'open', 'high', 'low', 'close', 'volume', 'amount']
**Verified sample:**
```
first: {'time': 1767888000000.0, 'open': 11.53, 'high': 11.53, 'low': 11.44, 'close': 11.459999999999999, 'volume': 983390.0, 'amount': 1128076547.0}
last:  {'time': 1776009600000.0, 'open': 11.049999999999999, 'high': 11.09, 'low': 11.03, 'close': 11.069999999999999, 'volume': 406104.0, 'amount': 449056813.0}
```
**Gotchas:**
- **必须先 download_history_data(sym, period) 才能 get_market_data_ex 出数据**（否则返回空）
- symbol 必须带 .SZ/.SH/.BJ 后缀
- period='1d' 日线；'1m'/'5m' 仅交易时段可查
- 返回是 dict[symbol, DataFrame]，需要 data[sym] 取值

## 日线全历史 (count=-1)
**Signature:** `get_market_data_ex(..., count=-1)`
**Status (verified 2026-04-13):** OK, 76ms
**Returns summary:** rows=8607
**Verified sample:**
```
first_time=663177600000.0, last_close=11.069999999999999
```
**Gotchas:**
- count=-1 取全历史；数据量大时注意性能

## 1m x 240 根
**Signature:** `get_market_data_ex(..., period='1m', count=240)`
**Status (verified 2026-04-13):** OK, 4818ms
**Returns summary:** rows=240
**Verified sample:**
```
{'time': {'20260413145900': 1776063540000, '20260413150000': 1776063600000}, 'open': {'20260413145900': 11.06, '20260413150000': 11.07}, 'close': {'20260413145900': 11.06, '20260413150000': 11.07}}
```
**Gotchas:**
- 分钟线仅交易时段可取最新；盘后可能返回历史最后一个交易日
- 分钟线数据量大，download 耗时比日线长

## 5m/15m/30m/60m 各周期
**Signature:** `get_market_data_ex(..., period='5m/15m/30m/60m')`
**Status (verified 2026-04-13):** OK, 913ms
**Returns summary:** 周期-行数: {'5m': 10, '15m': 10, '30m': 10, '60m': 10}
**Verified sample:**
```
{"5m": 10, "15m": 10, "30m": 10, "60m": 10}
```
**Gotchas:**
- 5m/15m/30m/60m 都要独立 download

## 周线/月线
**Signature:** `get_market_data_ex(..., period='1w'/'1mon')`
**Status (verified 2026-04-13):** OK, 36ms
**Returns summary:** week_rows=10, month_rows=10
**Verified sample:**
```
周线 period='1w'，月线 period='1mon'
```
**Gotchas:**
- 周线 period='1w'；月线 period='1mon'（不是 '1M' 或 '1month'）

## 复权对比
**Signature:** `get_market_data_ex(..., dividend_type=...)`
**Status (verified 2026-04-13):** OK, 46ms
**Returns summary:** 复权对比: {'none': 11.069999999999999, 'front': 11.069999999999999, 'back': 1146.2298343858886}
**Verified sample:**
```
{"none": 11.069999999999999, "front": 11.069999999999999, "back": 1146.2298343858886}
```
**Gotchas:**
- dividend_type 取值: 'none' / 'front' / 'back'（不是 'qfq'/'hfq'）

## get_full_tick
**Signature:** `xtdata.get_full_tick([sym])`
**Status (verified 2026-04-13):** OK, 1ms
**Returns summary:** 字段: ['time', 'timetag', 'lastPrice', 'open', 'high', 'low', 'lastClose', 'amount', 'volume', 'pvolume', 'stockStatus', 'openInt', 'settlementPrice', 'lastSettlementPrice', 'askPrice', 'bidPrice', 'askVol', 'bidVol']
**Verified sample:**
```
sample: lastPrice=11.07, time=1776063600000
```
**Gotchas:**
- 盘后 get_full_tick 返回上个交易日收盘；盘中才是实时

## subscribe_quote
**Signature:** `xtdata.subscribe_quote(sym, period, callback)`
**Status (verified 2026-04-13):** OK, 1019ms
**Returns summary:** subid=1, received=0 次回调
**Verified sample:**
```
回调数据样例前 200 字: []
```
**Gotchas:**
- subscribe_quote 长期订阅，必须 unsubscribe_quote 清理
- 盘后订阅不会触发回调，只能验证 API 调用成功

## get_instrument_detail
**Signature:** `xtdata.get_instrument_detail(sym)`
**Status (verified 2026-04-13):** OK, 1ms
**Returns summary:** 字段数=31, 前5字段=['ExchangeID', 'InstrumentID', 'InstrumentName', 'ProductID', 'ProductName']
**Verified sample:**
```
{"ExchangeID": "SZ", "InstrumentID": "000001", "InstrumentName": "平安银行", "ProductID": "", "ProductName": ""}
```

## 沪深A股 板块成分
**Signature:** `xtdata.get_stock_list_in_sector('沪深A股')`
**Status (verified 2026-04-13):** OK, 17ms
**Returns summary:** A股股票数=5199
**Verified sample:**
```
前5: ['600051.SH', '605090.SH', '600025.SH', '601222.SH', '688031.SH']
```
**Gotchas:**
- 板块名用中文：'沪深A股' / '科创板' / '创业板' / '中小板'

## 科创板 板块成分
**Signature:** `xtdata.get_stock_list_in_sector('科创板')`
**Status (verified 2026-04-13):** OK, 23ms
**Returns summary:** 科创板股票数=606
**Verified sample:**
```
前5: ['688031.SH', '688045.SH', '688528.SH', '688133.SH', '688147.SH']
```

## get_sector_list
**Signature:** `xtdata.get_sector_list()`
**Status (verified 2026-04-13):** OK, 20ms
**Returns summary:** 板块总数=36
**Verified sample:**
```
前10: ['上期所', '上证A股', '上证B股', '上证期权', '上证转债', '中金所', '京市A股', '创业板', '大商所', '沪市ETF']
```
**Gotchas:**
- 用此 API 发现所有可查板块名

## get_financial_data
**Signature:** `xtdata.get_financial_data([sym], ['Balance'])`
**Status (verified 2026-04-13):** OK, 6ms
**Returns summary:** keys=['000001.SZ']
**Verified sample:**
```
{'000001.SZ': {'Balance': Empty DataFrame
Columns: []
Index: []}}
```
**Gotchas:**
- 财务非 QMT 强项，项目继续走 Tushare/AKShare

## get_instrument_type
**Signature:** `xtdata.get_instrument_type(sym)`
**Status (verified 2026-04-13):** OK, 1ms
**Returns summary:** type={'stock': True}
**Verified sample:**
```
{'stock': True}
```
**Gotchas:**
- 区分股票/ETF/指数

## 非法 symbol
**Signature:** `get_market_data_ex(['999999.XX'], ...)`
**Status (verified 2026-04-13):** OK, 10ms
**Returns summary:** 非法 symbol 静默返回空
**Verified sample:**
```
{'999999.XX': Empty DataFrame
Columns: [time, close]
Index: []}
```
**Gotchas:**
- 非法 symbol 不抛异常，返回空 df —— 调用方必须自行校验

## 超长 count=1M
**Signature:** `get_market_data_ex(..., count=1_000_000)`
**Status (verified 2026-04-13):** OK, 59ms
**Returns summary:** count=1M 返回 rows=8607, 耗时 19ms
**Verified sample:**
```
超过历史总量安全截断
```
**Gotchas:**
- count 超过历史总量时截断为全历史，不报错

## 跨市场批量
**Signature:** `get_market_data_ex(['000001.SZ','600000.SH'])`
**Status (verified 2026-04-13):** OK, 125ms
**Returns summary:** 批量OK: ['000001.SZ', '600000.SH']
**Verified sample:**
```
两只股票各返回 5 行
```
**Gotchas:**
- 单次调用可批量传入跨市场多只股票

## 保留槽位（Level2 预留）
**Signature:** `reserved`
**Status (verified 2026-04-13):** OK, 0ms
**Returns summary:** skipped
**Verified sample:**
```
Level2 非本期目标
```


---

## Stress Findings (2026-04-14)

### Findings requiring attention
- 概念板块 `锂电池` 在 QMT 板块列表中完全缺失
- 概念板块 `CPO` 在 QMT 板块列表中完全缺失
- 概念板块 `人工智能` 在 QMT 板块列表中完全缺失
- 概念板块 `光伏` 在 QMT 板块列表中完全缺失
- 概念板块 `新能源车` 在 QMT 板块列表中完全缺失
- 概念板块 `消费电子` 在 QMT 板块列表中完全缺失
- 概念板块 `半导体` 在 QMT 板块列表中完全缺失
- 概念板块 `白酒` 在 QMT 板块列表中完全缺失
- 概念板块 `医疗器械` 在 QMT 板块列表中完全缺失
- 概念板块 `军工` 在 QMT 板块列表中完全缺失
- 概念板块 `房地产` 在 QMT 板块列表中完全缺失
- 概念板块 `银行` 在 QMT 板块列表中完全缺失

### Full stress report
See `docs/qmt_stress_report_20260414_000106.md` for complete details.
