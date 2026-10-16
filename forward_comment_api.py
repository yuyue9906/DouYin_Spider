"""Forward one exact comment through the Douyin IM API (not browser clicks)."""

import argparse
import json
import os
import re
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

from builder.auth import DouyinAuth
from comment_share import CommentShareError, forward_comment_share


def parse_args():
    parser = argparse.ArgumentParser(
        description="用抖音 IM API 转发单条评论；默认只解析预览，添加 --send 才会实际发送。"
    )
    parser.add_argument("--video", required=True, help="视频链接或作品 ID")
    parser.add_argument("--recipient", required=True, help="收件人抖音昵称，必须精确匹配")
    parser.add_argument("--recipient-uid", default="", help="同名账号较多时用于指定收件人 UID")
    parser.add_argument("--recipient-url", default="", help="可选：收件人抖音主页链接，用于唯一确认账号")
    parser.add_argument("--comment-id", default="", help="目标评论 ID；推荐提供")
    parser.add_argument("--comment-text", default="", help="目标评论完整内容；空白差异会忽略")
    parser.add_argument("--parent-comment-id", default="", help="目标是回复评论时，提供其一级父评论 ID")
    parser.add_argument("--max-pages", type=int, default=30, help="评论查找最多扫描页数，默认 30")
    parser.add_argument("--cover-file", default="", help="可选：包含 video_id 和 cover 的 JSON 文件，用于详情接口不可用时")
    parser.add_argument("--use-env-proxy", action="store_true", help="使用系统 HTTP(S)_PROXY 环境变量")
    parser.add_argument("--send", action="store_true", help="实际创建/获取私信会话并发送评论卡片")
    return parser.parse_args()


def load_auth():
    load_dotenv(Path(__file__).resolve().parent / ".env")
    cookie = (os.getenv("DY_COOKIES") or "").strip().strip("'\"")
    if not cookie:
        raise CommentShareError("项目 .env 中缺少 DY_COOKIES；请先配置已登录的抖音网页 Cookie。")

    auth = DouyinAuth()
    auth.perepare_auth(cookie)
    if not auth.cookie.get("s_v_web_id"):
        raise CommentShareError("DY_COOKIES 缺少 s_v_web_id，请重新复制完整的抖音网页 Cookie。")
    auth.ticket = os.getenv("DY_TICKET") or None
    auth.ts_sign = os.getenv("DY_TS_SIGN") or None
    auth.client_cert = os.getenv("DY_CLIENT_CERT") or None
    auth.private_key = os.getenv("DY_PRIVATE_KEY") or None
    return auth


def _safe_error(error):
    message = re.sub(
        r"(?i)(msToken|a_bogus|verifyFp|fp)=([^&\s]+)",
        lambda match: f"{match.group(1)}=<redacted>",
        str(error),
    )
    return message[:1000]


def main():
    args = parse_args()
    try:
        cover_data = None
        if args.cover_file:
            cover_data = json.loads(Path(args.cover_file).read_text(encoding="utf-8-sig"))
        auth = load_auth()
        with requests.Session() as session:
            # Direct access was the validated route in this project's local setup.
            # Opt in to environment proxies only when the user's network requires one.
            session.trust_env = args.use_env_proxy
            result = forward_comment_share(
                auth,
                args.video,
                args.recipient,
                comment_id=args.comment_id,
                comment_text=args.comment_text,
                recipient_uid=args.recipient_uid,
                recipient_url=args.recipient_url,
                parent_comment_id=args.parent_comment_id,
                max_pages=args.max_pages,
                send=args.send,
                session=session,
                cover_data=cover_data,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    except Exception as error:
        result = {
            "ok": False,
            "sent": False,
            "error_type": type(error).__name__,
            "error": _safe_error(error),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
