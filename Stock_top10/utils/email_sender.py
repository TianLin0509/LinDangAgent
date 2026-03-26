"""邮件工具 — 精简版（仅 smtp_configured + _get_smtp_config + _md_to_html_simple）"""

import re

from core.secrets_compat import _get_secret


def _get_smtp_config() -> tuple[str, int, str, str]:
    return (
        _get_secret("SMTP_HOST", ""),
        int(_get_secret("SMTP_PORT", "465")),
        _get_secret("SMTP_USER", ""),
        _get_secret("SMTP_PASS", ""),
    )


def smtp_configured() -> bool:
    host, _, user, pwd = _get_smtp_config()
    return bool(host and user and pwd)


def _md_to_html_simple(md: str) -> str:
    """简易 markdown → HTML（标题、加粗、列表、换行）"""
    html = md
    html = re.sub(r'^#{4}\s+(.+)$', r'<h4 style="color:#4f46e5;margin:12px 0 6px;">\1</h4>', html, flags=re.MULTILINE)
    html = re.sub(r'^#{3}\s+(.+)$', r'<h3 style="color:#1e1b4b;margin:14px 0 8px;">\1</h3>', html, flags=re.MULTILINE)
    html = re.sub(r'^#{2}\s+(.+)$', r'<h2 style="color:#1e1b4b;margin:16px 0 8px;">\1</h2>', html, flags=re.MULTILINE)
    html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html)
    html = re.sub(r'^[-*]\s+(.+)$', r'<li style="margin:2px 0;">\1</li>', html, flags=re.MULTILINE)
    html = html.replace('\n', '<br>\n')
    return html
