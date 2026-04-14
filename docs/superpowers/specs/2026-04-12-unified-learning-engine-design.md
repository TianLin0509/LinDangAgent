# 统一学习引擎设计文档

> 日期: 2026-04-12
> 状态: 设计通过，待实施
> 目标: 将 sim-train / dragon-train / evolution-engine 三套独立学习机制统一为五轮循环引擎，通过历史回测+Opus多轮反思实现分析质量持续进化

---

## 1. 问题背景

### 现有学习机制的短板

| 模块 | 做了什么 | 核心问题 |
|------|---------|---------|
| `simulation_training.py` | 随机选历史股票，Sonnet 单轮分析，生成教训存入 case_memory | 股票池小(30只)、Sonnet训练但Opus实战、没走完整war_room流程、没有批次反思环节 |
| `dragon_pullback.py` 的 `run_dragon_training` | 四轮循环(回测→反思→优化→验证) | 仅覆盖龙头反抽策略，不影响主分析流程 |
| `evolution_engine.py` | Pearson相关性校准维度权重 | 只调权重、需要手动approve、proposals积灰 |

### 核心矛盾

数据收集了(outcomes.db、case_memory.db)，但"学到的东西"到"改变下一次分析行为"之间断了多个环节。系统有学习的骨架，闭环没合上。

---

## 2. 设计目标

1. **训练=实战**: 回测使用完整 war_room 两轮对抗流程，与真实分析一致
2. **Opus 自主进化**: 不预设调什么，让 Opus 根据回测数据自主判断需要调整的环节
3. **多轮审视安全网**: 每条调整建议经过"反思→质疑→答辩→仲裁"多轮 Opus 审视
4. **验证才采纳**: 新配置必须在holdout验证集上证明比旧配置好才采纳
5. **Prompt变更人工审批**: Prompt修改必须用户确认，其余可自动采纳

---

## 3. 统一引擎架构

### 3.1 CLI 入口

```
python cli.py learn <mode> [options]

模式:
  learn general 50          # 通用分析回测(50只股票)
  learn dragon 6            # 龙头反抽策略学习(6个月)
  learn weights             # 维度权重校准
  learn full                # 全量: general + weights 联合学习
  learn approve-prompt      # 审批待定的 prompt 修改
```

### 3.2 五轮循环

```
Round 1: 批量回测
  → 完整 war_room 流程，时间锁定，70/30 holdout 分割
  
Round 2: Opus 统一反思
  → 分析系统性偏差，输出结构化调整建议(proposals)
  
Round 3: Opus 交叉审视(多轮)
  → 质疑→答辩→仲裁，只有通过审视的建议进入 Round 4
  
Round 4: 应用候选配置
  → 生成 staging 区配置，不直接覆盖生产
  
Round 5: 验证集对比
  → 新配置 vs 旧配置 A/B 对比，胜率+3%才采纳
```

### 3.3 文件结构

```
knowledge/
  learning_engine.py        # 统一引擎主入口 + 五轮循环编排
  learning_backtester.py    # Round 1: 各模式的回测执行器
  learning_reflector.py     # Round 2-3: Opus 反思 + 交叉审视
  learning_optimizer.py     # Round 4-5: 配置生成 + 验证 + 采纳/回退
  learning_config.py        # 学习参数、安全边界、staging 管理
```

---

## 4. Round 1: 批量回测

### 4.1 选题策略 (Z 模式: 70%熟悉 + 30%探索)

**70% 已知领域**:
- reports.db 中最近 3 个月分析过的股票
- case_memory 中出现频率高的板块
- 按板块弱项加权(命中率低的板块优先)

**30% 探索领域**:
- 全市场按市值分层随机抽(大盘/中盘/小盘 = 3:4:3)
- 排除已在 70% 中出现的股票

**筛选门槛(硬过滤)**:
- 流动性: 近 20 日日均成交额 >= 5000 万
- 波动性: 近 20 日振幅均值 >= 2% 或区间涨跌幅绝对值 >= 10%
- 非 ST/退市风险: 排除 ST、*ST、退市整理期
- 非停牌: exam_date 前后 10 个交易日内无长期停牌

**人气加权(软排序)**:
- 近期有龙虎榜/大宗交易/股东增减持 → 权重 +
- 所属板块近 20 日有异动(涨停数 >= 3 只) → 权重 +
- 在 reports.db 中被分析过 → 权重 +
- 弱项板块 → 权重 +

### 4.2 考试日期

