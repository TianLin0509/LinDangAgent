# Single Stock Analysis Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor single stock analysis from 5-phase multi-general architecture to 2-round Opus deep analysis with decision tree scoring and self-evolution engine.

**Architecture:** Replace 3 Sonnet generals + Opus commander with single Opus analyst doing Round 1 (deep analysis with decision tree) + Round 2 (adversarial self-critique). Add experience database for historical lesson injection and evolution engine for weight/tree auto-tuning.

**Tech Stack:** Python 3.11+, SQLite (WAL mode), Claude Opus CLI, existing CLI provider infrastructure, existing email system.

---

## File Structure

### Files to Create

| File | Responsibility |
|------|---------------|
| `data/knowledge/decision_tree.json` | Decision tree config (nodes, thresholds, weights) — editable by evolution engine |
| `services/decision_tree.py` | Decision tree loader, code-based scoring, tree path recording |
| `ai/prompts_analyst.py` | Round 1 (deep analysis) + Round 2 (self-critique) prompt templates |
| `knowledge/experience_db.py` | Experience CRUD, retrieval by relevance scoring, lesson formatting |
| `knowledge/evolution_engine.py` | Backtesting, health reports, weight/tree adjustment proposals |
| `tests/test_decision_tree.py` | Decision tree scoring unit tests |
| `tests/test_experience_db.py` | Experience DB retrieval tests |
| `tests/test_analyst_flow.py` | New 2-round analysis flow integration tests |

### Files to Modify

| File | Changes |
|------|---------|
| `services/war_room.py` | Replace multi-general Phases 1-4 with 2-round Opus flow; keep Phase 0 (scout) and Phase 5 (assemble) |
| `services/analysis_service.py:67-72` | Update SCORE_WEIGHTS to new defaults (基本面10%, 预期差40%, 资金面30%, 技术面20%) |
| `ai/prompts_war_room.py` | Keep `build_clerk_prompt` (line 48) for potential reuse; mark general personalities as deprecated |
| `cli.py` | Add `review` command; update `cmd_analyze` to use new flow |
| `knowledge/night_learner.py:44-84` | Embed evolution engine backtesting into Round 1 (22:00) |
| `knowledge/outcome_tracker.py` | Extend `evaluate_pending()` for batch review with tree path diagnosis |

---

## Task 1: Decision Tree Configuration & Scoring Engine

**Files:**
- Create: `data/knowledge/decision_tree.json`
- Create: `services/decision_tree.py`
- Create: `tests/test_decision_tree.py`

- [ ] **Step 1: Write the decision tree config file**

Create `data/knowledge/decision_tree.json`:

```json
{
  "version": "1.0.0",
  "updated_at": "2026-04-10",
  "weights": {
    "基本面": 0.10,
    "预期差": 0.40,
    "资金面": 0.30,
    "技术面": 0.20
  },
  "correction_rules": {
    "catalyst_capital_resonance": {
      "condition": "预期差>=75 AND 资金面>=70",
      "action": "+3"
    },
    "catalyst_capital_divergence": {
      "condition": "预期差>=75 AND 资金面<=45",
      "action": "-5"
    },
    "fundamental_circuit_breaker": {
      "condition": "基本面<=25",
      "cap": 30
    },
    "bucket_effect": {
      "condition": "ANY_DIM<=30",
      "cap": 60
    },
    "premortem_cap": {
      "condition": "high_prob_fatal>=1",
      "cap": 70
    }
  },
  "trees": {
    "预期差": {
      "Q1": {
        "question": "是否存在未被市场充分定价的催化事件？",
        "branches": {
          "否": {"score_range": [30, 45], "terminal": true},
          "是": {"next": "Q2"}
        }
      },
      "Q2": {
        "question": "催化的确定性？",
        "branches": {
          "A类_已公告已披露": {"next": "Q3", "modifier": 0},
          "B类_可靠渠道预期": {"next": "Q3", "modifier": -10},
          "C类_纯逻辑推演": {"score_cap": 55, "terminal": true}
        }
      },
      "Q3": {
        "question": "催化的时间窗口？",
        "branches": {
          "30天内": {"next": "Q4", "modifier": 0},
          "30_90天": {"next": "Q4", "modifier": -5},
          "90天以上": {"score_cap": 60, "terminal": true}
        }
      },
      "Q4": {
        "question": "市场定价程度？",
        "branches": {
          "未反应_横盘或下跌": {"next": "Q5", "modifier": 0},
          "部分反应_涨不足10pct": {"next": "Q5", "modifier": -10},
          "充分反应_涨超20pct": {"score_cap": 50, "terminal": true}
        }
      },
      "Q5": {
        "question": "催化的量级？",
        "branches": {
          "业绩拐点_行业变革_政策级": {"score_range": [80, 95], "terminal": true},
          "单季超预期_产品放量_订单落地": {"score_range": [65, 80], "terminal": true},
          "小事件_情绪催化": {"score_range": [50, 65], "terminal": true}
        }
      }
    },
    "资金面": {
      "Q1": {
        "question": "主力资金方向？（北向+融资+大单净额三者投票）",
        "branches": {
          "三者同向流入": {"next": "Q2", "base_score": 70},
          "两正一负": {"next": "Q2", "base_score": 55},
          "两负一正": {"next": "Q2", "base_score": 40},
          "三者同向流出": {"score_range": [25, 40], "terminal": true}
        }
      },
      "Q2": {
        "question": "量价配合度？",
        "branches": {
          "健康_放量上涨或缩量回调": {"next": "Q3", "modifier_range": [10, 15]},
          "量价平稳": {"next": "Q3", "modifier": 0},
          "背离_放量下跌或缩量上涨": {"next": "Q3", "modifier_range": [-15, -10]}
        }
      },
      "Q3": {
        "question": "筹码结构？",
        "branches": {
          "机构加仓_集中度提升": {"modifier_range": [5, 10], "terminal": true},
          "无明显变化": {"modifier": 0, "terminal": true},
          "机构减仓_筹码分散": {"modifier_range": [-10, -5], "terminal": true}
        }
      }
    },
    "技术面": {
      "Q1": {
        "question": "趋势状态？",
        "branches": {
          "上升趋势_MA20大于MA60且价在MA20上方": {"next": "Q2", "base_score": 65},
          "震荡整理": {"next": "Q2", "base_score": 50},
          "下降趋势": {"next": "Q2", "base_score": 35}
        }
      },
      "Q2": {
        "question": "关键位置？",
        "branches": {
          "突破重要压力位或平台": {"next": "Q3", "modifier_range": [10, 15]},
          "支撑位附近企稳": {"next": "Q3", "modifier_range": [5, 10]},
          "无明显关键位": {"next": "Q3", "modifier": 0},
          "跌破重要支撑位": {"next": "Q3", "modifier_range": [-15, -10]}
        }
      },
      "Q3": {
        "question": "形态信号？",
        "branches": {
          "经典看多形态": {"modifier_range": [5, 10], "terminal": true},
          "无明显形态": {"modifier": 0, "terminal": true},
          "经典看空形态": {"modifier_range": [-10, -5], "terminal": true}
        }
      }
    },
    "基本面": {
      "Q1": {
        "question": "是否存在硬伤？",
        "branches": {
          "财务造假_ST风险_巨额商誉": {"score_range": [15, 25], "terminal": true},
          "业绩持续下滑超3季": {"score_range": [30, 45], "terminal": true},
          "无硬伤但平庸": {"score_range": [50, 60], "terminal": true},
          "无硬伤": {"next": "Q2"}
        }
      },
      "Q2": {
        "question": "业绩趋势？",
        "branches": {
          "营收利润双升且加速": {"score_range": [75, 90], "terminal": true},
          "稳定增长": {"score_range": [60, 75], "terminal": true},
          "增速放缓但仍为正": {"score_range": [50, 60], "terminal": true}
        }
      }
    }
  }
}
```

- [ ] **Step 2: Write failing tests for decision tree scoring engine**

Create `tests/test_decision_tree.py`:

```python
"""Tests for decision tree scoring engine."""
import json
import pytest
from pathlib import Path


def test_load_decision_tree():
    from services.decision_tree import load_tree
    tree = load_tree()
    assert "weights" in tree
    assert "trees" in tree
    assert tree["weights"]["预期差"] == 0.40
    assert tree["weights"]["基本面"] == 0.10


def test_compute_weighted_score():
    from services.decision_tree import compute_weighted, load_tree
    tree = load_tree()
    scores = {"基本面": 60, "预期差": 80, "资金面": 70, "技术面": 65}
    result = compute_weighted(scores, tree["weights"])
    # 60*0.10 + 80*0.40 + 70*0.30 + 65*0.20 = 6+32+21+13 = 72.0
    assert result == 72.0


def test_apply_corrections_resonance():
    from services.decision_tree import apply_corrections, load_tree
    tree = load_tree()
    scores = {"基本面": 70, "预期差": 80, "资金面": 75, "技术面": 70, "综合加权": 76.5}
    result = apply_corrections(scores, tree["correction_rules"])
    # catalyst-capital resonance: +3
    assert result["综合加权"] == 79.5
    assert result.get("_resonance_bonus") is True


def test_apply_corrections_divergence():
    from services.decision_tree import apply_corrections, load_tree
    tree = load_tree()
    scores = {"基本面": 60, "预期差": 80, "资金面": 40, "技术面": 65, "综合加权": 65.0}
    result = apply_corrections(scores, tree["correction_rules"])
    # catalyst-capital divergence: -5
    assert result["综合加权"] == 60.0
    assert result.get("_divergence_penalty") is True


def test_apply_corrections_bucket_cap():
    from services.decision_tree import apply_corrections, load_tree
    tree = load_tree()
    scores = {"基本面": 25, "预期差": 80, "资金面": 70, "技术面": 65, "综合加权": 72.0}
    result = apply_corrections(scores, tree["correction_rules"])
    # bucket effect: any dim <=30 → cap 60
    assert result["综合加权"] <= 60


def test_apply_corrections_fundamental_breaker():
    from services.decision_tree import apply_corrections, load_tree
    tree = load_tree()
    scores = {"基本面": 20, "预期差": 80, "资金面": 70, "技术面": 65, "综合加权": 72.0}
    result = apply_corrections(scores, tree["correction_rules"])
    # fundamental circuit breaker: 基本面<=25 → cap 30
    assert result["综合加权"] <= 30


def test_apply_premortem_cap():
    from services.decision_tree import apply_corrections, load_tree
    tree = load_tree()
    scores = {"基本面": 70, "预期差": 85, "资金面": 80, "技术面": 75, "综合加权": 81.0}
    result = apply_corrections(scores, tree["correction_rules"], high_prob_fatal_count=1)
    assert result["综合加权"] <= 70
    assert result.get("_premortem_cap") is True


def test_format_tree_for_prompt():
    from services.decision_tree import format_tree_for_prompt, load_tree
    tree = load_tree()
    text = format_tree_for_prompt(tree["trees"])
    assert "预期差" in text
    assert "Q1" in text
    assert "催化" in text


def test_record_tree_path():
    from services.decision_tree import record_tree_path
    path = record_tree_path("预期差", ["Q1:是", "Q2:A类_已公告已披露", "Q3:30天内", "Q4:未反应_横盘或下跌", "Q5:单季超预期_产品放量_订单落地"], 75)
    assert path == "是→A类→30天内→未定价→单季超预期→75分"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd C:\LinDangAgent && python -m pytest tests/test_decision_tree.py -v`
