#!/usr/bin/env python3
"""生成知识库 HTML Dashboard"""
import sqlite3, json, html, os

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(BASE, "data", "knowledge")

def esc(s):
    if s is None: return ""
    return html.escape(str(s))

# --- 读取数据 ---
conn = sqlite3.connect(os.path.join(DATA, "wisdom.db"))
conn.row_factory = sqlite3.Row
wisdoms = [dict(r) for r in conn.execute("SELECT * FROM wisdom_entries ORDER BY category, rowid").fetchall()]
conn.close()

conn = sqlite3.connect(os.path.join(DATA, "case_memory.db"))
conn.row_factory = sqlite3.Row
cases = [dict(r) for r in conn.execute("SELECT * FROM cases ORDER BY report_date DESC, rowid DESC").fetchall()]
tags_raw = conn.execute("SELECT * FROM case_tags").fetchall()
case_tags = {}
for t in tags_raw:
    cid = t["case_id"]
    if cid not in case_tags:
        case_tags[cid] = []
    case_tags[cid].append(f'{t["tag_type"]}:{t["tag_value"]}')
conn.close()

def load_jsonl(name):
    path = os.path.join(DATA, name)
    if not os.path.exists(path): return []
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]

sessions = load_jsonl("session_log.jsonl")
war_rooms = load_jsonl("war_room_tracker.jsonl")
sims = load_jsonl("simulation_log.jsonl")
regimes = load_jsonl("regime_log.jsonl")

with open(os.path.join(DATA, "WISDOM.md"), "r", encoding="utf-8") as f:
    wisdom_md = f.read()
with open(os.path.join(DATA, "STATE.md"), "r", encoding="utf-8") as f:
    state_md = f.read()

# --- 统计 ---
wisdom_cats = {}
for w in wisdoms:
    cat = w.get("category", "unknown")
    wisdom_cats[cat] = wisdom_cats.get(cat, 0) + 1

case_outcomes = {"win": 0, "loss": 0, "draw": 0}
for c in cases:
    o = c.get("outcome_type", "")
    if o in case_outcomes:
        case_outcomes[o] += 1

hit_rate = case_outcomes["win"] / len(cases) * 100 if cases else 0

