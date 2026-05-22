import io
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import requests
import pdfplumber
import anthropic
import streamlit as st
from dotenv import load_dotenv

from auth_gate import inject_hide_streamlit_chrome, require_login

load_dotenv(Path(__file__).resolve().parent / ".env")


def _sync_secrets_to_env() -> None:
    """Streamlit Community Cloud: secrets.toml → miljövariabler för befintlig kod."""
    try:
        for key, value in st.secrets.items():
            if isinstance(value, (str, int, float, bool)):
                os.environ[key] = str(value)
    except Exception:
        pass


_sync_secrets_to_env()


def _env(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip()


ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")
LINKEDIN_ACCESS_TOKEN = _env("LINKEDIN_ACCESS_TOKEN")
LINKEDIN_PERSON_URN = _env("LINKEDIN_PERSON_URN")
LINKEDIN_ORGANIZATION_ID = _env("LINKEDIN_ORGANIZATION_ID")
LINKEDIN_POST_AUTHOR_URN = _env("LINKEDIN_POST_AUTHOR_URN")
LINKEDIN_API_VERSION = _env("LINKEDIN_API_VERSION", "202506")

from pdf_images import (
    PYMUPDF_AVAILABLE,
    extract_images_from_pdf_bytes,
    safe_image_name_prefix,
)
from sitesmart_uploader import (
    extract_speaker_data_from_pdf,
    format_sitesmart_demo_markdown,
    sitesmart_is_configured,
    upload_speaker_to_sitesmart,
)
EXAMPLE_POST = """🎤 Hur skapar vi arbetsplatser där människor mår bra, utvecklas och presterar hållbart över tid?
Sofia Norberg föreläser om hur organisationer kan bygga en kultur där arbetsmiljö, hälsa och ledarskap går hand i hand. Med en kombination av forskning, praktiska verktyg och verksamhetsnära exempel visar hon hur vi skapar arbetsplatser där människor kan prestera – utan att bränna ut sig.

💡 Sofia lyfter hur både ledare och medarbetare kan bidra till ett mer hållbart arbetsliv genom tydliga strukturer, psykologisk trygghet och ett mer medvetet sätt att arbeta tillsammans.

🔑 Nyckelpunkter från Sofias föreläsningar:
• Hållbart arbetsliv och balans mellan prestation och återhämtning
• Psykologisk trygghet och starka team
• Ledarskap som stärker arbetsmiljö och engagemang
• Förebygga stress och utmattning i organisationer
• Skapa en kultur där människor utvecklas och mår bra"""

SYSTEM_PROMPT = f"""Du är en expert på att skriva engagerande LinkedIn-inlägg för talare och föreläsare på svenska.
Du arbetar för ett talarförmedlingsbolag och ska skriva professionella, inspirerande inlägg som lockar till bokning.

Följ alltid detta format exakt:
1. Börja med en emoji (🎤) följt av en engagerande retorisk fråga om talarens ämne
2. Ett stycke som beskriver vad talaren föreläser om med betoning på värde för publiken
3. Tom rad
4. 💡 Emoji följt av ett stycke som lyfter ett specifikt perspektiv eller insikt från talaren
5. Tom rad
6. 🔑 Nyckelpunkter från [Talarens förnamn]s föreläsningar:
   • Punkt 1
   • Punkt 2
   • Punkt 3
   • Punkt 4
   • Punkt 5

Skriv på svenska. Håll tonen professionell men engagerande. Fokusera på värdet för arbetsgivare och organisationer.

Här är ett exempel på hur ett färdigt inlägg ska se ut:
{EXAMPLE_POST}"""


def extract_text_from_pdf_bytes(data: bytes) -> str:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        text = ""
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
    return text.strip()


def extract_text_from_pdf(uploaded_file) -> str:
    return extract_text_from_pdf_bytes(uploaded_file.getvalue())


def render_pdf_images_section(images: list, *, name_prefix: str, key_prefix: str) -> None:
    """Visa förhandsgranskning och nedladdning — motsvarar Sitesmart-steget Bilder/filer."""
    if not PYMUPDF_AVAILABLE:
        st.info("För att extrahera bilder i PDF: installera `pymupdf` (`pip install pymupdf`) och starta om appen.")
        return
    if not images:
        st.caption("Inga talarbilder hittades (logotyp i sidhuvud/sidfot filtreras bort automatiskt).")
        return
    st.subheader("Bilder från PDF")
    st.caption(
        "Ladda ner och ladda upp under **E-handel → Bilder** i Sitesmart. "
        "Organisationslogotyp i PDF-mallen visas inte här."
    )
    for i, img in enumerate(images):
        fn = f"{name_prefix}_{i + 1:02d}_{img['filename']}"
        st.image(img["data"], caption=f"{fn} · sida {img['page']} · {img['width']}×{img['height']}", use_container_width=True)
        st.download_button(
            label=f"Ladda ner {fn}",
            data=img["data"],
            file_name=fn,
            mime=img["mime"],
            key=f"{key_prefix}_img_{i}",
        )


def _first_http_url_from_text(text: str) -> str:
    if not text:
        return ""
    m = re.search(r"https?://[^\s\]\)>'\"]+", text)
    if not m:
        return ""
    return m.group(0).rstrip(".,;)]\"'")


def _normalize_article_url(url: str | None) -> str | None:
    u = (url or "").strip()
    if not u:
        return None
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    return u


def _linkedin_rest_post_payload(
    author: str, commentary: str, article_url: str | None
) -> dict:
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
        payload["content"] = {
            "article": {
                "source": article_url,
                "title": host or "Länk",
            }
        }
    return payload


def _linkedin_ugc_post_payload(
    author: str, text: str, article_url: str | None
) -> dict:
    share: dict = {
        "shareCommentary": {"text": text},
        "shareMediaCategory": "NONE",
    }
    if article_url:
        share["shareMediaCategory"] = "ARTICLE"
        share["media"] = [{"status": "READY", "originalUrl": article_url}]
    return {
        "author": author,
        "lifecycleState": "PUBLISHED",
        "specificContent": {"com.linkedin.ugc.ShareContent": share},
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }


def generate_linkedin_post(pdf_text: str) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"Här är informationen från talarens PDF-dokument:\n\n{pdf_text}\n\nSkriv ett LinkedIn-inlägg baserat på denna information.",
            }
        ],
    )
    return message.content[0].text


