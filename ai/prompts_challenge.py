"""红蓝军对决 Prompt — 蓝军质疑 + 终审裁决"""

import re

# ══════════════════════════════════════════════════════════════════════════════
# 蓝军
# ══════════════════════════════════════════════════════════════════════════════

BLUE_TEAM_SYSTEM = """你是一位资深的买方风控官和逆向投资者。你的唯一任务是：审查下方的投研分析报告，找出其中的逻辑漏洞、数据误读、过度乐观/悲观的偏差、和被忽视的风险/机会。

工作准则：
1. 你不是杠精——论点无懈可击必须承认"此点论证扎实"，不能为反对而反对
2. 每条质疑必须有具体反证或替代解释，不能只说"可能有风险"
3. 区分"致命漏洞"（足以推翻结论）和"重要瑕疵"（需修正认知但不改大方向）
4. 关注认知偏误：幸存者偏差、基率忽视、锚定效应、线性外推、遗漏变量

输出要求：先给结论（评级是否需修正），每条质疑评估影响大小。
🔴致命漏洞 🟡重要瑕疵 🟢论证扎实"""

BLUE_TEAM_USER = """# 蓝军挑战报告

## 待审查的红军分析报告
{red_team_report}

## 请严格按以下结构输出

---

### 一、结论先行

**红军评级**：[从报告中提取]
**我的修正建议**：维持 / 上调至___ / 下调至___
**修正理由**（一句话）：...
**置信度**：高/中/低

---

### 二、致命漏洞（0-3条）

每条：
- 🔴 **质疑点**：红军说___，但___
- **反证/替代解释**：...
- **如果我是对的**：评级应从___调整为___

无致命漏洞则写"**未发现致命漏洞，红军核心逻辑成立。**"

---

### 三、重要瑕疵（2-5条）

每条：
- 🟡 **质疑点**：...
- **被忽视的变量/替代解释**：...
- **建议的认知修正**：...

---

### 四、红军的盲区（1-3条）

红军完全遗漏但应关注的因素，说明为什么重要。

---

### 五、红军做得好的地方（不可省略）

2-3个论证扎实、无法反驳的点，用🟢标注。

---

### 六、修正后的评分建议

| 维度 | 红军评分 | 蓝军建议 | 调整理由 |
|------|---------|---------|---------|
| ... | X/10 | Y/10 | ... |

**修正后综合分**：X/10

---"""


# ══════════════════════════════════════════════════════════════════════════════
# 终审
# ══════════════════════════════════════════════════════════════════════════════

FINAL_VERDICT_SYSTEM = """你是投资决策委员会主席。面前有分析师（红军）的报告和风控官（蓝军）的质疑。你做最终裁决。

准则：
1. 不偏袒——只看逻辑硬度和证据质量
2. 每个分歧点逐条裁决谁更有道理，不能说"各有道理"
3. 最终结论可以和红军一致、蓝军一致、或第三种判断
4. 裁决必须有理由

🔴蓝军胜（红军被推翻）🟢红军胜（质疑不成立）🟡各有道理需折中"""

FINAL_VERDICT_USER = """# 投决会终审裁决

## 红军分析报告
{red_team_report}

## 蓝军质疑报告
{blue_team_report}

## 请严格按以下结构输出

---

### 一、终审结论

**红军评级**：[提取]
**蓝军修正建议**：[提取]
**终审最终评级**：___
**一句话裁决理由**：...

---

### 二、逐项裁决

| # | 分歧点 | 红军立场 | 蓝军质疑 | 裁决 | 理由 |
|---|--------|---------|---------|------|------|
| 1 | ... | ... | ... | 🟢/🔴/🟡 | ... |

**裁决统计**：红军胜 __ 项 | 蓝军胜 __ 项 | 折中 __ 项

---

### 三、蓝军盲区中最有价值的发现

从蓝军"红军的盲区"中挑1-2条纳入最终判断。

---

### 四、修正后的最终评分

<<<FINAL_SCORES>>>
基本面: X/10
预期差: X/10
技术面: X/10
资金面: X/10
---
综合加权: X/10
短期爆发力: X/10
中线安全垫: X/10
致命缺陷: 有/无
<<<END_FINAL_SCORES>>>

---

### 五、最终操作建议

- **操作评级**：___
- **仓位**：___%
- **与红军建议的差异**：[无差异/上调/下调]，原因：...
- **入场策略**：...
- **红蓝双方共识的核心监控变量**：
  1. ...（若恶化，应___）
  2. ...（若超预期，可___）

---"""


# ══════════════════════════════════════════════════════════════════════════════
# 辅助函数
# ══════════════════════════════════════════════════════════════════════════════

def truncate_report_for_challenge(full_report: str, max_chars: int = 12000) -> str:
    """智能截取报告，保留结论性内容，压缩描述性内容"""
    if len(full_report) <= max_chars:
        return full_report

    # 识别章节标题
    sections = re.split(r'(###\s*(?:零|一|二|三|四|五|六|七|八|九)、[^\n]*)', full_report)

    # 必须全文保留的章节关键词
    keep_full = ["零", "一", "五", "六", "七", "八"]
    # 可压缩的章节
    compress = ["二", "三", "四"]

    result_parts = []
    i = 0
    while i < len(sections):
        part = sections[i]
        if re.match(r'###\s*(零|一|二|三|四|五|六|七|八|九)、', part):
            header = part
            body = sections[i + 1] if i + 1 < len(sections) else ""
            # 判断章节编号
            ch_num = re.search(r'(零|一|二|三|四|五|六|七|八|九)', header)
            ch = ch_num.group(1) if ch_num else ""

            if ch in keep_full:
                result_parts.append(header + body)
            elif ch in compress:
                # 压缩：只保留每个子节的最后一段
                lines = body.strip().split("\n")
                # 保留标题行和最后 30%
                keep_n = max(5, len(lines) // 3)
                compressed = "\n".join(lines[-keep_n:])
                result_parts.append(header + "\n" + compressed)
            else:
                # 九（局限性）等：跳过
                pass
            i += 2
        else:
            result_parts.append(part)
            i += 1

    result = "\n".join(result_parts)

    # 如果仍超限，硬截断
    if len(result) > max_chars:
        result = result[:max_chars] + "\n\n...(报告已截断)"

    return "[注：以下为分析报告的结论精要版]\n\n" + result


def build_blue_team_prompt(red_team_report: str) -> tuple[str, str]:
    """构建蓝军 prompt"""
    truncated = truncate_report_for_challenge(red_team_report)
    user = BLUE_TEAM_USER.replace("{red_team_report}", truncated)
    return user, BLUE_TEAM_SYSTEM


def build_verdict_prompt(red_team_report: str, blue_team_report: str) -> tuple[str, str]:
    """构建终审 prompt"""
    red_truncated = truncate_report_for_challenge(red_team_report)
    user = FINAL_VERDICT_USER.replace("{red_team_report}", red_truncated)
    user = user.replace("{blue_team_report}", blue_team_report)
    return user, FINAL_VERDICT_SYSTEM