分散到多个日期，避免单边行情偏差:
- 回测 50 只 → 随机分配到 5 个考试日期(每日期 10 只)
- 日期范围: T-90 到 T-15(确保有 T+10 后续数据)

### 4.3 回测执行

调用 `war_room.run_war_room()` 并传入时间锁定参数:

```python
result = run_war_room(
    stock_name=exam["stock_name"],
    preset="opus",              # 完整两轮对抗
    time_lock=exam["exam_date"], # 数据截止日期
    skip_email=True,
    skip_report_save=True,       # 不污染 reports.db
)
```

**war_room 改动**: 增加 `time_lock` 参数:
- 数据获取层(report_data.py)只拉截止到 time_lock 的数据
- 新闻/舆情不注入(历史新闻难以还原)
- knowledge injector 只注入 time_lock 之前的案例

### 4.4 判卷: 三级归因

```
实际收益 = 个股 T+10 涨跌幅
大盘β   = 上证指数同期涨跌幅
板块β   = 所属行业同期涨跌幅
个股α   = 实际收益 - max(大盘β, 板块β)

判定:
  看多 + α>0 → hit
  看空 + α<0 → hit
  中性 + |α|<3% → hit
```

### 4.5 Holdout 分割

全部选题随机 70% 训练集 + 30% 验证集。训练集参与 Round 2 反思，验证集用于 Round 5 验证。

---

## 5. Round 2: Opus 统一反思

### 5.1 输入材料

```
给 Opus 的反思材料:
  ├── 整体统计: 胜率、分方向/板块/评分区间胜率
  ├── 典型失败案例 Top10(按超额收益最差排序)
  │     每个案例含: 四维评分、方向、实际α、完整分析摘要
  ├── 典型成功案例 Top5(正面参照)
  ├── 边界案例 Top5(综合评分 45-55 的模糊区)
  ├── 当前配置快照:
  │     ├── decision_tree.json 完整内容
  │     ├── 五条修正规则及阈值
  │     ├── 四维权重
  │     └── Round 1/Round 2 system prompt
  └── 历史学习记录(最近 3 次学习的调整及效果)
```

### 5.2 Opus 任务

不预设框架，让 Opus 自主判断。可调整范围:
1. 四维权重分配
2. 五条修正规则的阈值或逻辑
3. 决策树的分支结构(增/删/改问题节点)
4. Round 1 或 Round 2 的 system prompt 措辞

要求:
- 每条建议必须有数据支撑(引用具体案例或统计)
- 说明预期效果
- 标注风险
- 如果当前配置足够好，可以不改

### 5.3 输出格式

```
<<<PROPOSALS>>>
[{id, type, target, current_value, proposed_value,
  evidence, expected_effect, risk, confidence}]
<<<END_PROPOSALS>>>
```

type 枚举: weight / rule / tree / prompt

---

## 6. Round 3: Opus 交叉审视

### 6.1 三步审视流程

```
Step 1: 质疑者 Opus
  角色: "风控官，专找过拟合和样本偏差"
  输入: Round 2 的全部 proposals + 原始回测数据
  输出: 对每条 proposal 打分(通过/有疑虑/否决)

Step 2: 答辩(仅当有"有疑虑"的 proposal 时触发)
  将质疑反馈给 Round 2 的视角
  要求: 补充证据或承认问题并修改建议

Step 3: 仲裁者 Opus(仅当 Step 1-2 仍有分歧时触发)
  角色: "最终裁决人"
  输入: 原始 proposal + 质疑 + 答辩
  输出: 最终采纳列表(adopt/reject + 理由)
```

**快速路径**: Step 1 全部通过 → 跳过 Step 2-3，直接进 Round 4。

### 6.2 Opus 调用成本

```
Round 2: 1 次 Opus
Round 3 Step 1: 1 次 Opus
Round 3 Step 2: 0-1 次
Round 3 Step 3: 0-1 次
反思审视合计: 2-4 次 Opus

Round 1 每只股票 2 次 Opus(war_room 两轮)
50 只回测 = 100 次 + 2~4 次反思 ≈ 104 次 Opus
```

---

## 7. Round 4: 应用候选配置

### 7.1 Staging 机制

```
data/knowledge/
  decision_tree.json          ← 生产配置(不动)
  staging/
    decision_tree.json        ← 候选配置
    prompt_patches.json       ← prompt 修改记录
    correction_rules.json     ← 修正规则调整
    changelog.md              ← 本次变更摘要
```

