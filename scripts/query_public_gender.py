"""Query one public Douyin profile's gender using API first, then visible icon color."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import collect_ip_comments as collector


def normalize_profile_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or parsed.hostname not in {"douyin.com", "www.douyin.com"}:
        raise ValueError("请提供 https://www.douyin.com/user/<sec_uid> 格式的抖音主页链接")
    parts = parsed.path.strip("/").split("/")
    if len(parts) != 2 or parts[0] != "user" or not parts[1]:
        raise ValueError("链接不是抖音用户主页，请使用 /user/<sec_uid> 链接")
    return f"https://www.douyin.com/user/{parts[1]}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="先查询抖音公开资料接口 user.gender；无明确值时用主页公开性别图标颜色兜底。"
    )
    parser.add_argument("profile_url", help="抖音用户主页链接，例如 https://www.douyin.com/user/<sec_uid>")
    parser.add_argument("--api-only", action="store_true", help="只查询资料接口，不启动 Playwright 浏览器兜底")
    args = parser.parse_args()

    profile_url = normalize_profile_url(args.profile_url)
    load_dotenv(PROJECT_ROOT / ".env")
    auth = collector.build_auth()

    result = {
        "status": "unknown",
        "profile_url": profile_url,
        "gender": "未知",
        "gender_code": None,
        "gender_public": False,
        "gender_source": "未知",
        "gender_confidence": "未知",
        "api_gender": "未知",
        "fallback_attempted": False,
        "profile_error": "",
    }
    try:
        profile = collector.fetch_public_profile(auth, profile_url)
        result.update(profile)
        result["api_gender"] = profile.get("gender", "未知")
    except Exception as exc:
        result.update(collector._api_error_profile(exc))
        result["api_gender"] = "查询失败"

    if result["gender"] not in ("男", "女") and not args.api_only:
        row = {**result, "profile_url": profile_url}
        result["fallback_attempted"] = True
        collector.enrich_visible_genders([row], auth.cookie_str or "")
        for key in (
            "gender", "gender_code", "gender_public", "gender_source",
            "gender_confidence", "gender_evidence", "profile_error",
        ):
            if key in row:
                result[key] = row[key]

    result["status"] = "identified" if result["gender"] in ("男", "女") else "unknown"
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
