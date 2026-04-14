# 单股分析 QMT 深度接入设计（M 期）

*Date: 2026-04-14*
*Scope: 单股分析链路深度集成 QMT（元信息+财务+交易日历），QMT 为最高优先级，Tushare 兜底*
*Status: Approved by user, ready for implementation planning*

---

## 1. 背景与目标

### 1.1 背景
- 前序工作（2026-04-13）已完成 QMT K 线接入 + probe + stress test + deep audit；`get_price_df` 已通过 QMT 优先。
- 实测确认国金 MiniQMT 可用能力边界（见 `docs/qmt_reference.md`）：
  - ✅ K 线、元信息（83 字段）、批量元信息（1ms/只）、财务 8 表、除权因子、交易日历
  - ❌ 概念板、历史 ST 数据、Level2、可转债详情（需投研版）、北交所元信息（权限问题）

### 1.2 本期目标
将 QMT 的 **元信息 + 财务 + 交易日历** 三类能力深度接入单股分析链路（`/s <股票>`），满足：

- **前置过滤**：进门先判 ST/新股/退市/北交所，结果注入报告顶部警告
- **财务接入**：8 张财务表作为最高优先级数据源，核心 3 表空时整体降级 Tushare（口径一致）
- **涨跌幅精准**：N 日涨幅用真实交易日，不再 `iloc[-N]` 粗算
- **数据来源可追溯**：报告尾部 dump `_data_source_map`

### 1.3 非目标（本期不做）
- ❌ AI prompt 改造（新 QMT 字段暂不入 prompt，后期阶段 B 再做）
- ❌ 概念板 / 行业板 / 龙虎榜 / 资金流 / 股东信息（除 HolderNum 等基础表外）
- ❌ 多源交叉验证 flag（留阶段 B，先积累真实差异分布）
- ❌ 对照组压测（纯重构后压测，无需与 main 分支对比）

---

## 2. 关键决策

| 决策 | 结论 | 理由 |
|---|---|---|
| 作用域 | M：前置过滤 + 财务 + 交易日历 | S 吃不饱，L 改 AI prompt 风险过大 |
| 优先级 | **QMT > Tushare > AKShare > ...**（沿用 K 线模式） | 一致性；QMT 实测已稳定 |
| ST/停牌行为 | **B + 退市硬拦截**：警告注入报告仍跑分析；仅退市硬拦截 | 用户可能就是想分析 ST 股；退市股分析纯浪费 AI token |
| 停牌 | **暂不判定** | 发生概率低，盘后判停牌容易误报 |
| QMT 财务失败识别 | **C 核心表门控**：Balance/Income/PershareIndex 任一空 → 8 表全走 Tushare | 避免两源数据口径混用 |
| 缓存 | QMT 财务 3600s，交易日历 86400s，tradability 不缓存 | 财务日级更新；交易日几乎不变；ST 状态变化不频但也不值得缓存中间值 |
| 压测范围 | **8 场景纯数据层**，不调 AI | 验证接口准确稳定，不浪费 token |
| 对照组 | 无 | Git diff 已能审查；A/B 对照产出 AI 风格差异非 actionable |

---

## 3. 架构

```
/s 股票名
    ↓
services/analysis_service.py::analyze_stock(ts_code)
    ↓
    ├─ 【新增】data/stock_gate.py::check_tradability(ts_code)
    │    ↓
    │   QMT.get_instrument_detail(iscomplete=True) → TradabilityResult
    │    ↓
    │   hard_block(退市) → raise TradabilityBlocked → CLI 退出码 2
    │   其他 → warnings 注入 context，继续流程
    ↓
data/report_data.py::build_report_context(..., tradability=...)
    ↓
    ├─ get_basic_info    ← QMT → Tushare → AKShare → Sina
    ├─ get_price_df      ← QMT → Tushare → 东财 → AKShare → Baostock（已接）
    ├─ get_financial     ← QMT 8 表（门控）→ Tushare
    ├─ get_income / balancesheet / cashflow / fina_indicator  ← QMT → Tushare 各自独立
    ├─ （龙虎/资金流/股东交易/回购/预告/快报 等 10+ 维度）← 不动
    ↓
    涨跌幅：get_trading_dates_before → price_df 定位真实交易日前收盘
    QMT 挂 → iloc[-N] 兜底
    ↓
返回 (context, raw_data) 含：
    - _tradability_warnings: list[str]
    - _tradability_facts: dict
    - _data_source_map: dict[str, str]
```

