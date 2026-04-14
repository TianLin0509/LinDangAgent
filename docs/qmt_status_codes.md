# QMT InstrumentStatus 码表（2026-04-14 实测）

测试环境：国金证券 QMT 交易端，xtquant sp3 v1.0，数据服务 127.0.0.1:58610

---

## 发现汇总

| Symbol | Name | InstrumentStatus | IsTrading | 分类 | stock_gate 处理 |
|---|---|---|---|---|---|
| 000001.SZ | 平安银行 | 0 | False | normal | 放行 |
| 600036.SH | 招商银行 | 0 | False | normal | 放行 |
| 300750.SZ | 宁德时代 | 0 | False | normal | 放行 |
| 400010.BJ | (detail=None) | N/A | N/A | st_or_delisted (BJ) | detail=None，BJ 股 → 放行（BJ 板块特殊） |
| 000033.SZ | (detail=None) | N/A | N/A | st_or_delisted | detail=None → DELISTED hard_block |
| 600087.SH | (detail=None) | N/A | N/A | st_or_delisted | detail=None → DELISTED hard_block |
| 600832.SH | (detail=None) | N/A | N/A | st_or_delisted | detail=None → DELISTED hard_block |
| 000583.SZ | (detail=None) | N/A | N/A | st_or_delisted | detail=None → DELISTED hard_block |
| 300372.SZ | (detail=None) | N/A | N/A | st_or_delisted | detail=None → DELISTED hard_block |
| 510300.SH | 沪深300ETF华泰柏瑞 | 0 | False | etf | 放行（ETF 同正常） |
| 159915.SZ | 创业板ETF易方达 | 0 | False | etf | 放行（ETF 同正常） |
| 000300.SH | 沪深300 | 0 | False | index | 放行（指数同正常） |
| 000905.SH | 中证500 | 0 | False | index | 放行（指数同正常） |
| 600735.SH | ST新华锦 | **32** | False | ST（UpStop<6%） | ST，Status=32 |
| 600624.SH | ST复华 | 0 | False | ST（UpStop<6%） | ST，Status=0（ST 不一定改 Status） |
| 603268.SH | *ST松发 | 0 | False | ST（UpStop<6%） | *ST，Status=0 |
| 605199.SH | ST葫芦娃 | 0 | False | ST（UpStop<6%） | ST，Status=0 |
| 600381.SH | *ST春天 | 0 | False | ST（UpStop<6%） | *ST，Status=0 |
| 603389.SH | *ST亚振 | 0 | False | ST（UpStop<6%） | *ST，Status=0 |
| 600568.SH | ST中珠 | 0 | False | ST（UpStop<6%） | ST，Status=0 |

---

## 关键结论

### 退市股行为

**退市（或已摘牌）股票的 `get_instrument_detail()` 返回 `None`（整个 detail 对象为空）。**
QMT 不保留退市股的详情记录，也不用特殊 Status 码标记它们——直接从数据库抹去。

测试样本中 000033.SZ、600087.SH、600832.SH、000583.SZ、300372.SZ 全部返回 `detail=None`。

注意：400010.BJ 同样返回 `detail=None`，但 BJ 后缀股本就在北交所，QMT 数据覆盖可能不完整，
不能直接等同"退市"——应单独豁免。

### ST 股行为

ST 股**大多数 Status=0**，与正常股相同。只有极少数（本次扫描仅 600735.SH）出现 Status=32。
因此，**不能依赖 InstrumentStatus 识别 ST 状态**，应改用 InstrumentName 含"ST"/"*ST"判断，
或用 UpStopPrice/PreClose 比值（<6%）判断涨跌停限制。

### IsTrading 字段

所有测试样本（正常股、ETF、指数、ST 股）的 `IsTrading` 均返回 `False`，
因为查询时为非交易时段（16:57）。该字段不能用于区分正常/异常状态，
只反映当前实时是否正在交易，无分类价值。

### Status 码含义（可作为 stock_gate.py 常量）

```python
# 已知状态码（2026-04-14 实测，沪深A股 + ETF + 指数）
NORMAL_STATUS = {0}          # 正常股、ETF、指数、大多数ST股均为 0
ST_STATUS_CODES = {32}       # 极少数ST股为 32，但大多数ST仍是 0，不可靠
DELISTED_STATUS_CODES = set() # 退市股不返回 detail，Status 码不存在
```

---

## stock_gate.py 退市判定策略

### 选项 A（推荐）：detail=None + 非 BJ 后缀 → DELISTED hard_block

```python
def is_delisted(symbol: str) -> bool:
    """
    退市判定：QMT 对退市股不保留 detail，直接返回 None。
    BJ 后缀豁免（北交所数据覆盖可能不完整，不代表退市）。
    """
    if symbol.endswith(".BJ"):
        return False  # BJ 板块数据缺失属正常，不判退市
    detail = xtdata.get_instrument_detail(symbol, iscomplete=True)
    return detail is None
```

### 选项 B：专用 Status 码

**不适用**。实测中退市股无 Status 码（detail 整体为 None），无法用状态码识别。

### 实际采用

**选项 A**，理由：
- 退市股 `detail=None` 是实测一致结论（5/5 样本），无反例
- BJ 豁免处理了唯一的假阳性来源（400010.BJ 也返回 None）
- 逻辑简单，无需维护 Status 码枚举

### ST 检测附注

ST 不能用 Status 码检测，建议 stock_gate 里用 InstrumentName 前缀：

```python
def is_st(symbol: str) -> bool:
    detail = xtdata.get_instrument_detail(symbol, iscomplete=True)
    if not detail:
        return False
    name = detail.get("InstrumentName", "") or ""
    return "ST" in name  # 覆盖 "ST xxx" 和 "*ST xxx"
```