Expected: ModuleNotFoundError — `services.decision_tree` does not exist yet.

- [ ] **Step 4: Implement the decision tree scoring engine**

Create `services/decision_tree.py`:

```python
"""Decision tree scoring engine.

Loads tree config from JSON, computes weighted scores,
applies correction rules, and formats tree for prompt injection.
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_TREE_PATH = Path(__file__).resolve().parent.parent / "data" / "knowledge" / "decision_tree.json"
_cached_tree: dict | None = None


def load_tree(path: Path | None = None) -> dict:
    """Load decision tree config. Cached after first load."""
    global _cached_tree
    p = path or _TREE_PATH
    if _cached_tree is None or path is not None:
        with open(p, encoding="utf-8") as f:
            _cached_tree = json.load(f)
    return _cached_tree


def reload_tree() -> dict:
    """Force reload (after evolution engine updates the file)."""
    global _cached_tree
    _cached_tree = None
    return load_tree()


def compute_weighted(scores: dict, weights: dict) -> float:
    """Compute weighted composite from four dimension scores."""
    total = 0.0
    w_sum = 0.0
    for dim, w in weights.items():
        if dim in scores:
            total += scores[dim] * w
            w_sum += w
    if w_sum == 0:
        return 50.0
    return round(total / w_sum * w_sum / w_sum, 1)  # normalize if partial


def apply_corrections(
    scores: dict,
    rules: dict,
    high_prob_fatal_count: int = 0,
) -> dict:
    """Apply correction rules to scores. Returns modified copy."""
    s = dict(scores)
    dims = ["基本面", "预期差", "资金面", "技术面"]

    # Catalyst-capital resonance: +3
    if s.get("预期差", 0) >= 75 and s.get("资金面", 0) >= 70:
        s["综合加权"] = s.get("综合加权", 50) + 3
        s["_resonance_bonus"] = True

    # Catalyst-capital divergence: -5
    if s.get("预期差", 0) >= 75 and s.get("资金面", 0) <= 45:
        s["综合加权"] = s.get("综合加权", 50) - 5
        s["_divergence_penalty"] = True

    # Fundamental circuit breaker: 基本面<=25 → cap 30
    if s.get("基本面", 50) <= 25:
        s["综合加权"] = min(s.get("综合加权", 50), 30)
        s["_fundamental_breaker"] = True

    # Bucket effect: any dim <=30 → cap 60
    min_score = min(s.get(d, 50) for d in dims)
    if min_score <= 30 and not s.get("_fundamental_breaker"):
        s["综合加权"] = min(s.get("综合加权", 50), 60)
        s["_bucket_capped"] = True

    # Pre-mortem cap: high prob fatal >=1 → cap 70
    if high_prob_fatal_count >= 1:
        s["综合加权"] = min(s.get("综合加权", 50), 70)
        s["_premortem_cap"] = True

    # Clamp to [0, 100]
    s["综合加权"] = max(0, min(100, s.get("综合加权", 50)))

    return s


def format_tree_for_prompt(trees: dict) -> str:
    """Format decision trees as readable text for prompt injection."""
    lines = []
    dim_names = {"预期差": "预期差（权重最高）", "资金面": "资金面", "技术面": "技术面", "基本面": "基本面（体检式）"}
    for dim, label in dim_names.items():
        if dim not in trees:
            continue
        lines.append(f"\n### {label}评分决策树\n")
        tree = trees[dim]
        for qid in sorted(tree.keys()):
            node = tree[qid]
            lines.append(f"{qid}: {node['question']}")
            for branch_name, branch in node["branches"].items():
                display = branch_name.replace("_", " ")
                if branch.get("terminal"):
                    if "score_range" in branch:
                        lo, hi = branch["score_range"]
                        lines.append(f"  ├── {display} → {lo}-{hi}分")
                    elif "score_cap" in branch:
                        lines.append(f"  ├── {display} → 上限{branch['score_cap']}分")
                    else:
                        mod = branch.get("modifier", 0)
                        mr = branch.get("modifier_range")
                        if mr:
                            lines.append(f"  ├── {display} → {mr[0]:+d}~{mr[1]:+d}分")
                        else:
                            lines.append(f"  ├── {display} → {mod:+d}分")
                else:
                    extras = []
                    if "base_score" in branch:
                        extras.append(f"基础{branch['base_score']}分")
                    if "modifier" in branch and branch["modifier"] != 0:
                        extras.append(f"{branch['modifier']:+d}分")
                    if "modifier_range" in branch:
                        mr = branch["modifier_range"]
                        extras.append(f"{mr[0]:+d}~{mr[1]:+d}分")
                    if "score_cap" in branch:
                        extras.append(f"上限{branch['score_cap']}分")
                    suffix = f"（{'，'.join(extras)}）" if extras else ""
                    lines.append(f"  ├── {display} → {branch['next']}{suffix}")
            lines.append("")
    return "\n".join(lines)


def record_tree_path(dim: str, steps: list[str], final_score: int) -> str:
    """Format a tree traversal path for storage.

    Args:
        dim: Dimension name (e.g. "预期差")
        steps: List of "QN:branch_choice" strings
        final_score: Final score for this dimension

    Returns:
        Human-readable path string like "是→A类→30天内→未定价→单季超预期→75分"
    """
    labels = []
    for step in steps:
        _, choice = step.split(":", 1)
        # Clean up underscores and prefixes
        clean = choice.replace("_", "").split("（")[0].split("_")[0]
        # Shorten common labels
        short_map = {
            "是": "是", "否": "否",
            "A类已公告已披露": "A类",
            "B类可靠渠道预期": "B类",
            "C类纯逻辑推演": "C类",
            "30天内": "30天内",
            "3090天": "30-90天",
            "90天以上": ">90天",
            "未反应横盘或下跌": "未定价",
            "部分反应涨不足10pct": "部分定价",
            "充分反应涨超20pct": "充分定价",
        }
        label = short_map.get(clean, clean)
        labels.append(label)
    labels.append(f"{final_score}分")
    return "→".join(labels)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd C:\LinDangAgent && python -m pytest tests/test_decision_tree.py -v`
Expected: All 8 tests PASS.

- [ ] **Step 6: Fix any failing tests, iterate**

If `compute_weighted` normalization is wrong (partial weights), fix the formula:
```python
def compute_weighted(scores: dict, weights: dict) -> float:
    total = 0.0
    w_sum = 0.0
    for dim, w in weights.items():
        if dim in scores:
            total += scores[dim] * w
            w_sum += w
    return round(total / w_sum, 1) if w_sum > 0 else 50.0
```

- [ ] **Step 7: Commit**

```bash
git add data/knowledge/decision_tree.json services/decision_tree.py tests/test_decision_tree.py
git commit -m "feat: add decision tree scoring engine with JSON config"
```

---

## Task 2: New Analyst Prompts (Round 1 + Round 2)

**Files:**
- Create: `ai/prompts_analyst.py`

- [ ] **Step 1: Create the analyst prompt module**

Create `ai/prompts_analyst.py`:

