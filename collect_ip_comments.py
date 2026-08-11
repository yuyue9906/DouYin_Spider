"""Collect Douyin comments with IP labels and optional public profile details."""

import argparse
import csv
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from builder.auth import DouyinAuth
from dy_apis.douyin_api import DouyinAPI


GENDER_NAMES = {0: "未知", 1: "男", 2: "女"}


def parse_args():
    parser = argparse.ArgumentParser(
        description="抓取视频评论区内带 IP 归属地的评论，并可补充评论者主页公开信息。"
    )
    parser.add_argument("url", help="抖音视频链接，支持 /video/<id> 或 modal_id=<id>")
    parser.add_argument("--region", default="", help="只保留包含此文字的 IP 归属地；默认不过滤")
    parser.add_argument("--include-replies", action="store_true", help="同时抓取所有二级回复")
    parser.add_argument("--profile", action="store_true", help="进入评论者主页补充公开地区、性别等字段")
    parser.add_argument(
        "--web-profile-fallback", action="store_true",
        help="接口性别未知时，再读取主页公开展示区域（较慢）",
    )
    parser.add_argument("--max-pages", type=int, default=0, help="一级评论最大页数；0 表示直到末页")
    parser.add_argument("--page-size", type=int, default=20, help="每页请求评论数，默认 20（实际数量由平台决定）")
    parser.add_argument("--candidate-limit", type=int, default=0, help="收集到多少条候选后停止分页；0 表示不限")
    parser.add_argument("--interval", type=float, default=0.8, help="主页请求间隔秒数，默认 0.8")
    parser.add_argument("--days", type=int, default=0, help="只保留最近多少天的评论；0 表示不限")
    parser.add_argument("--limit", type=int, default=0, help="最多输出多少条；0 表示不限")
    parser.add_argument(
        "--exclude-male", action="store_true", help="补充主页后排除公开性别为男的用户（保留女和未知）"
    )
    parser.add_argument(
        "--exclude-file", action="append", default=[],
        help="排除结果 JSON 中已有的评论 ID；可以重复提供多次",
    )
    parser.add_argument("--exclude-name-keywords", default="", help="排除昵称中含有的词，多个词用英文逗号分隔")
    parser.add_argument("--exclude-content-regex", default="", help="排除评论内容的正则表达式")
    parser.add_argument("--output", default="datas/ip_comments.json", help="输出 .json 或 .csv 文件")
    parser.add_argument("--handoff-output", default="", help="额外输出项目 20 MCP 可直接接收的批次 JSON")
    parser.add_argument("--command-id", default="", help="交接批次 ID；默认自动生成")
    return parser.parse_args()


def extract_aweme_id(url):
    match = re.search(r"/video/(\d+)", url) or re.search(r"[?&]modal_id=(\d+)", url)
    if not match:
        raise ValueError("链接中找不到视频 ID，请使用 /video/<数字> 或带 modal_id=<数字> 的链接")
    return match.group(1)


def ip_text(comment):
    values = (
        comment.get("ip_label"),
        comment.get("ip_location"),
        (comment.get("user") or {}).get("ip_location"),
    )
    return next((str(value).strip() for value in values if value), "")


def normalize_gender(value):
    if isinstance(value, str):
        stripped = value.strip()
        if stripped in ("男", "女"):
            return stripped
        if stripped.isdigit():
            value = int(stripped)
    return GENDER_NAMES.get(value, "未知")


def public_profile(user):
    """Return public fields only; do not infer hidden profile attributes."""
    return {
        "profile_ip": user.get("ip_location") or "未知",
        "gender": normalize_gender(user.get("gender")),
        "age": user.get("user_age") or "未知",
        "signature": user.get("signature") or "",
        "following_count": public_count(user, "following_count"),
        "follower_count": public_count(user, "follower_count"),
        "total_favorited": public_count(user, "total_favorited"),
        "aweme_count": public_count(user, "aweme_count"),
    }


def public_count(user, key):
    value = user.get(key)
    return int(value) if value is not None else "未知"


