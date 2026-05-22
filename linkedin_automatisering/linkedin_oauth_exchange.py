"""
Hämta LINKEDIN_ACCESS_TOKEN med Client ID + Client Secret (OAuth 2.0 authorization code).

1. Lägg LINKEDIN_CLIENT_ID, LINKEDIN_CLIENT_SECRET och LINKEDIN_REDIRECT_URI i .env
   (redirect-URI måste vara identisk med den du registrerat i LinkedIn Developer-appen).
2. Kör: python linkedin_automatisering/linkedin_oauth_exchange.py
3. Öppna länken, logga in, kopiera redirect-URL:en (eller bara ?code=...-värdet).
4. Klistra in i terminalen (INTE i .env) — skriptet sparar access_token i .env åt dig.
"""
from __future__ import annotations

import os
import sys
import urllib.parse
from pathlib import Path

import requests
from dotenv import load_dotenv

_ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(_ENV_PATH)

CLIENT_ID = (os.getenv("LINKEDIN_CLIENT_ID") or "").strip()
CLIENT_SECRET = (os.getenv("LINKEDIN_CLIENT_SECRET") or "").strip()
REDIRECT_URI = (
    os.getenv("LINKEDIN_REDIRECT_URI") or "http://localhost:8765/linkedin-callback"
).strip()

# Organisationssida (du behöver inte w_member_social om du bara postar som företag)
SCOPES = "openid profile w_organization_social r_organization_social"


def _write_access_token_to_env(token: str) -> None:
    key = "LINKEDIN_ACCESS_TOKEN"
    lines: list[str] = []
    if _ENV_PATH.exists():
        lines = _ENV_PATH.read_text(encoding="utf-8").splitlines()
    found = False
    out: list[str] = []
    for line in lines:
        if line.strip().startswith(f"{key}="):
            out.append(f"{key}={token}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={token}")
    _ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")


def _verify_api_token(token: str) -> bool:
    """Kontrollera att token fungerar mot api.linkedin.com (inte bara introspect)."""
    org_id = (os.getenv("LINKEDIN_ORGANIZATION_ID") or "").strip()
    ver = (os.getenv("LINKEDIN_API_VERSION") or "202506").strip()
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Restli-Protocol-Version": "2.0.0",
        "Linkedin-Version": ver,
    }
    if org_id.isdigit():
        url = f"https://api.linkedin.com/rest/organizations/{org_id}"
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code == 200:
            return True
    r = requests.get(
        "https://api.linkedin.com/v2/userinfo",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    return r.status_code == 200


def _parse_code(pasted: str) -> str:
    s = pasted.strip().strip('"').strip("'")
    if "code=" in s:
        parsed = urllib.parse.urlparse(s)
        if parsed.query:
            qs = urllib.parse.parse_qs(parsed.query)
            codes = qs.get("code", [])
            if codes:
                return codes[0]
        m = urllib.parse.parse_qs(s.split("?", 1)[-1])
        if m.get("code"):
            return m["code"][0]
    return s


def main() -> None:
    if not CLIENT_ID or not CLIENT_SECRET:
        print(
            "Saknar LINKEDIN_CLIENT_ID eller LINKEDIN_CLIENT_SECRET i linkedin_automatisering/.env",
            file=sys.stderr,
        )
        sys.exit(1)

    auth_params = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPES,
            "state": "oauth_cli",
        }
    )
    auth_url = f"https://www.linkedin.com/oauth/v2/authorization?{auth_params}"

    print("--- Steg A: öppna denna URL i webbläsaren (inloggad på LinkedIn) ---\n")
    print(auth_url)
    print(
        "\n--- Steg B: efter godkännande kopierar du hela adressfältet från webbläsaren "
        "(innehåller code=...) eller bara koden ---\n"
        f"Redirect URI som används: {REDIRECT_URI}\n"
        "(Måste finnas under Auth i din LinkedIn-app.)\n\n"
        "VIKTIGT: Klistra bara in i TERMINALEN här — lägg INTE ?code=... i .env.\n"
        "Skriptet byter code mot access_token och sparar den i .env.\n"
    )
    pasted = input("Klistra in redirect-URL eller authorization code: ").strip()
    code = _parse_code(pasted)
    if not code:
        print("Kunde inte läsa någon code.", file=sys.stderr)
        sys.exit(1)

    r = requests.post(
        "https://www.linkedin.com/oauth/v2/accessToken",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if r.status_code != 200:
        print(f"Token-byte misslyckades ({r.status_code}): {r.text}", file=sys.stderr)
        sys.exit(1)

    data = r.json()
    token = (data.get("access_token") or "").strip()
    expires = data.get("expires_in", "")
    if not token:
        print(f"Oväntat svar: {data}", file=sys.stderr)
        sys.exit(1)

    if not _verify_api_token(token):
        print(
            "\nVARNING: Token byttes men api.linkedin.com svarade inte OK. "
            "Kontrollera redirect_uri, app-produkter och att du klistrade in rätt code.",
            file=sys.stderr,
        )
    else:
        print("\nOK: access_token verifierad mot LinkedIn API.")

    _write_access_token_to_env(token)
    print(f"\nSparade access_token i {_ENV_PATH} (längd {len(token)} tecken).")
    if expires:
        print(f"Token går ut om ca {expires} sekunder — kör detta skript igen efter utgång.")
    print("\nTesta org-poster:\n  python linkedin_automatisering/linkedin_test_post.py")
    print("Kolla scopes:\n  python linkedin_automatisering/linkedin_introspect_token.py")


if __name__ == "__main__":
    main()