```python
"""Prompt templates for the 2-round Opus analyst.

Round 1: Deep analysis with decision tree scoring.
Round 2: Adversarial self-critique with score correction.
"""


def build_round1_system(decision_tree_text: str, lessons_text: str = "") -> str:
    """Build Round 1 system prompt.

    Args:
        decision_tree_text: Formatted decision tree from format_tree_for_prompt()
        lessons_text: Optional 【历史镜鉴】block from experience DB
    """
    lessons_section = ""
    if lessons_text:
        lessons_section = f"""

---
以下是与本次分析相关的历史经验教训，供你参考但不盲从：

{lessons_text}
"""

    return f"""你是一位价值投机风格的短中线作手，擅长捕捉预期差驱动的交易机会。

## 你的投资哲学
- 核心驱动力是"预期差"——市场尚未充分定价的催化事件
- 资金面是确认信号——催化再好，没有资金认可就不动手
- 技术面是择时工具——用关键位和形态选入场点
- 基本面是安全网——只做体检，排除硬伤即可，不追求完美基本面

## 评分框架
你必须沿以下决策树对四个维度逐一打分。每个维度从 Q1 开始，沿分支走到叶子节点，得到该维度的分数。

**禁止自由裁量打分。你必须写出走过的决策树路径。**

走完决策树后，你可以附加 ±5 分的"综合研判微调"，但必须：
1. 明确写出微调方向和幅度
2. 给出具体理由（不接受"综合考虑"等模糊说辞）

{decision_tree_text}

## 证据分层
- A类（硬证据）：公告、财报、监管文件、交易所数据 → 可直接支撑结论
- B类（软证据）：券商研报、行业数据、供应链调研 → 需交叉验证
- C类（弱证据）：舆情、论坛讨论、未验证消息 → 仅供参考，不可作为打分依据

每个结论必须标注所用证据等级。无A/B类证据支撑的维度，该维度上限60分。

## 输出格式（6个必填区块，严格按序）

<<<ANALYSIS>>>

### 一、核心定调
[一句话：做多/做空/观望 + 核心逻辑（≤20字，必须可证伪）]

### 二、催化地图
[未来30天内的具体催化事件列表]
- 催化1：[事件] | 时间窗口：[具体日期或触发条件] | 证据等级：[A/B/C]
- 催化2：...
（无明确催化请写"当前无可识别的30天内催化"，不要编造）

### 三、四维评分

**预期差**（权重40%）
- 决策树路径：Q1:[选择] → Q2:[选择] → ... → [分数]分
- 微调：[±N分，理由] （无微调写"无"）
- 最终得分：[X]分

**资金面**（权重30%）
- 决策树路径：Q1:[选择] → Q2:[选择] → Q3:[选择] → [分数]分
- 微调：[±N分，理由]
- 最终得分：[X]分

**技术面**（权重20%）
- 决策树路径：Q1:[选择] → Q2:[选择] → Q3:[选择] → [分数]分
- 微调：[±N分，理由]
- 最终得分：[X]分

**基本面**（权重10%）
- 决策树路径：Q1:[选择] → Q2:[选择] → [分数]分
- 微调：[±N分，理由]
- 最终得分：[X]分

<<<SCORES>>>
基本面: [X]/100
预期差: [X]/100
资金面: [X]/100
技术面: [X]/100
<<<END_SCORES>>>

### 四、资金确认
- 北向资金：[具体数字，近5日净额] | 判读：[主动加仓/被动调仓/净流出]
- 融资余额：[变化趋势，具体数字]
- 主力大单：[近5日净额]
- 综合判断：[一句话]

### 五、技术研判
- 趋势：[上升/震荡/下降]，MA20=[X]，MA60=[X]
- 关键压力位：[具体价位]（理由）
- 关键支撑位：[具体价位]（理由）
- 形态：[识别到的形态或"无明显形态"]

### 六、仓位建议
- 操作建议：[做多/做空/观望]
- 入场点：[具体价位或条件]
- 止损位：[具体价位]（距入场约[X]%）
- 目标位：[具体价位]（距入场约[X]%）
- 持有周期：[X-Y个交易日]
- 证伪条件：[什么情况下此判断作废]

<<<END_ANALYSIS>>>
{lessons_section}"""


ROUND2_SYSTEM = """你是一位冷酷的魔鬼代言人。你的唯一使命是挑出上一轮分析中的漏洞、过度乐观和盲区。

## 你的工作原则
- 你不是来确认分析的，你是来摧毁它的
- 每一个乐观判断都要追问"真的吗？证据够硬吗？"
- 催化事件要追问"是否已经充分定价？"
- 资金信号要追问"是主动加仓还是被动调仓？"
- 对自己的反驳同样要求严谨，不接受廉价反驳

## 禁止使用的廉价论据
以下论据因过于泛化而无法提供决策价值，禁止使用：
- "宏观经济不确定性"
- "大盘系统性风险"
- "地缘政治风险"
- "市场情绪波动"
- "业绩不及预期的可能"（必须说明哪个业务线、为什么）

## 强制任务（5项全部完成）

### 1. 致命理由（必须找到3个）
找出3个可能导致买入后亏损20%以上的具体理由。每个理由必须：
- 指向具体的风险来源（不是泛化风险）
- 给出触发概率判定：高(>50%) / 中(20-50%) / 低(<20%)
- 给出可预警的信号（什么迹象出现时要跑）

### 2. 催化审视
逐条审视Round 1的催化地图：
- 哪些催化可能已被市场充分定价？
- 哪些催化的时间窗口判断可能过于乐观？
- 有没有被遗漏的负面催化？

### 3. 资金信号验证
- 北向资金流入是否可能是MSCI/FTSE指数调仓带来的被动流入？
- 融资余额增长是否可能是杠杆资金在顶部加仓？
- 大单净流入的持续性如何？

### 4. 评分修正
基于以上反驳，对四个维度评分进行修正：
- 每个维度最多±10分
- 必须说明修正理由
- 无需修正的维度写"维持原分，理由：[...]"

### 5. Pre-mortem 逆向验尸
假设你在今天买入，20个交易日后亏损20%。最可能的3条路径是什么？
- 路径1：[具体描述] → 概率：[高/中/低] → 预警信号：[...]
- 路径2：...
- 路径3：...

**铁律**：若有≥1个致命路径为"高概率"，你必须在修正中将综合加权压到70以下。

## 反驳质量自检
完成所有反驳后，检查：
- 是否每个反驳都有具体论据支撑（不是"可能""或许"）？
- 是否存在你没认真反驳就放过的乐观判断？
- 如果所有反驳都很弱，说明原分析可能确实很强——但你必须诚实承认这一点，而不是编造反驳

## 输出格式

<<<CRITIQUE>>>

### 致命理由
1. [理由] | 概率：[高/中/低] | 预警信号：[...]
2. ...
3. ...

### 催化审视
[逐条审视]

### 资金信号验证
[验证结论]

### 评分修正
<<<SCORE_CORRECTIONS>>>
基本面: [±N分] | 理由：[...]
预期差: [±N分] | 理由：[...]
资金面: [±N分] | 理由：[...]
技术面: [±N分] | 理由：[...]
<<<END_SCORE_CORRECTIONS>>>

### Pre-mortem
- 路径1：[...] → 概率：[...] → 预警信号：[...]
- 路径2：[...] → 概率：[...] → 预警信号：[...]
- 路径3：[...] → 概率：[...] → 预警信号：[...]

<<<HIGH_PROB_FATAL_COUNT>>>
[N]
<<<END_HIGH_PROB_FATAL_COUNT>>>

### 反驳质量自检
[诚实评估]

<<<END_CRITIQUE>>>"""


def build_round2_user(round1_output: str) -> str:
    """Build Round 2 user prompt by wrapping Round 1 output."""
    return f"""以下是你上一轮的分析报告。现在请切换到魔鬼代言人视角，逐条审视并质疑。

---
{round1_output}
---

请按照系统提示的格式完成所有5项强制任务。"""


def build_report_header(stock_name: str, final_scores: dict) -> str:
    """Build the report markdown header."""
    score = final_scores.get("综合加权", 50)
    rating = final_scores.get("_rating", "按兵不动")
    return f"# 【{stock_name}】深度分析报告\n\n**最终评定**：综合 {score:.0f} — {rating}\n"
```

- [ ] **Step 2: Commit**

```bash
git add ai/prompts_analyst.py
git commit -m "feat: add 2-round analyst prompt templates with decision tree integration"
```

---

## Task 3: Refactor War Room to 2-Round Flow

**Files:**
- Modify: `services/war_room.py` (major refactor of lines 451-864)
- Modify: `services/analysis_service.py:67-72` (update weights)

- [ ] **Step 1: Update SCORE_WEIGHTS in analysis_service.py**

In `services/analysis_service.py`, change lines 67-72:

```python
# Old:
SCORE_WEIGHTS = {
    "基本面": 0.15,
    "预期差": 0.35,
    "资金面": 0.30,
    "技术面": 0.20,
}

# New:
SCORE_WEIGHTS = {
    "基本面": 0.10,
    "预期差": 0.40,
    "资金面": 0.30,
    "技术面": 0.20,
}
```

Note: This is the static fallback. The primary weights now come from `decision_tree.json` and are loaded dynamically. Update `parse_scores()` to accept optional weights parameter:

At line 75, change signature:
```python
def parse_scores(text: str, weights: dict | None = None) -> dict | None:
```

At line 126-135 (weighted composite calculation), change to:
```python
    w = weights or SCORE_WEIGHTS
    total_w = sum(w.get(d, 0) for d in _DIMS if d in result)
    if total_w > 0:
        result["综合加权"] = round(
            sum(result.get(d, 0) * w.get(d, 0) for d in _DIMS) / total_w,
            1,
        )
```

- [ ] **Step 2: Refactor war_room.py — add new imports and update presets**

At the top of `services/war_room.py`, add imports after existing ones (around line 19):

```python
from services.decision_tree import load_tree, compute_weighted, apply_corrections, format_tree_for_prompt
from ai.prompts_analyst import build_round1_system, ROUND2_SYSTEM, build_round2_user, build_report_header
```

Replace `WAR_ROOM_PRESETS` (lines 28-55) with:

```python
WAR_ROOM_PRESETS = {
    "opus": {
        "label": "Opus 深度分析（两轮自我对话）",
        "analyst": "🧠 Claude Opus（MAX）",
    },
    "sonnet": {
        "label": "Sonnet 深度分析（速度优先）",
        "analyst": "⚡ Claude Sonnet（MAX）",
    },
    # Legacy presets kept for Top10/batch compatibility
    "balanced": {
        "label": "负载均衡阵容（Gemini+Codex将领，Claude Opus裁决）",
        "scouts": ["🔮 Gemini CLI（免费）", "🤖 Codex CLI（Plus）", "🔮 Gemini CLI（免费）"],
        "commander": "🧠 Claude Opus（MAX）",
        "_legacy": True,
    },
    "max": {
        "label": "全 Claude MAX 阵容（Sonnet将领+Opus裁决）",
        "scouts": ["⚡ Claude Sonnet（MAX）"] * 3,
        "commander": "🧠 Claude Opus（MAX）",
        "_legacy": True,
    },
    "gemini": {
        "label": "全 Gemini 阵容（免费）",
        "scouts": ["🔮 Gemini CLI（免费）"] * 3,
        "commander": "🔮 Gemini CLI（免费）",
        "_legacy": True,
    },
}
DEFAULT_PRESET = "opus"
```

- [ ] **Step 3: Implement the new 2-round analysis flow**