# --- HTML ---
CSS = """
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family: -apple-system, 'Microsoft YaHei', 'Segoe UI', sans-serif; background:#0f1923; color:#e0e0e0; }
.header { background:linear-gradient(135deg,#1a2a3a,#0d2137); padding:30px 40px; border-bottom:2px solid #2a4a6a; }
.header h1 { font-size:28px; color:#4fc3f7; }
.header .meta { color:#78909c; margin-top:8px; font-size:14px; }
.container { max-width:1400px; margin:0 auto; padding:20px; }
.stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:16px; margin:20px 0; }
.stat-card { background:#1a2a3a; border-radius:10px; padding:20px; border-left:4px solid #4fc3f7; }
.stat-card .num { font-size:32px; font-weight:bold; color:#4fc3f7; }
.stat-card .label { color:#90a4ae; font-size:13px; margin-top:4px; }
.stat-card.green .num { color:#66bb6a; }
.stat-card.green { border-left-color:#66bb6a; }
.tabs { display:flex; gap:4px; margin:20px 0 0; flex-wrap:wrap; }
.tab { padding:10px 20px; background:#1a2a3a; border:none; color:#90a4ae; cursor:pointer; border-radius:8px 8px 0 0; font-size:14px; transition:all .2s; }
.tab:hover { background:#253545; }
.tab.active { background:#1e3348; color:#4fc3f7; font-weight:bold; }
.panel { display:none; background:#1e3348; border-radius:0 8px 8px 8px; padding:20px; min-height:400px; }
.panel.active { display:block; }
table { width:100%; border-collapse:collapse; font-size:13px; }
th { background:#0f1923; color:#4fc3f7; padding:10px 8px; text-align:left; position:sticky; top:0; z-index:1; }
td { padding:8px; border-bottom:1px solid #2a3a4a; vertical-align:top; }
tr:hover td { background:#253545; }
.tag { display:inline-block; background:#2a4a6a; color:#81d4fa; padding:2px 8px; border-radius:10px; font-size:11px; margin:1px; }
.cat-tag { background:#1b5e20; color:#a5d6a7; }
.win { color:#66bb6a; font-weight:bold; }
.loss { color:#ef5350; font-weight:bold; }
.neutral { color:#ffa726; }
.direction-bullish { color:#66bb6a; }
.direction-bearish { color:#ef5350; }
.direction-neutral { color:#ffa726; }
.search-box { width:100%; padding:10px 16px; background:#0f1923; border:1px solid #2a4a6a; border-radius:8px; color:#e0e0e0; font-size:14px; margin-bottom:16px; }
.search-box:focus { outline:none; border-color:#4fc3f7; }
.wisdom-text { max-width:500px; line-height:1.5; }
.lesson-text { max-width:400px; line-height:1.4; font-size:12px; color:#b0bec5; }
.scroll-wrap { max-height:650px; overflow-y:auto; }
pre { background:#0f1923; padding:16px; border-radius:8px; overflow-x:auto; font-size:13px; line-height:1.6; white-space:pre-wrap; }
.filter-row { display:flex; gap:8px; margin-bottom:12px; flex-wrap:wrap; }
.filter-btn { padding:4px 12px; background:#1a2a3a; border:1px solid #2a4a6a; color:#90a4ae; border-radius:15px; cursor:pointer; font-size:12px; transition:all .2s; }
.filter-btn:hover { border-color:#4fc3f7; }
.filter-btn.active { background:#2a4a6a; color:#4fc3f7; border-color:#4fc3f7; }
.section-title { color:#4fc3f7; margin:20px 0 10px; font-size:18px; }
"""

JS = """
function showTab(name, el) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('tab-'+name).classList.add('active');
  el.classList.add('active');
}
function filterTable(input, tableId) {
  const q = input.value.toLowerCase();
  document.querySelectorAll('#'+tableId+' tbody tr').forEach(tr => {
    tr.style.display = tr.textContent.toLowerCase().includes(q) ? '' : 'none';
  });
}
function filterCat(btn, cat) {
  document.querySelectorAll('#wisdom-filters .filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('#wisdom-table tbody tr').forEach(tr => {
    if (cat === 'all') { tr.style.display = ''; }
    else { tr.style.display = tr.dataset.cat === cat ? '' : 'none'; }
  });
}
"""

lines = []
a = lines.append

a("<!DOCTYPE html>")
a('<html lang="zh-CN"><head><meta charset="UTF-8">')
a("<title>林铛知识库总览</title>")
a(f"<style>{CSS}</style></head><body>")

# Header
meta_line = state_md.split("\n")[1].replace("*", "").strip() if len(state_md.split("\n")) > 1 else ""
a('<div class="header">')
a("<h1>林铛知识库总览</h1>")
a(f'<div class="meta">{esc(meta_line)}</div>')
a("</div>")

# Stats
a('<div class="container">')
a('<div class="stats">')
a(f'<div class="stat-card"><div class="num">{len(wisdoms)}</div><div class="label">智慧条目</div></div>')
a(f'<div class="stat-card"><div class="num">{len(cases)}</div><div class="label">案例训练</div></div>')
a(f'<div class="stat-card green"><div class="num">{hit_rate:.0f}%</div><div class="label">命中率 ({case_outcomes["win"]}胜/{case_outcomes["loss"]}负)</div></div>')
a(f'<div class="stat-card"><div class="num">{len(war_rooms)}</div><div class="label">指挥部记录</div></div>')
a(f'<div class="stat-card"><div class="num">{len(sims)}</div><div class="label">模拟训练</div></div>')
a(f'<div class="stat-card"><div class="num">{len(sessions)}</div><div class="label">会话日志</div></div>')
a("</div>")

