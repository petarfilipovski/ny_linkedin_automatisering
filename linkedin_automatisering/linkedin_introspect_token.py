"""
Visa om access token är aktiv och vilka scopes den har (inkl. w_organization_social).

Kör: python linkedin_automatisering/linkedin_introspect_token.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

CLIENT_ID = (os.getenv("LINKEDIN_CLIENT_ID") or "").strip()
CLIENT_SECRET = (os.getenv("LINKEDIN_CLIENT_SECRET") or "").strip()
TOKEN = (os.getenv("LINKEDIN_ACCESS_TOKEN") or "").strip()


def main() -> None:
    if not all([CLIENT_ID, CLIENT_SECRET, TOKEN]):
        print("Saknar LINKEDIN_CLIENT_ID, LINKEDIN_CLIENT_SECRET eller LINKEDIN_ACCESS_TOKEN i .env", file=sys.stderr)
        sys.exit(1)
    r = requests.post(
        "https://www.linkedin.com/oauth/v2/introspectToken",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "token": TOKEN,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if r.status_code != 200:
        print(f"Introspect {r.status_code}: {r.text}", file=sys.stderr)
        sys.exit(1)
    data = r.json()
    print(json.dumps(data, indent=2, ensure_ascii=False))
    scopes = (data.get("scope") or "").replace(" ", "").split(",")
    scopes = [s for s in scopes if s]
    print("\n--- Koll för org-poster ---")
    if "w_organization_social" in scopes:
        print("OK: token har w_organization_social")
    else:
        print("SAKNAS: w_organization_social — kör linkedin_oauth_exchange.py igen och godkänn alla scopes.")

    print("\n--- API-test ---")
    ver = (os.getenv("LINKEDIN_API_VERSION") or "202506").strip()
    org_id = (os.getenv("LINKEDIN_ORGANIZATION_ID") or "").strip()
    api_ok = False
    if org_id.isdigit():
        r_org = requests.get(
            f"https://api.linkedin.com/rest/organizations/{org_id}",
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "X-Restli-Protocol-Version": "2.0.0",
                "Linkedin-Version": ver,
            },
            timeout=30,
        )
        if r_org.status_code == 200:
            api_ok = True
            print(f"OK: token fungerar (organisations-API, id {org_id})")
        else:
            print(f"Organisations-API: {r_org.status_code} — {r_org.text[:120]}")
    if not api_ok:
        r_ui = requests.get(
            "https://api.linkedin.com/v2/userinfo",
            headers={"Authorization": f"Bearer {TOKEN}"},
            timeout=30,
        )
        if r_ui.status_code == 200:
            api_ok = True
            print("OK: token fungerar (userinfo)")
    if not api_ok:
        print(
            "VARNING: token avvisas av api.linkedin.com — kör linkedin_oauth_exchange.py igen "
            "och klistra bara authorization code i terminalen (inte i .env)."
        )


if __name__ == "__main__":
    main()
