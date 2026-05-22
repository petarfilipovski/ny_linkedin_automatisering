"""
LinkedIn-publicering: text + artikel/länk med förhandsvisningskort (content.article).
"""
from __future__ import annotations

import re
from html import unescape
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

_LINK_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; LinkedInTalareBot/1.0; +https://streamlit.io)"
    ),
}
_MAX_PREVIEW_BYTES = 8_000_000
_MAX_DESC_LEN = 400
_MAX_TITLE_LEN = 200


def normalize_article_url(url: str | None) -> str | None:
    u = (url or "").strip()
    if not u:
        return None
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    return u


def strip_url_from_commentary(text: str, url: str | None) -> str:
    """Ta bort länken ur brödtext så den inte visas dubbelt — kortet bär URL:en."""
    if not text or not url:
        return text
    out = text
    candidates = {url}
    if url.endswith("/"):
        candidates.add(url.rstrip("/"))
    else:
        candidates.add(url + "/")
    for c in sorted(candidates, key=len, reverse=True):
        out = re.sub(re.escape(c) + r"\s*", "", out, flags=re.I)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out if out else " "


def _parse_meta_tag(html: str, *, prop: str) -> str:
    patterns = [
        rf'<meta[^>]+property=["\']og:{re.escape(prop)}["\'][^>]+content=["\']([^"\']+)',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:{re.escape(prop)}["\']',
        rf'<meta[^>]+name=["\']twitter:{re.escape(prop)}["\'][^>]+content=["\']([^"\']+)',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:{re.escape(prop)}["\']',
    ]
    if prop == "title":
        patterns.append(r"<title[^>]*>([^<]+)</title>")
    for pat in patterns:
        m = re.search(pat, html, re.I | re.S)
        if m:
            return unescape(m.group(1).strip())
    return ""


def fetch_link_preview(url: str) -> dict[str, str]:
    """
    Hämta titel, beskrivning och bild-URL från sidans Open Graph / HTML.
    """
    out: dict[str, str] = {"title": "", "description": "", "image_url": ""}
    try:
        r = requests.get(url, headers=_LINK_HEADERS, timeout=20, allow_redirects=True)
        r.raise_for_status()
    except requests.RequestException:
        return out
    if len(r.content) > _MAX_PREVIEW_BYTES:
        html = r.text[:500_000]
    else:
        html = r.text
    title = _parse_meta_tag(html, prop="title")
    desc = _parse_meta_tag(html, prop="description")
    image = _parse_meta_tag(html, prop="image")
    if image and not re.match(r"^https?://", image, re.I):
        image = urljoin(url, image)
    out["title"] = title[:_MAX_TITLE_LEN]
    out["description"] = desc[:_MAX_DESC_LEN]
    out["image_url"] = image
    return out


def _default_article_title(url: str, preview: dict[str, str], speaker_name: str) -> str:
    if preview.get("title"):
        return preview["title"][:_MAX_TITLE_LEN]
    if speaker_name:
        return speaker_name[:_MAX_TITLE_LEN]
    host = (urlparse(url).netloc or "länk").replace("www.", "")
    return host[:_MAX_TITLE_LEN] or "Länk"


def _default_article_description(
    url: str, preview: dict[str, str], commentary: str
) -> str:
    if preview.get("description"):
        return preview["description"][:_MAX_DESC_LEN]
    plain = re.sub(r"\s+", " ", (commentary or "").strip())
    if plain and plain != " ":
        return plain[:_MAX_DESC_LEN]
    return (urlparse(url).netloc or "Läs mer").replace("www.", "")[:_MAX_DESC_LEN]


def _download_image_bytes(image_url: str) -> bytes | None:
    try:
        r = requests.get(image_url, headers=_LINK_HEADERS, timeout=25, allow_redirects=True)
        r.raise_for_status()
        if len(r.content) > _MAX_PREVIEW_BYTES:
            return None
        return r.content
    except requests.RequestException:
        return None


def upload_linkedin_thumbnail(
    *,
    access_token: str,
    author_urn: str,
    api_version: str,
    image_bytes: bytes,
    content_type: str = "image/jpeg",
) -> str | None:
    """Ladda upp miniatyr via Images API; returnerar urn:li:image:…"""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
        "Linkedin-Version": api_version,
    }
    try:
        init = requests.post(
            "https://api.linkedin.com/rest/images?action=initializeUpload",
            headers=headers,
            json={"initializeUploadRequest": {"owner": author_urn}},
            timeout=60,
        )
        if init.status_code not in (200, 201):
            return None
        value = (init.json() or {}).get("value") or {}
        upload_url = value.get("uploadUrl")
        image_urn = value.get("image")
        if not upload_url or not image_urn:
            return None
        put = requests.put(
            upload_url,
            data=image_bytes,
            headers={"Content-Type": content_type},
            timeout=120,
        )
        if put.status_code not in (200, 201):
            return None
        return str(image_urn)
    except requests.RequestException:
        return None


def build_article_content(
    url: str,
    *,
    preview: dict[str, str] | None = None,
    commentary: str = "",
    speaker_name: str = "",
    thumbnail_urn: str | None = None,
) -> dict[str, Any]:
    """REST Posts API: content.article med source, title, description (+ valfri thumbnail)."""
    prev = preview or fetch_link_preview(url)
    article: dict[str, Any] = {
        "source": url,
        "title": _default_article_title(url, prev, speaker_name),
        "description": _default_article_description(url, prev, commentary),
    }
    if thumbnail_urn:
        article["thumbnail"] = thumbnail_urn
    return {"article": article}


def linkedin_rest_post_payload(
    author: str,
    commentary: str,
    url: str | None,
    *,
    preview: dict[str, str] | None = None,
    speaker_name: str = "",
    thumbnail_urn: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
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
    if url:
        payload["content"] = build_article_content(
            url,
            preview=preview,
            commentary=commentary,
            speaker_name=speaker_name,
            thumbnail_urn=thumbnail_urn,
        )
    return payload


def linkedin_ugc_post_payload(
    author: str,
    text: str,
    url: str | None,
    *,
    preview: dict[str, str] | None = None,
    speaker_name: str = "",
) -> dict[str, Any]:
    share: dict[str, Any] = {
        "shareCommentary": {"text": text},
        "shareMediaCategory": "NONE",
    }
    if url:
        prev = preview or fetch_link_preview(url)
        title = _default_article_title(url, prev, speaker_name)
        desc = _default_article_description(url, prev, text)
        media: dict[str, Any] = {
            "status": "READY",
            "originalUrl": url,
            "title": {"text": title},
            "description": {"text": desc},
        }
        share["shareMediaCategory"] = "ARTICLE"
        share["media"] = [media]
    return {
        "author": author,
        "lifecycleState": "PUBLISHED",
        "specificContent": {"com.linkedin.ugc.ShareContent": share},
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }


def prepare_article_post(
    post_text: str,
    url: str | None,
    *,
    access_token: str,
    author_urn: str,
    api_version: str,
    speaker_name: str = "",
) -> tuple[str, str | None, dict[str, str], str | None]:
    """
    Normalisera URL, rensa commentary, hämta förhandsvisning, ladda upp miniatyr.
    Returnerar (commentary, url, preview, thumbnail_urn).
    """
    link = normalize_article_url(url)
    if not link:
        return post_text, None, {}, None
    preview = fetch_link_preview(link)
    commentary = strip_url_from_commentary(post_text, link)
    thumbnail_urn: str | None = None
    image_url = preview.get("image_url") or ""
    if image_url:
        raw = _download_image_bytes(image_url)
        if raw:
            ctype = "image/png" if image_url.lower().endswith(".png") else "image/jpeg"
            thumbnail_urn = upload_linkedin_thumbnail(
                access_token=access_token,
                author_urn=author_urn,
                api_version=api_version,
                image_bytes=raw,
                content_type=ctype,
            )
    return commentary, link, preview, thumbnail_urn


def post_to_linkedin(
    post_text: str,
    *,
    url: str | None = None,
    article_url: str | None = None,
    access_token: str,
    author_urn: str,
    api_version: str = "202506",
    speaker_name: str = "",
) -> requests.Response:
    """
    Publicera inlägg. Med url/article_url: content.article (förhandsvisningskort).
    Försöker REST /rest/posts, sedan v2/ugcPosts.
    """
    raw_url = url or article_url
    commentary, link, preview, thumbnail_urn = prepare_article_post(
        post_text,
        raw_url,
        access_token=access_token,
        author_urn=author_urn,
        api_version=api_version,
        speaker_name=speaker_name,
    )

    headers_rest = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
        "Linkedin-Version": api_version,
    }
    rest_resp: requests.Response | None = None
    try:
        rest_resp = requests.post(
            "https://api.linkedin.com/rest/posts",
            headers=headers_rest,
            json=linkedin_rest_post_payload(
                author_urn,
                commentary,
                link,
                preview=preview,
                speaker_name=speaker_name,
                thumbnail_urn=thumbnail_urn,
            ),
            timeout=60,
        )
    except requests.RequestException:
        rest_resp = None

    if rest_resp is not None and rest_resp.status_code in (200, 201):
        return rest_resp

    headers_ugc = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
    }
    return requests.post(
        "https://api.linkedin.com/v2/ugcPosts",
        headers=headers_ugc,
        json=linkedin_ugc_post_payload(
            author_urn,
            commentary,
            link,
            preview=preview,
            speaker_name=speaker_name,
        ),
        timeout=60,
    )
