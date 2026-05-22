import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


@dataclass(frozen=True)
class SitesmartConfig:
    base_url: str
    username: str
    password: str
    speaker_form_url: str | None
    selectors_path: Path


def _load_selectors(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _safe_md_inline(s: str) -> str:
    """Escape characters that break Streamlit/markdown when pasting PDF text."""
    if not s:
        return ""
    return re.sub(r"([*_`\[\]#])", r"\\\1", s.strip())


def _parse_swedish_amount(token: str) -> int | None:
    """Parse amounts like '15 000', '15000', '15.000' (tusentalsavgränsare), '25 500'."""
    t = (token or "").strip().replace("\u00a0", " ").replace(" ", "")
    if not t or not re.search(r"\d", t):
        return None
    # Tusentals punkt/komma: 15.000 eller 15,000
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", t):
        t = re.sub(r"[.,]", "", t)
    elif t.count(",") == 1 and t.count(".") == 0:
        parts = t.split(",")
        if len(parts[1]) <= 2:
            t = parts[0].replace(".", "") + "." + parts[1]
        else:
            t = t.replace(",", "")
    else:
        t = t.replace(",", ".")
    try:
        v = float(t)
        return int(round(v)) if v == int(v) else int(v)
    except ValueError:
        digits = re.sub(r"[^\d]", "", token)
        return int(digits) if digits else None


def _extract_emails(text: str) -> list[str]:
    return list(
        dict.fromkeys(
            re.findall(
                r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
                text or "",
            )
        )
    )


def _extract_phone(text: str) -> str:
    m = re.search(
        r"(?:\+46\s?7|07[02369])\s?[\d\s\-]{6,14}\d",
        text or "",
        re.IGNORECASE,
    )
    if m:
        return re.sub(r"\s+", " ", m.group(0).strip())
    m = re.search(r"(?:tel|telefon|mobil)\s*[:\-]?\s*([\d\s\-+]{8,})", text or "", re.I)
    if m:
        return re.sub(r"\s+", " ", m.group(1).strip())
    return ""


def _extract_name_from_lines(lines: list[str]) -> str:
    label_re = re.compile(
        r"^(?:namn|talare|föreläsare|speaker|uppgifter\s*om)\s*[:\-]\s*(.+)$",
        re.I,
    )
    for ln in lines[:40]:
        m = label_re.match(ln.strip())
        if m:
            cand = m.group(1).strip()
            if 2 < len(cand) < 120 and not _extract_emails(cand):
                return cand
    for ln in lines[:15]:
        s = ln.strip()
        if not s or len(s) > 100:
            continue
        if _extract_emails(s) or re.search(r"^\d", s):
            continue
        if re.search(r"[A-Za-zÅÄÖåäö]{2,}", s) and re.match(
            r"^[\w\s\-'ÅÄÖåäö.]+$", s, re.UNICODE
        ):
            parts = s.split()
            if 2 <= len(parts) <= 5:
                return s
    return ""


def _extract_prices(text: str) -> tuple[int | None, int | None]:
    """
    Hitta lägsta/högsta belopp (kr) utifrån nyckelord och intervall.
    """
    raw = text or ""
    amounts: list[int] = []

    range_m = re.finditer(
        r"(\d{1,3}(?:\s?\d{3})+|\d{4,})\s*[-–]\s*(\d{1,3}(?:\s?\d{3})+|\d{4,})\s*(?:kr|SEK)?",
        raw,
        re.I,
    )
    for m in range_m:
        a, b = _parse_swedish_amount(m.group(1)), _parse_swedish_amount(m.group(2))
        if a is not None:
            amounts.append(a)
        if b is not None:
            amounts.append(b)

    for m in re.finditer(
        r"(?:från|lägst|min|grund|start)\s*[:\s]*(\d{1,3}(?:\s?\d{3})+|\d{4,})",
        raw,
        re.I,
    ):
        v = _parse_swedish_amount(m.group(1))
        if v is not None:
            amounts.append(v)

    for m in re.finditer(
        r"(?:till|högst|max|ca\.?)\s*[:\s]*(\d{1,3}(?:\s?\d{3})+|\d{4,})",
        raw,
        re.I,
    ):
        v = _parse_swedish_amount(m.group(1))
        if v is not None:
            amounts.append(v)

    for line in raw.splitlines():
        if re.search(
            r"pris|arvode|honorar|fee|kostnad|€|eur|sek", line, re.I
        ):
            for g in re.findall(
                r"(\d{1,3}(?:\s?\d{3})+|\d{4,})\s*(?:kr|SEK|sek)?",
                line,
                re.I,
            ):
                v = _parse_swedish_amount(g)
                if v is not None and 100 <= v <= 10_000_000:
                    amounts.append(v)

    if not amounts:
        for m in re.finditer(
            r"\b(\d{1,3}(?:\s?\d{3})+|\d{5,})\s*(?:kr|SEK|sek)\b",
            raw,
            re.I,
        ):
            v = _parse_swedish_amount(m.group(1))
            if v is not None and 500 <= v <= 10_000_000:
                amounts.append(v)

    if not amounts:
        return None, None
    return min(amounts), max(amounts)


def _extract_topics(lines: list[str]) -> list[str]:
    topics: list[str] = []
    capture = False
    keywords = re.compile(
        r"expert|ämne|område|kategori|nyckelord|talar om|föreläser",
        re.I,
    )
    for ln in lines:
        s = ln.strip()
        if keywords.search(s) and ":" in s:
            capture = True
            rest = s.split(":", 1)[1].strip()
            if rest:
                for part in re.split(r"[,;•|]", rest):
                    t = part.strip()
                    if 2 < len(t) < 80:
                        topics.append(t)
            continue
        if capture and s.startswith(("-", "•", "*", "·")):
            t = re.sub(r"^[\s\-•*·]+\s*", "", s).strip()
            if 2 < len(t) < 80:
                topics.append(t)
    if not topics:
        for ln in lines:
            m = re.match(r"^[\-\*•]\s*(.+)$", ln.strip())
            if m:
                t = m.group(1).strip()
                if 2 < len(t) < 80 and not re.search(r"^\d", t):
                    topics.append(t)
    seen = set()
    out: list[str] = []
    for t in topics:
        tl = t.lower()
        if tl not in seen and len(out) < 12:
            seen.add(tl)
            out.append(t)
    return out[:8]


def _is_metadata_line(line: str) -> bool:
    s = line.strip()
    if len(s) < 3:
        return True
    return bool(
        re.match(
            r"^(namn|talare|titel|pris|arvode|honorar|e-?post|email|mobil|telefon|tel\.|"
            r"expert|ämne|område|adress|org\.?nr)\s*[:\-]",
            s,
            re.I,
        )
    )


def _first_paragraph(bio: str) -> str:
    bio = (bio or "").strip()
    if not bio:
        return ""
    for block in re.split(r"\n\s*\n+", bio):
        t = block.strip()
        if len(t) < 30:
            continue
        lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
        if not lines:
            continue
        meta = sum(1 for ln in lines if _is_metadata_line(ln))
        if meta >= max(1, len(lines) // 2):
            continue
        return t[:2000]
    for block in re.split(r"\n\s*\n+", bio):
        t = block.strip()
        if len(t) > 25 and not _is_metadata_line(t.split("\n")[0]):
            return t[:2000]
    cleaned = " ".join(bio.split())
    return cleaned[:2000]


def extract_speaker_data_from_pdf(pdf_text: str) -> dict:
    """
    Strukturerad utläsning från talarpdf (samma källtext som LinkedIn-flödet använder).
    """
    raw = pdf_text or ""
    lines = [ln.rstrip() for ln in raw.splitlines()]
    stripped = [ln.strip() for ln in lines if ln.strip()]

    name = _extract_name_from_lines(stripped)
    emails = _extract_emails(raw)
    phone = _extract_phone(raw)
    lo, hi = _extract_prices(raw)
    price_low = f"{lo:,} kr".replace(",", " ") if lo is not None else ""
    price_high = f"{hi:,} kr".replace(",", " ") if hi is not None else ""
    if lo is not None and hi is not None and lo == hi:
        price_low = price_high

    topics_list = _extract_topics(stripped)
    topics_text = ", ".join(topics_list)

    cleaned = " ".join(raw.split())
    bio = cleaned[:8000]

    title = ""
    for ln in stripped[:30]:
        m = re.match(r"^(?:titel|yrkestitel|roll)\s*[:\-]\s*(.+)$", ln, re.I)
        if m:
            title = m.group(1).strip()[:200]
            break

    intro = _first_paragraph(raw)
    if not intro:
        intro = _first_paragraph(bio)

    return {
        "name": name,
        "title": title,
        "emails": emails,
        "emails_text": ", ".join(emails),
        "phone": phone,
        "price_low": price_low,
        "price_high": price_high,
        "price_low_num": lo,
        "price_high_num": hi,
        "topics_list": topics_list,
        "topics_text": topics_text,
        "bio": bio,
        "first_paragraph": intro,
    }


def _guess_speaker_fields_from_pdf(pdf_text: str) -> dict:
    """Bakåtkompatibel dict för Playwright + enkla fält."""
    d = extract_speaker_data_from_pdf(pdf_text)
    return {
        "name": d["name"],
        "title": d["title"],
        "bio": d["bio"][:1200],
        "topics": d["topics_text"],
    }


def sitesmart_is_configured() -> bool:
    """True when Playwright automation can run (all env + selectors file)."""
    return not validate_config(build_config())


def format_sitesmart_demo_markdown(pdf_text: str) -> str:
    """
    Checklista + värden utlästa från PDF (samma text som LinkedIn-flödet använder).
    """
    d = extract_speaker_data_from_pdf(pdf_text)
    name = _safe_md_inline(d.get("name") or "") or "*(kunde inte läsa namn — kontrollera PDF)*"
    title = _safe_md_inline(d.get("title") or "")
    emails = d.get("emails") or []
    emails_line = ", ".join(_safe_md_inline(e) for e in emails) or "*(inga e-postadresser hittades i PDF)*"
    phone = _safe_md_inline(d.get("phone") or "") or "*(ingen telefon hittades)*"

    pl = d.get("price_low") or ""
    ph = d.get("price_high") or ""
    if not pl and not ph:
        price_inkl = "*(inga priser hittades — leta efter t.ex. «Pris», «kr», «arvode» i PDF)*"
        price_start = "*(samma som ovan)*"
    elif pl and ph and pl == ph:
        price_inkl = ph
        price_start = ph
    else:
        price_inkl = ph or pl or "*(okänt)*"
        price_start = pl or ph or "*(okänt)*"

    topics = d.get("topics_list") or []
    if topics:
        topics_md = "\n".join(f"- {_safe_md_inline(t)}" for t in topics[:8])
    else:
        topics_md = "*(inga punktlista/ämnen hittades — lägg in expertområden manuellt, max 8)*"

    fp = (d.get("first_paragraph") or "").strip()
    if len(fp) >= 10:
        short_desc = f"**{_safe_md_inline(fp[:180])}**{_safe_md_inline(fp[180:400])}"
    else:
        short_desc = "**Inledning här** — klistra in första stycket från presentationen (fetmarkera första stycket)."

    art_nr = datetime.now().strftime("%y%m%d") + "-1"
    title_line = f"\n- **Yrkestitel / roll (om finns):** {title}" if title else ""

    return f"""### Företag (Användare → Företag → Nytt företag)
- **Företagstyp:** Drop shipping
- **E-postfält:** {emails_line}
- **Telefon (om relevant):** {phone}
- **Företag / namn (talare):** {name}{title_line}
- **Aktiv:** bocka i

### E-handel → Ny produkt
- **Art.nr** (tidigare Fortnox): `{art_nr}`
- **Namn:** {name}
- **Pris inkl. moms** (högsta): `{price_inkl}`
- **Kort beskrivning:** första stycket ska vara fetmarkerat — *Infoga som text*, inte *Keep*.

**Utläst från PDF:**

{short_desc}

### 2.1 Frakt & inventering
- **Fraktsätt:** bocka i **Förfrågan**

### Kategorier → Huvudkategori (max 8 ämnen)
{topics_md}

### Bilder / filer
- Namnge bildfiler efter **{name}**
- Mapp med talarens namn → ladda upp → **E-handel** → sök talaren → **Bilder**

### Pris
- **Startpris / grundavgift (lägsta):** `{price_start}` — **Aktivera via detta pris**

### Produktdata
- **Län, kön, språk:** fyll i från PDF om det finns
- Hoppa över om tomt: *Certifierad/diplomerad coach*, *SMS*, *Nominerad till talarpris*
- **Typ av föreläsning** (*Erbjuder*)

### Övrigt
- **Leverantör Dropshipping:** sök **{name}** (viktigt)

### Anpassade fält (Rubrik 1–10)
- *Talaren föreläser om* — se PDF / lång beskrivning
- **Effekten av** under *Ytterligare information* → **Lång beskrivning** (inte extra fält 1–3)
- **Fält:** Mer om priser, Mitt motto, Jag som talare, Så arbetar jag, Jag erbjuder, Jag passar till, Bakgrund

### Relaterade produkter
- Lägg till talarens **huvudkategori**

---
*Sätt `SITESMART_BASE_URL`, `SITESMART_USERNAME`, `SITESMART_PASSWORD` i `.env` för automatisk webbläsarifyllning.*
"""


def build_config() -> SitesmartConfig:
    base_url = os.getenv("SITESMART_BASE_URL", "").strip()
    username = os.getenv("SITESMART_USERNAME", "").strip()
    password = os.getenv("SITESMART_PASSWORD", "").strip()
    speaker_form_url = os.getenv("SITESMART_SPEAKER_FORM_URL", "").strip() or None

    selectors_path = Path(__file__).with_name("sitesmart_selectors.json")
    return SitesmartConfig(
        base_url=base_url,
        username=username,
        password=password,
        speaker_form_url=speaker_form_url,
        selectors_path=selectors_path,
    )


def validate_config(cfg: SitesmartConfig) -> list[str]:
    missing: list[str] = []
    if not cfg.base_url:
        missing.append("SITESMART_BASE_URL")
    if not cfg.username:
        missing.append("SITESMART_USERNAME")
    if not cfg.password:
        missing.append("SITESMART_PASSWORD")
    if not cfg.selectors_path.exists():
        missing.append(f"selectors file missing: {cfg.selectors_path.name}")
    return missing


def upload_speaker_to_sitesmart(pdf_text: str, *, headless: bool = False) -> dict:
    """
    Opens a browser, logs in, fills speaker form, submits.
    Returns a small status dict for the Streamlit UI.
    """
    cfg = build_config()
    missing = validate_config(cfg)
    if missing:
        return {"ok": False, "error": f"Missing config: {', '.join(missing)}"}

    selectors = _load_selectors(cfg.selectors_path)
    fields = _guess_speaker_fields_from_pdf(pdf_text)
    full = extract_speaker_data_from_pdf(pdf_text)

    login_sel = selectors.get("login", {})
    form_sel = selectors.get("speaker_form", {})

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()

        try:
            page.goto(cfg.base_url, wait_until="domcontentloaded", timeout=60_000)

            page.fill(login_sel["username"], cfg.username)
            page.fill(login_sel["password"], cfg.password)
            page.click(login_sel["submit"])

            # If a direct form URL is provided, go there after login.
            if cfg.speaker_form_url:
                page.goto(cfg.speaker_form_url, wait_until="domcontentloaded", timeout=60_000)

            # Fill form fields (only those that have selectors configured)
            if "name" in form_sel:
                page.fill(form_sel["name"], fields.get("name", ""))
            if "title" in form_sel:
                page.fill(form_sel["title"], fields.get("title", ""))
            if "bio" in form_sel:
                page.fill(form_sel["bio"], fields.get("bio", ""))
            if "topics" in form_sel:
                page.fill(form_sel["topics"], fields.get("topics", ""))
            if "email" in form_sel:
                page.fill(form_sel["email"], full.get("emails_text", ""))
            if "price_high" in form_sel:
                page.fill(form_sel["price_high"], full.get("price_high", ""))
            if "price_low" in form_sel:
                page.fill(form_sel["price_low"], full.get("price_low", ""))

            if "submit" in form_sel:
                page.click(form_sel["submit"])

            # Give the site a moment; real success detection depends on your UI.
            page.wait_for_timeout(1500)

            return {"ok": True, "message": "Sitesmart form submitted (best-effort)."}
        except PlaywrightTimeoutError as e:
            return {"ok": False, "error": f"Timeout: {e}"}
        except KeyError as e:
            return {
                "ok": False,
                "error": f"Selector missing in sitesmart_selectors.json: {e}",
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}
        finally:
            context.close()
            browser.close()

