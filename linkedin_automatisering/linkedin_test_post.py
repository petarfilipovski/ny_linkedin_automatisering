"""
Gör ett riktigt LinkedIn-anrop (REST /rest/posts, sedan v2/ugcPosts) utifrån linkedin_automatisering/.env.

Kör från projektroten:
  python linkedin_automatisering/linkedin_test_post.py
  python linkedin_automatisering/linkedin_test_post.py --text "Min text" --url https://example.com
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

_ENV = Path(__file__).resolve().parent / ".env"
load_dotenv(_ENV)

TOKEN = (os.getenv("LINKEDIN_ACCESS_TOKEN") or "").strip()
PERSON_URN = (os.getenv("LINKEDIN_PERSON_URN") or "").strip()
ORG_ID = (os.getenv("LINKEDIN_ORGANIZATION_ID") or "").strip()
AUTHOR_URN = (os.getenv("LINKEDIN_POST_AUTHOR_URN") or "").strip()
API_VERSION = (os.getenv("LINKEDIN_API_VERSION") or "202506").strip()


def _normalize_article_url(url: str | None) -> str | None:
    u = (url or "").strip()
    if not u:
        return None
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    return u


def _author_urn() -> str:
    if AUTHOR_URN:
        return AUTHOR_URN
    if ORG_ID.isdigit():
        return f"urn:li:organization:{ORG_ID}"
    if PERSON_URN:
        return PERSON_URN
    r = requests.get(
        "https://api.linkedin.com/v2/userinfo",
        headers={"Authorization": f"Bearer {TOKEN}"},
        timeout=30,
    )
    if r.status_code != 200:
        print(f"userinfo {r.status_code}: {r.text}", file=sys.stderr)
        sys.exit(1)
    sub = (r.json() or {}).get("sub")
    if not sub:
        print("userinfo saknar sub — lägg LINKEDIN_PERSON_URN eller openid-scope på token.", file=sys.stderr)
        sys.exit(1)
    return f"urn:li:person:{sub}"


def _rest_payload(author: str, commentary: str, article_url: str | None) -> dict:
    payload: dict = {
        "author": author,
        "commentary": commentary,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    if article_url:
        host = (urlparse(article_url).netloc or "länk").replace("www.", "")[:200]
        payload["content"] = {"article": {"source": article_url, "title": host or "Länk"}}
    return payload


def _ugc_payload(author: str, text: str, article_url: str | None) -> dict:
    share: dict = {"shareCommentary": {"text": text}, "shareMediaCategory": "NONE"}
    if article_url:
        share["shareMediaCategory"] = "ARTICLE"
        share["media"] = [{"status": "READY", "originalUrl": article_url}]
    return {
        "author": author,
        "lifecycleState": "PUBLISHED",
        "specificContent": {"com.linkedin.ugc.ShareContent": share},
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Testa LinkedIn-publicering från .env")
    p.add_argument(
        "--text",
        default="Testpost från ny_linkedin_automatisering (kan raderas).",
        help="Inläggstext",
    )
    p.add_argument("--url", default="", help="Valfri artikel-/webbadress")
    args = p.parse_args()

    if not TOKEN:
        print("Saknar LINKEDIN_ACCESS_TOKEN i .env", file=sys.stderr)
        sys.exit(1)

    author = _author_urn()
    link = _normalize_article_url(args.url or None)

    headers_rest = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
        "Linkedin-Version": API_VERSION,
    }
    rest_resp: requests.Response | None = None
    try:
        rest_resp = requests.post(
            "https://api.linkedin.com/rest/posts",
            headers=headers_rest,
            json=_rest_payload(author, args.text, link),
            timeout=60,
        )
    except requests.RequestException as e:
        print(f"REST anrop misslyckades: {e}", file=sys.stderr)
        rest_resp = None

    if rest_resp is not None and rest_resp.status_code in (200, 201):
        rid = rest_resp.headers.get("x-restli-id", "")
        print(f"OK via REST /rest/posts ({rest_resp.status_code})")
        if rid:
            print(f"x-restli-id: {rid}")
        return

    if rest_resp is not None:
        print(f"REST svarade {rest_resp.status_code}, provar UGC…\n{rest_resp.text[:800]}", file=sys.stderr)

    ugc = requests.post(
        "https://api.linkedin.com/v2/ugcPosts",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
        },
        json=_ugc_payload(author, args.text, link),
        timeout=60,
    )
    if ugc.status_code in (200, 201):
        print(f"OK via v2/ugcPosts ({ugc.status_code})")
        return
    print(f"UGC misslyckades ({ugc.status_code}): {ugc.text}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
