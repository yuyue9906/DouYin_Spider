# Codex Agent project instructions

## Forward a Douyin comment through the IM API

When a user asks to forward/share a Douyin comment to someone, follow
[`skills/douyin-comment-forward/SKILL.md`](skills/douyin-comment-forward/SKILL.md)
and use `forward_comment_api.py`. This is the direct IM API path; do not use
`forward_target_comments.py`, which automates browser clicks.

The request must identify all three: the video, the target comment, and the
recipient's Douyin nickname. Prefer a video URL/aweme ID and comment ID; exact
comment text is accepted when the ID is unavailable. If the request omits any
of these, ask only for the missing detail. If the recipient nickname matches
multiple accounts, ask the user to choose using the candidate UID or profile link.
If the comment text is ambiguous or the scan is incomplete, ask for the comment
ID or a larger scan range. Never infer a recipient or choose an ambiguous
comment.

The CLI only resolves and previews by default. Add `--send` only when the user
has clearly requested the actual forward and all required identities are
resolved. Do not ask for a second confirmation after such an explicit request.
For a reply comment, also require its parent top-level comment ID. Never retry
an IM send after an ambiguous timeout or server response; report that delivery
is unconfirmed to avoid duplicate messages.

Read credentials only from the project's `.env`. Never ask the user to paste
cookies, tickets, signing values, or private keys into chat, and never print
or include them in output. Treat `SEND_SUCCEED` as server acceptance, not proof
that the recipient has read the message.
