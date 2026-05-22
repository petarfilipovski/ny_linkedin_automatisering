"""
Resolve LINKEDIN_PERSON_URN from /v2/userinfo (needs openid + profile + email on the token).

Run from repo root:
  python linkedin_automatisering/print_linkedin_person_urn.py
"""
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

TOKEN = (os.getenv("LINKEDIN_ACCESS_TOKEN") or "").strip()
if not TOKEN:
    print("Saknar LINKEDIN_ACCESS_TOKEN i linkedin_automatisering/.env", file=sys.stderr)
    sys.exit(1)

r = requests.get(
    "https://api.linkedin.com/v2/userinfo",
    headers={"Authorization": f"Bearer {TOKEN}"},
    timeout=30,
)

if r.status_code == 403:
    print(
        "403 Forbidden på /v2/userinfo: token saknar rätt behörigheter.\n\n"
        "Gör så här:\n"
        "1. LinkedIn Developer -> din app -> Produkter: aktivera "
        "\"Sign In with LinkedIn using OpenID Connect\".\n"
        "2. Hämta ny åtkomsttoken via OAuth med scope (mellanslag-separerade):\n"
        "   openid profile email w_member_social\n"
        "   (openid krävs för userinfo; w_member_social för att posta.)\n"
        "3. Byt ut LINKEDIN_ACCESS_TOKEN i .env mot den nya token.\n",
        file=sys.stderr,
    )
    print(r.text, file=sys.stderr)
    sys.exit(1)

r.raise_for_status()
data = r.json()
sub = data.get("sub")
if not sub:
    print(f"Oväntat svar: {data}", file=sys.stderr)
    sys.exit(1)

urn = f"urn:li:person:{sub}"
print(f"sub (person-id): {sub}")
print(f"Sätt i .env: LINKEDIN_PERSON_URN={urn}")