def post_to_linkedin(post_text: str, article_url: str | None = None) -> requests.Response:
    """
    Försök POST https://api.linkedin.com/rest/posts (Linkedin-Version),
    annars fallback till POST /v2/ugcPosts.
    """
    author = linkedin_post_author_urn_effective()
    link = _normalize_article_url(article_url)

    headers_rest = {
        "Authorization": f"Bearer {LINKEDIN_ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
        "Linkedin-Version": LINKEDIN_API_VERSION,
    }
    rest_resp: requests.Response | None = None
    try:
        rest_resp = requests.post(
            "https://api.linkedin.com/rest/posts",
            headers=headers_rest,
            json=_linkedin_rest_post_payload(author, post_text, link),
            timeout=60,
        )
    except requests.RequestException:
        rest_resp = None

    if rest_resp is not None and rest_resp.status_code in (200, 201):
        return rest_resp

    headers_ugc = {
        "Authorization": f"Bearer {LINKEDIN_ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
    }
    return requests.post(
        "https://api.linkedin.com/v2/ugcPosts",
        headers=headers_ugc,
        json=_linkedin_ugc_post_payload(author, post_text, link),
        timeout=60,
    )


@st.cache_data(ttl=3600, show_spinner=False)
def _linkedin_person_urn_from_token(access_token: str) -> str | None:
    if not access_token:
        return None
    try:
        r = requests.get(
            "https://api.linkedin.com/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20,
        )
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    sub = (r.json() or {}).get("sub")
    return f"urn:li:person:{sub}" if sub else None


def linkedin_post_author_urn_effective() -> str:
    """
    Vem inlägget publiceras som. Standard: din personprofil.
    Sätt LINKEDIN_ORGANIZATION_ID eller LINKEDIN_POST_AUTHOR_URN för företagssida.
    """
    if LINKEDIN_POST_AUTHOR_URN:
        return LINKEDIN_POST_AUTHOR_URN
    if LINKEDIN_ORGANIZATION_ID.isdigit():
        return f"urn:li:organization:{LINKEDIN_ORGANIZATION_ID}"
    if LINKEDIN_PERSON_URN:
        return LINKEDIN_PERSON_URN
    return _linkedin_person_urn_from_token(LINKEDIN_ACCESS_TOKEN) or ""


