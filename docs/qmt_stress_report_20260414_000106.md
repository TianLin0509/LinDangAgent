# QMT Stress Test Report

*Generated: 2026-04-14 00:01:06*


## Critical Findings

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

---

## Scenario 1: 特殊标的兼容性

### 平安银行（主板深） `000001.SZ`
- K线 30根: rows=30  最后一根={'time': 1776009600000.0, 'open': 11.049999999999999, 'high': 11.09, 'low': 11.03, 'close': 11.069999999999999, 'volume': 406104.0}
- instrument_detail: 31 字段，前10=['ExchangeID', 'InstrumentID', 'InstrumentName', 'ProductID', 'ProductName', 'ProductType', 'ExchangeCode', 'UniCode', 'CreateDate', 'OpenDate']
  - `InstrumentID` = 000001
  - `InstrumentName` = 平安银行
  - `UpStopPrice` = 12.200000000000001
  - `DownStopPrice` = 9.98
  - `LastVolume` = 0
- instrument_type: {'stock': True}

### 招商银行（主板沪） `600036.SH`
- K线 30根: rows=30  最后一根={'time': 1776009600000.0, 'open': 39.13, 'high': 39.160000000000004, 'low': 38.92, 'close': 38.980000000000004, 'volume': 525145.0}
- instrument_detail: 31 字段，前10=['ExchangeID', 'InstrumentID', 'InstrumentName', 'ProductID', 'ProductName', 'ProductType', 'ExchangeCode', 'UniCode', 'CreateDate', 'OpenDate']
  - `InstrumentID` = 600036
  - `InstrumentName` = 招商银行
  - `UpStopPrice` = 43.13
  - `DownStopPrice` = 35.29
  - `LastVolume` = 0
- instrument_type: {'stock': True}

### 宁德时代（创业板） `300750.SZ`
- K线 30根: rows=30  最后一根={'time': 1776009600000.0, 'open': 418.0, 'high': 433.33, 'low': 416.0, 'close': 427.13, 'volume': 412399.0}
- instrument_detail: 31 字段，前10=['ExchangeID', 'InstrumentID', 'InstrumentName', 'ProductID', 'ProductName', 'ProductType', 'ExchangeCode', 'UniCode', 'CreateDate', 'OpenDate']
  - `InstrumentID` = 300750
  - `InstrumentName` = 宁德时代
  - `UpStopPrice` = 499.2
  - `DownStopPrice` = 332.8
  - `LastVolume` = 0
- instrument_type: {'stock': True}

### 中芯国际（科创板） `688981.SH`
- K线 30根: rows=30  最后一根={'time': 1776009600000.0, 'open': 99.85, 'high': 102.8, 'low': 99.72, 'close': 100.64, 'volume': 351885.0}
- instrument_detail: 31 字段，前10=['ExchangeID', 'InstrumentID', 'InstrumentName', 'ProductID', 'ProductName', 'ProductType', 'ExchangeCode', 'UniCode', 'CreateDate', 'OpenDate']
  - `InstrumentID` = 688981
  - `InstrumentName` = 中芯国际
  - `UpStopPrice` = 121.21
  - `DownStopPrice` = 80.81
  - `LastVolume` = 0
- instrument_type: {'stock': True}

### 北交所430段（或尝试 832145） `430300.BJ`
- K线 30根: rows=30  最后一根={'time': 1759161600000.0, 'open': 0.0, 'high': 0.0, 'low': -0.0, 'close': 0.0, 'volume': 12202.0}
- instrument_detail: None
- instrument_type: {}

### 北交所833段 `833454.BJ`
- K线 30根: rows=30  最后一根={'time': 1759161600000.0, 'open': 0.0, 'high': 0.0, 'low': -0.0, 'close': 0.0, 'volume': 21195.0}
- instrument_detail: None
- instrument_type: {}

