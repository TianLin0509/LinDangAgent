# QMT 数据源接入设计（阶段 A / P0）

*Date: 2026-04-13*
*Scope: 只读数据打通（Read-only data integration）*
*Status: Approved by user, ready for implementation planning*

---

## 1. 背景与目标

### 1.1 背景
- 用户于 2026-04-13 开通国金证券 QMT，xtquant SDK 已在本机 Python 312 安装（版本 `xtquant_250516`）
- LinDangAgent 现有数据层依赖 Tushare / AKShare / 东财 / Baostock，其中 Tushare 私有服务器经常宕机，影响分析稳定性
- 官方文档：https://dict.thinktrader.net/

### 1.2 目标（本期）
把 QMT 作为**日线 / 分钟线 / 实时行情 / 板块成分**四类数据的最高优先级数据源，接入 LinDangAgent 现有 fallback 链，并产出一份供 AI 未来会话使用的实测 reference。

### 1.3 非目标（本期不做）
- ❌ QMT 交易执行（下单 / 撤单 / 持仓查询）
- ❌ 盘中实时监控服务（`subscribe_quote` 长驻进程）
- ❌ 财务 / 新闻 / 基本面迁移
- ❌ 大范围改造下游模块（只改一个金丝雀模块）

---

## 2. 关键决策（brainstorm 结论）

| 决策点 | 结论 | 理由 |
|---|---|---|
| 作用域 | A：只读数据 | 第一次接入，先稳扎稳打 |
| 数据源定位 | A2：专项替代（K 线 + 实时 + 板块） | QMT 强项聚焦，财务继续走原数据源 |
| 客户端管理 | B1：人工启动 + 健康检查降级 | 阶段 A 无需常驻，成本最低 |
| 接入范围 | C2：金丝雀单模块（`quick_scout.py`） | 最小侵入，Surgical Changes |
| 知识沉淀 | D2'：AI-oriented reference，由 probe 脚本自动产出 | 未来 Claude 会话可检索 |

---

## 3. 架构

```
┌────────────────────────────────────────────────────────────┐
│  quick_scout.py (canary, 本期唯一接入下游)                 │
│         ↓                                                  │
│  data/fallback.py  ← 统一路由层（已存在，轻改）            │
│         ↓                                                  │
│  [QMT] → [东财] → [AKShare] → [Baostock] → [Tushare]      │
│   NEW      existing                                         │
└────────────────────────────────────────────────────────────┘

其他下游模块（report_data.py / Stock_top10 / 指挥部）本期不动，
等金丝雀稳定 1-2 天、qmt_reference.md 成熟后再逐步推广（下期）。
```

---

## 4. 模块设计

### 4.1 `data/qmt_client.py`（新建，~150 行）

**对外 API**：

```python
class QMTUnavailable(Exception):
    """QMT 客户端未登录或连接失败，调用方应降级到其他数据源"""

def is_alive() -> bool:
    """健康检查，3 秒超时，失败返回 False（不抛异常）"""

def get_kline(
    symbol: str,          # "000001" or "000001.SZ"，自动归一化
    period: str = "1d",   # "1d" / "1m" / "5m" / "15m" / "30m" / "60m" / "1w" / "1mon"
    start: str = None,    # "20260101" 或 None
    end: str = None,
    count: int = 120,     # 当 start/end 为空时取近 N 根
    adjust: str = "front" # "front" / "back" / "none"
) -> pd.DataFrame:
    """
    返回标准 OHLCV DataFrame：
      index = datetime
      columns = [open, high, low, close, volume, amount]
    QMT 不可用时抛 QMTUnavailable
    """

def get_realtime(symbols: list[str]) -> dict[str, dict]:
    """
    返回: {"000001": {"price": 12.3, "bid1": ..., "ask1": ..., "ts": ...}, ...}
    symbol 会自动补市场后缀；返回时去掉，保持上游契约
    """

def get_sector_stocks(sector: str) -> list[str]:
    """板块成分股；如 "沪深A股" / "科创板" / "锂电池" """
```

**内部实现要点**：
- **模块级 lazy init**：首次调用才 `xtdata.connect()`，失败标记不可用（`_is_available=False`）后续直接 raise `QMTUnavailable`
- **代码归一化**：`000001` → `000001.SZ`，`600000` → `600000.SH`，ETF 走 `.SH/.SZ` 判断规则
- **请求级超时**：每次 API 5s timeout，超时视为降级
- **无缓存**：缓存职责交给现有层
- **返回前 schema 校验**：若返回的 df 缺失关键列（如 `close`），抛 `QMTUnavailable` + ERROR 日志（schema 变更的早期信号）

### 4.2 `data/fallback.py` 修改（+~20 行）

