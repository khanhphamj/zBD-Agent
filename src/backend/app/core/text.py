import re
import unicodedata

from sqlalchemy import ColumnElement, func

_EXPLICIT = str.maketrans({"đ": "d", "Đ": "D", "ð": "d", "Ð": "D"})

_WORD_SPLIT = re.compile(r"[^0-9a-z]+")


def strip_accents(value: str) -> str:
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFD", value.translate(_EXPLICIT))
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def normalize(value: str) -> str:
    return " ".join(strip_accents(value).lower().split())


def slugify(value: str, max_length: int = 64) -> str:
    parts = [p for p in _WORD_SPLIT.split(normalize(value)) if p]
    return "-".join(parts)[:max_length].strip("-")


def initials(name: str, size: int = 2) -> str:
    cleaned = normalize(name)
    if not cleaned:
        return "?"
    words = cleaned.split()
    if len(words) >= size:
        return "".join(w[0] for w in words[:size]).upper()
    return cleaned[:size].lower()


def unaccent_column(column: ColumnElement[str]) -> ColumnElement[str]:
    return func.zbd_unaccent(column)


def search_term(q: str) -> str:
    return f"%{normalize(q)}%"


def tokens(q: str) -> list[str]:
    return [t for t in _WORD_SPLIT.split(normalize(q)) if t]


def next_version(current: str | None) -> str:
    if not current:
        return "v1.0"
    match = re.fullmatch(r"v?(\d+)\.(\d+)", current.strip())
    if not match:
        return "v1.0"
    major, minor = int(match.group(1)), int(match.group(2))
    if minor >= 9:
        return f"v{major + 1}.0"
    return f"v{major}.{minor + 1}"


def truncate(value: str, limit: int = 240, suffix: str = "…") -> str:
    value = (value or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - len(suffix)].rstrip() + suffix


def domain_of(email: str) -> str:
    return email.strip().lower().rsplit("@", 1)[-1] if "@" in email else ""


def handle_of(email: str) -> str:
    return email.strip().lower().split("@", 1)[0]


def clean_website(value: str) -> str:
    v = (value or "").strip().lower()
    v = re.sub(r"^[a-z]+://", "", v)
    v = v.split("/", 1)[0].split("?", 1)[0]
    return v.removeprefix("www.")
