# -*- coding: utf-8 -*-
"""知识库集中配置 — 路径、常量、枚举、阈值

所有知识库模块共享的配置项集中管理于此，避免魔法数字散落各处。
"""

from pathlib import Path

# ── 路径定义 ───────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = BASE_DIR / "data" / "knowledge"
STORAGE_DIR = BASE_DIR / "storage"
REPORTS_DB_PATH = STORAGE_DIR / "reports.db"
NIGHT_REPORT_DIR = STORAGE_DIR / "night_reports"
SCHEDULE_PID_FILE = STORAGE_DIR / "review_scheduler.pid"
SCHEDULE_LOG_FILE = STORAGE_DIR / "review_scheduler.log"

# 各数据库路径
CASE_MEMORY_DB = KNOWLEDGE_DIR / "case_memory.db"
INTEL_MEMORY_DB = KNOWLEDGE_DIR / "intel_memory.db"
KLINE_DIARY_DB = KNOWLEDGE_DIR / "kline_diary.db"
THESIS_JOURNAL_DB = KNOWLEDGE_DIR / "thesis_journal.db"
WISDOM_DB = KNOWLEDGE_DIR / "wisdom.db"
OUTCOMES_DB = KNOWLEDGE_DIR / "outcomes.db"

# 各 JSONL / Markdown 文件路径
OUTCOMES_FILE = KNOWLEDGE_DIR / "outcomes.jsonl"
REGIME_LOG_FILE = KNOWLEDGE_DIR / "regime_log.jsonl"
SIMULATION_LOG_FILE = KNOWLEDGE_DIR / "simulation_log.jsonl"
WAR_ROOM_TRACKER_FILE = KNOWLEDGE_DIR / "war_room_tracker.jsonl"
SESSION_LOG_FILE = KNOWLEDGE_DIR / "session_log.jsonl"
STATE_MD_PATH = KNOWLEDGE_DIR / "STATE.md"
WISDOM_MD_PATH = KNOWLEDGE_DIR / "WISDOM.md"
THESIS_MD_PATH = KNOWLEDGE_DIR / "THESIS.md"

# ── 评分体系 ───────────────────────────────────────────────────────

SCORE_WEIGHTS = {
    "基本面": 0.15,
    "预期差": 0.35,
    "资金面": 0.30,
    "技术面": 0.20,
}

SCORE_DIMENSIONS = list(SCORE_WEIGHTS.keys())

# ── 结果判定 ───────────────────────────────────────────────────────

OUTCOME_THRESHOLD = 2.0        # win/loss 判定阈值（10日收益率 %）
MIN_EVAL_DAYS = 8              # 报告评估最少间隔天数

# ── 市场环境检测 ──────────────────────────────────────────────────

SH_INDEX_CODE = "000001.SH"   # 上证指数代码
HS300_CODE = "000300.SH"       # 沪深300代码

REGIME_LABELS = {
    "bull": "牛市",
    "bear": "熊市",
    "shock": "震荡市",
    "rotation": "轮动市",
}

REGIME_NEAR_MA60_PCT = 3.0     # 震荡判定：MA60 附近 ±N%
REGIME_RET_BULL = 5.0          # 牛市判定：20日收益 > N%
REGIME_RET_BEAR = -5.0         # 熊市判定：20日收益 < N%
REGIME_RET_ROTATION = 2.0      # 轮动判定：20日收益 < N%
REGIME_HYSTERESIS_DAYS = 2     # 环境切换滞后天数

# ── 方向映射 ──────────────────────────────────────────────────────

DIRECTION_CN = {
    "bullish": "看多",
    "bearish": "看空",
    "neutral": "中性",
}

OUTCOME_CN = {
    "win": "盈利",
    "loss": "亏损",
    "draw": "平局",
}

# ── 统计阈值 ──────────────────────────────────────────────────────

MIN_SAMPLE_COUNT = 3           # 最小样本数（统计类计算）
HIT_RATE_MIN_SAMPLES = 5       # 胜率统计最小样本数
DEFAULT_SCORECARD_DAYS = 90    # 绩效卡默认统计天数

# ── 知识注入 ──────────────────────────────────────────────────────

INJECTOR_MAX_CHARS = 4000      # 知识库段落最大字符数
INJECTOR_PATTERN_MAX_CHARS = 400  # 模式匹配最大字符数
INJECTOR_AI_MAX_TOKENS = 2000  # AI 策展最大 token

# ── 智慧库 ────────────────────────────────────────────────────────

WISDOM_CATEGORIES = {
    "valuation": "估值与基本面",
    "timing": "择时与技术",
    "risk": "风控与仓位",
    "psychology": "心理与纪律",
    "sector": "板块与行业",
    "general": "通用投资哲学",
}

SOURCE_ICONS = {
    "book": "\U0001f4d6",    # 📖
    "blog": "\U0001f4dd",    # 📝
    "video": "\U0001f3ac",   # 🎬
    "experience": "\U0001f4a1",  # 💡
}

# ── 信念系统 ──────────────────────────────────────────────────────

BELIEF_CATEGORIES = {
    "market_structure": "市场结构",
    "sector_view": "板块观点",
    "methodology": "方法论",
    "risk_management": "风控纪律",
}

