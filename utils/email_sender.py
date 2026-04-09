"""统一邮件发送模块 — 替代项目中散落的 6 处 SMTP 实现。

用法::

    from utils.email_sender import send_text_email, send_image_email, send_html_email, smtp_configured

    send_text_email("主题", "正文")                         # 纯文本
    send_image_email("主题", "正文", "/path/to/img.png")    # 文本 + PNG 附件
    send_html_email("主题", "<h1>Hi</h1>")                  # HTML 邮件
"""

from __future__ import annotations

import logging
import smtplib
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from utils.app_config import get_secret

logger = logging.getLogger(__name__)

def _default_to() -> str:
    return get_secret("EMAIL_TO", "290045045@qq.com")


def _get_smtp_config() -> tuple[str, int, str, str]:
    return (
        get_secret("SMTP_HOST", "smtp.qq.com"),
        int(get_secret("SMTP_PORT", "465")),
        get_secret("SMTP_USER", ""),
        get_secret("SMTP_PASS", ""),
    )


def smtp_configured() -> bool:
    """SMTP 是否已配置（host + user + pass 均非空）。"""
    host, _, user, pwd = _get_smtp_config()
    return bool(host and user and pwd)


def _send(msg: MIMEMultipart, to_addr: str) -> None:
    """底层发送，支持 SSL(465) 和 STARTTLS。"""
    host, port, user, pwd = _get_smtp_config()
    if not user or not pwd:
        logger.debug("[email] SMTP not configured, skip")
        return

    msg["From"] = user
    msg["To"] = to_addr

    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=15) as server:
            server.login(user, pwd)
            server.sendmail(user, to_addr, msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls()
            server.login(user, pwd)
            server.sendmail(user, to_addr, msg.as_string())

    logger.info("[email] sent → %s | subject=%s", to_addr, msg["Subject"])


def send_text_email(subject: str, body: str, to_addr: str = "") -> None:
    """发送纯文本邮件。"""
    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))
    _send(msg, to_addr or _default_to())


def send_image_email(
    subject: str,
    body: str,
    image_path: str,
    to_addr: str = "",
    filename: str = "",
) -> None:
    """发送文本 + PNG/图片附件邮件。"""
    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    img_path = Path(image_path)
    with open(img_path, "rb") as f:
        img = MIMEImage(f.read())
    img.add_header(
        "Content-Disposition", "attachment",
        filename=filename or img_path.name,
    )
    msg.attach(img)
    _send(msg, to_addr or _default_to())


def send_html_email(subject: str, html_body: str, to_addr: str = "") -> None:
    """发送 HTML 邮件。"""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    _send(msg, to_addr or _default_to())
