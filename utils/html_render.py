"""HTML 渲染工具 — Markdown→HTML 转换 + Playwright 截图。

从 cli.py 提取，供 cli.py、war_room.py 等多处复用。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent


def md_to_html(md_text: str, title: str = "研报") -> str:
    """将 Markdown 文本转换为带样式的 HTML 页面。"""
    html_body = md_text

    # 转义 HTML
    html_body = html_body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # 标题
    html_body = re.sub(r"^######\s+(.+)$", r"<h6>\1</h6>", html_body, flags=re.MULTILINE)
    html_body = re.sub(r"^#####\s+(.+)$", r"<h5>\1</h5>", html_body, flags=re.MULTILINE)
    html_body = re.sub(r"^####\s+(.+)$", r"<h4>\1</h4>", html_body, flags=re.MULTILINE)
    html_body = re.sub(r"^###\s+(.+)$", r"<h3>\1</h3>", html_body, flags=re.MULTILINE)
    html_body = re.sub(r"^##\s+(.+)$", r"<h2>\1</h2>", html_body, flags=re.MULTILINE)
    html_body = re.sub(r"^#\s+(.+)$", r"<h1>\1</h1>", html_body, flags=re.MULTILINE)

    # 加粗和斜体
    html_body = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html_body)
    html_body = re.sub(r"\*(.+?)\*", r"<em>\1</em>", html_body)

    # 表格处理
    lines = html_body.split("\n")
    result = []
    in_table = False
    for line in lines:
        stripped = line.strip()
        if "|" in stripped and stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(re.match(r"^[-:]+$", c) for c in cells):
                continue
            if not in_table:
                result.append("<table>")
                in_table = True
            result.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
        else:
            if in_table:
                result.append("</table>")
                in_table = False
            result.append(line)
    if in_table:
        result.append("</table>")
    html_body = "\n".join(result)

    # 列表项
    html_body = re.sub(r"^\*\s+(.+)$", r"<li>\1</li>", html_body, flags=re.MULTILINE)
    html_body = re.sub(r"^-\s+(.+)$", r"<li>\1</li>", html_body, flags=re.MULTILINE)

    # 段落
    html_body = re.sub(r"\n{2,}", "\n<br><br>\n", html_body)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  body {{ font-family: -apple-system, "Microsoft YaHei", sans-serif; max-width: 900px; margin: 0 auto; padding: 20px; line-height: 1.8; color: #1a1a1a; background: #fafafa; }}
  h1 {{ color: #c0392b; border-bottom: 3px solid #c0392b; padding-bottom: 8px; }}
  h2 {{ color: #2c3e50; border-left: 4px solid #c0392b; padding-left: 12px; margin-top: 32px; }}
  h3 {{ color: #34495e; }}
  table {{ border-collapse: collapse; width: 100%; margin: 16px 0; }}
  td, th {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
  tr:nth-child(even) {{ background: #f5f5f5; }}
  tr:first-child {{ background: #2c3e50; color: white; font-weight: bold; }}
  strong {{ color: #c0392b; }}
  li {{ margin: 4px 0; }}
  .warning {{ color: #e67e22; font-weight: bold; }}
</style>
</head>
<body>
{html_body}
</body>
</html>"""


def html_to_image(html_path: str, png_path: str, width: int = 750) -> None:
    """用 Playwright 把 HTML 全页截图为 PNG。"""
    import asyncio
    from playwright.async_api import async_playwright

    async def _shot():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page(viewport={"width": width, "height": 900})
            await page.goto(f"file:///{html_path.replace(chr(92), '/')}")
            await page.wait_for_timeout(500)
            await page.screenshot(path=png_path, full_page=True)
            await browser.close()

    asyncio.run(_shot())


def save_and_open_html(md_text: str, stock_name: str, report_id: str) -> str:
    """保存 HTML 并用浏览器打开，返回文件路径。"""
    import subprocess

    html = md_to_html(md_text, title=f"{stock_name} 研报")
    safe_name = stock_name.replace(" ", "_")
    out_dir = BASE_DIR / "storage" / "export"
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / f"{safe_name}_{report_id[:8]}.html"
    html_path.write_text(html, encoding="utf-8")

    subprocess.Popen(
        ["cmd", "/c", "start", "", str(html_path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=0x00000008 | 0x08000000,
    )
    return str(html_path)