---

## 4. 模块设计

### 4.1 `data/stock_gate.py`（新建，~80 行）

```python
from dataclasses import dataclass, field
from enum import Enum

class TradabilityStatus(Enum):
    OK = "ok"
    ST = "st"                    # 含 *ST
    NEWLY_LISTED = "newly_listed"  # 上市 <20 交易日
    BSE_NO_DATA = "bse_no_data"  # 北交所 QMT 无权限
    DELISTED = "delisted"        # 退市 → hard_block
    UNKNOWN = "unknown"          # QMT+Tushare 都挂 → 不拦截

@dataclass
class TradabilityResult:
    status: TradabilityStatus
    hard_block: bool               # 仅 DELISTED=True
    warnings: list[str] = field(default_factory=list)
    facts: dict = field(default_factory=dict)  # InstrumentStatus/list_date 等

class TradabilityBlocked(Exception):
    """退市股硬拦截，CLI 层捕获"""
    def __init__(self, result: TradabilityResult):
        self.result = result

def check_tradability(ts_code: str) -> TradabilityResult: ...
```

**判定逻辑**（QMT 首选 → Tushare 兜底）：
1. QMT `get_instrument_detail(ts_code, iscomplete=True)`
   - 返 None/空：`.BJ` 后缀 → BSE_NO_DATA；其他 → UNKNOWN（放行）
   - `InstrumentStatus` ∈ {退市码集合} → DELISTED + hard_block
   - `InstrumentStatus ∈ (4, 31)` OR `InstrumentName` 以 "ST" / "*ST" 开头 → ST
   - `(UpStopPrice - PreClose) / PreClose < 0.06` 且非 ETF/指数 → ST（5% 涨跌停 = ST 标志）
   - `(today - OpenDate)` 交易日数 < 20 → NEWLY_LISTED
   - 都不命中 → OK
2. QMT 挂 → Tushare `get_basic_info`（判 name 含 "ST" + list_date 近期）
3. 两源都挂 → UNKNOWN，**放行不拦截**

### 4.2 `data/qmt_client.py`（扩展，+~200 行）

新增函数（全部在失败时 raise `QMTUnavailable`）：

```python
def get_instrument_info(ts_code: str) -> dict | None:
    """单股完整元信息（iscomplete=True 83 字段）；None = 未查到"""

def get_instrument_info_batch(ts_codes: list[str]) -> dict[str, dict]:
    """批量版，1ms/只，用于 gate/top10 批量预检"""

@compat_cache(ttl=3600)
def get_financial(ts_code: str, years: int = 3) -> dict[str, pd.DataFrame]:
    """
    下载+查询 8 张财务表。
    内部用 download_financial_data2 async + 60s threading timeout 保护。
    返回 {Balance, Income, CashFlow, Capital,
          Top10FlowHolder, Top10Holder, HolderNum, PershareIndex}
    窗口：近 years 年
    """

@compat_cache(ttl=86400)
def get_trading_dates_before(end_date: str, count: int, market: str = "SH") -> list[str]:
    """返回 end_date（含）之前 count 个真实交易日，'YYYY-MM-DD' 字符串升序"""

def get_divid_factors_safe(ts_code: str) -> pd.DataFrame:
    """除权因子表薄封装（异常转 QMTUnavailable）"""
```

### 4.3 `data/qmt_schema_map.py`（新建，~150 行）

字段映射 + 标准化工具：