Replace the body of `run_war_room()` (lines 353-864). Keep the function signature unchanged for backward compatibility, but add a router:

```python
def run_war_room(
    stock_name: str,
    username: str = "cli",
    preset: str = DEFAULT_PRESET,
    skip_extra_recon: bool = False,
) -> WarRoomResult:
    """Main entry point for stock analysis.

    New presets (opus/sonnet) use 2-round deep analysis.
    Legacy presets (balanced/max/gemini) use old multi-general flow.
    """
    cfg = WAR_ROOM_PRESETS.get(preset)
    if not cfg:
        logger.error("Unknown preset: %s", preset)
        return WarRoomResult(stock_name=stock_name)

    if cfg.get("_legacy"):
        return _run_war_room_legacy(stock_name, username, preset, skip_extra_recon)

    return _run_war_room_v2(stock_name, username, cfg)
```

Then add `_run_war_room_v2` as a new function (insert after `run_war_room`):

```python
def _run_war_room_v2(
    stock_name: str,
    username: str,
    preset_cfg: dict,
) -> WarRoomResult:
    """2-round Opus deep analysis flow."""
    report_id = str(uuid.uuid4())
    analyst_model = preset_cfg["analyst"]

    # ── Phase 0: Scout (reuse existing data collection) ──────────
    # [Keep existing Phase 0 logic from lines 381-449 as-is,
    #  extract into _phase0_scout() helper if not already]
    resolved_name, ts_code, data_brief, sentiment_ctx, macro_ctx = _phase0_scout(stock_name)

    # Inject evolution experience
    experience_text = _get_experience_lessons(ts_code, resolved_name)

    # Load decision tree and format for prompt
    tree = load_tree()
    tree_text = format_tree_for_prompt(tree["trees"])

    # ── Phase 1: Round 1 — Deep Analysis ─────────────────────────
    logger.info("Phase 1: Round 1 deep analysis with %s", analyst_model)
    round1_system = build_round1_system(tree_text, experience_text)
    round1_text = _call_single_model(data_brief, round1_system, analyst_model, max_tokens=8000)

    # Parse scores from Round 1
    from services.analysis_service import parse_scores
    round1_scores = parse_scores(round1_text, tree["weights"])
    if not round1_scores or round1_scores.get("_parse_failed"):
        # Fallback: retry with Claude Sonnet
        logger.warning("Round 1 score parse failed, retrying with Sonnet fallback")
        round1_text = _call_single_model(data_brief, round1_system, CLAUDE_FALLBACK, max_tokens=8000)
        round1_scores = parse_scores(round1_text, tree["weights"])

    if not round1_scores:
        round1_scores = {d: 50 for d in _SCORE_DIMS}
        round1_scores["综合加权"] = 50.0
        round1_scores["_parse_failed"] = True

    # ── Phase 2: Round 2 — Self-Critique ─────────────────────────
    logger.info("Phase 2: Round 2 self-critique with %s", analyst_model)
    round2_user = build_round2_user(round1_text)
    round2_text = _call_single_model(round2_user, ROUND2_SYSTEM, analyst_model, max_tokens=6000)

    # Parse score corrections from Round 2
    final_scores = _apply_round2_corrections(round1_scores, round2_text, tree)

    # Apply code-level corrections
    final_scores = apply_corrections(
        final_scores,
        tree["correction_rules"],
        high_prob_fatal_count=_extract_fatal_count(round2_text),
    )

    # Generate rating
    from services.analysis_service import apply_bucket_correction
    final_scores = apply_bucket_correction(final_scores)

    # ── Phase 3: Assemble Report ─────────────────────────────────
    combined_md = _build_v2_report(resolved_name, round1_text, round2_text, final_scores)

    # Save to DB
    from repositories.report_repo import save_report
    save_report(report_id, username, resolved_name, combined_md, ts_code=ts_code)

    # Save tracker for evolution engine
    _save_v2_tracker(report_id, resolved_name, ts_code, round1_scores, final_scores, round1_text, round2_text)

    result = WarRoomResult(
        stock_name=resolved_name,
        stock_code=ts_code,
        general_reports=[{"report_text": round1_text, "scores": round1_scores}],
        final_report=round2_text,
        final_scores=final_scores,
        combined_markdown=combined_md,
        report_id=report_id,
    )

    # Send email
    _send_war_room_email(result)

    return result
```

- [ ] **Step 4: Implement helper functions for the new flow**

Add these helpers after `_run_war_room_v2`:

```python
def _phase0_scout(stock_name: str) -> tuple[str, str, str, str, str]:
    """Extract Phase 0 scout logic into reusable helper.

    Returns: (resolved_name, ts_code, data_brief, sentiment_ctx, macro_ctx)
    """
    # [Move existing Phase 0 logic from lines 381-449 here]
    # This is a mechanical extraction, no logic changes.
    ...


def _get_experience_lessons(ts_code: str, stock_name: str) -> str:
    """Retrieve relevant lessons from experience DB.

    Returns formatted 【历史镜鉴】text or empty string.
    """
    try:
        from knowledge.experience_db import retrieve_lessons
        return retrieve_lessons(ts_code, stock_name)
    except Exception as e:
        logger.warning("Experience retrieval failed: %s", e)
        return ""


def _apply_round2_corrections(
    round1_scores: dict, round2_text: str, tree: dict
) -> dict:
    """Parse Round 2 score corrections and apply to Round 1 scores."""
    import re
    scores = dict(round1_scores)

    # Extract SCORE_CORRECTIONS block
    m = re.search(
        r"<<<SCORE_CORRECTIONS>>>(.*?)<<<END_SCORE_CORRECTIONS>>>",
        round2_text, re.DOTALL
    )
    if not m:
        logger.warning("No SCORE_CORRECTIONS block found in Round 2")
        return scores

    block = m.group(1)
    for dim in _SCORE_DIMS:
        # Match patterns like "预期差: -5分" or "预期差: +3分" or "预期差: ±0分"
        pat = re.compile(rf"{dim}:\s*([+-]?\d+)\s*分", re.IGNORECASE)
        dm = pat.search(block)
        if dm:
            correction = int(dm.group(1))
            correction = max(-10, min(10, correction))  # Clamp to ±10
            scores[dim] = max(0, min(100, scores.get(dim, 50) + correction))

    # Recompute weighted score with tree weights
    scores["综合加权"] = compute_weighted(scores, tree["weights"])
    return scores


def _extract_fatal_count(round2_text: str) -> int:
    """Extract high-probability fatal count from Round 2 output."""
    import re
    m = re.search(
        r"<<<HIGH_PROB_FATAL_COUNT>>>\s*(\d+)\s*<<<END_HIGH_PROB_FATAL_COUNT>>>",
        round2_text
    )
    if m:
        return int(m.group(1))

    # Fallback: count "高" probability mentions in Pre-mortem section
    premortem = round2_text.split("Pre-mortem")[-1] if "Pre-mortem" in round2_text else ""
    return len(re.findall(r"概率[：:]\s*高", premortem))


def _build_v2_report(
    stock_name: str,
    round1_text: str,
    round2_text: str,
    final_scores: dict,
) -> str:
    """Assemble the final report markdown."""
    header = build_report_header(stock_name, final_scores)

    score_line = " | ".join(
        f"{d}: {final_scores.get(d, 50):.0f}"
        for d in _SCORE_DIMS
    )
    composite = final_scores.get("综合加权", 50)
    rating = final_scores.get("_rating", "按兵不动")

    return f"""{header}

**四维评分**：{score_line}
**综合加权**：{composite:.0f} — {rating}

---

## 深度分析（Round 1）

{round1_text}

---

## 魔鬼代言人质疑（Round 2）

{round2_text}
"""


def _save_v2_tracker(
    report_id: str,
    stock_name: str,
    ts_code: str,
    round1_scores: dict,
    final_scores: dict,
    round1_text: str,
    round2_text: str,
):
    """Save tracker entry for evolution engine with tree paths."""
    import re
    tracker_path = BASE_DIR / "data" / "knowledge" / "war_room_tracker.jsonl"

    # Extract tree paths from Round 1 text
    tree_paths = {}
    for dim in _SCORE_DIMS:
        pat = re.compile(
            rf"{dim}.*?决策树路径[：:]\s*(.+?)(?:\n|$)", re.MULTILINE
        )
        m = pat.search(round1_text)
        if m:
            tree_paths[dim] = m.group(1).strip()

    entry = {
        "report_id": report_id,
        "stock_name": stock_name,
        "ts_code": ts_code,
        "timestamp": __import__("datetime").datetime.now().isoformat(),
        "version": "v2",
        "round1_scores": {d: round1_scores.get(d) for d in _SCORE_DIMS + ["综合加权"]},
        "final_scores": {d: final_scores.get(d) for d in _SCORE_DIMS + ["综合加权"]},
        "rating": final_scores.get("_rating", ""),
        "tree_paths": tree_paths,
    }

    with open(tracker_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
```

- [ ] **Step 5: Rename old flow for backward compatibility**

Rename the original `run_war_room` body (lines 353-864 old content) to `_run_war_room_legacy`:

```python
def _run_war_room_legacy(
    stock_name: str,
    username: str = "cli",
    preset: str = "balanced",
    skip_extra_recon: bool = False,
) -> WarRoomResult:
    """Legacy multi-general war room flow. Used by old presets (balanced/max/gemini)."""
    # [Entire old run_war_room body moved here unchanged]
    ...
```

This preserves backward compatibility for Top10/batch workflows that still use legacy presets.

- [ ] **Step 6: Update cli.py cmd_analyze to use new preset**

In `cli.py`, update `cmd_analyze` (line 203-205):

```python
def cmd_analyze(stock: str):
    """单股分析 — Opus两轮深度分析+决策树评分。"""
    cmd_war_room(stock, preset="opus")
```

Update `cmd_war_room` (line 153) to handle new presets:

```python
def cmd_war_room(stock: str, preset: str = "opus"):
```

- [ ] **Step 7: Commit**