### 沪深300ETF `510300.SH`
- K线 30根: rows=30  最后一根={'time': 1776009600000.0, 'open': 4.6290000000000004, 'high': 4.66, 'low': 4.623, 'close': 4.652, 'volume': 3928926.0}
- instrument_detail: 31 字段，前10=['ExchangeID', 'InstrumentID', 'InstrumentName', 'ProductID', 'ProductName', 'ProductType', 'ExchangeCode', 'UniCode', 'CreateDate', 'OpenDate']
  - `InstrumentID` = 510300
  - `InstrumentName` = 沪深300ETF华泰柏瑞
  - `UpStopPrice` = 5.106
  - `DownStopPrice` = 4.178
  - `LastVolume` = 0
- instrument_type: {'etf': True}

### 创业板ETF `159915.SZ`
- K线 30根: rows=30  最后一根={'time': 1776009600000.0, 'open': 3.43, 'high': 3.4850000000000003, 'low': 3.4210000000000003, 'close': 3.4680000000000004, 'volume': 10417629.0}
- instrument_detail: 31 字段，前10=['ExchangeID', 'InstrumentID', 'InstrumentName', 'ProductID', 'ProductName', 'ProductType', 'ExchangeCode', 'UniCode', 'CreateDate', 'OpenDate']
  - `InstrumentID` = 159915
  - `InstrumentName` = 创业板ETF易方达
  - `UpStopPrice` = 4.124
  - `DownStopPrice` = 2.75
  - `LastVolume` = 0
- instrument_type: {'etf': True, 'fund': True}

### 沪深300指数 `000300.SH`
- K线 30根: rows=30  最后一根={'time': 1776009600000.0, 'open': 4615.133, 'high': 4653.398, 'low': 4615.133, 'close': 4646.155, 'volume': 196712277.0}
- instrument_detail: 31 字段，前10=['ExchangeID', 'InstrumentID', 'InstrumentName', 'ProductID', 'ProductName', 'ProductType', 'ExchangeCode', 'UniCode', 'CreateDate', 'OpenDate']
  - `InstrumentID` = 000300
  - `InstrumentName` = 沪深300
  - `UpStopPrice` = 5100.222
  - `DownStopPrice` = 4172.909
  - `LastVolume` = 0
- instrument_type: {'index': True}

## Scenario 2: 财务数据全表

- symbol: 000001.SZ

### 表 `Balance`
- `Balance`: rows=0, cols=[]

### 表 `Income`
- `Income`: rows=0, cols=[]

### 表 `CashFlow`
- `CashFlow`: rows=0, cols=[]

### 表 `PershareIndex`
- `PershareIndex`: rows=0, cols=[]

### 表 `CapitalStructure`
- `CapitalStructure`: rows=0, cols=[]

### 表 `HolderNum`
- `HolderNum`: rows=0, cols=[]

### 表 `TopTenHolder`
- `TopTenHolder`: rows=0, cols=[]

### 表 `TopTenHolderFree`
- `TopTenHolderFree`: rows=0, cols=[]
## Scenario 3: 板块全景

### 全部板块名（36个）

```
上期所
上证A股
上证B股
上证期权
上证转债
中金所
京市A股
创业板
大商所
沪市ETF
沪市债券
沪市基金
沪市指数
沪深A股
沪深B股
沪深ETF
沪深京A股
沪深债券
沪深基金
沪深指数
沪深转债
深市ETF
深市债券
深市基金
深市指数
深证A股
深证B股
深证期权
深证转债
科创板
科创板CDR
能源中心
连续合约
郑商所
香港联交所指数
香港联交所股票
```

### 热门概念覆盖检查
- `锂电池`: exact=False, 模糊匹配=[]
- `CPO`: exact=False, 模糊匹配=[]
- `人工智能`: exact=False, 模糊匹配=[]
- `光伏`: exact=False, 模糊匹配=[]
- `新能源车`: exact=False, 模糊匹配=[]
- `消费电子`: exact=False, 模糊匹配=[]
- `半导体`: exact=False, 模糊匹配=[]
- `白酒`: exact=False, 模糊匹配=[]
- `医疗器械`: exact=False, 模糊匹配=[]
- `军工`: exact=False, 模糊匹配=[]
- `房地产`: exact=False, 模糊匹配=[]
- `银行`: exact=False, 模糊匹配=[]

### 前 3 个匹配板块的成分数量抽样
## Scenario 4: 批量性能基准（关键！决定夜间学习可行性）