```python
# QMT InstrumentDetail 83 字段 → Tushare basic_info schema
QMT_DETAIL_TO_TUSHARE_BASIC = {
    "InstrumentName": "name",
    "ExchangeID": "exchange",
    "OpenDate": "list_date",
    "FloatVolume": "float_share",
    "TotalVolume": "total_share",
    "PreClose": "pre_close",
    ...
}

# QMT PershareIndex 43 字段 → Tushare fina_indicator schema
QMT_PERSHARE_TO_TUSHARE_FINA = {
    "s_fa_eps_basic": "basic_eps",
    "s_fa_eps_diluted": "diluted_eps",
    "s_fa_bps": "bps",
    "s_fa_ocfps": "ocfps",
    ...
}

# QMT Balance / Income / CashFlow Wind 前缀字段 → 中文标准
QMT_BALANCE_TO_CN = {
    "tot_assets": "资产总计",
    "tot_liab": "负债合计",
    "cap_stk": "股本",
    "undistributed_profit": "未分配利润",
    ...
}

def qmt_detail_to_tushare_dict(detail: dict) -> dict:
    """QMT 元信息 dict → Tushare basic_info dict"""

def qmt_financials_to_tushare_text(tables: dict[str, pd.DataFrame]) -> str:
    """QMT 8 表整合成 Tushare get_financial 兼容字符串"""

def qmt_pershare_to_fina_indicator(df: pd.DataFrame) -> pd.DataFrame:
    """QMT PershareIndex DataFrame → Tushare fina_indicator schema"""
```

### 4.4 `data/tushare_client.py`（扩展）

沿用 Task 2 的 `qmt_fn` slot。本期给 6 个函数各加一个 `_qmt_*` 闭包：

- `get_basic_info` → `_qmt_basic_info`
- `get_financial` → `_qmt_financial`（核心表门控：Balance/Income/PershareIndex 任一空则 raise QMTUnavailable）
- `get_income` / `get_balancesheet` / `get_cashflow` / `get_fina_indicator` → 对应 `_qmt_*`

**门控样例**：

```python
def _qmt_financial():
    if not qmt_client.is_alive():
        raise QMTUnavailable()
    tables = qmt_client.get_financial(ts_code, years=3)
    core_empty = any(
        tables.get(k, pd.DataFrame()).empty
        for k in ("Balance", "Income", "PershareIndex")
    )
    if core_empty:
        raise QMTUnavailable("核心财务表空，降级")
    return qmt_financials_to_tushare_text(tables), None
```

### 4.5 `data/report_data.py`（小改）

**改动 A**：`build_report_context` 签名追加 `tradability: TradabilityResult | None = None`；把 `tradability.warnings / facts` 注入 context；新增 `_data_source_map` dump。

**改动 B**：涨跌幅辅助函数改用 `qmt_client.get_trading_dates_before`，QMT 挂则 fallback `iloc[-N]`。

### 4.6 `services/analysis_service.py`（小改）

- 入口处加 `from data.stock_gate import check_tradability, TradabilityBlocked`
- 在 `build_report_context` 前调 `check_tradability`
- `DELISTED` 时抛 `TradabilityBlocked`；CLI 层捕获打印友好消息 + 退出码 2

---

## 5. 数据流 & 降级

### 5.1 数据源日志

`_try_with_fallback` 改写：每次成功不仅 set 全局 `_data_source`，还写入 `_data_source_map[label] = source`。

`build_report_context` 结束前把 `_data_source_map` 复制到 context。

报告尾部渲染：
```
📊 数据来源：basic_info=qmt | price=qmt | financial=qmt | income=qmt | holder=tushare | capital_flow=eastmoney
```

### 5.2 每个数据维度独立降级

- 每个 `get_*` 函数独立调用 `_try_with_fallback`
- 核心财务表门控：Balance/Income/PershareIndex 空 → 8 表全走 Tushare
- 非核心表（HolderNum / Top10Holder 等）单独空不触发降级

---

## 6. 错误处理

| 异常 | 来源 | 处理 |
|---|---|---|
| `QMTUnavailable` | qmt_client | 当前维度降级下一源，不向用户报错 |
| `TradabilityBlocked` | stock_gate | CLI 层捕获，打印"此股已退市，不分析"，退出码 2 |
| `NoDataAvailable` | 所有源都挂 | 当前维度返空 string/DataFrame，标 `[unavailable]` |
| 未分类异常 | 任意 | 日志记 stack，该维度返空，分析继续 |