```bash
git add services/war_room.py services/analysis_service.py cli.py
git commit -m "feat: refactor war room to 2-round Opus deep analysis flow

Legacy multi-general flow preserved for Top10/batch compatibility.
New default preset 'opus' uses decision tree scoring + self-critique."
```

---

## Task 4: Experience Database & Retrieval

**Files:**
- Create: `knowledge/experience_db.py`
- Create: `tests/test_experience_db.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_experience_db.py`:

```python
"""Tests for experience database."""
import json
import pytest
from pathlib import Path


@pytest.fixture
def tmp_exp_db(tmp_path):
    db_path = tmp_path / "experience_db.json"
    db_path.write_text("[]", encoding="utf-8")
    return db_path


def test_add_experience(tmp_exp_db):
    from knowledge.experience_db import add_experience, load_db
    exp = {
        "stock_code": "300750",
        "stock_name": "宁德时代",
        "industry": "锂电池",
        "catalyst_type": ["财报超预期"],
        "pattern_tags": ["放量突破"],
        "tree_path": {"预期差": "是→A类→30天内→未定价→单季超预期→78分"},
        "prediction": {"score": 78, "direction": "做多", "target_pct": 15},
        "actual": {"return_5d": 3.2, "return_20d": -8.7, "max_drawdown": -14.2},
        "lesson": "资金面与催化背离时高分不可信",
        "tags": ["催化背离"],
    }
    add_experience(exp, db_path=tmp_exp_db)
    db = load_db(db_path=tmp_exp_db)
    assert len(db) == 1
    assert db[0]["stock_code"] == "300750"
    assert db[0]["id"].startswith("EXP-")


def test_retrieve_same_stock(tmp_exp_db):
    from knowledge.experience_db import add_experience, retrieve_lessons
    exp = {
        "stock_code": "300750",
        "stock_name": "宁德时代",
        "industry": "锂电池",
        "catalyst_type": ["财报超预期"],
        "pattern_tags": ["放量突破"],
        "prediction": {"score": 78, "direction": "做多"},
        "actual": {"return_20d": -8.7},
        "lesson": "教训ABC",
        "tags": ["催化背离"],
    }
    add_experience(exp, db_path=tmp_exp_db)
    result = retrieve_lessons("300750", "宁德时代", db_path=tmp_exp_db)
    assert "教训ABC" in result
    assert "宁德时代" in result


def test_retrieve_same_industry(tmp_exp_db):
    from knowledge.experience_db import add_experience, retrieve_lessons
    exp = {
        "stock_code": "002466",
        "stock_name": "天齐锂业",
        "industry": "锂电池",
        "catalyst_type": ["产能释放"],
        "pattern_tags": [],
        "prediction": {"score": 70, "direction": "做多"},
        "actual": {"return_20d": 5.0},
        "lesson": "同行业经验",
        "tags": [],
    }
    add_experience(exp, db_path=tmp_exp_db)
    result = retrieve_lessons(
        "300750", "宁德时代",
        current_industry="锂电池",
        db_path=tmp_exp_db,
    )
    assert "同行业经验" in result


def test_retrieve_empty_db(tmp_exp_db):
    from knowledge.experience_db import retrieve_lessons
    result = retrieve_lessons("300750", "宁德时代", db_path=tmp_exp_db)
    assert result == ""


def test_retrieve_top_k(tmp_exp_db):
    from knowledge.experience_db import add_experience, retrieve_lessons, load_db
    for i in range(10):
        add_experience({
            "stock_code": "300750",
            "stock_name": "宁德时代",
            "industry": "锂电池",
            "catalyst_type": [],
            "pattern_tags": [],
            "prediction": {"score": 60 + i},
            "actual": {"return_20d": float(i)},
            "lesson": f"教训{i}",
            "tags": [],
        }, db_path=tmp_exp_db)
    result = retrieve_lessons("300750", "宁德时代", top_k=5, db_path=tmp_exp_db)
    # Should contain at most 5 lessons
    assert result.count("教训") <= 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:\LinDangAgent && python -m pytest tests/test_experience_db.py -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement experience database**

Create `knowledge/experience_db.py`:

```python
"""Experience database for storing and retrieving analysis lessons.

Stores experiences as JSON entries with structured tags for retrieval.
Uses lightweight text matching (no vector DB needed at current scale).
"""
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "knowledge" / "experience_db.json"


def load_db(db_path: Path | None = None) -> list[dict]:
    """Load experience database from JSON file."""
    p = db_path or _DEFAULT_PATH
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _save_db(entries: list[dict], db_path: Path | None = None):
    p = db_path or _DEFAULT_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


def add_experience(exp: dict, db_path: Path | None = None):
    """Add an experience entry to the database."""
    entries = load_db(db_path)

    # Generate ID
    date_str = datetime.now().strftime("%Y%m%d")
    existing_today = sum(1 for e in entries if e.get("id", "").startswith(f"EXP-{date_str}"))
    exp_id = f"EXP-{date_str}-{existing_today + 1:03d}"

    entry = {
        "id": exp_id,
        "date": datetime.now().strftime("%Y-%m-%d"),
        **exp,
    }
    entries.append(entry)
    _save_db(entries, db_path)
    return exp_id


def retrieve_lessons(
    ts_code: str,
    stock_name: str,
    current_industry: str = "",
    current_catalysts: list[str] | None = None,
    current_patterns: list[str] | None = None,
    top_k: int = 5,
    db_path: Path | None = None,
) -> str:
    """Retrieve relevant lessons formatted as 【历史镜鉴】text.

    Scoring:
    - Same stock: +10
    - Same industry: +5
    - Catalyst type overlap: +3 per match
    - Pattern tag overlap: +3 per match
    - Time decay: 30d=1.0, 30-90d=0.7, 90d+=0.5
    """
    entries = load_db(db_path)
    if not entries:
        return ""

    catalysts = set(current_catalysts or [])
    patterns = set(current_patterns or [])
    now = datetime.now()
    scored = []

    for e in entries:
        score = 0.0

        # Same stock: +10
        if e.get("stock_code") == ts_code:
            score += 10

        # Same industry: +5
        if current_industry and e.get("industry") == current_industry:
            score += 5

        # Catalyst overlap: +3 each
        e_catalysts = set(e.get("catalyst_type", []))
        score += len(catalysts & e_catalysts) * 3

        # Pattern overlap: +3 each
        e_patterns = set(e.get("pattern_tags", []))
        score += len(patterns & e_patterns) * 3

        if score == 0:
            continue

        # Time decay
        try:
            entry_date = datetime.strptime(e["date"], "%Y-%m-%d")
            days_ago = (now - entry_date).days
        except (KeyError, ValueError):
            days_ago = 999

        if days_ago <= 30:
            decay = 1.0
        elif days_ago <= 90:
            decay = 0.7
        else:
            decay = 0.5

        scored.append((score * decay, e))

    if not scored:
        return ""

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:top_k]

    # Format as 【历史镜鉴】
    lines = [f"【历史镜鉴】（共{len(top)}条相关经验）\n"]
    for _, e in top:
        stock = e.get("stock_name", "?")
        date = e.get("date", "?")
        pred_score = e.get("prediction", {}).get("score", "?")
        actual_20d = e.get("actual", {}).get("return_20d")
        lesson = e.get("lesson", "")

        icon = "⚠️" if e.get("stock_code") == ts_code else "📌"
        label = "本股历史" if e.get("stock_code") == ts_code else "参考案例"

        actual_str = f"T+20收益{actual_20d:+.1f}%" if actual_20d is not None else "待回查"
        lines.append(f"{icon} {label}：{date} {stock}，评分{pred_score}，{actual_str}")
        if lesson:
            lines.append(f"   教训：{lesson}")
        lines.append("")

    return "\n".join(lines)
```

- [ ] **Step 4: Initialize empty experience_db.json**

```bash
echo "[]" > C:\LinDangAgent\data\knowledge\experience_db.json
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd C:\LinDangAgent && python -m pytest tests/test_experience_db.py -v`
Expected: All 5 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add knowledge/experience_db.py tests/test_experience_db.py data/knowledge/experience_db.json
git commit -m "feat: add experience database with relevance-based retrieval"
```

---

## Task 5: CLI Review Command (Batch Retrospective)

**Files:**
- Modify: `cli.py` (add `review` command)
- Modify: `knowledge/outcome_tracker.py` (extend for batch review with tree path diagnosis)

- [ ] **Step 1: Add review command to cli.py**

Add function before the COMMANDS dict (around line 1700):

```python
def cmd_review(args: list):
    """批量复盘：对比分析预测与实际走势，生成经验条目。

    Usage:
        python cli.py review                         # 复盘最近7天
        python cli.py review --from 2026-04-01 --to 2026-04-10
        python cli.py review 宁德时代                 # 复盘单只股票
    """
    from knowledge.batch_reviewer import run_batch_review

    stock_name = None
    date_from = None
    date_to = None

    i = 0
    while i < len(args):
        if args[i] == "--from" and i + 1 < len(args):
            date_from = args[i + 1]
            i += 2
        elif args[i] == "--to" and i + 1 < len(args):
            date_to = args[i + 1]
            i += 2
        else:
            stock_name = args[i]
            i += 1

    result = run_batch_review(
        stock_name=stock_name,
        date_from=date_from,
        date_to=date_to,
    )
    _json_out(result)
```

Register in COMMANDS dict:

```python
"review": lambda args: cmd_review(args),
```

- [ ] **Step 2: Create batch reviewer module**

Create `knowledge/batch_reviewer.py`:

