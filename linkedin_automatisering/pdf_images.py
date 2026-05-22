"""
Extrahera inbäddade rasterbilder från PDF (PyMuPDF).
Filtrerar bort organisationslogotyper (sidhuvud/sidfot, mallbilder på många sidor).
"""
from __future__ import annotations

import re
from typing import Any

try:
    import fitz  # PyMuPDF

    PYMUPDF_AVAILABLE = True
except ImportError:
    fitz = None  # type: ignore[misc, assignment]
    PYMUPDF_AVAILABLE = False

# Andel av sidhöjd där sidhuvud/sidfot brukar ligga i talarpdf:er
_HEADER_BAND = 0.16
_FOOTER_BAND = 0.10
# Samma bild på minst så här stor andel av sidorna → troligen logotyp/mall
_TEMPLATE_MIN_PAGE_FRACTION = 0.34


def safe_image_name_prefix(speaker_hint: str) -> str:
    """Filnamn-vänlig sträng (ASCII) för nedladdningar."""
    t = re.sub(r"[^\w\s\-]", "", (speaker_hint or "").strip(), flags=re.UNICODE)
    t = re.sub(r"\s+", "_", t)[:48]
    ascii_t = t.encode("ascii", "ignore").decode("ascii")
    return ascii_t or "talare"


def _placement_looks_like_logo(rect: Any, page_rect: Any) -> bool:
    """True om bilden sitter i sidhuvud/sidfot som en smal eller bred logotyp."""
    ph = float(page_rect.height)
    pw = float(page_rect.width)
    rh = float(rect.y1 - rect.y0)
    rw = float(rect.x1 - rect.x0)
    if ph <= 0 or pw <= 0:
        return False

    in_header = float(rect.y1) <= ph * _HEADER_BAND
    in_footer = float(rect.y0) >= ph * (1.0 - _FOOTER_BAND)
    if not (in_header or in_footer):
        return False

    # Tunn remsa eller bred logga i marginal — inte ett stort talarfoto
    if rh <= ph * 0.14:
        return True
    if rw / max(rh, 1.0) >= 2.5:
        return True
    if rw <= pw * 0.5 and rh <= ph * 0.12:
        return True
    return False


def _is_likely_org_logo(meta: dict[str, Any], total_pages: int) -> bool:
    """Heuristik: logotyp i mall (flera sidor) eller endast i sidhuvud/sidfot."""
    pages: set[int] = meta["pages"]
    placements: list[tuple[int, Any, Any]] = meta["placements"]

    if total_pages >= 2 and len(pages) >= max(2, int(total_pages * _TEMPLATE_MIN_PAGE_FRACTION)):
        return True

    if not placements:
        return False

    logo_like = sum(1 for _, rect, page_rect in placements if _placement_looks_like_logo(rect, page_rect))
    if logo_like == len(placements):
        return True

    w = int(meta.get("width") or 0)
    h = int(meta.get("height") or 0)
    if w and h and len(pages) >= 2:
        aspect = w / h
        if aspect >= 2.8 and max(w, h) < 500:
            return True

    return False


def extract_images_from_pdf_bytes(
    data: bytes,
    *,
    min_side: int = 64,
    min_bytes: int = 150,
) -> list[dict[str, Any]]:
    """
    Returnerar listor med nycklar: filename, data (bytes), mime, width, height, page.
    Dubbletter (samma bild på flera sidor) räknas en gång; organisationslogotyper filtreras bort.
    """
    if not data or not PYMUPDF_AVAILABLE or fitz is None:
        return []

    doc = None
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception:
        return []

    total_pages = len(doc)
    by_xref: dict[int, dict[str, Any]] = {}

    try:
        for page_num in range(total_pages):
            page = doc[page_num]
            page_rect = page.rect
            for img in page.get_images(full=True):
                xref = int(img[0])
                entry = by_xref.get(xref)
                if entry is None:
                    try:
                        base = doc.extract_image(xref)
                    except Exception:
                        continue
                    raw: bytes = base.get("image") or b""
                    if len(raw) < min_bytes:
                        continue
                    w = int(base.get("width") or 0)
                    h = int(base.get("height") or 0)
                    if w and h and (w < min_side or h < min_side):
                        continue
                    ext = (base.get("ext") or "png").lower()
                    if ext == "jpeg":
                        ext = "jpg"
                    mime = {
                        "png": "image/png",
                        "jpg": "image/jpeg",
                        "jpeg": "image/jpeg",
                        "gif": "image/gif",
                        "bmp": "image/bmp",
                        "tif": "image/tiff",
                        "tiff": "image/tiff",
                    }.get(ext, "application/octet-stream")
                    entry = {
                        "raw": raw,
                        "ext": ext,
                        "mime": mime,
                        "width": w,
                        "height": h,
                        "pages": set(),
                        "placements": [],
                        "first_page": page_num + 1,
                    }
                    by_xref[xref] = entry

                entry["pages"].add(page_num + 1)
                try:
                    rects = page.get_image_rects(xref)
                except Exception:
                    rects = []
                for rect in rects:
                    entry["placements"].append((page_num, rect, page_rect))

        out: list[dict[str, Any]] = []
        seq = 0
        for entry in by_xref.values():
            if _is_likely_org_logo(entry, total_pages):
                continue
            seq += 1
            ext = entry["ext"]
            fn = f"bild_{seq:02d}.{'jpeg' if ext == 'jpg' else ext}"
            out.append(
                {
                    "filename": fn,
                    "data": entry["raw"],
                    "mime": entry["mime"],
                    "width": entry["width"],
                    "height": entry["height"],
                    "page": entry["first_page"],
                }
            )
        return out
    finally:
        if doc is not None:
            doc.close()
