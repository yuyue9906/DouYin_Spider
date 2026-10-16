"""Resolve and send one Douyin comment share through the web IM API."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from dy_apis.douyin_api import DouyinAPI


class CommentShareError(RuntimeError):
    """Expected validation or resolution failure in the comment-share flow."""


def _normalize(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _video_id(value: str) -> str:
    value = str(value or "").strip()
    match = re.search(r"(?:modal_id=|/video/)(\d+)", value)
    if match:
        return match.group(1)
    if value.isdigit():
        return value
    raise CommentShareError("视频参数应为抖音视频链接、modal_id 链接或纯数字作品 ID。")


def _as_list(payload: dict, field: str) -> list:
    value = payload.get(field)
    if value is None:
        return []
    if not isinstance(value, list):
        raise CommentShareError(f"抖音接口返回的 {field} 字段格式异常。")
    return value


def _comment_matches(comment: dict, comment_id: str, comment_text: str) -> bool:
    if comment_id and str(comment.get("cid", "")) != comment_id:
        return False
    if comment_text and _normalize(comment.get("text")) != _normalize(comment_text):
        return False
    return True


def _comment_summary(comment: dict) -> dict:
    user = comment.get("user") or {}
    return {
        "comment_id": str(comment.get("cid", "")),
        "text": str(comment.get("text", "")),
        "author": str(user.get("nickname", "")),
    }


def _paginate_comments(fetch_page, comment_id: str, comment_text: str, max_pages: int,
                       stop_on_id: bool) -> tuple[list, bool]:
    matches = []
    cursor = "0"
    complete = False
    for _ in range(max_pages):
        payload = fetch_page(cursor)
        if not isinstance(payload, dict):
            raise CommentShareError("抖音评论接口返回了无法识别的数据。")
        status_code = payload.get("status_code", 0)
        if status_code not in (0, "0", None):
            raise CommentShareError(f"抖音评论接口返回异常状态：{status_code}。")
        comments = _as_list(payload, "comments")
        matches.extend(
            comment for comment in comments
            if isinstance(comment, dict) and _comment_matches(comment, comment_id, comment_text)
        )
        if stop_on_id and matches:
            return matches, True
        if payload.get("has_more") not in (1, "1", True):
            complete = True
            break
        next_cursor = str(payload.get("cursor", ""))
        if not next_cursor or next_cursor == cursor:
            raise CommentShareError("评论分页游标没有前进，已停止以避免重复请求。")
        cursor = next_cursor
    return matches, complete


def _find_comment(auth, video_url: str, comment_id: str, comment_text: str,
                  parent_comment_id: str, max_pages: int, session) -> dict:
    if parent_comment_id:
        parent_matches, _ = _paginate_comments(
            lambda cursor: DouyinAPI.get_work_out_comment(
                auth, video_url, cursor, count="20", session=session
            ),
            parent_comment_id, "", max_pages, stop_on_id=True,
        )
        if not parent_matches:
            raise CommentShareError(
                f"在前 {max_pages} 页一级评论中没有找到父评论 ID {parent_comment_id}。"
            )
        parent = parent_matches[0]
        matches, complete = _paginate_comments(
            lambda cursor: DouyinAPI.get_work_inner_comment(
                auth, parent, cursor, count="20", session=session
            ),
            comment_id, comment_text, max_pages, stop_on_id=bool(comment_id),
        )
    else:
        matches, complete = _paginate_comments(
            lambda cursor: DouyinAPI.get_work_out_comment(
                auth, video_url, cursor, count="20", session=session
            ),
            comment_id, comment_text, max_pages, stop_on_id=bool(comment_id),
        )

    if not complete and not comment_id:
        raise CommentShareError(
            f"扫描达到 {max_pages} 页上限，无法确认评论文本是否唯一；请提供评论 ID，或提高 --max-pages。"
        )
    if not matches:
        kind = "回复评论" if parent_comment_id else "一级评论"
        raise CommentShareError(
            f"没有找到匹配的{kind}。请确认视频和评论内容/ID；回复评论还需提供 --parent-comment-id。"
        )
    if len(matches) > 1:
        choices = "；".join(
            f"ID={item.get('cid', '')}，作者={((item.get('user') or {}).get('nickname', ''))}"
            for item in matches[:5]
        )
        raise CommentShareError(f"评论文本匹配到多条记录，请改用评论 ID。候选：{choices}")

    comment = matches[0]
    if comment.get("can_share") is False:
        raise CommentShareError("该评论当前不允许分享，已停止。")
    return comment


def _user_payload(row: dict) -> dict:
    for key in ("user_info", "user", "author"):
        nested = row.get(key)
        if isinstance(nested, dict) and any(
            field in nested for field in ("nickname", "uid", "user_id", "sec_uid", "sec_user_id")
        ):
            merged = dict(row)
            merged.update(nested)
            return merged
    return row


def _resolve_recipient(auth, recipient_name: str, recipient_uid: str, recipient_url: str, session) -> dict:
    if recipient_url:
        parsed_url = urlparse(recipient_url)
        if (
            parsed_url.scheme != "https"
            or parsed_url.hostname not in ("douyin.com", "www.douyin.com")
            or re.fullmatch(r"/user/[^/]+/?", parsed_url.path) is None
        ):
            raise CommentShareError("收件人主页链接必须是 www.douyin.com/user/<sec_uid>。")
        payload = DouyinAPI.get_user_info(auth, recipient_url, session=session)
        user = payload.get("user") or {}
        nickname = str(user.get("nickname") or "")
        uid = str(user.get("uid") or user.get("user_id") or "")
        if not uid or _normalize(nickname) != _normalize(recipient_name):
            raise CommentShareError("收件人主页的昵称与指定名字不一致，或无法读取账号 UID。")
        if recipient_uid and uid != recipient_uid:
            raise CommentShareError("收件人主页解析出的 UID 与指定 UID 不一致。")
        return {"nickname": nickname, "uid": uid}

    exact = []
    seen_uids = set()
    offset = 0
    page_size = 50
    search_complete = False
    for _ in range(4):
        payload = DouyinAPI.search_user(
            auth, recipient_name, offset=str(offset), num=str(page_size), session=session
        )
        rows = _as_list(payload, "user_list")
        for row in rows:
            if not isinstance(row, dict):
                continue
            user = _user_payload(row)
            nickname = str(user.get("nickname") or user.get("name") or "")
            uid = str(user.get("uid") or user.get("user_id") or row.get("uid") or "")
            if _normalize(nickname) != _normalize(recipient_name) or not uid:
                continue
            if recipient_uid and uid != recipient_uid:
                continue
            if uid in seen_uids:
                continue
            seen_uids.add(uid)
            exact.append({"nickname": nickname, "uid": uid})
        if recipient_uid and exact:
            search_complete = True
            break
        if payload.get("has_more") not in (1, "1", True):
            search_complete = True
            break
        if not rows:
            raise CommentShareError("收件人搜索分页未返回用户，但接口仍报告有下一页；已停止。")
        offset += page_size

    if not exact:
        suffix = f"（指定 UID {recipient_uid} 也必须与该名字匹配）" if recipient_uid else ""
        if not search_complete:
            raise CommentShareError(
                "收件人搜索达到 200 条上限，仍有下一页；请补充准确 UID 或主页链接。"
            )
        raise CommentShareError(
            f"用户搜索中没有找到昵称完全匹配“{recipient_name}”的账号{suffix}。"
            "请提供准确昵称，或补充该账号的抖音主页链接/UID 以便消歧。"
        )
    if len(exact) > 1:
        choices = "；".join(f"{item['nickname']}（UID {item['uid']}）" for item in exact[:8])
        raise CommentShareError(
            f"收件人昵称“{recipient_name}”对应多个账号，请用户选择一个并提供 UID：{choices}"
        )
    if not search_complete:
        raise CommentShareError(
            "收件人搜索达到 200 条上限，仍有下一页；不能证明昵称唯一，请提供准确 UID。"
        )
    return exact[0]


def _find_cover(auth, video_url: str, session) -> dict:
    payload = DouyinAPI.get_work_info(auth, video_url, session=session)
    detail = payload.get("aweme_detail") or payload.get("awemeDetail") or payload
    video = detail.get("video") or {}
    for key in ("cover", "origin_cover", "dynamic_cover"):
        media = video.get(key)
        if isinstance(media, dict) and media.get("url_list"):
            return media
    media = detail.get("cover_url")
    if isinstance(media, dict) and media.get("url_list"):
        return media
    raise CommentShareError("读取到视频详情，但没有拿到分享卡片所需的封面信息。")


def _provided_cover(cover_data: dict, aweme_id: str) -> dict:
    """Validate a user-supplied cover explicitly bound to the selected video."""
    if not isinstance(cover_data, dict) or str(cover_data.get("video_id", "")) != aweme_id:
        raise CommentShareError("封面文件的 video_id 必须与本次转发的视频 ID 一致。")
    cover = cover_data.get("cover")
    if not isinstance(cover, dict):
        raise CommentShareError("封面文件缺少 cover 对象。")
    uri = cover.get("uri")
    urls = cover.get("url_list")
    if not isinstance(uri, str) or not uri.strip() or not isinstance(urls, list) or not urls:
        raise CommentShareError("封面必须包含非空 uri 和完整的 url_list。")
    for url in urls:
        if not isinstance(url, str):
            raise CommentShareError("封面 url_list 中的每一项都必须是完整的 HTTPS 图片地址。")
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or not (host == "douyinpic.com" or host.endswith(".douyinpic.com"))):
            raise CommentShareError("手动提供的封面地址必须来自抖音 douyinpic.com 图片域名，并使用 HTTPS。")
    return dict(cover, url_list=list(urls))


def _validate_send_auth(auth) -> None:
    if not auth.cookie or not auth.cookie.get("s_v_web_id"):
        raise CommentShareError("登录 Cookie 不完整，请在项目 .env 中更新 DY_COOKIES。")
    missing = [
        name for name, value in (
            ("DY_TICKET", auth.ticket),
            ("DY_TS_SIGN", auth.ts_sign),
            ("DY_CLIENT_CERT", auth.client_cert),
            ("DY_PRIVATE_KEY", auth.private_key),
        ) if not value
    ]
    if missing:
        raise CommentShareError("发送评论分享需要 .env 中的登录签名项：" + ", ".join(missing))


def forward_comment_share(auth, video: str, recipient_name: str, *, comment_id: str = "",
                          comment_text: str = "", recipient_uid: str = "", recipient_url: str = "",
                          parent_comment_id: str = "", max_pages: int = 30,
                          send: bool = False, session=None, cover_data: dict | None = None) -> dict:
    """Resolve one exact comment and recipient; send only when ``send=True``.

    ``video`` accepts a Douyin video URL, a modal_id URL, or a numeric aweme ID.
    Replies require their top-level ``parent_comment_id``. This function never
    retries a message send because a timeout can occur after server acceptance.
    ``cover_data`` may contain a matching ``video_id`` and a user-provided
    ``cover`` object when video detail lookup is unavailable.
    """
    if not recipient_name or not recipient_name.strip():
        raise CommentShareError("必须提供收件人名字。")
    if not comment_id and not comment_text:
        raise CommentShareError("必须提供目标评论 ID 或完整评论内容。")
    if max_pages < 1:
        raise CommentShareError("max_pages 必须大于 0。")
    if send:
        _validate_send_auth(auth)

    aweme_id = _video_id(video)
    provided_cover = _provided_cover(cover_data, aweme_id) if cover_data is not None else None
    video_url = f"https://www.douyin.com/video/{aweme_id}"
    recipient = _resolve_recipient(
        auth, recipient_name.strip(), str(recipient_uid or "").strip(),
        str(recipient_url or "").strip(), session,
    )
    comment = _find_comment(
        auth, video_url, str(comment_id or "").strip(), str(comment_text or ""),
        str(parent_comment_id or "").strip(), max_pages, session,
    )
    cover = provided_cover if provided_cover is not None else _find_cover(auth, video_url, session)
    summary = {
        "ok": True,
        "mode": "send" if send else "dry_run",
        "video_id": aweme_id,
        "comment": _comment_summary(comment),
        "recipient": recipient,
        "cover_source": "provided" if provided_cover is not None else "video_detail",
        "sent": False,
        "send_attempted": False,
    }
    if not send:
        summary["message"] = "已完成目标解析；未创建会话，也未发送。加 --send 才会执行真实转发。"
        return summary

    conversation_id, conversation_short_id, ticket = DouyinAPI.create_conversation(
        auth, int(recipient["uid"]), session=session,
    )
    summary["send_attempted"] = True
    try:
        send_result = DouyinAPI.send_comment_share(
            auth, conversation_id, conversation_short_id, ticket, comment, cover, session=session,
        )
    except Exception as error:
        summary["ok"] = False
        summary["sent"] = "unknown"
        summary["message"] = "发送请求已发起，但结果未能确认；先检查抖音私信，不要直接重试。"
        summary["error_type"] = type(error).__name__
        return summary
    summary["sent"] = bool(send_result.get("ok"))
    summary["ok"] = bool(send_result.get("ok"))
    summary["send_result"] = {
        key: send_result.get(key)
        for key in ("http_status", "message", "status", "status_name", "check_code", "check_message")
    }
    summary["message"] = (
        "抖音 IM 已接受评论转发请求。"
        if summary["sent"] else "抖音 IM 未确认发送成功；不要直接重试，以免产生重复消息。"
    )
    return summary