```python
"""Batch review: compare predictions vs actual outcomes.

Triggered by user via CLI. Fetches actual price data,
compares with stored predictions, generates experience entries.
"""
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_TRACKER_PATH = Path(__file__).resolve().parent.parent / "data" / "knowledge" / "war_room_tracker.jsonl"


def run_batch_review(
    stock_name: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    """Run batch retrospective review.

    Args:
        stock_name: Optional filter by stock name
        date_from: Start date (YYYY-MM-DD), default 7 days ago
        date_to: End date (YYYY-MM-DD), default today

    Returns:
        Summary dict with review results
    """
    # Default date range: last 7 days
    if not date_from:
        date_from = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    if not date_to:
        date_to = datetime.now().strftime("%Y-%m-%d")

    # Load tracker entries
    entries = _load_tracker_entries(stock_name, date_from, date_to)
    if not entries:
        return {"status": "no_data", "message": "指定范围内无分析记录"}

    # Fetch actual outcomes for each entry
    results = []
    for entry in entries:
        outcome = _evaluate_single(entry)
        if outcome:
            results.append(outcome)
            # Write to experience DB
            _save_as_experience(outcome)

    # Build summary report
    summary = _build_review_summary(results)

    # Send email
    _send_review_email(summary, date_from, date_to)

    return summary


def _load_tracker_entries(
    stock_name: str | None,
    date_from: str,
    date_to: str,
) -> list[dict]:
    """Load matching tracker entries from JSONL."""
    if not _TRACKER_PATH.exists():
        return []

    entries = []
    with open(_TRACKER_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts = e.get("timestamp", "")[:10]
            if ts < date_from or ts > date_to:
                continue
            if stock_name and stock_name not in e.get("stock_name", ""):
                continue
            entries.append(e)

    return entries


def _evaluate_single(entry: dict) -> dict | None:
    """Evaluate a single analysis against actual outcome.

    Returns enriched entry with actual returns, or None if data unavailable.
    """
    ts_code = entry.get("ts_code", "")
    analysis_date = entry.get("timestamp", "")[:10]

    if not ts_code or not analysis_date:
        return None

    try:
        from data.indicators import get_price_data
        actual = _fetch_actual_returns(ts_code, analysis_date)
    except Exception as e:
        logger.warning("Failed to fetch actual returns for %s: %s", ts_code, e)
        return None

    if not actual:
        return None

    pred_score = entry.get("final_scores", {}).get("综合加权", 50)
    pred_direction = "做多" if pred_score >= 60 else ("观望" if pred_score >= 40 else "做空")

    direction_correct = (
        (pred_direction == "做多" and actual.get("return_20d", 0) > 0) or
        (pred_direction == "做空" and actual.get("return_20d", 0) < 0) or
        (pred_direction == "观望")
    )

    return {
        **entry,
        "actual": actual,
        "direction_correct": direction_correct,
        "prediction": {
            "score": pred_score,
            "direction": pred_direction,
        },
    }


def _fetch_actual_returns(ts_code: str, analysis_date: str) -> dict | None:
    """Fetch T+5 and T+20 actual returns after analysis date.

    Uses tushare/akshare for price data.
    """
    try:
        import tushare as ts
        from config import get_secret

        pro = ts.pro_api(get_secret("TUSHARE_TOKEN", ""))
        # Fetch daily prices for 30 trading days after analysis_date
        df = pro.daily(
            ts_code=ts_code,
            start_date=analysis_date.replace("-", ""),
            limit=25,
        )
        if df is None or df.empty:
            return None

        df = df.sort_values("trade_date").reset_index(drop=True)
        base_close = df.iloc[0]["close"]

        result = {}
        if len(df) >= 6:
            result["return_5d"] = round((df.iloc[5]["close"] / base_close - 1) * 100, 2)
        if len(df) >= 21:
            result["return_20d"] = round((df.iloc[20]["close"] / base_close - 1) * 100, 2)

        # Max drawdown within 20 days
        if len(df) >= 2:
            lows = df["low"].iloc[1:min(21, len(df))]
            result["max_drawdown"] = round((lows.min() / base_close - 1) * 100, 2)

        return result if result else None

    except Exception as e:
        logger.warning("Tushare fetch failed for %s: %s", ts_code, e)
        return None


def _save_as_experience(outcome: dict):
    """Convert a review outcome to an experience entry and save."""
    from knowledge.experience_db import add_experience

    actual = outcome.get("actual", {})
    return_20d = actual.get("return_20d")
    pred_score = outcome.get("prediction", {}).get("score", 50)

    # Generate lesson based on outcome
    if return_20d is not None and pred_score >= 70 and return_20d < -5:
        lesson = f"高分({pred_score})但实际下跌{return_20d:.1f}%，需检查评分过度乐观的原因"
    elif return_20d is not None and pred_score < 50 and return_20d > 10:
        lesson = f"低分({pred_score})但实际上涨{return_20d:.1f}%，可能遗漏了重要催化"
    elif return_20d is not None:
        direction = "正确" if outcome.get("direction_correct") else "错误"
        lesson = f"评分{pred_score}，T+20收益{return_20d:+.1f}%，方向{direction}"
    else:
        lesson = f"评分{pred_score}，实际走势数据不足"

    # Build tree_feedback from tree_paths
    tree_feedback = None
    tree_paths = outcome.get("tree_paths", {})
    if return_20d is not None and return_20d < -10 and tree_paths:
        # Find which dimension might have been misjudged
        r1_scores = outcome.get("round1_scores", {})
        for dim in ["预期差", "资金面", "技术面", "基本面"]:
            if r1_scores.get(dim, 50) >= 70:
                tree_feedback = {"node": f"{dim}.path", "issue": f"{dim}评分{r1_scores[dim]}但实际大幅下跌"}
                break

    exp = {
        "stock_code": outcome.get("ts_code", ""),
        "stock_name": outcome.get("stock_name", ""),
        "industry": "",  # Will be enriched later if available
        "catalyst_type": [],
        "pattern_tags": [],
        "tree_path": tree_paths,
        "prediction": outcome.get("prediction", {}),
        "actual": actual,
        "lesson": lesson,
        "tags": _auto_tag(outcome),
    }
    if tree_feedback:
        exp["tree_feedback"] = tree_feedback

    add_experience(exp)


def _auto_tag(outcome: dict) -> list[str]:
    """Auto-generate tags based on outcome patterns."""
    tags = []
    actual = outcome.get("actual", {})
    pred = outcome.get("prediction", {})
    r20 = actual.get("return_20d")
    score = pred.get("score", 50)

    if r20 is None:
        return tags

    if score >= 75 and r20 < -5:
        tags.append("高分陷阱")
    if score < 45 and r20 > 10:
        tags.append("低分逆袭")
    if abs(r20) < 3:
        tags.append("震荡无方向")
    if actual.get("max_drawdown", 0) < -15:
        tags.append("大幅回撤")
    if not outcome.get("direction_correct"):
        tags.append("方向错误")

    return tags


def _build_review_summary(results: list[dict]) -> dict:
    """Build review summary statistics."""
    if not results:
        return {"status": "no_results", "reviews": []}

    total = len(results)
    correct = sum(1 for r in results if r.get("direction_correct"))
    returns_20d = [r["actual"]["return_20d"] for r in results if r.get("actual", {}).get("return_20d") is not None]

    summary = {
        "status": "ok",
        "total_reviewed": total,
        "direction_accuracy": f"{correct}/{total} ({correct/total*100:.0f}%)" if total > 0 else "N/A",
        "avg_return_20d": f"{sum(returns_20d)/len(returns_20d):+.1f}%" if returns_20d else "N/A",
        "reviews": [
            {
                "stock": r.get("stock_name"),
                "date": r.get("timestamp", "")[:10],
                "score": r.get("prediction", {}).get("score"),
                "return_5d": r.get("actual", {}).get("return_5d"),
                "return_20d": r.get("actual", {}).get("return_20d"),
                "correct": r.get("direction_correct"),
            }
            for r in results
        ],
    }
    return summary


def _send_review_email(summary: dict, date_from: str, date_to: str):
    """Send review summary via email."""
    try:
        from utils.email_sender import send_text_email, smtp_configured
        if not smtp_configured():
            return

        total = summary.get("total_reviewed", 0)
        accuracy = summary.get("direction_accuracy", "N/A")
        avg_ret = summary.get("avg_return_20d", "N/A")

        body_lines = [
            f"复盘范围：{date_from} ~ {date_to}",
            f"复盘数量：{total}",
            f"方向准确率：{accuracy}",
            f"平均T+20收益：{avg_ret}",
            "",
            "详细列表：",
        ]
        for r in summary.get("reviews", []):
            mark = "✓" if r.get("correct") else "✗"
            body_lines.append(
                f"  {mark} {r['stock']} ({r['date']}) 评分{r['score']} "
                f"→ T+5:{r.get('return_5d', '?')}% T+20:{r.get('return_20d', '?')}%"
            )

        send_text_email(
            subject=f"[LinDangAgent] 批量复盘报告 {date_from}~{date_to}",
            body="\n".join(body_lines),
        )
    except Exception as e:
        logger.warning("Failed to send review email: %s", e)
```

- [ ] **Step 3: Commit**

```bash
git add knowledge/batch_reviewer.py cli.py
git commit -m "feat: add CLI review command for batch retrospective analysis"
```

---

## Task 6: Evolution Engine (Backtesting + Weight Adjustment)

**Files:**
- Create: `knowledge/evolution_engine.py`
- Create: `data/knowledge/weight_history.json`
- Create: `data/knowledge/tree_changelog.json`
- Modify: `knowledge/night_learner.py` (embed backtesting into 22:00 round)

- [ ] **Step 1: Create evolution engine**

Create `knowledge/evolution_engine.py`:

