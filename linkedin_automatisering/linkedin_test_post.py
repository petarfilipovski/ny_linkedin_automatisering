"""
Gör ett riktigt LinkedIn-anrop (REST /rest/posts, sedan v2/ugcPosts) utifrån linkedin_automatisering/.env.

Kör från projektroten:
  python linkedin_automatisering/linkedin_test_post.py
  python linkedin_automatisering/linkedin_test_post.py --text "Min text" --url https://example.com
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

from linkedin_post import post_to_linkedin

_ENV = Path(__file__).resolve().parent / ".env"
load_dotenv(_ENV)

TOKEN = (os.getenv("LINKEDIN_ACCESS_TOKEN") or "").strip()
PERSON_URN = (os.getenv("LINKEDIN_PERSON_URN") or "").strip()
ORG_ID = (os.getenv("LINKEDIN_ORGANIZATION_ID") or "").strip()
AUTHOR_URN = (os.getenv("LINKEDIN_POST_AUTHOR_URN") or "").strip()
API_VERSION = (os.getenv("LINKEDIN_API_VERSION") or "202506").strip()


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


def main() -> None:
    p = argparse.ArgumentParser(description="Testa LinkedIn-publicering från .env")
    p.add_argument(
        "--text",
        default="Testpost från ny_linkedin_automatisering (kan raderas).",
        help="Inläggstext",
    )
    p.add_argument("--url", default="", help="URL för artikelkort (content.article)")
    args = p.parse_args()

    if not TOKEN:
        print("Saknar LINKEDIN_ACCESS_TOKEN i .env", file=sys.stderr)
        sys.exit(1)

    author = _author_urn()
    resp = post_to_linkedin(
        args.text,
        url=args.url or None,
        access_token=TOKEN,
        author_urn=author,
        api_version=API_VERSION,
    )
    if resp.status_code in (200, 201):
        rid = resp.headers.get("x-restli-id", "")
        print(f"OK ({resp.status_code})")
        if rid:
            print(f"x-restli-id: {rid}")
        return
    print(f"Misslyckades ({resp.status_code}): {resp.text}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
