from __future__ import annotations

import hashlib
import io
import os
import re
from pathlib import Path
from typing import Any

UPLOADS_DIR_NAME = "uploads"
SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def safe_filename(value: str, fallback: str = "file") -> str:
    name = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ._-]+", "_", value or "").strip("._-")
    return name[:80] or fallback


def ensure_uploads_dir(base_dir: str | os.PathLike[str]) -> Path:
    path = Path(base_dir) / UPLOADS_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def file_digest(content: bytes, size: int = 16) -> str:
    return hashlib.sha256(content).hexdigest()[:size]


def media_url_from_path(base_dir: str | os.PathLike[str], path: str | os.PathLike[str]) -> str:
    try:
        rel = Path(path).resolve().relative_to((Path(base_dir) / UPLOADS_DIR_NAME).resolve())
        return "/uploads/" + rel.as_posix()
    except Exception:
        return ""


def save_binary_media(
    *,
    base_dir: str | os.PathLike[str],
    content: bytes,
    filename: str,
    kind: str = "image",
    title: str = "",
    page: int | None = None,
) -> dict[str, Any]:
    upload_dir = ensure_uploads_dir(base_dir)
    original = safe_filename(filename or f"{kind}.bin")
    stem, ext = os.path.splitext(original)
    ext = ext.lower() or ".bin"
    digest = file_digest(content)
    target = upload_dir / f"{digest}_{stem[:48]}{ext}"
    if not target.exists():
        target.write_bytes(content)
    item = {
        "kind": kind,
        "title": title or filename or kind,
        "filename": filename or target.name,
        "path": str(target),
        "url": media_url_from_path(base_dir, target),
        "size": len(content),
    }
    if page is not None:
        item["page"] = page
    return item


def extract_pdf_images(base_dir: str | os.PathLike[str], filename: str, content: bytes, *, limit: int = 12) -> list[dict[str, Any]]:
    """Extract embedded PDF images. Uses PyMuPDF when available, falls back to pypdf.

    The function is deliberately optional: text extraction must keep working even when
    a PDF has no embedded images or the optional image backend is unavailable.
    """
    images: list[dict[str, Any]] = []

    try:
        import fitz  # PyMuPDF

        doc = fitz.open(stream=content, filetype="pdf")
        for page_index in range(len(doc)):
            if len(images) >= limit:
                break
            page = doc[page_index]
            for img_index, img in enumerate(page.get_images(full=True)):
                if len(images) >= limit:
                    break
                xref = img[0]
                data = doc.extract_image(xref)
                raw = data.get("image") or b""
                if len(raw) < 512:
                    continue
                ext = (data.get("ext") or "png").lower()
                item = save_binary_media(
                    base_dir=base_dir,
                    content=raw,
                    filename=f"{Path(filename).stem}_p{page_index+1}_{img_index+1}.{ext}",
                    kind="image",
                    title=f"Изображение, стр. {page_index + 1}",
                    page=page_index + 1,
                )
                images.append(item)
        return images
    except Exception:
        pass

    try:
        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(content))
        for page_index, page in enumerate(reader.pages):
            if len(images) >= limit:
                break
            for img_index, image in enumerate(getattr(page, "images", []) or []):
                if len(images) >= limit:
                    break
                raw = getattr(image, "data", b"") or b""
                if len(raw) < 512:
                    continue
                name = getattr(image, "name", "image.png") or "image.png"
                item = save_binary_media(
                    base_dir=base_dir,
                    content=raw,
                    filename=f"{Path(filename).stem}_p{page_index+1}_{img_index+1}_{name}",
                    kind="image",
                    title=f"Изображение, стр. {page_index + 1}",
                    page=page_index + 1,
                )
                images.append(item)
    except Exception:
        return images
    return images


def primary_image_path(media: list[dict[str, Any]]) -> str | None:
    for item in media or []:
        if (item.get("kind") or "") == "image" and item.get("path"):
            return str(item["path"])
    return None