### 7.2 配置生成

```
type=weight  → 修改 staging/decision_tree.json 权重字段
type=rule    → 修改 staging/correction_rules.json 阈值/逻辑
type=tree    → 修改 staging/decision_tree.json 分支结构
type=prompt  → 写入 staging/prompt_patches.json
               格式: {target, action, anchor, content}
```

### 7.3 安全边界(硬限制)

```
权重: 单维度 <= 50%, >= 5%, 总和 = 100%
修正规则:
  熔断线 ∈ [15, 35]
  木桶线 ∈ [20, 40]
  预mortem 封顶 ∈ [60, 80]
决策树: 单维度问题数 ∈ [3, 8], 不能删除整个维度
Prompt: 单次修改不超过原 prompt 长度的 20%
```

### 7.4 采纳分级

```
自动采纳(通过 Round 5 验证即生效):
  ├── 权重调整
  ├── 修正规则阈值调整
  └── 决策树分支微调

人工审批(必须用户确认):
  └── Prompt 修改
      → 暂存 staging/prompt_patches.json
      → 发邮件通知用户(diff + 理由 + 验证数据)
      → python cli.py learn approve-prompt 确认
      → 超过 7 天未确认自动过期
```

---

## 8. Round 5: 验证集对比

### 8.1 A/B 对比

```
用旧配置跑 holdout 集 → 胜率 A、评分偏差 A
用新配置跑 holdout 集 → 胜率 B、评分偏差 B
```

### 8.2 采纳条件(全部满足)

1. 胜率 B >= 胜率 A + 3%(显著提升)
2. 没有出现"某类股票胜率断崖下跌 >15%"(防止顾此失彼)
3. 评分校准度改善: 综合评分>=70的股票实际平均α > 评分<50的股票实际平均α，且两者差距比旧配置拉大

### 8.3 采纳/回退

```
全部达标 → staging 覆盖到生产，记录 learning_log
未达标   → 回退，staging 清除，记录"本轮未采纳"原因
```

### 8.4 部分采纳(默认关闭)

如果整体未达标但某条 proposal 单独验证有效，可只采纳该条。需额外一轮单项验证。用户可通过配置启用。

---

## 9. 学习日志

```
data/knowledge/learning_log/
  2026-04-12_general_50.json
  {
    round1: {total, hit_rate, by_sector, by_direction, holdout_split},
    round2: {proposals: [...]},
    round3: {审视结果, 最终采纳列表},
    round4: {staging_diff},
    round5: {old_hit_rate, new_hit_rate, adopted, reason},
    summary: "本轮学习: ..."
  }
```

---

## 10. 迁移策略

### 分四阶段推进

**Phase 1: 搭建统一引擎骨架 + general 模式**
- 新建 4 个文件(engine/backtester/reflector/optimizer)
- CLI 入口: `python cli.py learn general 50`
- war_room 增加 time_lock 参数
- 五轮循环完整跑通
- sim-train 暂时保留，标记 deprecated

**Phase 2: 迁入 dragon 模式**
- dragon_pullback.py 的 run_dragon_training 改为调用统一引擎
- dragon 特有逻辑(Phase 1-3 扫描)保留原位，作为 backtester 的插件
- dragon-train CLI 重定向到 learn dragon

**Phase 3: 迁入 weights 模式**
- evolution_engine 的权重校准逻辑迁入 optimizer
- 不再独立 run_nightly_backtest，改为 learn weights
- night_learner 中的 evolution 调用指向新引擎

**Phase 4: 清理 deprecated 代码**
- 移除 simulation_training.py
- 移除 dragon_pullback 中的旧四轮循环
- 移除 evolution_engine.py
- 更新 night_learner 对接新引擎

### 每 Phase 审查要求

1. grep 遗留引用(确保旧入口全部重定向)
2. 端到端测试(至少 5 只股票的完整五轮循环)
3. 多方审查(Codex + Gemini 交叉 review)
4. 确认 night_learner 不受影响

---

## 11. 被替代的模块

| 现有模块 | 归宿 |
|---------|------|
| `knowledge/simulation_training.py` | 回测逻辑迁入 `learning_backtester.py`，原文件标记 deprecated |
| `services/dragon_pullback.py` 的 `run_dragon_training` | 四轮循环迁入统一引擎，扫描/回测函数保留原位 |
| `knowledge/evolution_engine.py` | 权重校准逻辑迁入 `learning_optimizer.py`，原文件标记 deprecated |