### 批量 N=10 只，取近 60 日日线
- download_history_data2 批量下载: 302ms (30.2ms/只)
- get_market_data_ex 批量取 60 日: 13ms, 有数据/总数 = 10/10
- get_local_data 批量取 60 日: 43ms, 有数据/总数 = 10/10
  → 相对 get_market_data_ex 提速 0.3x

### 批量 N=100 只，取近 60 日日线
- download_history_data2 批量下载: 3247ms (32.5ms/只)
- get_market_data_ex 批量取 60 日: 152ms, 有数据/总数 = 100/100
- get_local_data 批量取 60 日: 56ms, 有数据/总数 = 100/100
  → 相对 get_market_data_ex 提速 2.7x

### 批量 N=500 只，取近 60 日日线
- download_history_data2 批量下载: 15902ms (31.8ms/只)
- get_market_data_ex 批量取 60 日: 948ms, 有数据/总数 = 500/500
- get_local_data 批量取 60 日: 238ms, 有数据/总数 = 500/500
  → 相对 get_market_data_ex 提速 4.0x

### 批量 N=1000 只，取近 60 日日线
- download_history_data2 批量下载: 31627ms (31.6ms/只)
- get_market_data_ex 批量取 60 日: 1955ms, 有数据/总数 = 1000/1000
- get_local_data 批量取 60 日: 641ms, 有数据/总数 = 1000/1000
  → 相对 get_market_data_ex 提速 3.0x

### 批量含非法 symbol
- 5 正 + 2 非法，返回 keys: ['600051.SH', '605090.SH', '600025.SH', '601222.SH', '688031.SH', '999999.XX', '888888.YY']
- 非法 symbol 行为: '999999.XX' in data = True, empty=False
## Scenario 5: 复权一致性


### `002594.SZ`
- 除权因子: rows=11, cols=['time', 'interest', 'stockBonus', 'stockGift', 'allotNum', 'allotPrice', 'gugai', 'dr']
  - 最近3条: [{'time': 1690473600000.0, 'interest': 1.142, 'stockBonus': 0.0, 'stockGift': 0.0, 'allotNum': 0.0, 'allotPrice': 0.0, 'gugai': 0.0, 'dr': 1.004376}, {'time': 1722182400000.0, 'interest': 3.097772, 'stockBonus': 0.0, 'stockGift': 0.0, 'allotNum': 0.0, 'allotPrice': 0.0, 'gugai': 0.0, 'dr': 1.012308}, {'time': 1753718400000.0, 'interest': 3.974, 'stockBonus': 0.8, 'stockGift': 1.2, 'allotNum': 0.0, 'allotPrice': 0.0, 'gugai': 0.0, 'dr': 3.035662}]
- 近5日 close 三种复权对比: {"none": [97.98, 101.22, 99.03, 101.67, 104.25], "front": [97.98, 101.22, 99.03, 101.67, 104.25], "back": [303.4067720000001, 313.1267720000001, 306.55677200000014, 314.4767720000001, 322.2167720000001]}
  → 三者存在差异（近5日跨除权日）

### `300750.SZ`
- 除权因子: rows=9, cols=['time', 'interest', 'stockBonus', 'stockGift', 'allotNum', 'allotPrice', 'gugai', 'dr']
  - 最近3条: [{'time': 1737648000000.0, 'interest': 1.23, 'stockBonus': 0.0, 'stockGift': 0.0, 'allotNum': 0.0, 'allotPrice': 0.0, 'gugai': 0.0, 'dr': 1.004799}, {'time': 1745251200000.0, 'interest': 4.553, 'stockBonus': 0.0, 'stockGift': 0.0, 'allotNum': 0.0, 'allotPrice': 0.0, 'gugai': 0.0, 'dr': 1.02006}, {'time': 1755619200000.0, 'interest': 1.006999, 'stockBonus': 0.0, 'stockGift': 0.0, 'allotNum': 0.0, 'allotPrice': 0.0, 'gugai': 0.0, 'dr': 1.003581}]
- 近5日 close 三种复权对比: {"none": [384.59000000000003, 391.3, 389.99, 416.0, 427.13], "front": [384.59000000000003, 391.3, 389.99, 416.0, 427.13], "back": [717.3094112000001, 729.3874112000001, 727.0294112, 773.8474112, 793.8814112]}
  → 三者存在差异（近5日跨除权日）