```python
"""Evolution engine: backtesting, health reports, weight/tree adjustment proposals.

Three channels:
- Channel A: Batch review (user-triggered, see batch_reviewer.py)
- Channel B: Historical backtesting (nightly, this module)
- Channel C: Weight/tree adjustment proposals (this module)
"""
import json
import logging
import statistics
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
_TRACKER_PATH = BASE_DIR / "data" / "knowledge" / "war_room_tracker.jsonl"
_WEIGHT_HISTORY_PATH = BASE_DIR / "data" / "knowledge" / "weight_history.json"
_TREE_CHANGELOG_PATH = BASE_DIR / "data" / "knowledge" / "tree_changelog.json"
_REPORTS_DIR = BASE_DIR / "data" / "knowledge" / "evolution_reports"
_SCORE_DIMS = ["基本面", "预期差", "资金面", "技术面"]


def run_nightly_backtest(sample_size: int = 60) -> dict:
    """Run nightly backtesting for Channel B.

    Loads recent analyses with known outcomes,
    computes per-dimension predictive power and node discriminability.

    Returns health report dict.
    """
    entries = _load_evaluated_entries(sample_size)
    if len(entries) < 10:
        return {"status": "insufficient_data", "sample_size": len(entries)}

    # Compute per-dimension correlation with T+20 returns
    dim_correlations = {}
    for dim in _SCORE_DIMS:
        scores = []
        returns = []
        for e in entries:
            s = e.get("final_scores", {}).get(dim)
            r = e.get("actual", {}).get("return_20d")
            if s is not None and r is not None:
                scores.append(s)
                returns.append(r)
        if len(scores) >= 10:
            dim_correlations[dim] = _pearson_r(scores, returns)

    # Overall win rate by score bracket
    brackets = {
        ">=80": {"wins": 0, "total": 0, "returns": []},
        "60-79": {"wins": 0, "total": 0, "returns": []},
        "<60": {"wins": 0, "total": 0, "returns": []},
    }
    for e in entries:
        score = e.get("final_scores", {}).get("综合加权", 50)
        r20 = e.get("actual", {}).get("return_20d")
        if r20 is None:
            continue

        if score >= 80:
            bracket = ">=80"
        elif score >= 60:
            bracket = "60-79"
        else:
            bracket = "<60"

        brackets[bracket]["total"] += 1
        brackets[bracket]["returns"].append(r20)
        if r20 > 0:
            brackets[bracket]["wins"] += 1

    for k, v in brackets.items():
        if v["total"] > 0:
            v["win_rate"] = round(v["wins"] / v["total"] * 100, 1)
            v["avg_return"] = round(statistics.mean(v["returns"]), 2)
        else:
            v["win_rate"] = None
            v["avg_return"] = None

    report = {
        "status": "ok",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "sample_size": len(entries),
        "dim_correlations": dim_correlations,
        "brackets": brackets,
    }

    # Check for weight adjustment proposal
    proposal = _check_weight_proposal(dim_correlations, entries)
    if proposal:
        report["weight_proposal"] = proposal

    # Save report
    _save_health_report(report)

    return report


def _load_evaluated_entries(limit: int) -> list[dict]:
    """Load tracker entries that have been evaluated (have actual outcomes)."""
    from knowledge.experience_db import load_db

    experiences = load_db()
    # Return most recent entries with actual data
    evaluated = [
        e for e in experiences
        if e.get("actual", {}).get("return_20d") is not None
    ]
    evaluated.sort(key=lambda x: x.get("date", ""), reverse=True)
    return evaluated[:limit]


def _pearson_r(x: list[float], y: list[float]) -> float:
    """Compute Pearson correlation coefficient."""
    n = len(x)
    if n < 3:
        return 0.0
    mx = statistics.mean(x)
    my = statistics.mean(y)
    sx = statistics.stdev(x)
    sy = statistics.stdev(y)
    if sx == 0 or sy == 0:
        return 0.0
    return round(
        sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / ((n - 1) * sx * sy),
        3,
    )


def _check_weight_proposal(correlations: dict, entries: list) -> dict | None:
    """Check if weight adjustment is warranted.

    Triggers if strongest dimension's correlation exceeds current weight allocation
    by a meaningful margin, sustained over enough data.
    """
    from services.decision_tree import load_tree

    if len(correlations) < 4:
        return None

    tree = load_tree()
    current_weights = tree["weights"]

    # Rank by correlation strength
    ranked = sorted(correlations.items(), key=lambda x: abs(x[1]), reverse=True)

    # Check if top dimension is significantly underweighted
    top_dim, top_r = ranked[0]
    bottom_dim, bottom_r = ranked[-1]

    top_weight = current_weights.get(top_dim, 0.25)
    bottom_weight = current_weights.get(bottom_dim, 0.25)

    # Proposal threshold: top corr > 0.3 and bottom corr < 0.15
    # and the weight difference could be larger
    if abs(top_r) > 0.30 and abs(bottom_r) < 0.15 and top_weight < 0.50:
        new_weights = dict(current_weights)
        delta = 0.05
        new_weights[top_dim] = min(0.50, top_weight + delta)
        new_weights[bottom_dim] = max(0.05, bottom_weight - delta)

        # Simulate impact on historical entries
        old_win_rate, new_win_rate = _simulate_weight_change(entries, current_weights, new_weights)

        return {
            "type": "weight_adjustment",
            "changes": {
                top_dim: f"{current_weights[top_dim]*100:.0f}% → {new_weights[top_dim]*100:.0f}%",
                bottom_dim: f"{current_weights[bottom_dim]*100:.0f}% → {new_weights[bottom_dim]*100:.0f}%",
            },
            "reason": f"{top_dim} 相关系数 r={top_r:.3f}（最强），{bottom_dim} r={bottom_r:.3f}（最弱）",
            "impact": {
                "old_win_rate": f"{old_win_rate:.1f}%",
                "new_win_rate": f"{new_win_rate:.1f}%",
            },
            "new_weights": new_weights,
            "status": "pending_approval",
        }

    return None


def _simulate_weight_change(
    entries: list, old_weights: dict, new_weights: dict
) -> tuple[float, float]:
    """Simulate impact of weight change on historical win rate."""
    old_wins = 0
    new_wins = 0
    total = 0

    for e in entries:
        scores = e.get("prediction", {})
        if not isinstance(scores, dict):
            continue
        r20 = e.get("actual", {}).get("return_20d")
        if r20 is None:
            continue

        # Need per-dimension scores from tree_path
        dims = e.get("tree_path", {})
        if not dims:
            continue

        # Extract scores from tree path strings (last number before "分")
        import re
        dim_scores = {}
        for dim, path in dims.items():
            m = re.search(r"(\d+)分$", path)
            if m:
                dim_scores[dim] = int(m.group(1))

        if len(dim_scores) < 4:
            continue

        old_composite = sum(dim_scores.get(d, 50) * old_weights.get(d, 0.25) for d in _SCORE_DIMS)
        new_composite = sum(dim_scores.get(d, 50) * new_weights.get(d, 0.25) for d in _SCORE_DIMS)

        old_bullish = old_composite >= 65
        new_bullish = new_composite >= 65
        actually_up = r20 > 0

        total += 1
        if old_bullish == actually_up:
            old_wins += 1
        if new_bullish == actually_up:
            new_wins += 1

    if total == 0:
        return 50.0, 50.0
    return old_wins / total * 100, new_wins / total * 100


def apply_weight_change(new_weights: dict):
    """Apply approved weight change to decision tree config.

    Called after user confirms via email/CLI.
    Records change in weight_history.json.
    """
    from services.decision_tree import load_tree, reload_tree, _TREE_PATH

    tree = load_tree()
    old_weights = dict(tree["weights"])

    # Update tree config
    tree["weights"] = new_weights
    tree["updated_at"] = datetime.now().strftime("%Y-%m-%d")
    with open(_TREE_PATH, "w", encoding="utf-8") as f:
        json.dump(tree, f, ensure_ascii=False, indent=2)

    # Record in history
    history = []
    if _WEIGHT_HISTORY_PATH.exists():
        with open(_WEIGHT_HISTORY_PATH, encoding="utf-8") as f:
            history = json.load(f)

    history.append({
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "old": old_weights,
        "new": new_weights,
    })

    with open(_WEIGHT_HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    # Reload cached tree
    reload_tree()

    logger.info("Weight change applied: %s → %s", old_weights, new_weights)


def _save_health_report(report: dict):
    """Save health report to evolution_reports/ directory."""
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    date = report.get("date", datetime.now().strftime("%Y-%m-%d"))
    path = _REPORTS_DIR / f"{date}_health.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


def send_weight_proposal_email(proposal: dict):
    """Send weight adjustment proposal to user for approval."""
    try:
        from utils.email_sender import send_text_email, smtp_configured
        if not smtp_configured():
            logger.warning("SMTP not configured, cannot send proposal email")
            return

        changes = proposal.get("changes", {})
        reason = proposal.get("reason", "")
        impact = proposal.get("impact", {})

        body = f"""[LinDangAgent] 决策树权重调节建议

建议内容：
{chr(10).join(f'  {dim}: {change}' for dim, change in changes.items())}

依据：
  {reason}

回测影响：
  旧权重胜率: {impact.get('old_win_rate', 'N/A')}
  新权重胜率: {impact.get('new_win_rate', 'N/A')}

请回复"同意"生效，回复"否决"维持现状。
也可通过 CLI 执行：python cli.py apply-weights
"""
        send_text_email(
            subject=f"[LinDangAgent] 权重调节建议 - {datetime.now().strftime('%Y-%m-%d')}",
            body=body,
        )
    except Exception as e:
        logger.warning("Failed to send proposal email: %s", e)
```

- [ ] **Step 2: Initialize weight_history.json and tree_changelog.json**

```bash
echo "[]" > C:\LinDangAgent\data\knowledge\weight_history.json
echo "[]" > C:\LinDangAgent\data\knowledge\tree_changelog.json
mkdir -p C:\LinDangAgent\data\knowledge\evolution_reports
```

- [ ] **Step 3: Embed backtesting into night learner 22:00 round**

In `knowledge/night_learner.py`, at the end of `round1_scan()` (around line 84), add:

```python
    # Channel B: Evolution engine backtesting
    try:
        from knowledge.evolution_engine import run_nightly_backtest, send_weight_proposal_email
        backtest_result = run_nightly_backtest()
        if backtest_result.get("weight_proposal"):
            send_weight_proposal_email(backtest_result["weight_proposal"])
            logger.info("Weight proposal generated and emailed")
        results["backtest"] = backtest_result
    except Exception as e:
        logger.warning("Nightly backtest failed: %s", e)
```

- [ ] **Step 4: Add apply-weights CLI command**