在现有 `get_kline_with_fallback` 式的路由函数中，将 QMT 插入数据源列表**最前端**：

```python
sources = [
    ("qmt", qmt_client.get_kline),       # NEW
    ("eastmoney", ...),                  # existing
    ("akshare", ...),
    ("baostock", ...),
    ("tushare", ...),
]
for name, fn in sources:
    try:
        df = fn(symbol, **kw)
        log.info(f"[data_source={name}] {symbol} rows={len(df)}")
        return df
    except QMTUnavailable:
        continue                          # QMT 挂了静默降级
    except Exception as e:
        log.warning(f"[{name}] failed: {e}")
        continue
raise NoDataAvailable(symbol)
```

**降级策略**：
- QMT 未登录 / 挂了 → 静默降级（预期行为）
- QMT 返回脏数据（schema 不符）→ ERROR 日志 + 降级（schema 变更告警）
- 其他数据源失败 → WARN 日志 + 降级

### 4.3 `quick_scout.py` 修改（1 处调用替换）

唯一改动：把原本直接调 `akshare_data.get_kline(...)` 的调用点替换为 `fallback.get_kline_with_fallback(...)`。

- 不改函数签名、不改返回格式、不改调用方
- 日志自动出现 `[data_source=qmt]` / `[data_source=akshare]`，肉眼确认 QMT 实际生效

---

## 5. 测试与验证

### 5.1 `tests/qmt_probe.py`（新建，~200 行，核心产出）

**性质**：全功能 API 探测脚本，**穷尽式验证** xtquant 在本机 + 国金账户下的真实行为；不是 pass/fail 断言，而是产出**体检报告**并**自动喂养** `docs/qmt_reference.md`。

**覆盖的 API 清单**（20 项）：

```
【连接层】
  [1]  xtdata.connect()                  连接 QMT 客户端
  [2]  get_client_version                版本号

【历史 K 线】
  [3]  get_market_data_ex  日线 × 60根
  [4]  get_market_data_ex  日线 × 全历史 (count=-1)
  [5]  get_market_data_ex  1m × 240根
  [6]  get_market_data_ex  5m/15m/30m/60m 各周期
  [7]  get_market_data_ex  周线/月线
  [8]  get_market_data_ex  前/后/不复权对比

【实时行情】
  [9]  get_full_tick([symbols])          快照
  [10] subscribe_quote + callback        订阅式（盘后记录行为）

【标的元信息】
  [11] get_instrument_detail
  [12] get_stock_list_in_sector("沪深A股")
  [13] get_stock_list_in_sector("科创板")
  [14] get_sector_list

【财务 / 扩展】
  [15] get_financial_data
  [16] get_instrument_type

【边界与错误】
  [17] 未登录时调用                       → 捕获异常类型
  [18] 非法 symbol "999999.XX"            → 错误处理
  [19] 超长 count (1000000)               → 性能/截断
  [20] 跨市场批量 ["000001.SZ","600000.SH"]
```

**输出格式（控制台）**：

```
======== QMT API Probe Report @ 2026-04-13 15:32 ========
[1]  xtdata.connect                        ✅ OK     12ms
[2]  get_client_version                     ✅ OK     "xtquant_250516"
[3]  日线×60                                ✅ OK     rows=60, cols=[...], 183ms
     sample: 2026-04-11  close=12.34  vol=1234567
...
[17] 未登录调用                             ✅ RAISED ConnectionError
[18] 非法 symbol                            ⚠️  返回空 df 不抛异常   ← 记入 reference
======== Summary: 18/20 OK, 2 Warning, 0 Fatal ========

→ Report saved to: docs/qmt_probe_report_20260413.md
→ qmt_reference.md auto-appended: 13 APIs with verified schemas
```

**关键特性**：
1. 每个 API 实测后**自动把 schema / 样例 / 耗时 append 到 `docs/qmt_reference.md`**——保证文档内容都是真实跑通的
2. 探测报告保留历史版本（`qmt_probe_report_YYYYMMDD.md`），未来 QMT 升级可 diff
3. 建议**盘前 / 盘中 / 盘后各跑一次**，时段相关 API 行为差异能看出来

### 5.2 `tests/test_qmt_smoke.py`（新建，轻量）

**金丝雀验收测试**：

```python
# 一键体检：
1. qmt_client.is_alive() → True
2. qmt_client.get_kline("000001", count=60) → df 非空且列齐
3. 运行 quick_scout 000001 → 日志包含 "[data_source=qmt]"
```

跑法：`python tests/test_qmt_smoke.py`

---

## 6. 产出的 reference 文档

### 6.1 `docs/qmt_reference.md`（由 probe 脚本自动生成）

**性质**：AI-oriented，供未来 Claude 会话检索使用，非人类速查手册。

