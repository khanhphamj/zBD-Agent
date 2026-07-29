from functools import lru_cache
from pathlib import Path

from app.core.logging import get_logger

log = get_logger(__name__)

FALLBACK = "Helvetica"

CANDIDATES: list[tuple[str, str, str | None]] = [
    (
        "DejaVuSans",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ),
    (
        "DejaVuSans",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    ),
    (
        "LiberationSans",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ),
    (
        "NotoSans",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    ),
    ("ArialUnicode", "/System/Library/Fonts/Supplemental/Arial Unicode.ttf", None),
    ("ArialUnicode", "/Library/Fonts/Arial Unicode.ttf", None),
    (
        "Arial",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ),
]

PROBE = "Đắk Lắk – Cà phê ưu đãi ệ ữ ỹ ợ"


@lru_cache(maxsize=1)
def unicode_font() -> tuple[str, str]:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for family, regular, bold in CANDIDATES:
        if not Path(regular).is_file():
            continue
        try:
            pdfmetrics.registerFont(TTFont(family, regular))
            bold_name = family
            if bold and Path(bold).is_file():
                bold_name = f"{family}-Bold"
                pdfmetrics.registerFont(TTFont(bold_name, bold))
            pdfmetrics.registerFontFamily(
                family, normal=family, bold=bold_name, italic=family, boldItalic=bold_name
            )
            pdfmetrics.getFont(family).stringWidth(PROBE, 10)
        except Exception as exc:
            log.debug("font_candidate_rejected", family=family, path=regular, error=str(exc))
            continue
        else:
            log.info("pdf_font_registered", family=family, path=regular, bold=bold_name)
            return family, bold_name

    log.warning(
        "pdf_font_fallback",
        font=FALLBACK,
        detail="Không tìm thấy font Unicode; dấu tiếng Việt trong PDF sẽ hiển thị sai.",
    )
    return FALLBACK, "Helvetica-Bold"