BELIEF_INITIAL_CONFIDENCE = 0.5  # 新信念初始置信度

# ── 板块/概念关键词（用于标签提取）──────────────────────────────────

SECTOR_KEYWORDS = {
    # 新能源
    "光伏", "储能", "锂电", "新能源车", "充电桩", "风电", "氢能", "钠电池",
    # 科技
    "AI算力", "AI应用", "人工智能", "大模型", "液冷", "算力", "CPO", "光模块",
    "半导体", "芯片", "EDA", "先进封装", "存储", "DRAM", "HBM",
    "消费电子", "手机产业链", "苹果产业链", "华为产业链", "折叠屏",
    # 高端制造
    "机器人", "人形机器人", "减速器", "低空经济", "eVTOL", "无人机",
    "军工", "航天", "卫星互联网", "商业航天",
    # 消费
    "白酒", "食品饮料", "医药", "创新药", "中药", "医疗器械", "CXO",
    "免税", "旅游", "酒店", "零食", "宠物经济",
    # 金融地产
    "券商", "保险", "银行", "地产", "REITs",
    # 周期
    "有色", "黄金", "煤炭", "钢铁", "化工", "石油", "稀土", "铜",
    # TMT
    "游戏", "传媒", "短剧", "影视", "教育", "数据要素", "信创",
    "云计算", "网络安全", "数字经济",
    # 其他热点
    "固态电池", "钙钛矿", "碳化硅", "第三代半导体",
    "工业母机", "数控机床", "3D打印",
    "预制菜", "减肥药", "脑机接口", "合成生物", "量子计算",
    "智能驾驶", "车路协同", "激光雷达",
    "电力", "特高压", "核电", "虚拟电厂",
    "国企改革", "并购重组", "高股息", "红利", "北交所",
}

# ── 模式模板（评分模式匹配）──────────────────────────────────────

PATTERN_TEMPLATES = {
    "four_high": {
        "description": "四面合围（全维度压制，四维均>=70）",
        "condition": lambda s: all(
            s.get(d, 0) >= 70 for d in ["基本面", "预期差", "资金面", "技术面"]
        ),
    },
    "high_fund_low_tech": {
        "description": "后方坚固·前线待突破（基本面>=70+技术面<=40）",
        "condition": lambda s: s.get("基本面", 0) >= 70 and s.get("技术面", 100) <= 40,
    },
    "high_tech_low_fund": {
        "description": "孤军深入（纯动量突击，技术面>=70+基本面<=40）",
        "condition": lambda s: s.get("技术面", 0) >= 70 and s.get("基本面", 100) <= 40,
    },
    "high_expectation": {
        "description": "弹药充足（强催化驱动，预期差>=80）",
        "condition": lambda s: s.get("预期差", 0) >= 80,
    },
    "capital_diverge": {
        "description": "友军到位·阵地空虚（资金面>=70+基本面<=40）",
        "condition": lambda s: s.get("资金面", 0) >= 70 and s.get("基本面", 100) <= 40,
    },
    "high_score_buy": {
        "description": "总攻条件成熟（综合加权>=75）",
        "condition": lambda s: s.get("综合加权", 0) >= 75,
    },
    "low_score_avoid": {
        "description": "全线溃败（综合加权<=30）",
        "condition": lambda s: s.get("综合加权", 100) <= 30,
    },
}

# ── 模型配置 ──────────────────────────────────────────────────────

REFLECTION_MODEL = "⚡ Claude Sonnet（MAX）"
REFLECTION_FALLBACK = "🟤 豆包 · Seed 2.0 Mini"

# ── 案例检索评分权重 ─────────────────────────────────────────────

CASE_RANK_REGIME_WEIGHT = 0.2    # 环境匹配权重
CASE_RANK_TAG_WEIGHT = 0.5       # 板块标签重叠权重
CASE_RANK_SCORE_WEIGHT = 0.3     # 评分距离权重（越近越高）
CASE_RANK_SCORE_MAX_DIST = 20.0  # 评分距离归一化基数
CASE_RANK_DIRECTION_BONUS = 0.15  # 方向一致加分

# ── 深度反思 ──────────────────────────────────────────────────────

WEEKLY_REFLECTION_DAYS = 7       # 周度反思时间窗口
MONTHLY_REFLECTION_DAYS = 30     # 月度反思时间窗口
SCORECARD_CONTEXT_DAYS = 90      # 反思中绩效统计天数

# ── 抖音学习 ──────────────────────────────────────────────────────

DOUYIN_STORAGE_DIR = STORAGE_DIR / "douyin_learner"
DISTILLER_PRIMARY_MODEL = "🟡 豆包 · Seed 2.0 Lite"
DISTILLER_FALLBACK_MODEL = "🟤 豆包 · Seed 2.0 Mini"
DISTILLER_MAX_TRANSCRIPT_LEN = 6000  # 转录文本截断长度
MIN_VIDEO_SIZE = 100 * 1024          # 最小有效视频文件大小 (100KB)
MIN_AUDIO_SIZE = 1024                # 最小有效音频文件大小 (1KB)