def linkedin_person_urn_effective() -> str:
    """Alias: samma som post-author (används i UI-koll)."""
    return linkedin_post_author_urn_effective()


# --- Streamlit UI ---

st.set_page_config(
    page_title="LinkedIn Talare Automatisering",
    page_icon="🎤",
    layout="centered",
    menu_items={
        "Get Help": None,
        "Report a bug": None,
        "About": None,
    },
)

inject_hide_streamlit_chrome()
require_login()

with st.sidebar:
    if st.button("Logga ut", use_container_width=True):
        st.session_state.authenticated = False
        st.rerun()

st.markdown(
    """
    <style>
      /* LinkedIn button (blue) */
      div.linkedin-button button {
        background-color: #0A66C2 !important;
        border: 1px solid #0A66C2 !important;
        color: white !important;
      }
      div.linkedin-button button:hover {
        background-color: #004182 !important;
        border-color: #004182 !important;
      }

      /* Hemsida button (purple) */
      div.hemsida-button button {
        background-color: #6F42C1 !important;
        border: 1px solid #6F42C1 !important;
        color: white !important;
      }
      div.hemsida-button button:hover {
        background-color: #59359C !important;
        border-color: #59359C !important;
      }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🎤 LinkedIn Talare Automatisering")
st.markdown("Ladda upp talarens PDF-dokument och generera ett LinkedIn-inlägg automatiskt.")

# Session state init
if "generated_post" not in st.session_state:
    st.session_state.generated_post = None
if "published" not in st.session_state:
    st.session_state.published = False
if "pdf_text" not in st.session_state:
    st.session_state.pdf_text = None
if "pdf_images" not in st.session_state:
    st.session_state.pdf_images = []

# --- Step 1: Upload PDF ---
if not st.session_state.generated_post and not st.session_state.published:
    st.header("Steg 1: Ladda upp PDF")
    uploaded_file = st.file_uploader(
        "Välj talarens PDF-dokument", type=["pdf"], help="Ladda upp PDF-formuläret som talaren fyllt i"
    )

    if uploaded_file:
        with st.spinner("Läser PDF..."):
            try:
                raw_pdf = uploaded_file.getvalue()
                pdf_text = extract_text_from_pdf_bytes(raw_pdf)
                st.session_state.pdf_text = pdf_text
                st.session_state.pdf_images = extract_images_from_pdf_bytes(raw_pdf)
            except Exception as e:
                st.error(f"Kunde inte läsa PDF-filen: {e}")
                st.stop()

        with st.expander("Visa extraherad text från PDF", expanded=False):
            st.text(pdf_text if pdf_text else "Ingen text hittades i PDF:en.")

        with st.expander("Bilder från PDF", expanded=bool(st.session_state.pdf_images)):
            sp = extract_speaker_data_from_pdf(pdf_text or "")
            prefix = safe_image_name_prefix(sp.get("name") or "talare")
            render_pdf_images_section(
                st.session_state.pdf_images,
                name_prefix=prefix,
                key_prefix="step1",
            )

        if not pdf_text:
            st.warning("PDF-filen verkar inte innehålla läsbar text. Prova en annan fil.")
        else:
            st.markdown('<div class="linkedin-button">', unsafe_allow_html=True)
            if st.button("Generera LinkedIn-inlägg", type="primary", use_container_width=True):
                if not ANTHROPIC_API_KEY:
                    st.session_state.generated_post = EXAMPLE_POST
                    st.rerun()
                else:
                    with st.spinner("AI genererar inlägg..."):
                        try:
                            post = generate_linkedin_post(pdf_text)
                            st.session_state.generated_post = post
                            st.rerun()
                        except Exception as e:
                            st.error(f"Fel vid generering: {e}")
            st.markdown("</div>", unsafe_allow_html=True)

            st.markdown("")

            st.markdown('<div class="hemsida-button">', unsafe_allow_html=True)
            if st.button("Generera hemsida uppläggning", use_container_width=True):
                if sitesmart_is_configured():
                    with st.spinner("Fyller Sitesmart-formulär..."):
                        result = upload_speaker_to_sitesmart(pdf_text, headless=False)
                        if result.get("ok"):
                            st.success(result.get("message", "Klart."))
                        else:
                            st.error(f"Sitesmart-fel: {result.get('error', 'Okänt fel')}")
                else:
                    st.success("Underlag för Sitesmart är redo.")
                    with st.expander("Checklista: Företag → E-handel → produkt …", expanded=True):
                        st.markdown(format_sitesmart_demo_markdown(pdf_text))
                        sp = extract_speaker_data_from_pdf(pdf_text or "")
                        prefix = safe_image_name_prefix(sp.get("name") or "talare")
                        render_pdf_images_section(
                            st.session_state.get("pdf_images") or [],
                            name_prefix=prefix,
                            key_prefix="hemsida",
                        )
            st.markdown("</div>", unsafe_allow_html=True)

# --- Step 2: Approval page ---
elif st.session_state.generated_post and not st.session_state.published:
    st.header("Steg 2: Granska och godkänn inlägg")
    st.markdown("Redigera texten vid behov och klicka sedan på **Publicera** för att lägga upp det på LinkedIn.")

    if "linkedin_web_url" not in st.session_state:
        st.session_state.linkedin_web_url = _first_http_url_from_text(
            st.session_state.pdf_text or ""
        )

    edited_post = st.text_area(
        "LinkedIn-inlägg",
        value=st.session_state.generated_post,
        height=350,
        help="Du kan redigera texten direkt här innan publicering",
    )

    st.text_input(
        "Länk till webbsida (artikel/länk i inlägget)",
        key="linkedin_web_url",
        placeholder="https://exempel.se/talare/anna-andersson",
        help="Valfritt men rekommenderas. Första http(s)-länk i PDF föreslås automatiskt.",
    )

    st.markdown("---")

    col1, col2, col3 = st.columns([1, 1, 1])

    with col1:
        if st.button("Generera om", use_container_width=True):
            if not ANTHROPIC_API_KEY:
                st.session_state.generated_post = EXAMPLE_POST
                st.rerun()
            else:
                with st.spinner("Genererar nytt inlägg..."):
                    try:
                        post = generate_linkedin_post(st.session_state.pdf_text)
                        st.session_state.generated_post = post
                        st.rerun()
                    except Exception as e:
                        st.error(f"Fel vid generering: {e}")

    with col2:
        if st.button("Tillbaka", use_container_width=True):
            st.session_state.generated_post = None
            st.session_state.pdf_text = None
            st.session_state.pdf_images = []
            st.session_state.pop("linkedin_web_url", None)
            st.rerun()

    with col3:
        if st.button("Publicera på LinkedIn", type="primary", use_container_width=True):
            if not LINKEDIN_ACCESS_TOKEN or not linkedin_person_urn_effective():
                st.session_state.published = True
                st.session_state.generated_post = edited_post
                st.rerun()
            else:
                with st.spinner("Publicerar..."):
                    try:
                        web = (st.session_state.get("linkedin_web_url") or "").strip()
                        response = post_to_linkedin(
                            edited_post,
                            article_url=web or None,
                        )
                        if response.status_code in (200, 201):
                            st.session_state.published = True
                            st.session_state.generated_post = edited_post
                            st.rerun()
                        else:
                            st.error(
                                f"LinkedIn API-fel ({response.status_code}): {response.text}\n\n"
                                "Först anropas **REST** `/rest/posts`, sedan **v2/ugcPosts**. "
                                "Kontrollera token, `LINKEDIN_PERSON_URN` och vid REST-fel prova "
                                f"annan `LINKEDIN_API_VERSION` (nu **{LINKEDIN_API_VERSION}**)."
                            )
                    except Exception as e:
                        st.error(f"Kunde inte publicera: {e}")

# --- Step 3: Success page ---
elif st.session_state.published:
    st.success("Inlägget har publicerats på LinkedIn!")
    st.balloons()

    st.header("Publicerat inlägg")
    st.markdown(
        f"""<div style="background-color:#f0f2f6;color:#111827;padding:1.2rem;border-radius:8px;white-space:pre-wrap;font-size:0.95rem;border:1px solid #e5e7eb;">
{st.session_state.generated_post}
</div>""",
        unsafe_allow_html=True,
    )

    st.markdown("---")
    if st.button("Lägg upp en ny talare", type="primary", use_container_width=True):
        st.session_state.generated_post = None
        st.session_state.published = False
        st.session_state.pdf_text = None
        st.session_state.pdf_images = []
        st.session_state.pop("linkedin_web_url", None)
        st.rerun()