def comment_row(comment, level, parent_cid=""):
    user = comment.get("user") or {}
    sec_uid = user.get("sec_uid") or user.get("sec_user_id") or ""
    create_time = int(comment.get("create_time") or 0)
    return {
        "cid": comment.get("cid") or "",
        "parent_cid": parent_cid,
        "level": level,
        "aweme_id": comment.get("aweme_id") or "",
        "text": comment.get("text") or "",
        "create_time": create_time,
        "time_text": format_comment_time(create_time),
        "like_count": int(comment.get("digg_count") or 0),
        "reply_count": int(comment.get("reply_comment_total") or 0),
        "ip_location": ip_text(comment),
        "nickname": user.get("nickname") or "",
        "uid": user.get("uid") or "",
        "sec_uid": sec_uid,
        "profile_url": f"https://www.douyin.com/user/{sec_uid}" if sec_uid else "",
        "profile_ip": "",
        "gender": "",
        "gender_source": "",
        "age": "",
        "signature": "",
        "profile_error": "",
        "following_count": "未知",
        "follower_count": "未知",
        "total_favorited": "未知",
        "aweme_count": "未知",
    }


def format_comment_time(timestamp, now=None):
    if not timestamp:
        return "未知"
    china_tz = timezone(timedelta(hours=8))
    now = now or datetime.now(china_tz)
    created = datetime.fromtimestamp(timestamp, china_tz)
    days = max(0, (now.date() - created.date()).days)
    if days == 0:
        return "今天"
    if days == 1:
        return "一天前"
    if days == 3:
        return "三天前"
    if days == 7:
        return "一周前"
    if days < 7:
        return f"{days}天前"
    return f"{created.month}月{created.day}日"


def is_recent(row, days, now_timestamp=None):
    if not days:
        return True
    timestamp = row.get("create_time") or 0
    if not timestamp:
        return False
    now_timestamp = now_timestamp or time.time()
    return now_timestamp - timestamp <= days * 86400


def checked_comments(payload):
    if payload.get("status_code") not in (None, 0):
        raise RuntimeError(f"抖音接口返回错误：{payload}")
    return payload.get("comments") or []


def collect_comments(
    auth, url, region="", include_replies=False, max_pages=0, page_size=20,
    row_filter=None, candidate_limit=0,
):
    rows = []
    cursor = "0"
    page = 0
    while True:
        page += 1
        payload = DouyinAPI.get_work_out_comment(auth, url, cursor, count=str(page_size))
        comments = checked_comments(payload)
        for comment in comments:
            row = comment_row(comment, 1)
            if (row["ip_location"] and (not region or region in row["ip_location"])
                    and (row_filter is None or row_filter(row))):
                rows.append(row)
                if candidate_limit and len(rows) >= candidate_limit:
                    return rows, page

            if include_replies and int(comment.get("reply_comment_total") or 0) > 0:
                replies = DouyinAPI.get_work_all_inner_comment(auth, comment)
                for reply in replies:
                    reply_row = comment_row(reply, 2, row["cid"])
                    if (reply_row["ip_location"] and (not region or region in reply_row["ip_location"])
                            and (row_filter is None or row_filter(reply_row))):
                        rows.append(reply_row)
                        if candidate_limit and len(rows) >= candidate_limit:
                            return rows, page

        if not comments or payload.get("has_more") != 1 or (max_pages and page >= max_pages):
            return rows, page
        next_cursor = str(payload.get("cursor", "0"))
        if next_cursor == cursor:
            raise RuntimeError(f"评论游标未前进，已在第 {page} 页停止，避免无限循环")
        cursor = next_cursor


