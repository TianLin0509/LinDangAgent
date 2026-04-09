#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""测试单股舆情爬取并生成网页展示"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger(__name__)


def render_sentiment_html(bundle, posts_short, posts_mid) -> str:
    """渲染单股舆情分析网页（参考 sentiment_radar 风格）"""
    stock_name = bundle.stock_name
    stock_code = bundle.stock_code
    fetched_at = bundle.fetched_at

    short = bundle.short_term
    mid = bundle.mid_term

    # 构建帖子列表 HTML
    def _render_posts(posts, title):
        if not posts:
            return f"<h3>{title}</h3><p>无帖子数据</p>"

        lines = [f"<h3>{title}（共 {len(posts)} 条）</h3>"]
        lines.append('<div class="posts">')
        for i, p in enumerate(posts[:20], 1):
            fans = p.get('followers_count', 0)
            fans_label = f"{fans // 10000}万粉" if fans >= 10000 else f"{fans}粉"
            quality = p.get('_quality', 0)
            lines.append(f'''
            <div class="post">
                <div class="post-header">
                    <span class="post-num">[{i}]</span>
                    <span class="post-user">{p.get('user_name', '?')}</span>
                    <span class="post-fans">（{fans_label}）</span>
                    <span class="post-stats">👍 {p.get('like_count', 0)} 💬 {p.get('reply_count', 0)}</span>
                    <span class="post-quality">质量分: {quality:.0f}</span>
                </div>
                <div class="post-content">{p.get('text', '')[:500]}</div>
                <div class="post-time">{p.get('created_at', '')}</div>
            </div>
            ''')
        lines.append('</div>')
        return '\n'.join(lines)

    # 构建分析结果 HTML
    def _render_analysis(result, window_label):
        if not result or result.error:
            return f"<h3>{window_label}分析</h3><p>分析失败: {result.error if result else '无数据'}</p>"

        lines = [f"<h3>{window_label}分析（{result.posts_count} 条帖子）</h3>"]
        lines.append('<div class="analysis">')
        lines.append(f'<p><strong>情绪方向:</strong> {result.sentiment_label} | <strong>置信度:</strong> {result.confidence}</p>')

        if result.bull_points:
            lines.append('<p><strong>看多逻辑:</strong></p><ul>')
            for pt in result.bull_points:
                lines.append(f'<li>{pt}</li>')
            lines.append('</ul>')

        if result.bear_points:
            lines.append('<p><strong>看空逻辑:</strong></p><ul>')
            for pt in result.bear_points:
                lines.append(f'<li>{pt}</li>')
            lines.append('</ul>')

        if result.key_concerns:
            lines.append('<p><strong>关键争议:</strong></p><ul>')
            for pt in result.key_concerns:
                lines.append(f'<li>{pt}</li>')
            lines.append('</ul>')

        if result.catalysts:
            lines.append('<p><strong>催化预期:</strong></p><ul>')
            for pt in result.catalysts:
                lines.append(f'<li>{pt}</li>')
            lines.append('</ul>')

        if result.risks:
            lines.append('<p><strong>风险提示:</strong></p><ul>')
            for pt in result.risks:
                lines.append(f'<li>{pt}</li>')
            lines.append('</ul>')

        if result.one_liner:
            lines.append(f'<p class="one-liner">💡 {result.one_liner}</p>')

        lines.append('</div>')
        return '\n'.join(lines)

    posts_short_html = _render_posts(posts_short, "短期帖子（24小时）")
    posts_mid_html = _render_posts(posts_mid, "中线帖子（2周）")

    analysis_short_html = _render_analysis(short, "短期舆情（24小时）")
    analysis_mid_html = _render_analysis(mid, "中线舆情（2周）")

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{stock_name}（{stock_code}）舆情分析</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Microsoft YaHei', sans-serif;
    max-width: 1200px;
    margin: 0 auto;
    padding: 20px;
    background: #f5f5f5;
  }}
  .header {{
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: white;
    padding: 24px;
    border-radius: 12px;
    margin-bottom: 20px;
  }}
  .header h1 {{ margin: 0 0 8px 0; font-size: 28px; }}
  .header .meta {{ opacity: 0.9; font-size: 14px; }}

  .section {{
    background: white;
    padding: 24px;
    border-radius: 12px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.08);
    margin-bottom: 20px;
  }}

  .section h2 {{
    color: #4a5568;
    border-bottom: 2px solid #667eea;
    padding-bottom: 8px;
    margin-top: 0;
  }}

  .section h3 {{
    color: #667eea;
    margin-top: 24px;
    margin-bottom: 12px;
  }}

  .analysis {{
    background: #f7fafc;
    padding: 16px;
    border-radius: 8px;
    margin-top: 12px;
  }}

  .analysis p {{ margin: 8px 0; }}
  .analysis ul {{ margin: 8px 0; padding-left: 24px; }}
  .analysis li {{ margin: 4px 0; }}

  .one-liner {{
    background: #edf2f7;
    padding: 12px;
    border-left: 4px solid #667eea;
    margin-top: 12px;
    font-weight: 500;
  }}

  .posts {{ margin-top: 12px; }}

  .post {{
    background: #fafafa;
    padding: 12px;
    border-radius: 6px;
    margin-bottom: 12px;
    border-left: 3px solid #e2e8f0;
  }}

  .post-header {{
    font-size: 13px;
    color: #718096;
    margin-bottom: 8px;
  }}

  .post-num {{ font-weight: 600; color: #667eea; }}
  .post-user {{ font-weight: 500; color: #2d3748; }}
  .post-fans {{ color: #a0aec0; }}
  .post-stats {{ margin-left: 12px; }}
  .post-quality {{
    float: right;
    background: #edf2f7;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 12px;
  }}

  .post-content {{
    line-height: 1.6;
    color: #2d3748;
    margin: 8px 0;
  }}

  .post-time {{
    font-size: 12px;
    color: #a0aec0;
    margin-top: 6px;
  }}

  .footer {{
    text-align: center;
    color: #999;
    font-size: 12px;
    margin-top: 20px;
  }}
</style>
</head>
<body>
<div class="header">
  <h1>📊 {stock_name}（{stock_code}）舆情分析</h1>
  <div class="meta">采集时间: {fetched_at}</div>
</div>

<div class="section">
  <h2>📈 分析结果</h2>
  {analysis_short_html}
  {analysis_mid_html}
</div>

<div class="section">
  <h2>💬 原始帖子</h2>
  {posts_short_html}
  {posts_mid_html}
</div>

<div class="footer">Powered by LinDangAgent · 立花道雪</div>
</body>
</html>"""


def main():
    stock_name = "兆易创新"

    logger.info("=" * 60)
    logger.info(f"开始测试单股舆情爬取: {stock_name}")
    logger.info("=" * 60)

    # Step 1: 解析股票代码
    from data.tushare_client import resolve_stock
    ts_code, resolved_name, warn = resolve_stock(stock_name)
    logger.info(f"股票解析: {stock_name} → {ts_code} ({resolved_name})")

    if not ts_code:
        logger.error("无法解析股票代码")
        return

    # Step 2: 爬取帖子（不调用 LLM，只测试爬取）
    from data.stock_sentiment import _fetch_stock_posts, _to_xueqiu_symbol

    symbol = _to_xueqiu_symbol(ts_code)
    logger.info(f"雪球 symbol: {symbol}")

    logger.info("\n--- 爬取短期帖子（24小时）---")
    posts_short = _fetch_stock_posts(ts_code, resolved_name, hours=24, midterm=False)
    logger.info(f"短期帖子: {len(posts_short)} 条")
    if posts_short:
        logger.info(f"  第一条: {posts_short[0]['user_name']} ({posts_short[0]['followers_count']}粉) 质量分={posts_short[0].get('_quality', 0):.0f}")
        logger.info(f"  内容: {posts_short[0]['text'][:100]}")

    logger.info("\n--- 爬取中线帖子（2周）---")
    posts_mid = _fetch_stock_posts(ts_code, resolved_name, hours=336, midterm=True)
    logger.info(f"中线帖子: {len(posts_mid)} 条")
    if posts_mid:
        logger.info(f"  第一条: {posts_mid[0]['user_name']} ({posts_mid[0]['followers_count']}粉) 质量分={posts_mid[0].get('_quality', 0):.0f}")

    # Step 3: 调用 LLM 分析
    logger.info("\n--- 调用 LLM 分析 ---")
    from data.stock_sentiment import fetch_stock_sentiment

    bundle = fetch_stock_sentiment(
        ts_code=ts_code,
        stock_name=resolved_name,
    )

    logger.info(f"短期舆情: {bundle.short_term.sentiment_label} (置信度: {bundle.short_term.confidence})")
    if bundle.short_term.error:
        logger.warning(f"  错误: {bundle.short_term.error}")
    else:
        logger.info(f"  看多: {bundle.short_term.bull_points}")
        logger.info(f"  看空: {bundle.short_term.bear_points}")

    logger.info(f"中线舆情: {bundle.mid_term.sentiment_label} (置信度: {bundle.mid_term.confidence})")
    if bundle.mid_term.error:
        logger.warning(f"  错误: {bundle.mid_term.error}")
    else:
        logger.info(f"  看多: {bundle.mid_term.bull_points}")
        logger.info(f"  看空: {bundle.mid_term.bear_points}")

    # Step 4: 生成网页
    logger.info("\n--- 生成网页 ---")
    html = render_sentiment_html(bundle, posts_short, posts_mid)

    output_dir = Path(__file__).parent / "storage" / "sentiment_test"
    output_dir.mkdir(parents=True, exist_ok=True)

    output_file = output_dir / f"{resolved_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    output_file.write_text(html, encoding='utf-8')

    logger.info(f"网页已生成: {output_file}")
    logger.info(f"请在浏览器中打开: file:///{output_file.as_posix()}")

    # Step 5: 保存原始数据（供调试）
    debug_file = output_dir / f"{resolved_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_debug.json"
    debug_data = {
        "stock_name": resolved_name,
        "stock_code": ts_code,
        "posts_short_count": len(posts_short),
        "posts_mid_count": len(posts_mid),
        "short_term": {
            "sentiment": bundle.short_term.sentiment_label,
            "confidence": bundle.short_term.confidence,
            "error": bundle.short_term.error,
            "bull_points": bundle.short_term.bull_points,
            "bear_points": bundle.short_term.bear_points,
        },
        "mid_term": {
            "sentiment": bundle.mid_term.sentiment_label,
            "confidence": bundle.mid_term.confidence,
            "error": bundle.mid_term.error,
            "bull_points": bundle.mid_term.bull_points,
            "bear_points": bundle.mid_term.bear_points,
        },
    }
    debug_file.write_text(json.dumps(debug_data, ensure_ascii=False, indent=2), encoding='utf-8')
    logger.info(f"调试数据已保存: {debug_file}")

    logger.info("\n" + "=" * 60)
    logger.info("测试完成！")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
