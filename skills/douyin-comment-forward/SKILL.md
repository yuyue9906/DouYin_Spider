---
name: douyin-comment-forward
description: Forward one precisely identified Douyin video comment to a named recipient through the project's direct IM API. Use when the user asks to share or forward a comment, not for browser-click automation.
---

# Douyin comment forwarding via API

Use this skill for a single, user-requested comment share through
`imapi.douyin.com/v1/message/send` (`SHARE_COMMENT`). The CLI is
`forward_comment_api.py`; the importable function is
`comment_share.forward_comment_share`.

## Required details

Before a real send, make sure the request identifies:

1. The Douyin video URL or numeric aweme ID.
2. One target comment, preferably by comment ID. If no ID is available, use
   the complete comment text and require a unique exact match.
3. The recipient's exact Douyin nickname.

Ask only for missing details. Never infer the recipient from an unrelated
conversation or pick among duplicate names. If exact-name search returns more
than one account, show the returned nicknames and UIDs and ask the user which
one to use. The user can then supply `--recipient-uid` or a Douyin profile link
with `--recipient-url`; the profile's nickname must still match the requested name.

For a reply rather than a top-level comment, also require the parent top-level
comment ID and pass `--parent-comment-id`. If name/comment resolution is
ambiguous, or a text-only scan reaches its page limit, stop and ask for a
comment ID, recipient UID, or larger page limit as appropriate.

## Run

From the project root, first run without `--send` to resolve and preview. This
performs read-only lookups and does not create a conversation or send a message:

```powershell
python forward_comment_api.py --video "<video URL or aweme ID>" --comment-id "<comment ID>" --recipient "<Douyin nickname>"
```

For a unique exact-text match instead:

```powershell
python forward_comment_api.py --video "<video URL or aweme ID>" --comment-text "<complete comment text>" --recipient "<Douyin nickname>"
```

When the user explicitly requested the forward, the preview identifies the
resolved video, comment, and recipient. Then run the same command with
`--send` to create/get the IM conversation and send once. Do not require an
extra confirmation when the user already clearly asked for the send.

Do not retry after a timeout or unclear send response: the server may have
accepted the message even if the client did not receive its response. Report
the result as unconfirmed and let the user inspect the Douyin conversation.
`SEND_SUCCEED` means the IM server accepted the message; it does not prove that
the recipient opened or read it.

## Optional cover supplied by the user

If the video detail endpoint fails but the user provides that video's cover
from DevTools, save only the cover metadata in a local JSON file:

```json
{
  "video_id": "<the same numeric video ID>",
  "cover": {
    "uri": "<cover URI>",
    "url_list": ["https://<Douyin image CDN>/<complete signed image URL>"]
  }
}
```

Add `--cover-file "<path>"` to the normal preview and send commands. The Python
entry point accepts the same object as `cover_data`. The video ID must match;
URLs must be complete HTTPS URLs on `douyinpic.com` or its subdomains. This
skips only the video detail lookup; comment and recipient resolution still run.
Do not invent a cover URL from its URI or treat a collapsed `url_list: [...]`
as complete data. Keep supplied cover metadata associated with its video ID.

## Credentials and output

The command reads `DY_COOKIES`, `DY_TICKET`, `DY_TS_SIGN`, `DY_CLIENT_CERT`,
and `DY_PRIVATE_KEY` from the project `.env`. Never request these values in
chat, print them, or save them to result files. The CLI emits a small JSON
result containing the resolved IDs and message status. It connects directly by
default; add `--use-env-proxy` only when the user's network requires the system
HTTP proxy.