**边界场景**（见设计时对话，均有覆盖）：MiniQMT 未开 / 盘后未同步 / 财务空 / 北交所 / 新股 / QMT 同步版 hang / 字段映射漏。

---

## 7. 压测方案（纯数据层，不调 AI）

### 7.1 文件
`tests/test_qmt_single_stock_refactor.py`

### 7.2 8 个场景

| # | 场景 | 测试股票 | 关键断言 |
|---|---|---|---|
| 1 | 正常股 | `000001.SZ` | gate.status=OK; basic_info/price/financial 的 _data_source 都是 qmt |
| 2 | ST 股 | 运行时从 QMT `UpStop/Pre<0.06` 筛一只 | warnings 含 "ST 标记"; 不 crash |
| 3 | 新股 | 运行时从 OpenDate 筛上市 <20 日 | warnings 含 "上市 N 日" |
| 4 | 北交所 | `830300.BJ` 或实跑 sector 取一只 | warnings 含 "BSE 无 QMT 数据"; basic_info=tushare |
| 5 | 除权股 | `002594.SZ` 比亚迪 | 前复权 vs 后复权首日 close 不等；trading_dates 算出的 5 日收盘 ≠ iloc[-5] 兜底 |
| 6 | QMT 整体挂 | monkey-patch `qmt_client.is_alive=lambda: False` | 所有 _data_source 都不是 qmt；报告不空；不 crash |
| 7 | QMT 财务核心表空 | monkey-patch `qmt_client.get_financial` 返 Balance 空、Income 非空 | financial 源=tushare；basic_info 源=qmt |
| 8 | 退市股模拟 | monkey-patch `get_instrument_detail` 返 `InstrumentStatus=<退市码>` | 抛 TradabilityBlocked |

### 7.3 运行
```bash
python -m pytest tests/test_qmt_single_stock_refactor.py -v
# Expected: 8 passed
```

### 7.4 验收
- **8/8 通过 + 0 regression**（已有 15 个 QMT 测试不掉）

---

## 8. 交付物

- [ ] `data/stock_gate.py` 新（~80 行）
- [ ] `data/qmt_client.py` 扩（+200 行）
- [ ] `data/qmt_schema_map.py` 新（~150 行）
- [ ] `data/tushare_client.py` 改（6 函数加 qmt_fn + `_data_source_map`）
- [ ] `data/report_data.py` 改（tradability 注入 + 涨跌幅精准化 + _data_source_map dump）
- [ ] `services/analysis_service.py` 改（前置 gate + 处理 TradabilityBlocked）
- [ ] `tests/test_qmt_single_stock_refactor.py` 新（8 场景）
- [ ] `tests/fixtures/qmt_mocks.py` 新（共享 monkey-patch 工具）

**工作量**：~1 天。

---

## 9. 风险与应对

| 风险 | 应对 |
|---|---|
| QMT 字段映射漏 → AI prompt 输入缺字段 | schema_map 转换函数遇未知字段记 warning 丢弃，保底不 crash |
| 核心财务表门控误降级 | 仅 Balance/Income/PershareIndex 参与门控；其他表独立 |
| 退市码认识不全 | 从 QMT `InstrumentStatus` 文档抄；未匹配上的状态走 OK（放行）不拦截 |
| 北交所数据全无 | warnings 明确标"QMT 无元信息"，Tushare 兜底；BSE 股分析质量依赖 Tushare 水准 |
| tradability 两源都挂 → UNKNOWN | 放行但记 warning 日志，不让数据源挂掉阻塞用户 |
| 字段映射工作量超预估 | schema_map 只做"本期下游会消费的字段"，不做全映射 |

---

## 10. 后续阶段展望（本期不做）

- **阶段 B**：多源交叉验证 flag（QMT vs Tushare 财务差异 >10% 由 AI flag）
- **阶段 C**：AI prompt 重构吃 QMT 原生字段（PershareIndex 43 指标 > Tushare 11 指标）
- **阶段 D**：概念板/龙虎/资金流的 QMT 化（需投研版升级）