**每个 API 一个 `## section` 作为锚点**：

```markdown
# QMT / xtquant Reference (AI-oriented, auto-generated)
Last verified: 2026-04-13 by tests/qmt_probe.py

## xtdata.get_market_data_ex
Signature: get_market_data_ex(field_list, stock_list, period, start_time, end_time, count)
Returns: dict[symbol, DataFrame], columns=[time, open, high, low, close, volume, amount]
Gotchas:
- symbol 必须带 .SZ/.SH 后缀
- period="1d" 日线；"1m"/"5m" 仅交易时段可查
- count=-1 表示全部历史
Verified example (2026-04-13):
  get_market_data_ex(["open","high","low","close","volume"], ["000001.SZ"], "1d", count=60)
  → rows=60, first=2026-01-14, last=2026-04-11, 183ms
```

**原则**：只写这次实测过的 API，没跑过的 API 不写（宁愿空白也不写未验证的，AI 读了会误信）。

### 6.2 memory `reference_qmt.md`

在 `C:\Users\lintian\.claude\projects\C--Users-lintian\memory\` 下新增，内容：

```markdown
---
name: QMT / xtquant API Reference 文档位置
description: QMT API 实测 reference，编码相关查询必先查此文档
type: reference
---

## QMT / xtquant API Reference
- 文档路径: `C:\LinDangAgent\docs\qmt_reference.md`（由 `tests/qmt_probe.py` 自动生成）
- 探测脚本: `C:\LinDangAgent\tests\qmt_probe.py`
- 官方文档: https://dict.thinktrader.net/
- 客户端封装: `C:\LinDangAgent\data\qmt_client.py`

**How to apply:** 涉及 QMT / xtquant / 数据层查询时，先读 qmt_reference.md 找实测 schema；没记录的 API 才去查官方文档并补到 reference。
```

并在 `MEMORY.md` 索引中添加一行。

---

## 7. 交付物清单

- [ ] `data/qmt_client.py`（新建，~150 行）
- [ ] `data/fallback.py`（修改，+~20 行）
- [ ] `quick_scout.py`（修改，1 处调用替换）
- [ ] `tests/qmt_probe.py`（新建，~200 行，核心学习产出）
- [ ] `tests/test_qmt_smoke.py`（新建，金丝雀验收）
- [ ] `docs/qmt_reference.md`（probe 脚本自动生成）
- [ ] `docs/qmt_probe_report_20260413.md`（首次探测报告）
- [ ] memory `reference_qmt.md` + `MEMORY.md` 索引更新

---

## 8. 验收标准

**"第一次彻底打通" = 全部满足**：

1. ✅ QMT 客户端登录后，`python -c "from data.qmt_client import get_kline; print(get_kline('000001', count=60))"` 能正常拿到 60 根日线
2. ✅ `quick_scout.py` 分析单股时，K 线**实际从 QMT 来**（日志出现 `[data_source=qmt]`）；QMT 未登录时自动降级到现有 fallback，分析不中断
3. ✅ `tests/qmt_probe.py` 一键跑完，报告 ≥15/20 API 正常（边界测试允许 warning）
4. ✅ `docs/qmt_reference.md` 包含 ≥10 个 API 的实测记录（schema / 样例 / 耗时 / 坑）
5. ✅ memory 索引更新，未来会话提到 QMT 能自动找到 reference

---

## 9. 工作量估算

- 核心编码 + 探测脚本开发：4-6 小时
- 实测与 reference 产出（需盘中至少跑一次）：1-2 小时
- 金丝雀验证 + 文档收尾：1 小时
- **合计：约 1 天**（分盘前/盘中两次 session 最高效）

---

## 10. 风险与应对

| 风险 | 应对 |
|---|---|
| xtquant SDK 版本不兼容官方文档 | probe 脚本实测为准，发现差异立即记入 reference 坑 |
| QMT 客户端频繁掉线 | `is_alive()` 健康检查 + 静默降级，分析不受影响 |
| 返回字段 schema 变化 | schema 校验 + ERROR 日志，早期发现 |
| 盘后无法测实时行情 API | probe 脚本记录"非交易时段预期行为"，盘中再跑一次对比 |
| 金丝雀改坏 `quick_scout.py` | 改动极小（1 处调用替换），Git diff 可秒回滚 |

---

## 11. 后续阶段展望（本期不做，仅记录）

- **阶段 B**：推广到 `report_data.py` / `Stock_top10` / 指挥部所有 K 线调用点
- **阶段 C**：`subscribe_quote` 常驻服务 + 盘中异动实时推送
- **阶段 D**：`xttrader` 模拟账户下单 → 全链路闭环
- **阶段 E**：Level2 / 分钟级策略 / 夜间学习白天执行
