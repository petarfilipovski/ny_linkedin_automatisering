"""
Extrahera inbäddade rasterbilder från PDF (PyMuPDF).
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


def safe_image_name_prefix(speaker_hint: str) -> str:
    """Filnamn-vänlig sträng (ASCII) för nedladdningar."""
    t = re.sub(r"[^\w\s\-]", "", (speaker_hint or "").strip(), flags=re.UNICODE)
    t = re.sub(r"\s+", "_", t)[:48]
    ascii_t = t.encode("ascii", "ignore").decode("ascii")
    return ascii_t or "talare"


def extract_images_from_pdf_bytes(
    data: bytes,
    *,
    min_side: int = 64,
    min_bytes: int = 150,
) -> list[dict[str, Any]]:
    """
    Returnerar listor med nycklar: filename, data (bytes), mime, width, height, page.
    Dubbletter (samma bild på flera sidor) filtreras bort via xref.
    """
    if not data or not PYMUPDF_AVAILABLE or fitz is None:
        return []
    doc = None
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    seen_xref: set[int] = set()
    seq = 0
    try:
        for page_num in range(len(doc)):
            page = doc[page_num]
            for img in page.get_images(full=True):
                xref = img[0]
                if xref in seen_xref:
                    continue
                seen_xref.add(xref)
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
                seq += 1
                fn = f"bild_{seq:02d}.{'jpeg' if ext == 'jpg' else ext}"
                out.append(
                    {
                        "filename": fn,
                        "data": raw,
                        "mime": mime,
                        "width": w,
                        "height": h,
                        "page": page_num + 1,
                    }
                )
    finally:
        if doc is not None:
            doc.close()
    return out