# Tabs
tab_defs = [
    ("wisdom", f"智慧库 ({len(wisdoms)})"),
    ("cases", f"案例库 ({len(cases)})"),
    ("warroom", "指挥部"),
    ("sessions", "会话日志"),
    ("sims", "模拟训练"),
    ("state", "工作状态"),
]
a('<div class="tabs">')
for i, (tid, tlabel) in enumerate(tab_defs):
    cls = ' active' if i == 0 else ''
    a(f'<div class="tab{cls}" onclick="showTab(\'{tid}\',this)">{tlabel}</div>')
a("</div>")

# === Wisdom Tab ===
a('<div class="panel active" id="tab-wisdom">')
a('<input class="search-box" placeholder="搜索智慧..." onkeyup="filterTable(this,\'wisdom-table\')">')
cats = sorted(wisdom_cats.keys())
a('<div class="filter-row" id="wisdom-filters">')
a(f'<div class="filter-btn active" onclick="filterCat(this,\'all\')">全部 ({len(wisdoms)})</div>')
for cat in cats:
    a(f'<div class="filter-btn" onclick="filterCat(this,\'{cat}\')">{esc(cat)} ({wisdom_cats[cat]})</div>')
a("</div>")
a('<div class="scroll-wrap"><table id="wisdom-table"><thead><tr>')
a("<th>#</th><th>分类</th><th>智慧</th><th>背景</th><th>来源</th><th>标签</th><th>使用</th>")
a("</tr></thead><tbody>")
for i, w in enumerate(wisdoms, 1):
    tags_list = json.loads(w.get("tags", "[]")) if w.get("tags") else []
    tags_html = "".join(f'<span class="tag">{esc(t)}</span>' for t in tags_list)
    cat = w.get("category", "")
    a(f'<tr data-cat="{esc(cat)}">')
    a(f"<td>{i}</td>")
    a(f'<td><span class="tag cat-tag">{esc(cat)}</span></td>')
    a(f'<td class="wisdom-text">{esc(w.get("wisdom", ""))}</td>')
    a(f"<td>{esc(w.get('context', ''))}</td>")
    a(f'<td style="font-size:11px;color:#78909c">{esc(w.get("source_name", ""))}</td>')
    a(f"<td>{tags_html}</td>")
    a(f'<td style="text-align:center">{w.get("used_count", 0)}</td>')
    a("</tr>")
a("</tbody></table></div></div>")

# === Cases Tab ===
a('<div class="panel" id="tab-cases">')
a('<input class="search-box" placeholder="搜索案例（股票名/代码）..." onkeyup="filterTable(this,\'cases-table\')">')
a('<div class="scroll-wrap"><table id="cases-table"><thead><tr>')
a("<th>日期</th><th>股票</th><th>代码</th><th>方向</th><th>综合分</th><th>基/预/资/技</th><th>10D收益</th><th>结果</th><th>教训</th><th>标签</th>")
a("</tr></thead><tbody>")
for c in cases:
    dir_cls = f'direction-{c.get("direction", "")}'
    outcome_cls = c.get("outcome_type", "")
    r10 = c.get("return_10d")
    r10_str = f"{r10:+.1f}%" if r10 is not None else "-"
    r10_color = "#66bb6a" if r10 and r10 > 0 else "#ef5350" if r10 and r10 < 0 else ""
    ct = case_tags.get(c["case_id"], [])
    ct_html = "".join(f'<span class="tag">{esc(t)}</span>' for t in ct[:5])
    scores = f'{int(c.get("score_fundamental", 0))}/{int(c.get("score_expectation", 0))}/{int(c.get("score_capital", 0))}/{int(c.get("score_technical", 0))}'
    lesson = (c.get("lesson", "") or "")[:120]
    a("<tr>")
    a(f'<td style="white-space:nowrap">{esc(c.get("report_date", ""))}</td>')
    a(f'<td><b>{esc(c.get("stock_name", ""))}</b></td>')
    a(f'<td style="font-size:11px">{esc(c.get("stock_code", ""))}</td>')
    a(f'<td class="{dir_cls}">{esc(c.get("direction", ""))}</td>')
    a(f'<td style="font-size:16px;font-weight:bold">{c.get("score_weighted", 0)}</td>')
    a(f'<td style="font-size:11px;color:#90a4ae">{scores}</td>')
    a(f'<td style="color:{r10_color}">{r10_str}</td>')
    a(f'<td class="{outcome_cls}">{esc(c.get("outcome_type", ""))}</td>')
    a(f'<td class="lesson-text">{esc(lesson)}</td>')
    a(f"<td>{ct_html}</td>")
    a("</tr>")
