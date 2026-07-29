from app.core.text import normalize

_PAIRS: list[tuple[str, list[str]]] = [
    ("ca phe", ["coffee", "cafe"]),
    ("tra sua", ["milk tea", "bubble tea"]),
    ("tra", ["tea"]),
    ("banh", ["bakery", "cake"]),
    ("do an", ["food"]),
    ("thuc pham", ["food", "grocery"]),
    ("nha hang", ["restaurant"]),
    ("sieu thi", ["supermarket", "mart"]),
    ("bach hoa", ["grocery", "mart"]),
    ("ban le", ["retail"]),
    ("ngan hang", ["bank", "banking"]),
    ("tai chinh", ["finance", "financial"]),
    ("thanh toan", ["payment", "pay"]),
    ("vi dien tu", ["e-wallet", "wallet"]),
    ("chung khoan", ["securities"]),
    ("bao hiem", ["insurance"]),
    ("hang khong", ["airline", "air", "aviation"]),
    ("du lich", ["travel", "tourism"]),
    ("khach san", ["hotel"]),
    ("van tai", ["logistics", "transport"]),
    ("giao hang", ["delivery", "express"]),
    ("duoc", ["pharma", "pharmacy"]),
    ("nha thuoc", ["pharmacy", "drugstore"]),
    ("benh vien", ["hospital"]),
    ("y te", ["health", "healthcare", "medical"]),
    ("giao duc", ["education"]),
    ("truong", ["school"]),
    ("dai hoc", ["university"]),
    ("bat dong san", ["real estate", "property", "land"]),
    ("xay dung", ["construction"]),
    ("vien thong", ["telecom", "telecommunication"]),
    ("cong nghe", ["technology", "tech"]),
    ("dien may", ["electronics", "appliance"]),
    ("dien thoai", ["mobile", "phone"]),
    ("thoi trang", ["fashion", "apparel"]),
    ("my pham", ["cosmetics", "beauty"]),
    ("oto", ["automotive", "car", "auto"]),
    ("xe may", ["motorbike", "motorcycle"]),
    ("nang luong", ["energy"]),
    ("san xuat", ["manufacturing"]),
    ("nong nghiep", ["agriculture", "agri"]),
    ("giai tri", ["entertainment"]),
    ("the thao", ["sport", "sports"]),
    ("game", ["gaming"]),
]

_INDEX: dict[str, set[str]] = {}


def _register(key: str, value: str) -> None:
    _INDEX.setdefault(key, set()).add(value)


for _vi, _others in _PAIRS:
    _vi_norm = normalize(_vi)
    for _other in _others:
        _other_norm = normalize(_other)
        _register(_vi_norm, _other_norm)
        _register(_other_norm, _vi_norm)
        for _sibling in _others:
            _sibling_norm = normalize(_sibling)
            if _sibling_norm != _other_norm:
                _register(_other_norm, _sibling_norm)


def expand(query: str) -> list[str]:
    base = normalize(query)
    if not base:
        return []

    variants: dict[str, None] = {base: None}
    for alias in _INDEX.get(base, ()):
        variants[alias] = None

    words = base.split()
    if len(words) > 1:
        for size in range(len(words), 1, -1):
            for start in range(len(words) - size + 1):
                phrase = " ".join(words[start : start + size])
                for alias in _INDEX.get(phrase, ()):
                    replaced = " ".join([*words[:start], alias, *words[start + size :]])
                    variants[replaced] = None
                    variants[alias] = None

    for index, word in enumerate(words):
        for alias in _INDEX.get(word, ()):
            replaced = " ".join([*words[:index], alias, *words[index + 1 :]])
            variants[replaced] = None
            if len(words) == 1:
                variants[alias] = None

    return list(variants)


def patterns(query: str) -> list[str]:
    return [f"%{variant}%" for variant in expand(query)]