In `cli.py`, add:

```python
def cmd_apply_weights(args: list):
    """应用最新的权重调节建议。"""
    from knowledge.evolution_engine import _REPORTS_DIR
    import json, glob

    # Find latest health report with pending proposal
    reports = sorted(_REPORTS_DIR.glob("*_health.json"), reverse=True)
    for rp in reports:
        with open(rp, encoding="utf-8") as f:
            report = json.load(f)
        proposal = report.get("weight_proposal")
        if proposal and proposal.get("status") == "pending_approval":
            print(f"找到待审批建议: {rp.name}")
            print(json.dumps(proposal, ensure_ascii=False, indent=2))
            from knowledge.evolution_engine import apply_weight_change
            apply_weight_change(proposal["new_weights"])
            # Mark as applied
            proposal["status"] = "applied"
            with open(rp, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            _json_out({"status": "applied", "new_weights": proposal["new_weights"]})
            return
    _json_out({"status": "no_pending_proposal"})
```

Register in COMMANDS:
```python
"apply-weights": lambda args: cmd_apply_weights(args),
```

- [ ] **Step 5: Commit**

```bash
git add knowledge/evolution_engine.py knowledge/night_learner.py cli.py data/knowledge/weight_history.json data/knowledge/tree_changelog.json
git commit -m "feat: add evolution engine with nightly backtesting and weight adjustment proposals"
```

---

## Task 7: Integration Wiring & Cleanup

**Files:**
- Modify: `services/war_room.py` (extract Phase 0 into helper)
- Create: `tests/test_analyst_flow.py`

- [ ] **Step 1: Extract Phase 0 into _phase0_scout helper**

In `services/war_room.py`, extract lines 381-449 (the existing Phase 0 data collection block inside `run_war_room`) into the `_phase0_scout()` function defined in Task 3 Step 4. This is a mechanical move — copy the existing code block into the function body, replacing the `...` placeholder.

The legacy flow `_run_war_room_legacy` should also call `_phase0_scout()` to avoid duplication.

- [ ] **Step 2: Write integration test**

Create `tests/test_analyst_flow.py`:

```python
"""Integration tests for the new 2-round analyst flow.

These tests verify the wiring between components without making real API calls.
Real API testing requires manual verification (see CLAUDE.md testing rules).
"""
import json
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path


def test_decision_tree_loads_and_formats():
    """Verify decision tree loads from JSON and formats for prompt."""
    from services.decision_tree import load_tree, format_tree_for_prompt
    tree = load_tree()
    text = format_tree_for_prompt(tree["trees"])
    # All four dimensions present
    assert "预期差" in text
    assert "资金面" in text
    assert "技术面" in text
    assert "基本面" in text
    # Key questions present
    assert "催化" in text
    assert "主力资金" in text


def test_round1_prompt_includes_tree():
    """Verify Round 1 system prompt includes decision tree."""
    from ai.prompts_analyst import build_round1_system
    from services.decision_tree import load_tree, format_tree_for_prompt
    tree = load_tree()
    tree_text = format_tree_for_prompt(tree["trees"])
    prompt = build_round1_system(tree_text)
    assert "决策树" in prompt
    assert "价值投机" in prompt
    assert "<<<SCORES>>>" in prompt


def test_round1_prompt_includes_lessons():
    """Verify lessons are injected when provided."""
    from ai.prompts_analyst import build_round1_system
    prompt = build_round1_system("fake tree", "⚠️ 本股历史：曾经翻车")
    assert "历史镜鉴" in prompt
    assert "曾经翻车" in prompt


def test_round2_prompt_structure():
    """Verify Round 2 prompt has required sections."""
    from ai.prompts_analyst import ROUND2_SYSTEM, build_round2_user
    assert "致命理由" in ROUND2_SYSTEM
    assert "Pre-mortem" in ROUND2_SYSTEM
    assert "<<<SCORE_CORRECTIONS>>>" in ROUND2_SYSTEM

    user = build_round2_user("fake round 1 output")
    assert "fake round 1 output" in user


def test_score_corrections_parsing():
    """Verify Round 2 score corrections are parsed correctly."""
    from services.war_room import _apply_round2_corrections

    round1_scores = {"基本面": 60, "预期差": 80, "资金面": 70, "技术面": 65, "综合加权": 73.0}
    round2_text = """
Some analysis...
<<<SCORE_CORRECTIONS>>>
基本面: +0分 | 理由：无硬伤确认
预期差: -5分 | 理由：催化可能已部分定价
资金面: -3分 | 理由：北向可能是被动调仓
技术面: +0分 | 理由：技术面判断合理
<<<END_SCORE_CORRECTIONS>>>
"""
    from services.decision_tree import load_tree
    tree = load_tree()
    result = _apply_round2_corrections(round1_scores, round2_text, tree)
    assert result["预期差"] == 75  # 80 - 5
    assert result["资金面"] == 67  # 70 - 3
    assert result["基本面"] == 60  # unchanged
    assert result["技术面"] == 65  # unchanged


def test_fatal_count_extraction():
    """Verify HIGH_PROB_FATAL_COUNT extraction."""
    from services.war_room import _extract_fatal_count

    text1 = "blah\n<<<HIGH_PROB_FATAL_COUNT>>>\n2\n<<<END_HIGH_PROB_FATAL_COUNT>>>"
    assert _extract_fatal_count(text1) == 2

    text2 = "Pre-mortem\n路径1：... 概率：高\n路径2：... 概率：低"
    assert _extract_fatal_count(text2) == 1


def test_corrections_clamp_to_10():
    """Verify corrections are clamped to ±10."""
    from services.war_room import _apply_round2_corrections
    from services.decision_tree import load_tree

    round1_scores = {"基本面": 60, "预期差": 80, "资金面": 70, "技术面": 65, "综合加权": 73.0}
    round2_text = """
<<<SCORE_CORRECTIONS>>>
基本面: +0分 | 理由：ok
预期差: -20分 | 理由：trying to over-correct
资金面: +0分 | 理由：ok
技术面: +0分 | 理由：ok
<<<END_SCORE_CORRECTIONS>>>
"""
    tree = load_tree()
    result = _apply_round2_corrections(round1_scores, round2_text, tree)
    assert result["预期差"] == 70  # Clamped: 80 - 10, not 80 - 20


def test_new_presets_exist():
    """Verify new preset structure."""
    from services.war_room import WAR_ROOM_PRESETS
    assert "opus" in WAR_ROOM_PRESETS
    assert "analyst" in WAR_ROOM_PRESETS["opus"]
    # Legacy presets still exist
    assert "balanced" in WAR_ROOM_PRESETS
    assert WAR_ROOM_PRESETS["balanced"].get("_legacy") is True


def test_experience_roundtrip(tmp_path):
    """Verify experience add → retrieve roundtrip."""
    from knowledge.experience_db import add_experience, retrieve_lessons

    db_path = tmp_path / "exp.json"
    db_path.write_text("[]", encoding="utf-8")

    add_experience({
        "stock_code": "600519",
        "stock_name": "贵州茅台",
        "industry": "白酒",
        "catalyst_type": ["财报超预期"],
        "pattern_tags": ["放量突破"],
        "prediction": {"score": 75, "direction": "做多"},
        "actual": {"return_20d": -3.5},
        "lesson": "白酒板块整体走弱时不要逆势做多",
        "tags": ["板块走弱"],
    }, db_path=db_path)

    result = retrieve_lessons("600519", "贵州茅台", db_path=db_path)
    assert "白酒板块" in result
```

- [ ] **Step 3: Run all tests**

Run: `cd C:\LinDangAgent && python -m pytest tests/test_decision_tree.py tests/test_experience_db.py tests/test_analyst_flow.py -v`
Expected: All tests PASS.

- [ ] **Step 4: Commit**

```bash
git add services/war_room.py tests/test_analyst_flow.py
git commit -m "feat: complete integration wiring for 2-round analyst flow"
```

---

## Task 8: Grep Cleanup — Remove Dead References

After the refactor, run cleanup to catch any broken references.

- [ ] **Step 1: Grep for old references**

Search for references to removed/renamed constructs:

```bash
cd C:\LinDangAgent
grep -rn "GENERAL_PERSONALITIES" --include="*.py" | grep -v "prompts_war_room.py"
grep -rn "LIN_BIAO_SYSTEM" --include="*.py" | grep -v "prompts_war_room.py"
grep -rn "build_lin_biao_prompt" --include="*.py" | grep -v "prompts_war_room.py"
grep -rn "build_han_veto_prompt" --include="*.py" | grep -v "prompts_war_room.py"
grep -rn "_run_bull_bear_debate" --include="*.py"
grep -rn "num_generals\|scout_models\|scouts" --include="*.py" | grep "war_room"
```

- [ ] **Step 2: Fix any broken references found**

For each hit, update to use the new flow or remove the dead reference. Common expected hits:
- `war_room.py` legacy flow references: should be contained within `_run_war_room_legacy()`
- External files referencing old functions: update or remove

- [ ] **Step 3: Run full test suite**

```bash
cd C:\LinDangAgent && python -m pytest tests/ -v --tb=short
```

- [ ] **Step 4: Commit cleanup**

```bash
git add -A
git commit -m "chore: clean up dead references after war room refactor"
```

---

## Post-Implementation Notes

### Manual Verification Required

Per CLAUDE.md testing rules, the following must be manually verified (cannot be mocked):

1. **Run a real single stock analysis**: `python cli.py analyze 贵州茅台`
   - Verify Opus Round 1 produces decision tree paths
   - Verify Round 2 produces score corrections
   - Verify final report assembles correctly
   - Verify email sends

2. **Run a review**: `python cli.py review` (after enough time has passed for T+5 data)

3. **Verify legacy presets still work**: `python cli.py war-room 贵州茅台 balanced`

### Multi-Review After Completion

Per CLAUDE.md rules, this is a large refactor (>3 files, module deletion, interface changes). Run `/multi-review` or `python cli.py code-review` on all changed files after implementation.