a("</tbody></table></div></div>")

# === War Room Tab ===
a('<div class="panel" id="tab-warroom">')
a('<div class="scroll-wrap"><table><thead><tr>')
a("<th>时间</th><th>股票</th><th>分数</th><th>方向</th><th>详情</th>")
a("</tr></thead><tbody>")
for wr in reversed(war_rooms):
    summary = wr.get("summary", "")
    if not summary:
        summary = json.dumps(wr, ensure_ascii=False)[:200]
    a("<tr>")
    a(f'<td style="white-space:nowrap">{esc(str(wr.get("timestamp", ""))[:16])}</td>')
    a(f'<td><b>{esc(wr.get("stock_name", ""))}</b> {esc(wr.get("stock_code", ""))}</td>')
    a(f'<td style="font-size:16px;font-weight:bold">{wr.get("final_score", "")}</td>')
    a(f'<td>{esc(wr.get("direction", ""))}</td>')
    a(f'<td style="font-size:11px;max-width:400px">{esc(summary[:200])}</td>')
    a("</tr>")
a("</tbody></table></div></div>")

# === Sessions Tab ===
a('<div class="panel" id="tab-sessions">')
a('<div class="scroll-wrap"><table><thead><tr><th>时间</th><th>内容</th></tr></thead><tbody>')
for s in reversed(sessions):
    ts = str(s.get("timestamp", ""))[:16]
    content = json.dumps(s, ensure_ascii=False)[:500]
    a(f'<tr><td style="white-space:nowrap">{esc(ts)}</td><td style="font-size:12px;line-height:1.5">{esc(content)}</td></tr>')
a("</tbody></table></div></div>")

# === Sims Tab ===
a('<div class="panel" id="tab-sims">')
a('<div class="scroll-wrap"><table><thead><tr><th>时间</th><th>内容</th></tr></thead><tbody>')
for s in reversed(sims[-30:]):
    ts = str(s.get("timestamp", s.get("date", "")))[:16]
    content = json.dumps(s, ensure_ascii=False)[:500]
    a(f'<tr><td style="white-space:nowrap">{esc(ts)}</td><td style="font-size:12px;line-height:1.5">{esc(content)}</td></tr>')
a("</tbody></table></div></div>")

# === State Tab ===
a('<div class="panel" id="tab-state">')
a(f"<pre>{esc(state_md)}</pre>")
a('<h3 class="section-title">WISDOM.md</h3>')
a(f"<pre>{esc(wisdom_md)}</pre>")
a("</div>")

# JS
a(f"<script>{JS}</script>")
a("</div></body></html>")

# Write
out_path = os.path.join(BASE, "storage", "export", "knowledge_dashboard.html")
with open(out_path, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print(f"Done: {out_path}")
print(f"Wisdom: {len(wisdoms)}, Cases: {len(cases)}, WarRoom: {len(war_rooms)}, Sessions: {len(sessions)}, Sims: {len(sims)}")
