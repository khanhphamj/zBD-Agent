import re
from dataclasses import dataclass, field
from math import gcd
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)

THUMB_WIDTH = 640

_VI_MARKS = "ăâđêôơưàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ"
_VI_WORDS = (
    " và ",
    " của ",
    " cho ",
    " khách hàng ",
    " giải pháp ",
    " doanh nghiệp ",
    " triển khai ",
)


# PDF nhúng font Type3 vô danh dạng "Type3 (1031 0 R)" — là tên nội bộ, không
# phải font người dùng chọn, nên bỏ khỏi danh sách hiển thị.
_JUNK_FONT_RE = re.compile(r"^(Type\d|F\d+$|[A-Z]{1,3}\d+$)", re.I)


@dataclass(slots=True)
class DeckMeta:
    kind: str = "pdf"
    ratio: str | None = None
    ratio_exact: str | None = None
    palette: list[str] = field(default_factory=list)
    fonts: list[str] = field(default_factory=list)
    language: str | None = None
    page_size: str | None = None
    thumbnail: bytes | None = None
    thumbnail_ext: str | None = None

    def as_theme(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "ratio": self.ratio,
            "ratioExact": self.ratio_exact,
            "palette": self.palette,
            "fonts": self.fonts,
            "language": self.language,
            "pageSize": self.page_size,
            "detected": True,
        }


def _ratio(width: float, height: float) -> tuple[str | None, str | None]:
    if width <= 0 or height <= 0:
        return None, None
    approx = width / height
    for label, value in (("16:9", 16 / 9), ("4:3", 4 / 3), ("16:10", 16 / 10), ("A4", 1.4142)):
        if abs(approx - value) < 0.02:
            return label, f"{approx:.3f}"
    w, h = round(width), round(height)
    g = gcd(w, h) or 1
    return f"{w // g}:{h // g}", f"{approx:.3f}"


def detect_language(text: str) -> str | None:
    sample = text[:20_000]
    if not sample.strip():
        return None
    lowered = f" {sample.lower()} "
    marks = sum(lowered.count(ch) for ch in _VI_MARKS)
    words = sum(lowered.count(w) for w in _VI_WORDS)
    if marks >= 12 or words >= 3:
        return "VI"
    letters = sum(1 for ch in sample if ch.isalpha())
    return "EN" if letters >= 40 else None


def _dedupe(items: list[str], limit: int) -> list[str]:
    out: list[str] = []
    for item in items:
        clean = item.strip()
        if clean and clean not in out:
            out.append(clean)
        if len(out) >= limit:
            break
    return out


def from_pdf(data: bytes) -> DeckMeta:
    meta = DeckMeta(kind="pdf")
    try:
        import fitz
    except ImportError:
        log.warning("pymupdf_missing")
        return meta

    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        log.warning("pdf_open_failed", error=str(exc))
        return meta

    with doc:
        if not doc.page_count:
            return meta

        first = doc.load_page(0)
        rect = first.rect
        meta.ratio, meta.ratio_exact = _ratio(rect.width, rect.height)
        meta.page_size = f"{rect.width / 72:.2f}in × {rect.height / 72:.2f}in"

        fonts: list[str] = []
        colors: list[str] = []
        text_parts: list[str] = []

        for index in range(min(doc.page_count, 6)):
            page = doc.load_page(index)
            text_parts.append(page.get_text() or "")
            try:
                blocks = page.get_text("dict")
            except Exception:
                continue
            for block in blocks.get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        name = str(span.get("font") or "")
                        if name:
                            clean = re.sub(r"^[A-Z]{6}\+", "", name).split("-")[0]
                            if clean and not _JUNK_FONT_RE.match(clean):
                                fonts.append(clean)
                        raw = span.get("color")
                        if isinstance(raw, int) and raw != 0:
                            colors.append(f"#{raw & 0xFFFFFF:06X}")

        meta.fonts = _dedupe(fonts, 4)
        meta.palette = _dedupe(colors, 10)
        meta.language = detect_language(" ".join(text_parts))

        try:
            scale = THUMB_WIDTH / rect.width if rect.width else 1
            pix = first.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            meta.thumbnail = pix.tobytes("png")
            meta.thumbnail_ext = "png"
        except Exception as exc:
            log.warning("pdf_thumbnail_failed", error=str(exc))

    return meta


def render_page(data: bytes, index: int, width: int = 1280) -> bytes | None:
    """Render 1 trang PDF thành PNG. index tính từ 1."""
    try:
        import fitz
    except ImportError:
        return None
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        log.warning("pdf_open_failed", error=str(exc))
        return None
    with doc:
        if index < 1 or index > doc.page_count:
            return None
        page = doc.load_page(index - 1)
        scale = width / page.rect.width if page.rect.width else 1
        try:
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            return pix.tobytes("png")
        except Exception as exc:
            log.warning("pdf_render_failed", page=index, error=str(exc))
            return None


def page_count(data: bytes) -> int:
    try:
        import fitz
    except ImportError:
        return 0
    try:
        with fitz.open(stream=data, filetype="pdf") as doc:
            return doc.page_count
    except Exception:
        return 0


__all__ = [
    "THUMB_WIDTH",
    "DeckMeta",
    "detect_language",
    "from_pdf",
    "page_count",
    "render_page",
]