### `000651.SZ`
- 除权因子: rows=37, cols=['time', 'interest', 'stockBonus', 'stockGift', 'allotNum', 'allotPrice', 'gugai', 'dr']
  - 最近3条: [{'time': 1747238400000.0, 'interest': 1.0, 'stockBonus': 0.0, 'stockGift': 0.0, 'allotNum': 0.0, 'allotPrice': 0.0, 'gugai': 0.0, 'dr': 1.021786}, {'time': 1756396800000.0, 'interest': 2.0, 'stockBonus': 0.0, 'stockGift': 0.0, 'allotNum': 0.0, 'allotPrice': 0.0, 'gugai': 0.0, 'dr': 1.044208}, {'time': 1769097600000.0, 'interest': 1.0, 'stockBonus': 0.0, 'stockGift': 0.0, 'allotNum': 0.0, 'allotPrice': 0.0, 'gugai': 0.0, 'dr': 1.025144}]
- 近5日 close 三种复权对比: {"none": [37.33, 37.800000000000004, 37.32, 37.339999999999996, 37.16], "front": [37.33, 37.800000000000004, 37.32, 37.339999999999996, 37.16], "back": [8172.697436000003, 8233.979666750003, 8171.393558750005, 8174.0013132500035, 8150.531522750003]}
  → 三者存在差异（近5日跨除权日）

### `600519.SH`
- 除权因子: rows=29, cols=['time', 'interest', 'stockBonus', 'stockGift', 'allotNum', 'allotPrice', 'gugai', 'dr']
  - 最近3条: [{'time': 1734624000000.0, 'interest': 23.882, 'stockBonus': 0.0, 'stockGift': 0.0, 'allotNum': 0.0, 'allotPrice': 0.0, 'gugai': 0.0, 'dr': 1.015637}, {'time': 1750867200000.0, 'interest': 27.673, 'stockBonus': 0.0, 'stockGift': 0.0, 'allotNum': 0.0, 'allotPrice': 0.0, 'gugai': 0.0, 'dr': 1.019649}, {'time': 1766073600000.0, 'interest': 23.957, 'stockBonus': 0.0, 'stockGift': 0.0, 'allotNum': 0.0, 'allotPrice': 0.0, 'gugai': 0.0, 'dr': 1.0170029999999999}]
- 近5日 close 三种复权对比: {"none": [1440.02, 1465.0200000000002, 1460.49, 1453.96, 1443.31], "front": [1440.02, 1465.0200000000002, 1460.49, 1453.96, 1443.31], "back": [8700.994048156801, 8826.613828156804, 8803.851524020802, 8771.0396374848, 8717.525611204801]}
  → 三者存在差异（近5日跨除权日）

### `000858.SZ`
- 除权因子: rows=29, cols=['time', 'interest', 'stockBonus', 'stockGift', 'allotNum', 'allotPrice', 'gugai', 'dr']
  - 最近3条: [{'time': 1737561600000.0, 'interest': 2.576, 'stockBonus': 0.0, 'stockGift': 0.0, 'allotNum': 0.0, 'allotPrice': 0.0, 'gugai': 0.0, 'dr': 1.020192}, {'time': 1752768000000.0, 'interest': 3.169, 'stockBonus': 0.0, 'stockGift': 0.0, 'allotNum': 0.0, 'allotPrice': 0.0, 'gugai': 0.0, 'dr': 1.02596}, {'time': 1765987200000.0, 'interest': 2.578, 'stockBonus': 0.0, 'stockGift': 0.0, 'allotNum': 0.0, 'allotPrice': 0.0, 'gugai': 0.0, 'dr': 1.023365}]
- 近5日 close 三种复权对比: {"none": [103.11000000000001, 104.19000000000001, 102.6, 102.07, 102.42], "front": [103.11000000000001, 104.19000000000001, 102.6, 102.07, 102.42], "back": [2115.5522888768005, 2132.4416675648004, 2107.5767489408, 2099.2884427328004, 2104.7618524928002]}
  → 三者存在差异（近5日跨除权日）