def enrich_profiles(auth, rows, interval=0.8):
    cache = {}
    for row in rows:
        sec_uid = row["sec_uid"]
        if not sec_uid:
            row["profile_error"] = "评论数据中没有 sec_uid"
            continue
        if sec_uid not in cache:
            try:
                payload = DouyinAPI.get_user_info(auth, row["profile_url"])
                if payload.get("status_code") not in (None, 0):
                    raise RuntimeError(f"status_code={payload.get('status_code')}")
                cache[sec_uid] = public_profile(payload.get("user") or {})
            except (KeyError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
                cache[sec_uid] = {"profile_error": str(exc)}
            if interval > 0:
                time.sleep(interval)
        row.update(cache[sec_uid])
    return len(cache)


def enrich_and_select_profiles(auth, rows, interval=0.8, exclude_male=False, limit=0):
    cache = {}
    selected = []
    for row in rows:
        sec_uid = row["sec_uid"]
        if not sec_uid:
            row["profile_error"] = "评论数据中没有 sec_uid"
        elif sec_uid not in cache:
            try:
                payload = DouyinAPI.get_user_info(auth, row["profile_url"])
                if payload.get("status_code") not in (None, 0):
                    raise RuntimeError(f"status_code={payload.get('status_code')}")
                cache[sec_uid] = public_profile(payload.get("user") or {})
            except (KeyError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
                cache[sec_uid] = {"profile_error": str(exc), "gender": "未知"}
            if interval > 0:
                time.sleep(interval)
        if sec_uid:
            row.update(cache[sec_uid])
        if exclude_male and row.get("gender") == "男":
            continue
        selected.append(row)
        if limit and len(selected) >= limit:
            break
    return selected, len(cache)


def save_rows(rows, output):
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".csv":
        fieldnames = list(comment_row({}, 1).keys())
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    elif path.suffix.lower() == ".json":
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        raise ValueError("--output 只支持 .json 或 .csv")
    return path.resolve()


def build_handoff_command(rows, video_url, aweme_id, command_id=""):
    """Build the canonical Project 17 -> Project 20 MCP command envelope."""
    command_id = str(command_id or f"dy-{aweme_id}-{uuid.uuid4().hex[:12]}").strip()
    targets = []
    seen = set()
    for row in rows:
        comment_id = str(row.get("comment_id") or row.get("cid") or "").strip()
        content = str(row.get("comment_text") or row.get("text") or "").strip()
        identity = (comment_id or content, str(row.get("nickname") or "").strip())
        if (not comment_id and not content) or identity in seen:
            continue
        seen.add(identity)
        targets.append({
            "comment_id": comment_id,
            "author_nickname": str(row.get("author_nickname") or row.get("nickname") or "").strip(),
            "comment_text": content,
            "sec_uid": str(row.get("sec_uid") or "").strip(),
            "user_id": str(row.get("user_id") or row.get("uid") or "").strip(),
            "author_url": str(row.get("author_url") or row.get("profile_url") or "").strip(),
            "ip_location": str(row.get("ip_location") or row.get("ip_label") or "").strip(),
            "published_at": row.get("published_at") or row.get("create_time") or "",
            "parent_comment_id": str(row.get("parent_comment_id") or row.get("parent_cid") or "").strip(),
        })
    return {
        "schema_version": "douyin.comment-forward.v1",
        "command_id": command_id,
        "video_url": video_url,
        "aweme_id": str(aweme_id),
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "targets": targets,
    }


def save_handoff_command(command, output):
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(command, ensure_ascii=False, indent=2), encoding="utf-8")
    return path.resolve()


def load_excluded_cids(paths):
    result = set()
    for path in paths or []:
        rows = json.loads(Path(path).read_text(encoding="utf-8"))
        result.update(str(row.get("cid")) for row in rows if row.get("cid"))
    return result


def browser_cookies(cookie_string):
    result = []
    for part in cookie_string.split(";"):
        name, separator, value = part.strip().partition("=")
        if separator and name:
            result.append({"name": name, "value": value, "domain": ".douyin.com", "path": "/"})
    return result


def enrich_visible_genders(rows, cookie_string, wait_ms=2500):
    """Read only an explicitly displayed 男/女 label from the profile header."""
    checked = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        context.add_cookies(browser_cookies(cookie_string))
        page = context.new_page()
        for row in rows:
            if row.get("gender") != "未知" or not row.get("profile_url"):
                continue
            try:
                page.goto(row["profile_url"], wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(wait_ms)
                header = page.locator("#user_detail_element")
                visible_text = header.inner_text(timeout=5000) if header.count() else ""
                labels = {line.strip() for line in visible_text.splitlines()}
                if "女" in labels:
                    row["gender"] = "女"
                    row["gender_source"] = "主页公开展示"
                elif "男" in labels:
                    row["gender"] = "男"
                    row["gender_source"] = "主页公开展示"
                else:
                    row["gender_source"] = "主页未公开"
                checked += 1
            except PlaywrightError as exc:
                row["profile_error"] = (row.get("profile_error") + "; " + str(exc)).strip("; ")
        browser.close()
    return checked


def build_auth():
    load_dotenv()
    cookie = os.getenv("DY_COOKIES", "").strip().strip("'\"")
    if not cookie:
        raise RuntimeError("缺少 DY_COOKIES，请先在 .env 中填入已登录 www.douyin.com 的 Cookie")
    auth = DouyinAuth()
    auth.perepare_auth(cookie)
    auth.ticket = os.getenv("DY_TICKET") or None
    auth.ts_sign = os.getenv("DY_TS_SIGN") or None
    auth.client_cert = os.getenv("DY_CLIENT_CERT") or None
    auth.private_key = os.getenv("DY_PRIVATE_KEY") or None
    return auth


def main():
    args = parse_args()
    aweme_id = extract_aweme_id(args.url)
    url = f"https://www.douyin.com/video/{aweme_id}"
    auth = build_auth()
    excluded_cids = load_excluded_cids(args.exclude_file)
    excluded_words = [word.strip() for word in args.exclude_name_keywords.split(",") if word.strip()]
    content_pattern = re.compile(args.exclude_content_regex, re.IGNORECASE) if args.exclude_content_regex else None

    def row_filter(row):
        return (
            is_recent(row, args.days)
            and str(row.get("cid")) not in excluded_cids
            and not any(word in row.get("nickname", "") for word in excluded_words)
            and not (content_pattern and content_pattern.search(row.get("text", "")))
        )

    rows, pages = collect_comments(
        auth, url, region=args.region, include_replies=args.include_replies,
        max_pages=args.max_pages, page_size=args.page_size,
        row_filter=row_filter, candidate_limit=args.candidate_limit,
    )
    rows = [row for row in rows if is_recent(row, args.days)]
    rows = [row for row in rows if str(row.get("cid")) not in excluded_cids]
    if excluded_words:
        rows = [row for row in rows if not any(word in row.get("nickname", "") for word in excluded_words)]
    if content_pattern:
        rows = [row for row in rows if not content_pattern.search(row.get("text", ""))]
    rows.sort(key=lambda row: int(row.get("create_time") or 0), reverse=True)
    if args.exclude_male and not args.profile:
        raise ValueError("--exclude-male 必须和 --profile 一起使用")
    if args.profile:
        profile_limit = args.limit + 10 if args.web_profile_fallback and args.limit else args.limit
        rows, profile_count = enrich_and_select_profiles(
            auth, rows, args.interval, exclude_male=args.exclude_male, limit=profile_limit
        )
    else:
        profile_count = 0
        if args.limit:
            rows = rows[:args.limit]
    if args.web_profile_fallback:
        if not args.profile:
            raise ValueError("--web-profile-fallback 必须和 --profile 一起使用")
        load_dotenv()
        cookie_string = os.getenv("DY_COOKIES", "").strip().strip("'\"")
        enrich_visible_genders(rows, cookie_string)
        if args.exclude_male:
            rows = [row for row in rows if row.get("gender") != "男"]
        if args.limit:
            rows = rows[:args.limit]
    output = save_rows(rows, args.output)
    handoff = None
    command_id = ""
    if args.handoff_output:
        command = build_handoff_command(rows, url, aweme_id, args.command_id)
        handoff = save_handoff_command(command, args.handoff_output)
        command_id = command["command_id"]
    print(json.dumps({
        "ok": True,
        "aweme_id": aweme_id,
        "pages": pages,
        "comments": len(rows),
        "profiles": profile_count,
        "output": str(output),
        "command_id": command_id,
        "handoff_output": str(handoff) if handoff else "",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, KeyError, json.JSONDecodeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        sys.exit(1)
