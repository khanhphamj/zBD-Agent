import re
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

BRIDGE_TAG = '<script src="/zb-api-bridge.js"></script>'

ICON_TAG = '<link rel="icon" href="/favicon.ico">'

PAGE_TITLE = "zBD Agent"
OLD_TITLES = ("ZB Business Development",)

INNER_ICON_RE = re.compile(r'(rel=\\"icon\\"[^>]*?href=\\")[^"\\]*(\\")')

BLANK_CUST_METHOD = (
    "  _zbBlank() { return this.__zbBlank || (this.__zbBlank = { key: '', prio: 0, "
    "name: 'Chưa có khách hàng', short: '—', web: '', init: '?', industry: '—', "
    "status: 'Hoàn tất nghiên cứu', tone: 0, owner: 0, updated: '' }); }"
)

BLOCK_SOURCES: dict[str, str] = {
    "_CUST": "customers",
    "_KBS": "kbs",
    "_SKILLS": "skills",
    "_TPLS": "tpls",
}

ONELINE_SOURCES: dict[str, str] = {
    "_DIR": "dir",
}

EMPTY_SOURCES: tuple[str, ...] = ("_LOCKED", "_shareReqSeed")


@dataclass(slots=True)
class PatchReport:
    applied: list[str] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missed


def _mark(report: PatchReport, name: str, ok: bool) -> None:
    (report.applied if ok else report.missed).append(name)


_BALANCE_PAIRS = (("{", "}"), ("(", ")"), ("[", "]"))


def check_balance(before: str, after: str) -> list[str]:
    """So delta ngoặc trước/sau khi vá.

    Prototype vốn không cân bằng tuyệt đối (có ngoặc trong chuỗi, trong CSS), nên
    không thể đòi delta bằng 0 — nhưng bản vá **phải giữ nguyên delta của bản gốc**.
    Lệch đi tức là một khối JS bị chèn hở, thứ chỉ lộ ra thành SyntaxError trên
    browser. Đã vướng 3 lần: newline thô, dấu " thô, và block { không đóng.
    """
    problems: list[str] = []
    for left, right in _BALANCE_PAIRS:
        delta_before = before.count(left) - before.count(right)
        delta_after = after.count(left) - after.count(right)
        if delta_before != delta_after:
            problems.append(f"{left}{right} lệch: gốc {delta_before} -> sau vá {delta_after}")
    return problems


def _jsafe(fragment: str, where: str) -> str:
    """Source component nằm trong một chuỗi JSON: ký tự " hoặc newline thô sẽ cắt
    chuỗi và làm vỡ khâu unpack. Chặn ngay lúc dựng patch thay vì để vỡ trên browser."""
    if '"' in fragment:
        raise ValueError(f'patch {where}: chứa dấu " thô — dùng “ ” hoặc \\\\" thay thế')
    if "\n" in fragment or "\r" in fragment:
        raise ValueError(f"patch {where}: chứa newline thô — dùng nl đã escape")
    return fragment


def _patch_block(src: str, method: str, key: str, nl: str) -> tuple[str, bool]:
    anchor = f"{method}() {{{nl}"
    if anchor not in src:
        return src, False
    guard = (
        f"{anchor}"
        f"    const __zb = window.__ZB;{nl}"
        f"    if (__zb && __zb.ready && __zb.{key}) return __zb.{key};{nl}"
    )
    return src.replace(anchor, guard, 1), True


def _patch_oneline(src: str, method: str, key: str) -> tuple[str, bool]:
    anchor = f"{method}() {{ return ["
    if anchor not in src:
        return src, False
    guard = (
        f"{method}() {{ "
        f"const __zb = window.__ZB; "
        f"if (__zb && __zb.ready && __zb.{key}) return __zb.{key}; "
        f"return ["
    )
    return src.replace(anchor, guard, 1), True


def _patch_empty(src: str, method: str) -> tuple[str, bool]:
    anchor = f"{method}() {{ return ["
    if anchor not in src:
        return src, False
    guard = f"{method}() {{ const __zb = window.__ZB; if (__zb && __zb.ready) return []; return ["
    return src.replace(anchor, guard, 1), True


def _patch_prods(src: str, nl: str) -> tuple[str, bool]:
    anchor = f"_PRODS() {{ if (!this.__prods) this.__prods = [{nl}"
    if anchor not in src:
        return src, False
    guard = (
        "_PRODS() { const __zb = window.__ZB; "
        "if (__zb && __zb.ready && __zb.prods) return __zb.prods; "
        f"if (!this.__prods) this.__prods = [{nl}"
    )
    return src.replace(anchor, _jsafe(guard, "_PRODS"), 1), True


def _patch_onboarding_done(src: str) -> tuple[str, int]:
    hits = 0
    mark = (
        "if (window.__ZB && window.__ZB.ready && window.__ZB.completeOnboarding) "
        "window.__ZB.completeOnboarding(); "
    )

    ob_old = (
        "obNext: () => { const st = this.state.obStep || 1; if (st === 3) { "
        "this.setState({ screen: 'dashboard', onboarded: true }); "
    )
    if ob_old in src:
        src = src.replace(ob_old, ob_old + _jsafe(mark, "obNext"), 1)
        hits += 1

    fin_old = "finishOnboarding: () => { this.setState({ screen: 'dashboard', onboarded: true }); "
    if fin_old in src:
        src = src.replace(fin_old, fin_old + _jsafe(mark, "finishOnboarding"), 1)
        hits += 1

    tour_old = "this.setState({ tour: null, tourRect: null, tourDone: true });"
    tour_new = (
        "this.setState({ tour: null, tourRect: null, tourDone: true }); "
        "if (window.__ZB && window.__ZB.ready && window.__ZB.completeTour) "
        "window.__ZB.completeTour();"
    )
    if tour_old in src:
        src = src.replace(tour_old, _jsafe(tour_new, "tourDone"), 1)
        hits += 1

    return src, hits


def _patch_skill_versions(src: str) -> tuple[str, int]:
    """Khối “Phiên bản” trong trình soạn skill vốn là HTML cứng với v2.3/v2.1 giả.

    Thay bằng sc-for trên dữ liệu thật từ /skills/{id}/versions, và bỏ ngày cứng
    20/07/2026 ở dòng “hiện tại”.
    """
    hits = 0
    dot = (
        '<div style=\\"width:8px;height:8px;flex:none;border-radius:50%;'
        'background:#B6C3DD\\"><\\u002Fdiv>'
    )
    label = 'style=\\"font:600 11.5px Roboto,sans-serif;color:#4d5f85\\"'
    meta = 'style=\\"font:10px Roboto,sans-serif;color:#8FA2C7\\"'
    link = 'style=\\"font:600 10.5px Roboto,sans-serif;color:#0068FF;cursor:pointer\\"'

    row_open = (
        '<div style=\\"display:flex;align-items:center;gap:9px;padding:10px 0;'
        'border-bottom:1px solid #EDF2FB\\">'
    )
    old_23 = (
        f"{row_open}{dot}"
        f'<div style=\\"flex:1\\"><div {label}>v2.3<\\u002Fdiv>'
        f"<div {meta}>02/07/2026 · thêm nguồn báo chí<\\u002Fdiv><\\u002Fdiv>"
        f'<span sc-camel-on-click=\\"{{{{ skRestore23 }}}}\\" {link}>'
        f"Khôi phục<\\u002Fspan><\\u002Fdiv>"
    )
    old_21 = (
        f'<div style=\\"display:flex;align-items:center;gap:9px;padding:10px 0\\">{dot}'
        f'<div style=\\"flex:1\\"><div {label}>v2.1<\\u002Fdiv>'
        f"<div {meta}>15/06/2026<\\u002Fdiv><\\u002Fdiv>"
        f'<span sc-camel-on-click=\\"{{{{ skRestore21 }}}}\\" {link}>'
        f"Khôi phục<\\u002Fspan><\\u002Fdiv>"
    )
    live_rows = (
        f'<sc-for list=\\"{{{{ skVersions }}}}\\" as=\\"sv\\" hint-placeholder-count=\\"2\\">'
        f"{row_open}{dot}"
        f'<div style=\\"flex:1\\"><div {label}>{{{{ sv.ver }}}}<\\u002Fdiv>'
        f"<div {meta}>{{{{ sv.sub }}}}<\\u002Fdiv><\\u002Fdiv>"
        f'<span sc-camel-on-click=\\"{{{{ sv.restore }}}}\\" {link}>'
        f"Khôi phục<\\u002Fspan><\\u002Fdiv><\\u002Fsc-for>"
    )

    if old_23 in src and old_21 in src:
        src = src.replace(old_23, live_rows, 1)
        src = src.replace(old_21, "", 1)
        hits += 1

    old_date = f"<div {meta}>20/07/2026 · {{{{ userName }}}}<\\u002Fdiv>"
    new_date = f"<div {meta}>{{{{ skUpdated }}}} · {{{{ userName }}}}<\\u002Fdiv>"
    if old_date in src:
        src = src.replace(old_date, new_date, 1)
        hits += 1

    old_diff = "v2.3 (02/07) → {{ skVer }} hiện tại"
    if old_diff in src:
        src = src.replace(old_diff, "{{ skDiffFrom }} → {{ skVer }} hiện tại", 1)
        hits += 1

    old_head = "{{ skVer }} · cập nhật 20/07/2026 · {{ userName }}"
    if old_head in src:
        src = src.replace(old_head, "{{ skVer }} · cập nhật {{ skUpdated }} · {{ userName }}", 1)
        hits += 1

    if "Khôi phục v2.3" in src:
        src = src.replace("Khôi phục v2.3", "Khôi phục {{ skDiffFrom }}", 1)
        hits += 1

    old_restore = "skRestore23: () => this._skRestore('v2.3'),"
    new_restore = (
        "skRestore23: () => { const __zb = window.__ZB; "
        "const __id = (this.state.skill || {}).__id; "
        "const __list = (__zb && __zb.ready && __zb.skillVersions[__id]) || []; "
        "const __prev = __list.length > 1 ? __list[1].ver : null; "
        "if (!__prev) { this._toast('Chưa có phiên bản trước để khôi phục'); return; } "
        "__zb.restoreSkillVersion(__id, __prev).then((r) => { "
        "if (r.ok) { this.setState({ skVer: r.version, skDiffOpen: false }); "
        "this._toast('Đã khôi phục ' + __prev + ' thành ' + r.version); } "
        "else this._toast('Không khôi phục được: ' + (r.message || 'lỗi backend')); }); }, "
    )
    if old_restore in src:
        src = src.replace(old_restore, _jsafe(new_restore, "skRestore23"), 1)
        hits += 1

    anchor = "skName: (this.state.skill || {}).name || 'Skill mới',"
    if anchor in src:
        prop = (
            "skVersions: (() => { const __zb = window.__ZB; "
            "const __id = (this.state.skill || {}).__id; "
            "if (!__zb || !__zb.ready || !__id) return []; "
            "if (__zb.loadSkillVersions) __zb.loadSkillVersions(__id); "
            "const __cur = this.state.skVer || (this.state.skill || {}).ver; "
            "return (__zb.skillVersions[__id] || []) "
            ".filter((v) => v.ver !== __cur) "
            ".map((v) => ({ ver: v.ver, "
            "sub: [v.when, v.author, v.note].filter(Boolean).join(' · '), "
            "restore: () => __zb.restoreSkillVersion(__id, v.ver)"
            ".then((r) => { if (r.ok) { this.setState({ skVer: r.version }); "
            "this._toast('Đã khôi phục ' + v.ver + ' thành ' + r.version); } "
            "else this._toast('Không khôi phục được: ' + (r.message || 'lỗi backend')); }) })); "
            "})(), "
            "skUpdated: ((this.state.skill || {}).updated) || 'vừa xong', "
            "skDiffFrom: (() => { const __zb = window.__ZB; "
            "const __id = (this.state.skill || {}).__id; "
            "const __list = (__zb && __zb.skillVersions[__id]) || []; "
            "return __list.length > 1 ? __list[1].ver : 'chưa có bản trước'; })(), "
        )
        src = src.replace(anchor, _jsafe(prop, "skVersions") + anchor, 1)
        hits += 1

    return src, hits


def _patch_no_save_template(src: str) -> tuple[str, bool]:
    """Template chỉ đến từ upload, nên bỏ luôn đường “lưu proposal thành template”.

    Dialog đó mở từ finalizeProp khi đánh dấu proposal hoàn tất; chặn ở đây là cả
    dialog lẫn stSave thành không thể tới được.
    """
    old = "this.setState({ propStatus: 'final', saveTpl: true });"
    if old not in src:
        return src, False
    new = "this.setState({ propStatus: 'final' }); this._toast('Đã đánh dấu proposal hoàn tất'); "
    return src.replace(old, _jsafe(new, "finalizeProp"), 1), True


CARD_PREFIX = '<div style=\\"background:#fff;border:1px solid #DCE6F7;border-radius:12px;padding:'
DIV_CLOSE = "<\\u002Fdiv>"


def _remove_card(src: str, label: str) -> tuple[str, bool]:
    """Cắt trọn card chứa `label`.

    Đếm thẻ mở/đóng để tìm đúng biên thay vì đoán độ dài — card lồng nhiều div nên
    cắt theo số ký tự sẽ làm lệch cây DOM.
    """
    pos = src.find(label)
    if pos < 0:
        return src, False
    start = src.rfind(CARD_PREFIX, 0, pos)
    if start < 0:
        return src, False

    depth = 0
    cursor = start
    while cursor < len(src):
        next_open = src.find("<div", cursor + 1)
        next_close = src.find(DIV_CLOSE, cursor + 1)
        if next_close < 0:
            return src, False
        if 0 <= next_open < next_close:
            depth += 1
            cursor = next_open
            continue
        if depth == 0:
            return src[:start] + src[next_close + len(DIV_CLOSE) :], True
        depth -= 1
        cursor = next_close
    return src, False


def _patch_template_versions(src: str) -> tuple[str, int]:
    """Panel “Phiên bản” của Template cũng là HTML cứng: `v2.0 — hiện tại` kèm ngày
    18/07/2026, và một dòng `v1.2` với nút Khôi phục gọi `_confirm('Khôi phục v1.2?')`.
    Thay bằng dữ liệu từ /templates/{id}/versions."""
    hits = 0
    meta = 'style=\\"font:10px Roboto,sans-serif;color:#8FA2C7\\"'
    label_cur = 'style=\\"font:600 11.5px Roboto,sans-serif;color:#021F4E\\"'
    label_old = 'style=\\"font:600 11.5px Roboto,sans-serif;color:#4d5f85\\"'
    link = 'style=\\"font:600 10.5px Roboto,sans-serif;color:#0068FF;cursor:pointer\\"'
    dot_old = (
        '<div style=\\"width:8px;height:8px;flex:none;border-radius:50%;'
        'background:#B6C3DD\\"><\\u002Fdiv>'
    )

    cur_old = (
        f"<div {label_cur}>v2.0 — hiện tại<\\u002Fdiv>"
        f"<div {meta}>18/07/2026 · thêm slide Bảng giá<\\u002Fdiv>"
    )
    cur_new = (
        f"<div {label_cur}>{{{{ tplVer }}}} — hiện tại<\\u002Fdiv>"
        f"<div {meta}>{{{{ tplUpdated }}}}<\\u002Fdiv>"
    )
    if cur_old in src:
        src = src.replace(cur_old, cur_new, 1)
        hits += 1

    row_old = (
        f'<div style=\\"display:flex;align-items:center;gap:9px;padding:10px 0\\">{dot_old}'
        f'<div style=\\"flex:1\\"><div {label_old}>v1.2<\\u002Fdiv>'
        f"<div {meta}>30/06/2026<\\u002Fdiv><\\u002Fdiv>"
        f'<span sc-camel-on-click=\\"{{{{ tplRestore }}}}\\" {link}>'
        f"Khôi phục<\\u002Fspan><\\u002Fdiv>"
    )
    row_new = (
        f'<sc-for list=\\"{{{{ tplVersions }}}}\\" as=\\"tv\\" hint-placeholder-count=\\"2\\">'
        f'<div style=\\"display:flex;align-items:center;gap:9px;padding:10px 0;'
        f'border-top:1px solid #EDF2FB\\">{dot_old}'
        f'<div style=\\"flex:1\\"><div {label_old}>{{{{ tv.ver }}}}<\\u002Fdiv>'
        f"<div {meta}>{{{{ tv.sub }}}}<\\u002Fdiv><\\u002Fdiv>"
        f'<span sc-camel-on-click=\\"{{{{ tv.restore }}}}\\" {link}>'
        f"Khôi phục<\\u002Fspan><\\u002Fdiv><\\u002Fsc-for>"
    )
    if row_old in src:
        src = src.replace(row_old, row_new, 1)
        hits += 1

    if "v1.2 (30/06) → v2.0 hiện tại" in src:
        src = src.replace(
            "v1.2 (30/06) → v2.0 hiện tại", "{{ tplDiffFrom }} → {{ tplVer }} hiện tại", 1
        )
        hits += 1

    btn_old = ">Khôi phục v1.2<\\u002Fdiv>"
    if btn_old in src:
        src = src.replace(btn_old, ">Khôi phục {{ tplDiffFrom }}<\\u002Fdiv>", 1)
        hits += 1

    restore_old = "tplRestore: () => this._confirm({ title: 'Khôi phục v1.2?'"
    # Giữ BIỂU THỨC: đuôi gốc `})` đóng _confirm(, không đóng block arrow function.
    restore_new = (
        "tplRestore: () => (window.__ZB && window.__ZB.ready "
        "&& (this.state.tpl || {}).__id) "
        "? ((__list) => __list.length > 1 "
        "? window.__ZB.restoreTemplateVersion((this.state.tpl || {}).__id, __list[1].ver)"
        ".then((r) => this._toast(r.ok "
        "? ('Đã khôi phục ' + __list[1].ver + ' thành ' + r.version) "
        ": ('Không khôi phục được: ' + (r.message || 'lỗi backend')))) "
        ": this._toast('Chưa có phiên bản trước để khôi phục'))"
        "(window.__ZB.templateVersions[(this.state.tpl || {}).__id] || []) "
        ": this._confirm({ title: 'Khôi phục v1.2?'"
    )
    if restore_old in src:
        src = src.replace(restore_old, _jsafe(restore_new, "tplRestore"), 1)
        hits += 1

    return src, hits


def _patch_template_meta(src: str) -> tuple[str, int]:
    """Bảng màu / tỷ lệ / font / ngôn ngữ lấy từ theme đã detect lúc upload,
    lịch sử phiên bản lấy từ /templates/{id}/versions, và bỏ hai khối không dùng."""
    hits = 0

    for label in ("Cấu trúc proposal", "Áp dụng mặc định"):
        src, ok = _remove_card(src, label)
        if ok:
            hits += 1

    meta_old = (
        '<div style=\\"font:8px Roboto,sans-serif;color:rgba(255,255,255,.97);'
        'margin-top:3px\\">16:9 · Roboto,sans-serif · brand Zalo<\\u002Fdiv>'
    )
    meta_new = (
        '<div style=\\"font:8px Roboto,sans-serif;color:rgba(255,255,255,.97);'
        'margin-top:3px\\">{{ t.coverMeta }}<\\u002Fdiv>'
    )
    if meta_old in src:
        src = src.replace(meta_old, meta_new, 1)
        hits += 1

    seed_old = "        coverText: tp.name,"
    seed_new = (
        "        coverText: tp.name, "
        "coverMeta: [tp.ratio || '16:9', (tp.fonts || []).slice(0, 2).join(', '), "
        "tp.lang, tp.kind ? tp.kind.toUpperCase() : null].filter(Boolean).join(' · '), "
    )
    if seed_old in src:
        src = src.replace(seed_old, _jsafe(seed_new, "coverMeta"), 1)
        hits += 1

    old_cols = "tplColors: cols.map((c, i) => ({"
    new_cols = (
        "tplColors: (((window.__ZB && window.__ZB.ready "
        "&& ((this.state.tpl || {}).palette || [])).length "
        "? (this.state.tpl || {}).palette : cols)).map((c, i) => ({"
    )
    if old_cols in src:
        src = src.replace(old_cols, _jsafe(new_cols, "tplColors"), 1)
        hits += 1

    anchor = "tplColorAddOpen: !!this.state.teColorAdd,"
    if anchor in src:
        prop = (
            "tplRatioVal: ((this.state.tpl || {}).ratio) || '16:9', "
            "tplLangVal: ((this.state.tpl || {}).lang) || 'VI', "
            "tplFontVal: (((this.state.tpl || {}).fonts || []).join(', ')) || '—', "
            "tplPageSize: ((this.state.tpl || {}).pageSize) || '', "
            "tplVersions: (() => { const __zb = window.__ZB; "
            "const __id = (this.state.tpl || {}).__id; "
            "if (!__zb || !__zb.ready || !__id) return []; "
            "if (__zb.loadTemplateVersions) __zb.loadTemplateVersions(__id); "
            "const __cur = (this.state.tpl || {}).ver; "
            "return (__zb.templateVersions[__id] || []).filter((v) => v.ver !== __cur) "
            ".map((v) => ({ ver: v.ver, "
            "sub: [v.when, v.author, v.note].filter(Boolean).join(' · '), "
            "restore: () => __zb.restoreTemplateVersion(__id, v.ver).then((r) => "
            "this._toast(r.ok ? ('Đã khôi phục ' + v.ver + ' thành ' + r.version) "
            ": ('Không khôi phục được: ' + (r.message || 'lỗi backend')))) })); })(), "
            "tplUpdated: [((this.state.tpl || {}).updated) || 'vừa xong', "
            "(this.state.tpl || {}).owner || null].filter(Boolean).join(' · '), "
            "tplDiffFrom: (() => { const __zb = window.__ZB; "
            "const __id = (this.state.tpl || {}).__id; "
            "const __list = (__zb && __zb.templateVersions[__id]) || []; "
            "return __list.length > 1 ? __list[1].ver : 'chưa có bản trước'; })(), "
            "tplPreview: () => { const __zb = window.__ZB; "
            "const __id = (this.state.tpl || {}).__id; "
            "if (!__zb || !__zb.ready || !__id) { this._toast('Chưa kết nối backend'); return; } "
            "__zb.templateFileUrl(__id).then((r) => { "
            "if (!r.ok) { this._toast('Không mở được file: ' "
            "+ (r.message || 'lỗi backend')); return; } "
            "window.open(r.url, '_blank'); }); }, "
        )
        src = src.replace(anchor, _jsafe(prop, "tplMeta") + anchor, 1)
        hits += 1

    return src, hits


def _patch_template_preview(src: str) -> tuple[str, int]:
    """Panel “Xem trước” vốn vẽ slide giả (badge + tiêu đề + khối xám).

    Cho nó hiện **ảnh trang thật** render từ PDF. Các prop nằm trong cùng một object
    literal nên không khai báo biến giữa được; `tplPrevMeta` được tính trước nhất
    nên nó tính info rồi gắn vào `this.__zbPrev`, các prop sau đọc lại.
    """
    hits = 0

    meta_old = "tplPrevMeta: rows.length + ' slide · '"
    meta_new = (
        "tplPrevMeta: (() => { const __zb = window.__ZB; "
        "const __id = (this.state.tpl || {}).__id; "
        "const __i = (this.state.tePrevI || 0) + 1; "
        "let __info = { img: null, total: 0, reason: null }; "
        "if (__zb && __zb.ready && __id && __zb.loadTemplatePages) { "
        "const __m = __zb.templatePages[__id]; "
        "if (!__m) __zb.loadTemplatePages(__id); "
        "else { __info.total = __m.total; __info.reason = __m.renderable ? null : __m.reason; "
        "if (__m.renderable) __info.img = "
        "__zb.pageImageReady(__id, __i) || __zb.pageImage(__id, __i); } } "
        "this.__zbPrev = __info; "
        "return (__info.total || (this.state.tpl || {}).slides || rows.length) "
        "+ ' slide · ' + (__info.img ? 'ảnh thật từ file · ' : '') + ''; })() + ''.concat('')"
    )
    if meta_old in src:
        src = src.replace(meta_old, _jsafe(meta_new, "tplPrevMeta"), 1)
        hits += 1

    stage_old = (
        "tplPrevStage: 'margin-top:16px;border-radius:14px;padding:34px 36px;"
        "min-height:236px;box-sizing:border-box;display:flex;flex-direction:column;"
        "justify-content:center;transition:all .3s;' + (dark ? "
    )
    stage_new = (
        "tplPrevStage: (this.__zbPrev || {}).img "
        # Tỷ lệ lấy từ chính file (ratioExact ~1.778) thay vì hardcode 16:9, và bỏ
        # min-height vì nó chèn với aspect-ratio làm hộp cao hơn tỷ lệ thật của slide.
        "? 'margin-top:16px;border-radius:14px;box-sizing:border-box;overflow:hidden;"
        "border:1px solid #DCE6F7;aspect-ratio:' "
        "+ (((this.state.tpl || {}).ratioExact) || 1.7778) "
        "+ ';background:#F2F6FD center\u002fcontain no-repeat url(' "
        "+ this.__zbPrev.img + ')' "
        ": 'margin-top:16px;border-radius:14px;padding:34px 36px;min-height:236px;"
        "box-sizing:border-box;display:flex;flex-direction:column;justify-content:center;"
        "transition:all .3s;' + (dark ? "
    )
    if stage_old in src:
        src = src.replace(stage_old, _jsafe(stage_new, "tplPrevStage"), 1)
        hits += 1

    title_old = "tplPrevTitle: (cur || ['—'])[0],"
    title_new = (
        "tplPrevTitle: (this.__zbPrev || {}).img ? '' "
        ": ((this.state.tpl || {}).__id ? 'Chưa xem trước được file này' "
        ": (cur || ['—'])[0]), "
    )
    if title_old in src:
        src = src.replace(title_old, _jsafe(title_new, "tplPrevTitle"), 1)
        hits += 1

    blocks_old = "tplPrevBlocks: (prevI === 0 ? [] : [1, 2, 3])"
    blocks_new = (
        "tplPrevBlocks: ((this.__zbPrev || {}).img || (this.state.tpl || {}).__id "
        "|| prevI === 0 ? [] : [1, 2, 3])"
    )
    if blocks_old in src:
        src = src.replace(blocks_old, _jsafe(blocks_new, "tplPrevBlocks"), 1)
        hits += 1

    body_old = "tplPrevBody: prevI === 0 ? "
    body_new = (
        "tplPrevBody: (this.__zbPrev || {}).img ? '' "
        ": (this.state.tpl || {}).__id "
        "? ((this.__zbPrev || {}).reason "
        "? 'Không render được trang từ file này — upload lại bản PDF.' "
        ": 'Đang tải ảnh trang từ file đã upload…') "
        ": prevI === 0 ? "
    )
    if body_old in src:
        src = src.replace(body_old, _jsafe(body_new, "tplPrevBody"), 1)
        hits += 1

    hint_old = "tplPrevHint: 'Bản xem trước dùng dữ liệu mẫu',"
    hint_new = (
        "tplPrevHint: (this.__zbPrev || {}).img "
        "? 'Ảnh render từ chính file đã upload' "
        ": ((this.__zbPrev || {}).reason || 'Bản xem trước dùng dữ liệu mẫu'), "
    )
    if hint_old in src:
        src = src.replace(hint_old, _jsafe(hint_new, "tplPrevHint"), 1)
        hits += 1

    # điều hướng phải theo số trang thật, không theo rows.length của mock
    nav_old = "tplPrevNext: () => this.setState({ tePrevI: Math.min(rows.length - 1, prevI + 1) }),"
    nav_new = (
        "tplPrevNext: () => this.setState({ tePrevI: Math.min("
        "(((this.__zbPrev || {}).total || rows.length) - 1), prevI + 1) }), "
    )
    if nav_old in src:
        src = src.replace(nav_old, _jsafe(nav_new, "tplPrevNext"), 1)
        hits += 1

    no_old = "tplPrevNo: 'Slide ' + (prevI + 1) + '/' + rows.length,"
    no_new = (
        "tplPrevNo: 'Slide ' + (prevI + 1) + '\u002f' "
        "+ ((this.__zbPrev || {}).total || (this.state.tpl || {}).slides || rows.length),"
    )
    if no_old in src:
        src = src.replace(no_old, _jsafe(no_new, "tplPrevNo"), 1)
        hits += 1

    return src, hits


PDF_ONLY_TEXT: tuple[tuple[str, str], ...] = (
    ("tpl: 'Upload PowerPoint'", "tpl: 'Upload PDF'"),
    (
        "tpl: 'File .pptx sẽ được tách bố cục thành template mới'",
        "tpl: 'File PDF sẽ được tách từng trang thành slide của template'",
    ),
    (
        "tpl: 'Đã tạo template nháp từ file PowerPoint'",
        "tpl: 'Đã thêm template từ file PDF'",
    ),
    ("Sinh proposal &amp; xuất PowerPoint", "Sinh proposal &amp; xuất PDF"),
    (
        "'Upload PowerPoint sẵn có để dùng làm mẫu'",
        "'Upload proposal PDF sẵn có để dùng làm mẫu'",
    ),
    ("'Xuất PowerPoint / PDF'", "'Xuất proposal PDF'"),
)

EXP_FORMATS_OLD = (
    "expFormats: [['PPTX', 'PowerPoint (.pptx)', "
    "'Sửa được text, hình và bố cục trong PowerPoint hoặc Google Slides.'], "
    "['PDF', 'PDF (.pdf)', "
    "'Giữ nguyên bố cục và font trên mọi máy — không sửa được.']]"
)
EXP_FORMATS_NEW = (
    "expFormats: [['PDF', 'PDF (.pdf)', "
    "'Giữ nguyên bố cục và font trên mọi máy — chuẩn để gửi khách hàng.']]"
)

EXP_FMT_OLD = "const fmt = (this.state.expFmt || 0) === 0 ? 'PPTX' : 'PDF';"
EXP_FMT_NEW = "const fmt = 'PDF';"

# Nhãn/tên file/kích cỡ trong hộp kết xuất đều suy ra từ chỉ số expFmt (0 = PPTX).
# Còn một định dạng nên mọi biểu thức đó phải trở thành PDF, nếu không UI vẫn ghi
# “PPTX” cho lựa chọn duy nhất.
PDF_ONLY_EVERY: tuple[tuple[str, str], ...] = (
    ("(this.state.expFmt || 0) === 0 ? 'PPTX' : 'PDF'", "'PDF'"),
    ("(this.state.expFmt || 0) === 0 ? 'PDF' : 'PPTX'", "'PDF'"),
    ("(this.state.expFmt || 0) === 0 ? '8,4 MB' : '3,1 MB'", "'3,1 MB'"),
    ("fmt === 'PPTX' ? '8,4 MB' : '3,1 MB'", "'3,1 MB'"),
    ("fmt === 'PPTX' ? 'pptx' : 'pdf'", "'pdf'"),
    (
        "h.fmt === 'PPTX' ? 'background:#E8F1FF;color:#1A30A0' "
        ": 'background:#FDF0EE;color:#B93327'",
        "'background:#FDF0EE;color:#B93327'",
    ),
)

EXP_SEED_OLD = "if (cur < 2) return [];"
EXP_SEED_NEW = "if (cur < 2) return []; return [];"


def _patch_pdf_only(src: str) -> tuple[str, int]:
    """Proposal chỉ còn định dạng PDF: template chỉ nhận upload PDF và hộp xuất bản
    bỏ lựa chọn PPTX. Prototype vốn ghi cứng “PowerPoint” ở nhiều chỗ chữ, và
    _EXPSEED dựng sẵn danh sách file đã xuất (kèm dung lượng bịa) — trả rỗng để
    danh sách chỉ hiện file thật của phiên làm việc.
    """
    hits = 0
    for old, new in PDF_ONLY_TEXT:
        if old in src:
            src = src.replace(old, _jsafe(new, "pdfOnly"), 1)
            hits += 1
    for old, new in PDF_ONLY_EVERY:
        found = src.count(old)
        if found:
            src = src.replace(old, new)
            hits += found
    for old, new in (
        (EXP_FORMATS_OLD, EXP_FORMATS_NEW),
        (EXP_FMT_OLD, EXP_FMT_NEW),
        (EXP_SEED_OLD, EXP_SEED_NEW),
    ):
        if old in src:
            src = src.replace(old, new, 1)
            hits += 1
    return src, hits


CUST_STATUS_OLD = (
    '<span style=\\"font:600 10.5px Roboto,sans-serif;color:#12A150;background:#E7F8EF;'
    'border-radius:20px;padding:4px 10px\\">Hoàn tất nghiên cứu<\\u002Fspan>'
)
CUST_STATUS_NEW = '<span style=\\"{{ custStatusStyle }}\\">{{ custStatus }}<\\u002Fspan>'

CUST_STATUS_PROPS = (
    "custStatus: cust.status || 'Chưa nghiên cứu', "
    "custStatusStyle: 'font:600 10.5px Roboto,sans-serif;border-radius:20px;padding:4px 10px;'"
    " + ((cust.status || '').indexOf('Đang') === 0 "
    "? 'color:#B26A00;background:#FFF4E0' "
    ": (cust.status || '').indexOf('Hoàn tất') === 0 "
    "? 'color:#12A150;background:#E7F8EF' "
    ": 'color:#4d5f85;background:#EDF2FB'), "
)


PRIO_HEADER = (
    '<span sc-camel-on-click=\\"{{ custSortPrio }}\\" '
    'title=\\"Sắp xếp theo độ ưu tiên\\" style=\\"{{ custPrioHdr }}\\">'
    "Ưu tiên {{ custPrioArrow }}<\\u002Fspan>"
)

PRIO_CELL_DASH = (
    '<span style=\\"min-width:0\\"><span style=\\"{{ c.prioChip }}\\">'
    '<span style=\\"{{ c.prioDot }}\\"><\\u002Fspan>{{ c.prio }}'
    "<\\u002Fspan><\\u002Fspan>"
)

PRIO_SKELETON = ' <span class=\\"zb-skel\\" style=\\"{{ sk.mid }}\\"><\\u002Fspan>'

CUST_GRID_OLD = "grid-template-columns:26px 1.9fr .8fr .9fr 1.3fr .9fr .9fr 34px"
CUST_GRID_NEW = "grid-template-columns:26px 1.9fr .9fr 1.3fr .9fr .9fr 34px"



def _patch_cust_columns(src: str) -> tuple[str, int]:
    """Bỏ cột “Ưu tiên” khỏi hai bảng khách hàng.

    Bảng ở màn Khách hàng khai 8 cột grid nhưng hàng chỉ render 7 ô — **không có ô ưu
    tiên** — nên từ cột thứ ba trở đi mọi thứ lệch trái một nhịp: chip trạng thái nằm
    dưới NGÀNH, người phụ trách nằm dưới TRẠNG THÁI. Bảng ở Dashboard lệch kiểu khác:
    5 ô và 5 tiêu đề nhưng grid chỉ khai 4 cột, nên ô cuối rớt xuống dòng dưới.

    Grid của bảng Dashboard giữ nguyên 4 cột: bỏ ô ưu tiên là còn đúng 4 ô.

    Chọn cách bỏ cột thay vì thêm ô: `Customer` trong DB **không có** trường độ ưu
    tiên, `_PRIO()` của prototype là High/Medium/Low còn bridge gán cứng `prio: 0` —
    thêm ô vào chỉ để hiện “High” cho mọi khách hàng, tức lại là dữ liệu bịa.
    """
    hits = 0

    count = src.count(PRIO_HEADER)
    if count:
        src = src.replace(PRIO_HEADER, "")
        hits += count

    if PRIO_CELL_DASH in src:
        src = src.replace(PRIO_CELL_DASH, "", 1)
        hits += 1

    skeleton_at = src.find(CUST_GRID_OLD)
    if skeleton_at != -1:
        window_end = src.find("<\u002Fsc-for>", skeleton_at)
        window = src[skeleton_at:window_end]
        if PRIO_SKELETON in window:
            src = src[:skeleton_at] + window.replace(PRIO_SKELETON, "", 1) + src[window_end:]
            hits += 1

    count = src.count(CUST_GRID_OLD)
    if count:
        src = src.replace(CUST_GRID_OLD, CUST_GRID_NEW)
        hits += count

    return src, hits


def _patch_cust_status(src: str) -> tuple[str, int]:
    """Badge trạng thái ở trang chi tiết khách hàng bị ghi cứng “Hoàn tất nghiên cứu”
    ngay trong markup, nên một khách hàng đang chạy Deep Research vẫn hiện là đã hoàn
    tất. Nối vào `cust.status` — cùng nguồn với bảng danh sách."""
    hits = 0
    if CUST_STATUS_OLD in src:
        src = src.replace(CUST_STATUS_OLD, CUST_STATUS_NEW, 1)
        hits += 1
    anchor = "custName: cust.name,"
    if anchor in src:
        src = src.replace(anchor, _jsafe(CUST_STATUS_PROPS, "custStatus") + anchor, 1)
        hits += 1
    return src, hits


def _patch_template_upload(src: str) -> tuple[str, int]:
    """Template Library chỉ nhận upload PDF — bỏ đường tạo template thủ công.

    Nút “+ Tạo template” trước đây điều hướng sang trình soạn `tpledit` và không lưu
    gì vào DB; nay nó mở hộp chọn file và đẩy qua POST /templates/import.
    """
    hits = 0

    btn = (
        "border-radius:8px;padding:9px 14px;font:600 12.5px Roboto,sans-serif;"
        "white-space:nowrap;flex:none;cursor:pointer;"
        "transition:all .22s cubic-bezier(.22,1,.36,1)"
    )
    two_buttons = (
        f'<div style=\\"border:1px solid #DCE6F7;color:#4d5f85;{btn}\\" '
        f'sc-camel-on-click=\\"{{{{ openUpTpl }}}}\\" '
        f'style-hover=\\"border-color:#0068FF;color:#0068FF\\">'
        f"Upload PowerPoint<\\u002Fdiv>"
        f'<div style=\\"background:#0068FF;color:#fff;{btn}\\" '
        f'style-hover=\\"background:#1A30A0\\" '
        f'sc-camel-on-click=\\"{{{{ openNewTpl }}}}\\">+ Tạo template<\\u002Fdiv>'
    )
    one_button = (
        f'<div style=\\"border:1px solid #DCE6F7;color:#4d5f85;{btn}\\" '
        f'style-hover=\\"border-color:#0068FF;color:#0068FF\\" '
        f'sc-camel-on-click=\\"{{{{ tplReload }}}}\\">Reload<\\u002Fdiv>'
        f'<div style=\\"background:#0068FF;color:#fff;{btn}\\" '
        f'style-hover=\\"background:#1A30A0\\" '
        f'sc-camel-on-click=\\"{{{{ openUpTpl }}}}\\">'
        f"+ Upload PDF<\\u002Fdiv>"
    )
    if two_buttons in src:
        src = src.replace(two_buttons, one_button, 1)
        hits += 1

    reload_anchor = "openUpTpl: () => "
    reload_prop = (
        "tplReload: () => { const __zb = window.__ZB; "
        "if (!__zb || !__zb.ready || !__zb.loadAll) "
        "{ this._toast('Chưa kết nối backend'); return; } "
        "__zb.loadAll().then(() => this._toast('Đã tải lại danh sách template'), "
        "() => this._toast('Tải lại thất bại')); }, "
    )
    if reload_anchor in src:
        src = src.replace(reload_anchor, _jsafe(reload_prop, "tplReload") + reload_anchor, 1)
        hits += 1

    old = "openUpTpl: () => this.setState({ kbUp: 'form', kbUpQ: [], kbUpPct: 0, upFor: 'tpl' }),"
    new = (
        "openUpTpl: () => { const __zb = window.__ZB; "
        "if (!__zb || !__zb.ready || !__zb.pickTemplateFile) "
        "{ this._toast('Chưa kết nối backend'); return; } "
        "__zb.pickTemplateFile().then((r) => { "
        "if (r.cancelled) return; "
        "if (!r.ok) { this._toast('Tải template thất bại: ' "
        "+ (r.message || 'lỗi backend')); return; } "
        "const bad = (r.items || []).filter((x) => x.status === 'failed'); "
        "const pages = (r.items || []).reduce((n, x) => n + (x.slides || 0), 0); "
        "this.setState({ libScope: 0, tplQ: '', tplF: 0 }); "
        "this._toast('Đã tải ' + r.created + ' template (' + pages + ' trang) vào database' "
        "+ (bad.length ? ' — ' + bad.length + ' file lỗi: ' + bad[0].message : '')); "
        "}); }, "
    )
    if old in src:
        src = src.replace(old, _jsafe(new, "openUpTpl"), 1)
        hits += 1

    return src, hits


TOOL_KEYS = ("web_search", "crawler", "product_kb", "template_library")


def _patch_skill_tools(src: str) -> tuple[str, bool]:
    """Panel “Công cụ & nguồn dữ liệu” vốn toggle vào state cục bộ `seTools` và mất
    khi rời màn. Nay đọc từ `skill.tools` của DB và ghi lại qua PATCH /skills/{id}."""
    old = "        const on = tov[i] !== undefined ? tov[i] : t[2];"
    if old not in src:
        return src, False
    keys = ", ".join(f"'{k}'" for k in TOOL_KEYS)
    new = (
        f"        const __keys = [{keys}];{{NL}}"
        "        const __zb = window.__ZB;{NL}"
        "        const __sid = (this.state.skill || {}).__id;{NL}"
        "        const __live = (__zb && __zb.ready && __sid) "
        "? ((this.state.skill || {}).tools || []) : null;{NL}"
        "        const on = tov[i] !== undefined ? tov[i] "
        ": (__live ? __live.indexOf(__keys[i]) > -1 : t[2]);"
    )
    return src.replace(old, _jsafe(new.replace("{NL}", " "), "skTools"), 1), True


def _patch_skill_tools_save(src: str) -> tuple[str, bool]:
    old = (
        "          toggle: () => { const nv = Object.assign({}, tov); nv[i] = !on; "
        "this.setState({ seTools: nv }); "
        "this._toast((!on ? 'Đã bật ' : 'Đã tắt ') + t[0]); } };"
    )
    if old not in src:
        return src, False
    new = (
        "          toggle: () => { const nv = Object.assign({}, tov); nv[i] = !on; "
        "this.setState({ seTools: nv }); "
        "const __zb2 = window.__ZB; "
        "const __sid2 = (this.state.skill || {}).__id; "
        "if (__zb2 && __zb2.ready && __sid2 && __zb2.saveSkillTools) { "
        "const __sel = []; "
        "for (let k = 0; k < __keys.length; k++) { "
        "const __v = nv[k] !== undefined ? nv[k] : "
        "(((this.state.skill || {}).tools || []).indexOf(__keys[k]) > -1); "
        "if (__v) __sel.push(__keys[k]); } "
        "__zb2.saveSkillTools(__sid2, __sel).then((r) => this._toast(r.ok "
        "? ((!on ? 'Đã bật ' : 'Đã tắt ') + t[0] + ' — đã lưu') "
        ": ('Không lưu được công cụ: ' + (r.message || 'lỗi backend')))); return; } "
        "this._toast((!on ? 'Đã bật ' : 'Đã tắt ') + t[0]); } };"
    )
    return src.replace(old, _jsafe(new, "skToolsSave"), 1), True


KB_ICON_CSS = (
    "width:13px;height:16px;flex:none;border-radius:1.5px 3px 3px 1.5px;"
    "background:#0068FF;"
    "box-shadow:inset 2.5px 0 0 rgba(255,255,255,.97),inset 3.5px 0 0 rgba(2,31,78,.5)"
)


def _patch_kb_icon(src: str) -> tuple[str, bool]:
    """Knowledge Base dùng glyph CSS (trang giấy có gờ sách) thay vì ảnh pptx luân phiên.

    Giữ đúng hình học người dùng đưa (13×16, bo góc lệch, spine bằng inset shadow),
    nhưng đổi hai màu trắng sang màu thương hiệu — ô icon nằm trên card trắng nên
    `rgba(255,255,255,.9)` sẽ vô hình.
    """
    old = (
        "iconStyle: 'width:22px;height:22px;flex:none;background:url(' "
        "+ window.__resources['pptx/media/image' + kb.icon + '.png'] "
        "+ ') center/contain no-repeat',"
    )
    if old not in src:
        return src, False
    new = f"iconStyle: '{KB_ICON_CSS}',"
    return src.replace(old, _jsafe(new, "kbIcon"), 1), True


def _patch_prod_hub_sub(src: str) -> tuple[str, int]:
    hits = 0

    old_text = (
        "4 giải pháp ZBS: OA · Business Message · Mini App · Zalo AI · nguồn: "
        "zalo.solutions, oa.zalo.me, mini.zalo.me, zalo.ai · cập nhật 26/07/2026"
    )
    if old_text in src:
        src = src.replace(old_text, "{{ prodHubSub }}", 1)
        hits += 1

    anchor = "prodNewOpen: !!this.state.prodNew,"
    if anchor in src:
        prop = (
            "prodHubSub: (() => { const __ps = this._PRODS(); "
            "const __fam = Array.from(new Set(__ps.map((p) => p.family).filter(Boolean))); "
            "const __src = Array.from(new Set(__ps.flatMap((p) => (p.sources || [])"
            ".map((s) => String(s[0]).split('/')[0])))).slice(0, 4); "
            "return __ps.length + ' sản phẩm'"
            " + (__fam.length ? ' · ' + __fam.join(' · ') : '')"
            " + (__src.length ? ' · nguồn: ' + __src.join(', ') : ''); })(), "
        )
        src = src.replace(anchor, _jsafe(prop, "prodHubSub") + anchor, 1)
        hits += 1

    return src, hits


NOTIF_OLD_UNREAD = (
    "_unread() { const rd = this.state.notifRead || {}; let n = 0; "
    "for (let i = 0; i < 3; i++) if (rd[i] !== true) n++; return n; }"
)

NOTIF_METHODS = (
    "_unread() { return this._zbNotifs().filter((n) => !n.read).length; }"
    " _zbNotifs() { const z = window.__ZB; return (z && z.notifs) || []; }"
    " _zbNotifFiltered() { const f = this.state.notifF || 0; "
    "return this._zbNotifs().filter((n) => f === 0 || n.f === f); }"
    " _zbWeek() { const z = window.__ZB; "
    "return (z && z.notifWeek) || { research: 0, proposal: 0, share: 0, comment: 0, total: 0 }; }"
    " _zbTopProds() { const z = window.__ZB; return (z && z.topProds) || []; }"
    " _zbNotifIcon(n) { const r = window.__resources || {}; "
    "return r['pptx/media/image' + n.icon + '.png'] || ''; }"
    " _zbNotifRead(n, on) { const z = window.__ZB; if (z && z.setNotifRead) "
    "z.setNotifRead(n.id, on); }"
    " _zbNotifOpen(n) { if (!n.read) this._zbNotifRead(n, true); this._zbNotifGo(n); }"
    " _zbNotifGo(n) { "
    "if (n.screen === 'customer') { "
    "const c = this._LIVECUST().find((x) => x.__id === n.refId); "
    "if (c) { this.setState({ screen: 'customer', tab: 'research', cust: c, "
    "notifOpen: false }); return; } return this.go('customers'); } "
    "if (n.screen === 'proposal') { "
    "const p = this._LIVEPROPS().find((x) => x.__id === n.refId); "
    "if (p) { this.setState({ screen: 'proposal', prop: p, notifOpen: false }); return; } "
    "return this.go('proposals'); } "
    "return this.go(n.screen); }"
    " _zbProdOf(t) { return this._PRODS().find((x) => x.__id === t.id) || this._prodBy(t.name); }"
    " _zbCustDetail() { const c = this.state.cust || {}; const id = c.__id; "
    "if (!id) return null; "
    "const cache = this.__zbCd || (this.__zbCd = {}); "
    "if (Object.prototype.hasOwnProperty.call(cache, id)) return cache[id]; "
    "cache[id] = null; const z = window.__ZB; "
    "if (z && z.customerDetail) z.customerDetail(id).then((r) => { "
    "cache[id] = r.ok ? r.customer : null; this.forceUpdate(); }); "
    "return null; }"
    " _zbCustSet(f, v) { const c = this.state.cust || {}; const z = window.__ZB; "
    "if (!z || !c.__id) return; const d = this.__zbCd[c.__id]; "
    "if (d) d.facts = (d.facts || []).map((x) => x.id === f.id "
    "? Object.assign({}, x, { verdict: v === 'no' ? 'rejected' : v }) : x); "
    "this.forceUpdate(); "
    "z.setFactVerdict(c.__id, f.id, v).then((r) => this._toast(r.ok "
    "? (v === 'ok' ? 'Đã xác nhận “' + f.topic + '”' : 'Đã loại bỏ “' + f.topic + '”') "
    ": ('Không lưu được: ' + (r.message || 'lỗi backend')))); }"
    " _zbCut(x, n) { const t = String(x || ''); "
    "return t.length > n ? t.slice(0, n - 1) + '…' : t; }"
    " _zbAuditDet() { const d = this.state.auditDet; if (!d || !d.id) return null; "
    "const cache = this.__zbAudD || (this.__zbAudD = {}); "
    "if (Object.prototype.hasOwnProperty.call(cache, d.id)) return cache[d.id]; "
    "cache[d.id] = null; const z = window.__ZB; "
    "if (z && z.auditDetail) z.auditDetail(d.id).then((r) => { "
    "cache[d.id] = r.ok ? r.detail : null; this.forceUpdate(); }); "
    "return null; }"
    " _zbAudit() { const cats = this.state.auditCats || []; "
    "const cat = cats[(this.state.auditF || 0) - 1] || ''; "
    "const size = 20 + 20 * (this.state.auditN || 0); "
    "const days = [1, 7, 30, 0][this.state.auditRange || 0]; "
    "const key = cat + '|' + size + '|' + days; "
    "const cache = this.__zbAud || (this.__zbAud = {}); "
    "if (Object.prototype.hasOwnProperty.call(cache, key)) return cache[key]; "
    "cache[key] = null; const z = window.__ZB; "
    "if (z && z.auditLog) z.auditLog({ category: cat, size: size, days: days })"
    ".then((r) => { "
    "cache[key] = r; "
    "if (r.ok && r.categories.length && !(this.state.auditCats || []).length) "
    "this.setState({ auditCats: r.categories }); else this.forceUpdate(); }); "
    "return null; }"
    " _zbHost(u) { if (!u) return ''; "
    "const t = String(u); const j = t.indexOf('//'); "
    "const h = (j >= 0 ? t.slice(j + 2) : t).split('/')[0]; "
    "return h.slice(0, 4) === 'www.' ? h.slice(4) : h; }"
)

NOTIF_TOP_HEAD = "notifTop: [0, 1, 4].map("
NOTIF_TOP_TAIL = "go: () => this.go('notifs') }; }),"
NOTIF_TOP_NEW = (
    "notifTop: this._zbNotifs().slice(0, 3).map((n, i, a) => ({ title: n.title, "
    "body: (n.body ? n.body + ' · ' : '') + n.time, "
    "dot: 'width:8px;height:8px;flex:none;border-radius:50%;margin-top:5px;background:' "
    "+ (n.read ? 'rgba(182,195,221,.9)' : '#0068FF'), "
    "rowStyle: 'display:flex;gap:10px;padding:11px 4px;cursor:pointer;transition:all .2s' "
    "+ (i < a.length - 1 ? ';border-bottom:1px solid rgba(220,230,247,.7)' : ''), "
    "go: () => this._zbNotifOpen(n) })), "
    "notifTopEmpty: this._zbNotifs().length === 0,"
)

NOTIF_ROWS_HEAD = "notifRows: ["
NOTIF_ROWS_TAIL = (
    "}).filter((n) => { const f = this.state.notifF || 0; return f === 0 || n.f === f; }),"
)
NOTIF_ROWS_NEW = (
    "notifRows: this._zbNotifFiltered().map((n, i) => ({ i, title: n.title, body: n.body, "
    "time: n.time, cat: n.cat, f: n.f, "
    "rowStyle: 'display:flex;align-items:flex-start;gap:12px;padding:14px 0;"
    "border-bottom:1px solid #EDF2FB;cursor:pointer;transition:all .22s ease;"
    "animation:zbRise .32s cubic-bezier(.22,1,.36,1) both;animation-delay:' + (i * .025) "
    "+ 's;' + (n.read ? 'opacity:.6' : ''), "
    "dotStyle: 'width:8px;height:8px;flex:none;border-radius:50%;margin-top:6px;' "
    "+ (n.read ? 'background:transparent' : 'background:#0068FF'), "
    "iconStyle: 'width:20px;height:20px;flex:none;margin-top:1px;background:url(' "
    "+ this._zbNotifIcon(n) + ') center/contain no-repeat', "
    "titleStyle: 'font:' + (n.read ? '600' : '700') + ' 12.5px Roboto,sans-serif;color:#021F4E', "
    "catChip: 'flex:none;font:600 9.5px Roboto,sans-serif;border-radius:20px;padding:3px 9px;' "
    "+ ({ Research: 'color:#1A30A0;background:#DFF4FF', "
    "Proposal: 'color:#E8A13C;background:#FDF3E3', "
    "'Chia sẻ': 'color:#1A30A0;background:#E8F1FF', "
    "'Bình luận': 'color:#12A150;background:#E7F8EF' }[n.cat] "
    "|| 'color:#4d5f85;background:#F2F6FD'), "
    "readLabel: n.read ? 'Chưa đọc' : 'Đã đọc', "
    "toggleRead: (e) => { e.stopPropagation(); this._zbNotifRead(n, !n.read); }, "
    "open: () => this._zbNotifOpen(n) })), "
    "notifListEmpty: this._zbNotifFiltered().length === 0, "
    "notifEmptyText: this._zbNotifs().length "
    "? 'Không có thông báo nào trong nhóm này.' "
    ": 'Chưa có thông báo nào — Deep Research xong, proposal sinh xong, bình luận và "
    "chia sẻ sẽ hiện ở đây.',"
)

NOTIF_SUMMARY_OLD = (
    "notifSummary: (() => { const rd = this.state.notifRead || {}; "
    "const un = [0,1,2,3,4,5,6].filter((i) => !rd[i]).length; "
    "return un + ' chưa đọc · 7 thông báo trong 7 ngày qua'; })(),"
)
NOTIF_SUMMARY_NEW = (
    "notifSummary: this._unread() + ' chưa đọc · ' + this._zbWeek().total "
    "+ ' thông báo trong 7 ngày qua', "
    "nwResearch: this._zbWeek().research, nwProposal: this._zbWeek().proposal, "
    "nwShare: this._zbWeek().share, nwComment: this._zbWeek().comment,"
)

MARK_ALL_RE = re.compile(
    r"markAllRead: \(\) => \{ const all = \{\}; for \(let i = 0; i < 7; i\+\+\) "
    r"all\[i\] = true; this\.setState\(\{ notifRead: all[^)]*\}\); "
    r"this\._toast\('[^']*'\); \},"
)
MARK_ALL_NEW = (
    "markAllRead: () => { const z = window.__ZB; this.setState({ notifOpen: false }); "
    "if (!z || !z.markAllRead) return; "
    "if (!this._zbNotifs().some((n) => !n.read)) "
    "{ this._toast('Không còn thông báo chưa đọc'); return; } "
    "z.markAllRead().then((r) => this._toast(r && r.ok === false "
    "? ('Không cập nhật được: ' + (r.message || 'lỗi backend')) "
    ": 'Đã đánh dấu tất cả thông báo là đã đọc')); },"
)

NOTIF_FILTERS_OLD = "notifFilters: ['Tất cả', 'Research', 'Proposal', 'Sản phẩm', 'Bình luận']"
NOTIF_FILTERS_NEW = "notifFilters: ['Tất cả', 'Research', 'Proposal', 'Chia sẻ', 'Bình luận']"

WEEK_BOLD = '<b style=\\"margin-left:auto;color:#021F4E\\">'
WEEK_ROWS: tuple[tuple[str, str, str, str], ...] = (
    ("Deep Research hoàn tất", "6", "nwResearch", ""),
    ("Proposal cần xử lý", "3", "nwProposal", ""),
    ("Cập nhật sản phẩm", "4", "nwShare", "Chia sẻ &amp; quyền"),
    ("Bình luận nhắc bạn", "2", "nwComment", ""),
)

TOP_PRODS_HEAD = "topProds: [['ZBS Template Message', 68, '#0068FF']"
TOP_PRODS_TAIL = "go: () => this.setState({ screen: 'products', prod: pd }) }; }),"
TOP_PRODS_NEW = (
    "topProds: this._zbTopProds().map((t) => ({ name: t.name, pct: t.pct + '%', "
    "barStyle: t.barStyle, "
    "go: () => this.setState({ screen: 'products', prod: this._zbProdOf(t) }) })), "
    "topProdsEmpty: this._zbTopProds().length === 0,"
)

EMPTY_STATES: tuple[tuple[str, str], ...] = (
    (
        '<sc-for list=\\"{{ topProds }}\\" as=\\"tp\\" hint-placeholder-count=\\"3\\">',
        '<sc-if value=\\"{{ topProdsEmpty }}\\" hint-placeholder-val=\\"{{ false }}\\">'
        '<div style=\\"font:11.5px/1.7 Roboto,sans-serif;color:#8FA2C7\\">'
        "Chưa có dữ liệu đề xuất. Chạy matching giải pháp cho khách hàng để thống kê "
        "xuất hiện ở đây.<\\u002Fdiv><\\u002Fsc-if>",
    ),
    (
        '<sc-for list=\\"{{ notifTop }}\\" as=\\"n\\" hint-placeholder-count=\\"3\\">',
        '<sc-if value=\\"{{ notifTopEmpty }}\\" hint-placeholder-val=\\"{{ false }}\\">'
        '<div style=\\"font:11.5px/1.7 Roboto,sans-serif;color:#8FA2C7;padding:14px 4px\\">'
        "Chưa có thông báo nào.<\\u002Fdiv><\\u002Fsc-if>",
    ),
    (
        '<sc-for list=\\"{{ notifRows }}\\" as=\\"n\\" hint-placeholder-count=\\"6\\">',
        '<sc-if value=\\"{{ notifListEmpty }}\\" hint-placeholder-val=\\"{{ false }}\\">'
        '<div style=\\"font:12px/1.7 Roboto,sans-serif;color:#8FA2C7;'
        'padding:26px 0 30px;text-align:center\\">{{ notifEmptyText }}'
        "<\\u002Fdiv><\\u002Fsc-if>",
    ),
)


def _mk(markup: str) -> str:
    return markup.replace('"', '\\"').replace("</", "<\\u002F")


def _splice(src: str, head: str, tail: str, replacement: str) -> tuple[str, bool]:
    start = src.find(head)
    if start < 0:
        return src, False
    end = src.find(tail, start)
    if end < 0:
        return src, False
    return src[:start] + replacement + src[end + len(tail) :], True


def _patch_notifs(src: str) -> tuple[str, int]:
    hits = 0

    if NOTIF_OLD_UNREAD in src:
        src = src.replace(NOTIF_OLD_UNREAD, _jsafe(NOTIF_METHODS, "notifMethods"), 1)
        hits += 1

    for name, head, tail, new in (
        ("notifTop", NOTIF_TOP_HEAD, NOTIF_TOP_TAIL, NOTIF_TOP_NEW),
        ("notifRows", NOTIF_ROWS_HEAD, NOTIF_ROWS_TAIL, NOTIF_ROWS_NEW),
    ):
        src, ok = _splice(src, head, tail, _jsafe(new, name))
        hits += int(ok)

    if NOTIF_SUMMARY_OLD in src:
        src = src.replace(NOTIF_SUMMARY_OLD, _jsafe(NOTIF_SUMMARY_NEW, "notifSummary"), 1)
        hits += 1

    replacement = _jsafe(MARK_ALL_NEW, "markAllRead")
    src, n = MARK_ALL_RE.subn(lambda _m: replacement, src)
    hits += int(n == 2)

    if NOTIF_FILTERS_OLD in src:
        src = src.replace(NOTIF_FILTERS_OLD, NOTIF_FILTERS_NEW, 1)
        hits += 1

    for label, seeded, prop, relabel in WEEK_ROWS:
        old = f"<span>{label}<\\u002Fspan>{WEEK_BOLD}{seeded}<\\u002Fb>"
        if old not in src:
            continue
        new = f"<span>{relabel or label}<\\u002Fspan>{WEEK_BOLD}{{{{ {prop} }}}}<\\u002Fb>"
        src = src.replace(old, new, 1)
        hits += 1

    return src, hits


def _patch_top_prods(src: str) -> tuple[str, bool]:
    return _splice(src, TOP_PRODS_HEAD, TOP_PRODS_TAIL, _jsafe(TOP_PRODS_NEW, "topProds"))


def _patch_empty_states(src: str) -> tuple[str, int]:
    hits = 0
    for anchor, block in EMPTY_STATES:
        if anchor in src:
            src = src.replace(anchor, block + anchor, 1)
            hits += 1
    return src, hits


RESEARCH_METHODS = (
    "_zbKbIds() { const sel = this._rsKbSel(); "
    "return this._KBS().filter((k) => sel.indexOf(k.name) > -1)"
    ".map((k) => k.__id).filter(Boolean); }"
    " _zbResearchGo(id, jobId, name, web) { "
    "clearTimeout(this._zbPollT); "
    "this._zbJid = this._jobStart('research', 'Deep Research · ' + name, "
    "() => this.setState({ screen: 'research' })); "
    "this.setState((st) => ({ createdId: id, createdName: name, "
    "createdWeb: web || 'chưa có liên kết', rJobId: jobId, progress: 0, rLog: [], "
    "rDetail: null, rFail: false, rFailMsg: '', rRun: (st.rRun || 0) + 1, "
    "screen: 'research' })); "
    "this._zbPoll(jobId); }"
    " _zbPoll(jobId) { const z = window.__ZB; if (!z || !z.jobStatus) return; "
    "z.jobStatus(jobId).then((r) => { "
    "if (this.state.rJobId !== jobId) return; "
    "if (!r.ok) { this._zbMiss = (this._zbMiss || 0) + 1; "
    "if (this._zbMiss < 4) "
    "{ this._zbPollT = setTimeout(() => this._zbPoll(jobId), 2000); return; } "
    "this.setState({ rFail: true, "
    "rFailMsg: r.message || 'Không đọc được trạng thái job' }); "
    "this._jobKill('research'); return; } "
    "this._zbMiss = 0; "
    "this.setState({ progress: r.progress, rLog: r.log, rStage: r.stage, "
    "rFail: r.failed, rFailMsg: r.error || '' }); "
    "this._jobPct(this._zbJid, r.progress, "
    "r.done ? 'Deep Research xong — hồ sơ sẵn sàng' : null); "
    "if (r.done) { this._zbDetail(); return; } "
    "if (r.failed) { this._jobKill('research'); return; } "
    "this._zbPollT = setTimeout(() => this._zbPoll(jobId), 1500); }); }"
    " _zbDetail() { const z = window.__ZB, id = this.state.createdId; "
    "if (!z || !id) return; "
    "z.customerDetail(id).then((r) => { "
    "if (r.ok) this.setState({ rDetail: r.customer, progress: 100 }); "
    "if (z.loadAll) z.loadAll(); }); }"
    " _zbFacts() { return (this.state.rDetail || {}).facts || []; }"
    " _zbSources() { return (this.state.rDetail || {}).sources || []; }"
    " _zbFactSet(f, v) { const z = window.__ZB, id = this.state.createdId; "
    "if (!z || !id) return; "
    "const d = Object.assign({}, this.state.rDetail); "
    "d.facts = (d.facts || []).map((x) => x.id === f.id "
    "? Object.assign({}, x, { verdict: v === 'no' ? 'rejected' : v }) : x); "
    "this.setState({ rDetail: d }); "
    "z.setFactVerdict(id, f.id, v).then((r) => this._toast(r.ok "
    "? (v === 'ok' ? 'Đã xác nhận “' + f.topic + '”' : 'Đã loại bỏ “' + f.topic + '”') "
    ": ('Không lưu được: ' + (r.message || 'lỗi backend')))); }"
)

RESEARCH_OLD_RUN_HEAD = "_runResearch(again) {"
RESEARCH_OLD_RUN_TAIL = "\\n  }\\n  _runGen() {"
RESEARCH_NEW_RUN = (
    "_runResearch(again) { const z = window.__ZB; "
    "if (!z || !z.ready) { this._toast('Chưa kết nối backend'); return; } "
    "const id = this.state.createdId; "
    "if (!id) { this._toast('Chưa có hồ sơ khách hàng để nghiên cứu'); return; } "
    "z.rerunResearch(id, { kbIds: this._zbKbIds() }).then((r) => { "
    "if (!r.ok) { this._toast('Không chạy được Deep Research: ' "
    "+ (r.message || 'lỗi backend')); return; } "
    "this._zbResearchGo(id, r.jobId, this.state.createdName, this.state.createdWeb); }); }"
    f"{RESEARCH_METHODS}"
)

CREATE_HEAD = "createCustomer: () => {"
CREATE_TAIL = "this._toast('Đã tạo hồ sơ — bắt đầu Deep Research');\\n      },"
CREATE_NEW = (
    "createCustomer: () => { if (!cOk) return; "
    "const z = window.__ZB; "
    "if (!z || !z.ready) { this._toast('Chưa kết nối backend'); return; } "
    "const links = (this.state.newLinks || '').split('\\\\n')"
    ".map((x) => x.trim()).filter(Boolean); "
    "const web = (links.find((x) => !/linkedin\\\\.com|facebook\\\\.com|fb\\\\.com/i.test(x)) "
    "|| links[0] || '').replace(/^https?:\\\\/\\\\//, '').replace(/\\\\/$/, ''); "
    "const name = this.state.newName.trim() "
    "|| (web ? web.split('.')[0].replace(/^./, (c) => c.toUpperCase()) : '') "
    "|| (this.state.newDetail || '').trim().split(/[.,\\\\n]/)[0].slice(0, 40) "
    "|| 'Khách hàng mới'; "
    "if (this.state.creating) return; "
    "this.setState({ creating: true }); "
    "z.createCustomer({ name: name, website: web, kbIds: this._zbKbIds() }).then((r) => { "
    "this.setState({ creating: false }); "
    "if (!r.ok) { this._toast('Không tạo được khách hàng: ' "
    "+ (r.message || 'lỗi backend')); return; } "
    "this.setState({ newName: '', newWebsite: '', newLinks: '', newDetail: '' }); "
    "if (!r.jobId) { this._toast('Đã lưu hồ sơ “' + r.name + '” — chưa chạy research'); "
    "this.go('customers'); return; } "
    "this._zbResearchGo(r.id, r.jobId, r.name, r.website || web); "
    "this._toast('Đã lưu “' + r.name + '” vào database — bắt đầu Deep Research'); }); },"
)

RESEARCH_LOG_HEAD = "researchLog: [[1,"
RESEARCH_LOG_TAIL = "})),\\n      custName:"
RESEARCH_LOG_NEW = "researchLog: this.state.rLog || [],"

RESEARCH_STEPS_HEAD = "researchSteps: [['Sản phẩm & dịch vụ kinh doanh'"
RESEARCH_STEPS_TAIL = "};\\n      }),\\n      researchLog:"
RESEARCH_STEPS_NEW = (
    "researchSteps: [['Thu thập nguồn', 'Website, LinkedIn, báo chí, tuyển dụng', 0, 35], "
    "['Đối chiếu Knowledge Base', 'Tài liệu nội bộ đã chọn', 35, 55], "
    "['Rút dữ kiện', 'Hoạt động tại thị trường Việt Nam', 55, 85], "
    "['Dựng hồ sơ', 'Report + cơ hội Zalo', 85, 100]].map(([label, sub, a, b]) => { "
    "const failed = !!this.state.rFail; "
    "const done = p >= b, stopped = failed && !done && p >= a, "
    "active = !failed && !done && p >= a && s === 'research'; "
    "return { label, sub: active && this.state.rStage ? this.state.rStage : sub, "
    "dotChar: done ? '✓' : stopped ? '!' : '', "
    "labelStyle: 'font:600 12px Roboto,sans-serif;color:' "
    "+ (stopped ? '#B93A2D' : done || active ? '#021F4E' : '#8FA2C7'), "
    "dotStyle: stopped "
    "? 'width:18px;height:18px;flex:none;border-radius:50%;background:#D9483B;color:#fff;"
    "font:700 10px/18px Montserrat,Roboto,sans-serif;text-align:center' "
    ": done "
    "? 'width:18px;height:18px;flex:none;border-radius:50%;background:#0068FF;color:#fff;"
    "font:700 10px/18px Montserrat,Roboto,sans-serif;text-align:center' "
    ": active "
    "? 'width:18px;height:18px;flex:none;border-radius:50%;border:2.5px solid #DCE6F7;"
    "border-top-color:#0068FF;box-sizing:border-box;animation:zbSpin .7s linear infinite' "
    ": 'width:18px;height:18px;flex:none;border-radius:50%;border:2.5px solid #DCE6F7;"
    "box-sizing:border-box' }; }),"
)

SRC_COUNT_OLD = (
    "rSrcCount: (() => { const n = Math.min(14, Math.round(p / 7)); return n + '/14 nguồn'; })(),"
)
SRC_COUNT_NEW = (
    "rSrcCount: (() => { const ss = this._zbSources(); "
    "const ok = ss.filter((x) => x.status === 'ok').length; "
    "return ss.length ? ok + '/' + ss.length + ' nguồn' : 'đang thu thập'; })(),"
)

SOURCES_HEAD = "rSources: [[3, 'Website chính thức'"
SOURCES_TAIL = "}; }),\\n      rFacts:"
SOURCES_NEW = (
    "rSources: this._zbSources().map((sc) => { const done = sc.status === 'ok'; "
    "return { name: sc.title || sc.name || sc.url || 'Nguồn', "
    "sub: sc.url || sc.reason || '', "
    "kind: done ? 'Đã lấy' : (sc.reason || sc.status), mark: done ? '✓' : '!', "
    "dot: 'width:16px;height:16px;flex:none;border-radius:50%;display:flex;"
    "align-items:center;justify-content:center;font:700 8.5px Roboto,sans-serif;color:#fff;"
    "transition:all .3s;' + (done ? 'background:#12A150' : 'background:#D9483B'), "
    "chip: 'flex:none;font:600 9.5px Roboto,sans-serif;border-radius:20px;padding:3px 8px;"
    "color:#1A30A0;background:#E8F1FF', "
    "rowStyle: 'display:flex;align-items:center;gap:10px;padding:10px 0;"
    "border-bottom:1px solid #EDF2FB;animation:zbRise .3s cubic-bezier(.22,1,.36,1) both' "
    "}; }),"
)

FACTS_HEAD = "rFacts: [['Thông báo giao dịch'"
FACTS_TAIL = "};\\n      }),\\n      rFactLine:"
FACTS_NEW = (
    "rFacts: this._zbFacts().map((f) => { const v = f.verdict || 'ai'; "
    "const CH = { ok: ['✓ Đã xác minh', 'color:#12A150;background:#E7F8EF'], "
    "ai: ['AI suy luận', 'color:#E8A13C;background:#FDF3E3'], "
    "rejected: ['Đã loại bỏ', 'color:#D9483B;background:#FDECEA'] }; "
    "const ch = CH[v] || CH.ai; "
    "const ic = f.isOpportunity ? '74' : '102'; "
    "return { name: f.topic, desc: f.summary, "
    "src: [this._zbHost(f.sourceUrl) || 'Deep Research', "
    "f.confidence ? f.confidence + '% tin cậy' : ''].filter(Boolean).join(' · '), "
    "icon: 'width:20px;height:20px;flex:none;margin-top:2px;background:url(' "
    "+ (window.__resources['pptx/media/image' + ic + '.png'] || '') "
    "+ ') center/contain no-repeat', "
    "chip: ch[0], chipStyle: 'font:600 10px Roboto,sans-serif;border-radius:20px;"
    "padding:3px 9px;white-space:nowrap;' + ch[1], "
    "actWrap: v === 'ai' ? 'display:flex;gap:8px' : 'display:none', "
    "accept: () => this._zbFactSet(f, 'ok'), reject: () => this._zbFactSet(f, 'no'), "
    "cardStyle: 'display:flex;gap:12px;align-items:flex-start;border:1px solid ' "
    "+ (v === 'rejected' ? '#EDF2FB' : '#DCE6F7') "
    "+ ';border-radius:10px;padding:14px;transition:all .25s cubic-bezier(.22,1,.36,1)' "
    "+ (v === 'rejected' ? ';opacity:.55' : '') }; }),"
)

RERUN_OLD = (
    "rerunResearch: () => { this.setState({ createdName: cust.name, "
    "createdWeb: cust.web, rRun: this._baseRun(cust.name) || 1, screen: 'research' }); "
    "setTimeout(() => this._runResearch(true), 0); "
    "this._toast('Đang chạy lại Deep Research — Run #' "
    "+ ((this._baseRun(cust.name) || 1) + 1)); },"
)
RERUN_NEW = (
    "rerunResearch: () => { if (!cust.__id) "
    "{ this._toast('Hồ sơ này chưa có trong database'); return; } "
    "this.setState({ createdId: cust.__id, createdName: cust.name, "
    "createdWeb: cust.web, rDetail: null, rLog: [], progress: 0, rFail: false, "
    "screen: 'research' }); "
    "setTimeout(() => this._runResearch(true), 0); },"
)

FACT_LINE_HEAD = "rFactLine: (() => { const vs = [0, 1, 2]"
FACT_LINE_TAIL = "})(),"
FACT_LINE_NEW = (
    "rFactLine: (() => { const fs = this._zbFacts(); "
    "const by = (v) => fs.filter((f) => (f.verdict || 'ai') === v).length; "
    "return by('ok') + ' đã xác nhận · ' + by('ai') + ' chờ duyệt · ' "
    "+ by('rejected') + ' loại bỏ'; })(),"
)

TOGGLE_OLD = (
    "rToggleRun: () => { if (p >= 100) { this._runResearch(true); return; } "
    "this._rsStop = true; this._jobKill('research'); "
    "(this._rsTs || []).forEach(clearTimeout); "
    "this._toast('Đã dừng research — dữ liệu thu được vẫn giữ lại'); },"
)
TOGGLE_NEW = (
    "rToggleRun: () => { if (p >= 100) { this._runResearch(true); return; } "
    "const z = window.__ZB, jid = this.state.rJobId; "
    "clearTimeout(this._zbPollT); this._jobKill('research'); "
    "this.setState({ rJobId: null }); "
    "if (z && jid) z.cancelResearch(jid).then((r) => this._toast(r.ok "
    "? 'Đã dừng research — dữ liệu thu được vẫn giữ lại' "
    ": ('Không dừng được: ' + (r.message || 'lỗi backend')))); },"
)

RETRY_OLD = (
    "rRetry: () => {\\n"
    "        if (this.state.netDown) { this._toast('Vẫn chưa có kết nối — thử lại sau'); "
    "return; }\\n"
    "        this.setState({ rFail: false });\\n"
    "        this._runResearch(true);\\n"
    "      },"
)
RETRY_NEW = "rRetry: () => { this.setState({ rFail: false }); this._runResearch(true); },"


CRM_HEAD = "_CRM() { return [["
CRM_TAIL = "]]; }"
CRM_NEW = (
    "_CRM() { const d = this._zbCustDetail(); if (!d) return []; "
    "const pf = d.profile || {}; const fs = d.facts || []; "
    "const srcOf = (f) => [this._zbHost(f.sourceUrl) || 'Deep Research', "
    "f.confidence ? f.confidence + '%' : ''].filter(Boolean).join(' · '); "
    "const rows = (arr) => (arr || []).filter(Boolean).map((x) => [String(x), '']); "
    "const out = []; "
    "const head = rows([pf.industry && 'Ngành: ' + pf.industry, "
    "pf.market && 'Thị trường: ' + pf.market, pf.size && 'Quy mô: ' + pf.size, "
    "pf.hq && 'Trụ sở: ' + pf.hq]); "
    "if (pf.marketPosition || head.length) "
    "out.push(['Tổng quan doanh nghiệp', pf.marketPosition || '', head]); "
    "if ((pf.channels || []).length) "
    "out.push(['Kênh số đang dùng', '', rows(pf.channels)]); "
    "if ((pf.techStack || []).length) "
    "out.push(['Công nghệ đang dùng', '', rows(pf.techStack)]); "
    "const opp = fs.filter((f) => f.isOpportunity); "
    "const rest = fs.filter((f) => !f.isOpportunity); "
    "if (opp.length) out.push(['Nhu cầu & cơ hội Zalo', '', "
    "opp.map((f) => [f.topic + ': ' + f.summary, srcOf(f)])]); "
    "if (rest.length) out.push(['Dữ kiện đã thu thập', '', "
    "rest.map((f) => [f.topic + ': ' + f.summary, srcOf(f)])]); "
    "return out; }"
)


CARD = "background:#fff;border:1px solid #DCE6F7;border-radius:12px;padding:20px 22px"
EYEBROW = (
    "font:600 10px Roboto,sans-serif;color:#8FA2C7;text-transform:uppercase;letter-spacing:.07em"
)
SEC_NUM = "font:700 12px ui-monospace,Menlo,monospace;color:#B6C3DD"
SEC_TITLE = "font:700 15px Montserrat,Roboto,sans-serif;color:#021F4E"
SEC_HEAD = "display:flex;align-items:baseline;gap:9px"
CHIP = (
    "font:600 11px Roboto,sans-serif;color:#1A30A0;background:#E8F1FF;"
    "border-radius:20px;padding:5px 11px"
)
CHIP_ALT = (
    "font:600 11px Roboto,sans-serif;color:#4d5f85;background:#F2F6FD;"
    "border:1px solid #E3EAF7;border-radius:20px;padding:5px 11px"
)
GHOST = (
    "flex:1;text-align:center;border:1px solid #DCE6F7;border-radius:8px;padding:7px 0;"
    "font:600 11px Roboto,sans-serif;color:#4d5f85;cursor:pointer;"
    "transition:all .22s cubic-bezier(.22,1,.36,1)"
)
ACT = "font:600 10.5px Roboto,sans-serif;cursor:pointer;white-space:nowrap"

PROFILE_TAB_HEAD = '<sc-if value=\\"{{ isTabProfile }}\\" hint-placeholder-val=\\"{{ false }}\\">'
PROFILE_TAB_TAIL = (
    "Chỉnh sửa hồ sơ →<\\u002Fspan><\\u002Fdiv>\\n        <\\u002Fdiv>\\n"
    "      <\\u002Fdiv>\\n      <\\u002Fsc-if>"
)

PROFILE_TAB_NEW = PROFILE_TAB_HEAD + _mk(
    '<div style="display:grid;grid-template-columns:236px minmax(0,1fr);gap:20px;'
    'padding:22px 28px 40px;{{ custTabAnim }}">'
    '<div><div style="position:sticky;top:18px;display:flex;flex-direction:column;gap:12px">'
    f'<div style="{CARD};padding:8px">'
    f'<div style="{EYEBROW};padding:8px 10px 6px">Mục lục</div>'
    '<sc-for list="{{ crmToc }}" as="tc" hint-placeholder-count="5">'
    '<div sc-camel-on-click="{{ tc.go }}" style="{{ tc.style }}" style-hover="background:#F2F6FD">'
    '<span style="{{ tc.no }}">{{ tc.num }}</span>'
    '<span style="flex:1;min-width:0">{{ tc.label }}</span>'
    '<span style="{{ tc.badgeStyle }}">{{ tc.badge }}</span></div></sc-for></div>'
    f'<div style="{CARD};padding:14px">'
    '<div style="display:flex;align-items:center;gap:9px">'
    '<div style="width:28px;height:28px;flex:none;border-radius:50%;background:#787CA6;'
    "display:flex;align-items:center;justify-content:center;"
    'font:700 10.5px Montserrat,Roboto,sans-serif;color:#fff">{{ custOwnerInit }}</div>'
    '<div style="min-width:0">'
    '<div style="font:600 11.5px Roboto,sans-serif;color:#021F4E">{{ custOwner }}</div>'
    '<div style="font:10px Roboto,sans-serif;color:#8FA2C7">Phụ trách chính</div></div></div>'
    '<div style="display:flex;gap:7px;margin-top:11px">'
    f'<div sc-camel-on-click="{{{{ custShare }}}}" style="{GHOST}" '
    'style-hover="border-color:#0068FF;color:#0068FF">Chia sẻ</div>'
    f'<div sc-camel-on-click="{{{{ openHandover }}}}" style="{GHOST}" '
    'style-hover="border-color:#0068FF;color:#0068FF">Chuyển giao</div></div></div>'
    f'<div style="{CARD};padding:14px"><div style="{EYEBROW}">Ghi chú</div>'
    '<div style="font:11.5px/1.65 Roboto,sans-serif;color:#4d5f85;margin-top:7px">'
    "{{ crmNote }}</div></div></div></div>"
    '<div style="display:flex;flex-direction:column;gap:14px;min-width:0">'
    f'<div id="crm-s0" style="{CARD}">'
    '<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">'
    '<span style="font:600 10.5px Roboto,sans-serif;color:#0068FF;text-transform:uppercase;'
    'letter-spacing:.08em">Tóm tắt điều hành</span>'
    '<span style="margin-left:auto;font:11px Roboto,sans-serif;color:#8FA2C7">'
    "{{ crmMetaLine }}</span></div>"
    '<div style="font:13.5px/1.8 Roboto,sans-serif;color:#33415e;margin-top:10px;'
    'max-width:78ch;text-wrap:pretty">{{ crmSummary }}</div></div>'
    f'<div id="crm-s1" style="{CARD}"><div style="{SEC_HEAD}">'
    f'<span style="{SEC_NUM}">01</span><span style="{SEC_TITLE}">Thông tin doanh nghiệp</span>'
    "</div>"
    '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));'
    'gap:14px 26px;margin-top:15px">'
    '<sc-for list="{{ crmInfo }}" as="nf" hint-placeholder-count="4">'
    f'<div style="min-width:0"><div style="{EYEBROW}">{{{{ nf.label }}}}</div>'
    '<div style="font:12.5px/1.6 Roboto,sans-serif;color:#021F4E;margin-top:4px">'
    "{{ nf.value }}</div></div></sc-for></div></div>"
    f'<div id="crm-s2" style="{CARD}"><div style="{SEC_HEAD}">'
    f'<span style="{SEC_NUM}">02</span><span style="{SEC_TITLE}">Cơ hội Zalo</span>'
    '<span style="margin-left:auto;font:11px Roboto,sans-serif;color:#8FA2C7">'
    "{{ crmOppLine }}</span></div>"
    '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));'
    'gap:10px;margin-top:14px">'
    '<sc-for list="{{ crmOpps }}" as="op" hint-placeholder-count="3">'
    '<div style="border:1px solid #DCE6F7;border-radius:10px;padding:13px 14px;'
    'display:flex;flex-direction:column;min-width:0">'
    '<div style="display:flex;align-items:flex-start;gap:8px">'
    '<span style="flex:1;min-width:0;font:600 12.5px Roboto,sans-serif;color:#021F4E">'
    "{{ op.name }}</span>"
    '<span style="{{ op.chipStyle }}">{{ op.chip }}</span></div>'
    '<div style="{{ op.descStyle }}">{{ op.desc }}</div>'
    '<div style="display:flex;align-items:center;gap:8px;margin-top:10px">'
    '<span style="{{ op.confStyle }}">{{ op.conf }}</span>'
    '<span style="flex:1;min-width:0;font:10.5px Roboto,sans-serif;color:#8FA2C7;'
    'white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{{ op.src }}</span>'
    '<div style="{{ op.actWrap }}">'
    f'<span sc-camel-on-click="{{{{ op.accept }}}}" style="{ACT};color:#0068FF">Xác nhận</span>'
    f'<span sc-camel-on-click="{{{{ op.reject }}}}" style="{ACT};color:#B6C3DD">Loại bỏ</span>'
    "</div></div></div></sc-for></div>"
    '<sc-if value="{{ crmOppEmpty }}" hint-placeholder-val="{{ false }}">'
    '<div style="font:12px/1.7 Roboto,sans-serif;color:#8FA2C7;padding:10px 0">'
    "Chưa có cơ hội nào được đánh dấu trong lần research gần nhất.</div></sc-if></div>"
    f'<div id="crm-s3" style="{CARD}"><div style="{SEC_HEAD}">'
    f'<span style="{SEC_NUM}">03</span><span style="{SEC_TITLE}">Kênh &amp; công nghệ</span>'
    "</div>"
    f'<div style="margin-top:14px"><div style="{EYEBROW}">Kênh đang dùng</div>'
    '<div style="display:flex;flex-wrap:wrap;gap:7px;margin-top:8px">'
    '<sc-for list="{{ crmChannels }}" as="cn" hint-placeholder-count="5">'
    f'<span style="{CHIP}">{{{{ cn.label }}}}</span></sc-for></div></div>'
    f'<div style="margin-top:16px"><div style="{EYEBROW}">Công nghệ</div>'
    '<div style="display:flex;flex-wrap:wrap;gap:7px;margin-top:8px">'
    '<sc-for list="{{ crmTech }}" as="tk" hint-placeholder-count="5">'
    f'<span style="{CHIP_ALT}">{{{{ tk.label }}}}</span></sc-for></div></div></div>'
    f'<div id="crm-s4" style="{CARD}"><div style="{SEC_HEAD}">'
    f'<span style="{SEC_NUM}">04</span><span style="{SEC_TITLE}">Dữ kiện đã thu thập</span>'
    '<span style="margin-left:auto;font:11px Roboto,sans-serif;color:#8FA2C7">'
    "{{ crmFactLine }}</span></div>"
    '<div style="margin-top:4px">'
    '<sc-for list="{{ crmFacts }}" as="fx" hint-placeholder-count="6">'
    '<div style="{{ fx.row }}"><div style="flex:1;min-width:0">'
    '<div style="font:600 12.5px Roboto,sans-serif;color:#021F4E">{{ fx.name }}</div>'
    '<div style="{{ fx.descStyle }}">{{ fx.desc }}</div>'
    '<div style="font:10.5px Roboto,sans-serif;color:#8FA2C7;margin-top:4px;'
    'white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{{ fx.src }}</div></div>'
    '<div style="display:flex;flex-direction:column;align-items:flex-end;gap:6px;flex:none">'
    '<span style="{{ fx.chipStyle }}">{{ fx.chip }}</span>'
    '<div style="{{ fx.actWrap }}">'
    f'<span sc-camel-on-click="{{{{ fx.accept }}}}" style="{ACT};color:#0068FF">Xác nhận</span>'
    f'<span sc-camel-on-click="{{{{ fx.reject }}}}" style="{ACT};color:#B6C3DD">Loại bỏ</span>'
    "</div></div></div></sc-for></div></div>"
    '<div style="display:flex;align-items:center;gap:8px;padding:2px 4px">'
    '<span style="font:11px/1.6 Roboto,sans-serif;color:#8FA2C7;min-width:0">'
    "{{ crmFooter }}</span>"
    '<span sc-camel-on-click="{{ openReview }}" style="margin-left:auto;flex:none;'
    'font:600 11.5px Roboto,sans-serif;color:#0068FF;cursor:pointer">Chỉnh sửa hồ sơ →</span>'
    "</div></div></div></sc-if>"
)

CRMVALS_HEAD = "_crmVals() {"
CRMVALS_TAIL = "\\n    };\\n  }\\n  _jobVals() {"
CRMVALS_NEW = (
    "_crmVals() { const d = this._zbCustDetail() || {}; "
    "const pf = d.profile || {}; const fs = d.facts || []; "
    "const cur = this.state.crmSec || 0; "
    "const opp = fs.filter((f) => f.isOpportunity); "
    "const rest = fs.filter((f) => !f.isOpportunity); "
    "const CH = { ok: ['✓ Đã xác minh', 'color:#12A150;background:#E7F8EF'], "
    "ai: ['AI suy luận', 'color:#E8A13C;background:#FDF3E3'], "
    "rejected: ['Đã loại bỏ', 'color:#D9483B;background:#FDECEA'] }; "
    "const chipOf = (v) => CH[v] || CH.ai; "
    "const srcOf = (f) => this._zbHost(f.sourceUrl) || 'Deep Research'; "
    "const confOf = (f) => (f.confidence || 0) + '%'; "
    "const confStyle = (f) => 'flex:none;font:600 10px ui-monospace,Menlo,monospace;"
    "border-radius:6px;padding:2px 6px;' + ((f.confidence || 0) >= 80 "
    "? 'color:#0B7A3E;background:#E7F8EF' : 'color:#8A6212;background:#FDF3E3'); "
    "const clamp = (n) => 'font:12px/1.6 Roboto,sans-serif;color:#787CA6;margin-top:5px;"
    "display:-webkit-box;-webkit-line-clamp:' + n + ';-webkit-box-orient:vertical;"
    "overflow:hidden'; "
    "const card = (f) => { const v = f.verdict || 'ai'; const c = chipOf(v); "
    "return { name: f.topic, desc: f.summary, src: srcOf(f), "
    "conf: confOf(f), confStyle: confStyle(f), descStyle: clamp(3), "
    "chip: c[0], chipStyle: 'flex:none;font:600 9.5px Roboto,sans-serif;border-radius:20px;"
    "padding:3px 8px;white-space:nowrap;' + c[1], "
    "actWrap: v === 'ai' ? 'display:flex;gap:8px;flex:none' : 'display:none', "
    "accept: () => this._zbCustSet(f, 'ok'), reject: () => this._zbCustSet(f, 'no') }; }; "
    "const row = (f) => Object.assign(card(f), { descStyle: clamp(2), "
    "row: 'display:flex;gap:14px;align-items:flex-start;padding:12px 0;"
    "border-bottom:1px solid #EDF2FB' }); "
    "const toc = [['Tóm tắt', ''], ['Thông tin doanh nghiệp', ''], "
    "['Cơ hội Zalo', opp.length || ''], ['Kênh & công nghệ', "
    "((pf.channels || []).length + (pf.techStack || []).length) || ''], "
    "['Dữ kiện đã thu thập', rest.length || '']]; "
    "return { "
    "crmSummary: pf.marketPosition || d.notes "
    "|| 'Chưa có tóm tắt — chạy Deep Research để dựng hồ sơ.', "
    "crmMetaLine: [d.research && d.research.researchedAt "
    "? 'Cập nhật ' + new Date(d.research.researchedAt).toLocaleDateString('vi-VN') : '', "
    "((d.sources || []).length) + ' nguồn', fs.length + ' dữ kiện']"
    ".filter(Boolean).join(' · '), "
    "crmInfo: [['Ngành', pf.industry], ['Thị trường', pf.market], "
    "['Quy mô', pf.size], ['Trụ sở', pf.hq]]"
    ".filter((x) => x[1]).map((x) => ({ label: x[0], value: x[1] })), "
    "crmOpps: opp.map(card), "
    "crmOppEmpty: opp.length === 0, "
    "crmOppLine: opp.length + ' cơ hội · ' "
    "+ opp.filter((f) => (f.verdict || 'ai') === 'ai').length + ' chờ duyệt', "
    "crmChannels: (pf.channels || []).map((x) => ({ label: this._zbCut(x, 52) })), "
    "crmTech: (pf.techStack || []).map((x) => ({ label: this._zbCut(x, 52) })), "
    "crmFacts: rest.map(row), "
    "crmFactLine: rest.length + ' dữ kiện · ' "
    "+ rest.filter((f) => (f.verdict || 'ai') === 'ai').length + ' chờ duyệt', "
    "crmFooter: 'Report do AI tổng hợp từ ' + ((d.sources || []).length) "
    "+ ' nguồn công khai — kiểm tra số liệu quan trọng trước khi gửi khách.', "
    "crmToc: toc.map(([label, badge], i) => ({ label, num: '0' + i, badge: String(badge), "
    "no: 'flex:none;font:700 10px ui-monospace,Menlo,monospace;color:' "
    "+ (cur === i ? '#0068FF' : '#B6C3DD'), "
    "badgeStyle: 'flex:none;font:600 9.5px ui-monospace,Menlo,monospace;color:#B6C3DD'"
    " + (badge ? '' : ';display:none'), "
    "style: 'display:flex;align-items:center;gap:9px;padding:8px 10px;border-radius:8px;"
    "cursor:pointer;font:600 11.5px Roboto,sans-serif;transition:all .18s;' "
    "+ (cur === i ? 'color:#0068FF;background:#F2F8FF' : 'color:#4d5f85'), "
    "go: () => { this.setState({ crmSec: i }); "
    "const el = document.getElementById('crm-s' + i); if (!el) return; "
    "let sp = el.parentElement; "
    "while (sp && sp.scrollHeight <= sp.clientHeight + 4) sp = sp.parentElement; "
    "if (sp) sp.scrollTo({ top: el.offsetTop - 80, behavior: 'smooth' }); } }))"
    "\\n    };\\n  }\\n  _jobVals() {"
)


AUDIT_DET_HEAD = "auditDetRows: (() => { const d = this.state.auditDet || {}; return [['Thời điểm'"
AUDIT_DET_TAIL = "].map((r) => ({ k: r[0], v: r[1] })); })(),"
AUDIT_DET_NEW = (
    "auditDetRows: (() => { const d = this.state.auditDet || {}; "
    "const x = this._zbAuditDet(); "
    "if (!x) return [['Thời điểm', d.time || ''], ['Người thực hiện', d.actor || ''], "
    "['Hành động', d.action || ''], ['Đối tượng', d.target || ''], "
    "['Nhóm', d.cat || ''], ['Chi tiết', 'Đang tải…']]"
    ".map((r) => ({ k: r[0], v: r[1] })); "
    "const meta = x.meta || {}; "
    "const rows = [['Thời điểm', new Date(x.createdAt).toLocaleString('vi-VN')], "
    "['Người thực hiện', (x.actor && x.actor.name) || 'Hệ thống'], "
    "['Hành động', x.action], ['Diễn giải', x.summary], "
    "['Đối tượng', x.resourceLabel || '—'], "
    "['Loại tài nguyên', x.resourceType || '—'], "
    "['Nhóm', x.category], ['Địa chỉ IP', x.ip || '—'], "
    "['Thiết bị', x.userAgent || '—'], ['Mã bản ghi', x.id]]; "
    "Object.keys(meta).forEach((k) => rows.push([k, String(meta[k])])); "
    "return rows.map((r) => ({ k: r[0], v: r[1] })); })(),"
)

AUDIT_MORE_OLD = (
    "auditMoreLabel: auditN >= 2 ? 'Đã tải hết bản ghi hôm nay' : 'Tải thêm ↓',"
    "\\n      auditMoreStyle: 'font:600 12px Roboto,sans-serif;transition:all .2s;color:' "
    "+ (auditN >= 2 ? '#8FA2C7' : '#0068FF') + (auditN >= 2 ? '' : ';cursor:pointer'),"
    "\\n      auditMore: () => { if (auditN >= 2) return; "
    "this.setState({ auditN: auditN + 1 }); this._toast('Đã tải thêm 8 bản ghi'); },"
)
AUDIT_MORE_NEW = (
    "auditDone: (() => { const a = this._zbAudit() || {}; "
    "return (a.items || []).length >= (a.total || 0); })(), "
    "auditMoreLabel: (() => { const a = this._zbAudit() || {}; "
    "const n = (a.items || []).length; const t = a.total || 0; "
    "return n >= t ? 'Đã hiện hết ' + t + ' bản ghi' : 'Tải thêm ' + (t - n) + ' bản ghi ↓'; })(), "
    "auditMoreStyle: (() => { const a = this._zbAudit() || {}; "
    "const done = (a.items || []).length >= (a.total || 0); "
    "return 'font:600 12px Roboto,sans-serif;transition:all .2s;color:' "
    "+ (done ? '#8FA2C7' : '#0068FF') + (done ? '' : ';cursor:pointer'); })(), "
    "auditMore: () => { const a = this._zbAudit() || {}; "
    "if ((a.items || []).length >= (a.total || 0)) return; "
    "this.setState({ auditN: (this.state.auditN || 0) + 1 }); },"
)

AUDIT_EXP_OLD = (
    "auditExpNote: 'Ước tính ' + [128, 640, 2450, 18400][this.state.auditRange || 0] "
    "+ ' dòng · CSV UTF-8"
)
AUDIT_EXP_NEW = (
    "auditExpNote: 'Ước tính ' + (((this._zbAudit() || {}).total) || 0) + ' dòng · CSV UTF-8"
)

AUDIT_ROWS_HEAD = "auditRows: [\\n        ['10:24'"
AUDIT_ROWS_TAIL = "|| 'color:#8FA2C7;background:#F2F6FD')\\n      })),"
AUDIT_ROWS_NEW = (
    "auditRows: ((this._zbAudit() || {}).items || []).map((a, i) => ({ "
    "detailOpen: () => this.setState({ auditDet: { i, id: a.id, time: a.time, "
    "actor: a.actor, action: a.action, target: a.target, cat: a.cat } }), "
    "time: a.time, init: a.init, actor: a.actor, action: a.action, "
    "target: a.target, detail: a.detail, cat: a.cat, "
    "avStyle: 'width:28px;height:28px;flex:none;border-radius:50%;background:' + a.color "
    "+ ';display:flex;align-items:center;justify-content:center;"
    "font:700 10px Montserrat,Roboto,sans-serif;color:#fff', "
    "rowStyle: 'display:flex;align-items:center;gap:14px;padding:13px 0;"
    "border-bottom:1px solid #EDF2FB;animation:zbRise .32s cubic-bezier(.22,1,.36,1) both;"
    "animation-delay:' + (i * .022) + 's', "
    "catChip: 'flex:none;font:600 9.5px Roboto,sans-serif;border-radius:20px;padding:3px 9px;' "
    "+ ({ 'Dữ liệu KH': 'color:#1A30A0;background:#E8F1FF', "
    "'Proposal': 'color:#E8A13C;background:#FDF3E3', "
    "'Phân quyền': 'color:#D9483B;background:#FDEBE9', "
    "'Tri thức': 'color:#12A150;background:#E7F8EF', "
    "'Hệ thống': 'color:#1A30A0;background:#DFF4FF' }[a.cat] "
    "|| 'color:#8FA2C7;background:#F2F6FD') })),"
)

AUDIT_FILTERS_OLD = (
    "auditFilters: ['Tất cả', 'Đăng nhập', 'Dữ liệu KH', 'Sản phẩm', 'Proposal', 'Phân quyền']"
    ".map((label, i) => {"
)
AUDIT_FILTERS_NEW = (
    "auditFilters: ['Tất cả'].concat(this.state.auditCats || []).map((label, i) => {"
)

AUDIT_CLICK_OLD = "click: () => this.setState({ auditF: i }),"
AUDIT_CLICK_NEW = "click: () => this.setState({ auditF: i, auditN: 0 }),"

AUDIT_RANGE_OLD = "click: () => this.setState({ auditRange: i }),"
AUDIT_RANGE_NEW = "click: () => this.setState({ auditRange: i, auditN: 0, auditRangeOpen: false }),"

REPORT_SUB_OLD = "Run #{{ rRunNo }} · 14 nguồn · 42 dữ kiện · lưu trong hồ sơ khách hàng"
REPORT_SUB_NEW = "{{ rReportSub }}"

REPORT_MAP_OLD = (
    "return { text: fov[k] !== undefined ? fov[k] : f[0], src: f[1], "
    "textChange: (e) => this.setState({ rRepFactOv: Object.assign({}, fov, "
    "{ [k]: e.target.value }) }) };"
)
REPORT_MAP_NEW = (
    "return { text: fov[k] !== undefined ? fov[k] : f[0], src: f[1], "
    "srcStyle: 'margin-left:12px;margin-top:5px;font:10.5px Roboto,sans-serif;color:#8FA2C7;"
    "white-space:nowrap;overflow:hidden;text-overflow:ellipsis'"
    " + (f[1] ? '' : ';display:none'), "
    "textChange: (e) => this.setState({ rRepFactOv: Object.assign({}, fov, "
    "{ [k]: e.target.value }) }) };"
)

REPORT_SUB_ANCHOR = "rReport: this._CRM().map((x, i) => {"
REPORT_SUB_PROP = (
    "rReportSub: (() => { const d = this._zbCustDetail() || {}; "
    "return ['Run #' + ((d.activity || [])"
    ".filter((a) => a.kind === 'research_started').length || 1), "
    "((d.sources || []).length) + ' nguồn', "
    "((d.facts || []).length) + ' dữ kiện'].join(' · '); })(), "
)


REPORT_ROW_HEAD = '<sc-for list=\\"{{ rs.facts }}\\" as=\\"rf\\" hint-placeholder-count=\\"2\\">'
REPORT_ROW_TAIL = "{{ rf.src }}<\\u002Fspan><\\u002Fdiv>"
REPORT_ROW_NEW = _mk(
    '<sc-for list="{{ rs.facts }}" as="rf" hint-placeholder-count="2">'
    '<div style="background:#F7FAFF;border:1px solid #EDF2FB;border-radius:8px;'
    'padding:9px 12px;min-width:0">'
    '<div style="display:flex;align-items:flex-start;gap:8px">'
    '<span style="flex:none;margin-top:6px;width:4px;height:4px;border-radius:50%;'
    'background:#0068FF"></span>'
    '<sc-if value="{{ rRepReading }}" hint-placeholder-val="{{ true }}">'
    '<span style="flex:1;min-width:0;font:11.5px/1.6 Roboto,sans-serif;color:#33415e">'
    "{{ rf.text }}</span></sc-if>"
    '<sc-if value="{{ rRepEdit }}" hint-placeholder-val="{{ false }}">'
    '<input value="{{ rf.text }}" sc-camel-on-change="{{ rf.textChange }}" '
    'style="flex:1;min-width:0;border:1.5px solid #B9D5FF;border-radius:6px;padding:5px 9px;'
    'font:11.5px Roboto,sans-serif;color:#021F4E;outline:none;background:#fff" '
    'style-focus="border-color:#0068FF"></sc-if></div>'
    '<div style="{{ rf.srcStyle }}">{{ rf.src }}</div></div>'
)

CRM_OWNER_OLD = (
    '<span>Phụ trách: <b style=\\"color:#021F4E\\">{{ userName }}<\\u002Fb> +2<\\u002Fspan>'
)
CRM_OWNER_NEW = (
    '<span>Phụ trách: <b style=\\"color:#021F4E\\">{{ custOwner }}<\\u002Fb><\\u002Fspan>'
)

CRM_MARKET_OLD = "<span>·<\\u002Fspan><span>Việt Nam<\\u002Fspan>"
CRM_MARKET_NEW = "<span>·<\\u002Fspan><span>{{ custMarket }}<\\u002Fspan>"

CRM_EXTRA_PROPS = (
    "crmNote: (() => { const d = this._zbCustDetail() || {}; "
    "return d.notes || 'Chưa có ghi chú.'; })(), "
    "crmUpdated: (() => { const d = this._zbCustDetail() || {}; "
    "const r = d.research || {}; "
    "const when = r.researchedAt "
    "? 'Cập nhật ' + new Date(r.researchedAt).toLocaleString('vi-VN') "
    ": 'Chưa chạy Deep Research'; "
    "return when + ' · ' + ((d.sources || []).length) + ' nguồn'; })(), "
    "custOwner: (() => { const d = this._zbCustDetail() || {}; "
    "const o = d.owner || {}; return o.handle || o.name || this._me(); })(), "
    "custOwnerInit: (() => { const d = this._zbCustDetail() || {}; "
    "const o = d.owner || {}; "
    "return (o.initials || (o.handle || this._me() || '?').slice(0, 2)).toUpperCase(); })(), "
    "custMarket: (() => { const pf = ((this._zbCustDetail() || {}).profile) || {}; "
    "return pf.market || 'Việt Nam'; })(), "
)

CT_SUMMARY_HEAD = (
    '<div style=\\"font:13px/1.7 Roboto,sans-serif;color:#33415e;margin-top:10px\\">'
    "Chuỗi cà phê ~150 cửa hàng"
)
CT_SUMMARY_TAIL = "cho CSKH tự động.<\\u002Fdiv>"
CT_SUMMARY_NEW = (
    '<div style=\\"font:13px/1.7 Roboto,sans-serif;color:#33415e;margin-top:10px\\">'
    "{{ custSummary }}<\\u002Fdiv>"
)

CT_CHIP = (
    '<span style=\\"font:600 11px Roboto,sans-serif;color:#1A30A0;background:#E8F1FF;'
    'border-radius:20px;padding:5px 12px\\">'
)
CT_CHIPS_HEAD = f'<div style=\\"display:flex;gap:8px;margin-top:12px\\">{CT_CHIP}B2C'
CT_CHIPS_TAIL = "Transaction-based<\\u002Fspan><\\u002Fdiv>"
CT_CHIPS_NEW = (
    '<div style=\\"display:flex;gap:8px;margin-top:12px;flex-wrap:wrap\\">'
    '<sc-for list=\\"{{ custBizTags }}\\" as=\\"bt\\" hint-placeholder-count=\\"4\\">'
    f"{CT_CHIP}{{{{ bt.label }}}}<\\u002Fspan><\\u002Fsc-for><\\u002Fdiv>"
)

CT_CARDS_HEAD = (
    '<div style=\\"display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:14px\\">'
)
CT_CARDS_TAIL = "Chi phí cao, không rich media<\\u002Fdiv><\\u002Fdiv>"
CT_CARDS_NEW = (
    '<div style=\\"display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-top:14px\\">'
    '<sc-for list=\\"{{ custChannels }}\\" as=\\"cc\\" hint-placeholder-count=\\"4\\">'
    '<div style=\\"border:1px solid #DCE6F7;border-radius:10px;padding:12px\\">'
    '<div style=\\"display:flex;align-items:center;gap:7px\\">'
    '<img src=\\"da865d11-d2ca-401f-9096-7bf24e71ff18\\" style=\\"width:15px;height:15px\\">'
    '<span style=\\"font:600 12px Roboto,sans-serif;color:#021F4E\\">{{ cc.name }}'
    "<\\u002Fspan><\\u002Fdiv><\\u002Fdiv><\\u002Fsc-for>"
)

CT_FACTS_HEAD = (
    '<div style=\\"display:flex;flex-direction:column;gap:10px;margin-top:14px\\">\\n'
    '              <div style=\\"border:1px solid #DCE6F7;border-radius:10px;padding:14px;'
    'display:flex;gap:12px;align-items:flex-start\\">\\n'
    '                <img src=\\"d4f885ca-2c47-4650-a0f1-f736a929a664\\"'
)
CT_FACTS_TAIL = (
    "Loại bỏ<\\u002Fspan><\\u002Fdiv><\\u002Fdiv>\\n"
    "              <\\u002Fdiv>\\n            <\\u002Fdiv>"
)
CT_FACTS_NEW = (
    '<div style=\\"display:flex;flex-direction:column;gap:10px;margin-top:14px\\">'
    '<sc-for list=\\"{{ custFacts }}\\" as=\\"cf\\" hint-placeholder-count=\\"3\\">'
    '<div style=\\"border:1px solid #DCE6F7;border-radius:10px;padding:14px;display:flex;'
    'gap:12px;align-items:flex-start\\"><div style=\\"{{ cf.icon }}\\"><\\u002Fdiv>'
    '<div style=\\"flex:1;min-width:0\\">'
    '<div style=\\"font:600 13px Roboto,sans-serif;color:#021F4E\\">{{ cf.name }}<\\u002Fdiv>'
    '<div style=\\"font:12px/1.6 Roboto,sans-serif;color:#787CA6;margin-top:3px\\">'
    "{{ cf.desc }}<\\u002Fdiv>"
    '<div style=\\"font:11px Roboto,sans-serif;color:#8FA2C7;margin-top:6px\\">'
    "{{ cf.src }}<\\u002Fdiv><\\u002Fdiv>"
    '<div style=\\"display:flex;flex-direction:column;gap:6px;align-items:flex-end\\">'
    '<span style=\\"{{ cf.chipStyle }}\\">{{ cf.chip }}<\\u002Fspan>'
    '<div style=\\"{{ cf.actWrap }}\\">'
    '<span sc-camel-on-click=\\"{{ cf.accept }}\\" style=\\"font:600 10.5px Roboto,sans-serif;'
    'color:#0068FF;cursor:pointer\\">Xác nhận<\\u002Fspan>'
    '<span sc-camel-on-click=\\"{{ cf.reject }}\\" style=\\"font:600 10.5px Roboto,sans-serif;'
    'color:#B6C3DD;cursor:pointer\\">Loại bỏ<\\u002Fspan>'
    "<\\u002Fdiv><\\u002Fdiv><\\u002Fdiv><\\u002Fsc-for><\\u002Fdiv>"
)

CT_SRCLINE_OLD = "14 nguồn · website, LinkedIn, báo chí"
CT_SRCLINE_NEW = "{{ custSrcLine }}"
CT_RUNLINE_OLD = "Run #4 · 25/07/2026 09:40"
CT_RUNLINE_NEW = "{{ custRunLine }}"
CT_SRCTITLE_OLD = "Nguồn dữ liệu (14)"
CT_SRCTITLE_NEW = "{{ custSrcTitle }}"
CT_EXTRACT_OLD = "42 dữ kiện, 9 suy luận"
CT_EXTRACT_NEW = "{{ custExtractLine }}"
CT_SUMSTEP_OLD = "Đề xuất 6 cập nhật · 3 chờ duyệt"
CT_SUMSTEP_NEW = "{{ custFactLine }}"

CT_SRCROW = (
    '<div style=\\"display:flex;align-items:center;gap:9px;padding:10px 0;'
    'border-bottom:1px solid #EDF2FB\\">'
)
CT_SRCS_HEAD = (
    CT_SRCROW + '<div style=\\"width:8px;height:8px;border-radius:50%;background:#0068FF\\">'
)
CT_SRCS_TAIL = (
    "cafef.vn/the-coffee-house-mo-rong…<\\u002Fdiv>"
    '<div style=\\"font:10px Roboto,sans-serif;color:#8FA2C7\\">Báo chí · crawl 09:39'
    "<\\u002Fdiv><\\u002Fdiv>"
    '<img src=\\"e911b227-f243-4157-86dc-ca9c3a8dca74\\" '
    'style=\\"width:13px;height:13px\\"><\\u002Fdiv>'
)
CT_SRCS_NEW = (
    '<sc-for list=\\"{{ custSources }}\\" as=\\"cs\\" hint-placeholder-count=\\"3\\">'
    f"{CT_SRCROW}"
    '<div style=\\"{{ cs.dot }}\\"><\\u002Fdiv><div style=\\"flex:1;min-width:0\\">'
    '<div style=\\"font:600 11.5px Roboto,sans-serif;color:#021F4E;white-space:nowrap;'
    'overflow:hidden;text-overflow:ellipsis\\">{{ cs.name }}<\\u002Fdiv>'
    '<div style=\\"font:10px Roboto,sans-serif;color:#8FA2C7\\">{{ cs.sub }}<\\u002Fdiv>'
    "<\\u002Fdiv><\\u002Fdiv><\\u002Fsc-for>"
)


CT_RUNNO_OLD = "custRunNo: this._baseRun(cust.name) || 1,"
CT_RUNNO_NEW = (
    "custRunNo: (() => { const d = this._zbCustDetail() || {}; "
    "return ((d.activity || []).filter((a) => a.kind === 'research_started').length) || 1; })(),"
)

CT_PROPS_ANCHOR = "custRunNo:"
CT_PROPS = (
    "custSummary: (() => { const d = this._zbCustDetail() || {}; "
    "const pf = d.profile || {}; return pf.marketPosition || d.notes "
    "|| 'Chưa có tóm tắt — chạy Deep Research để dựng hồ sơ.'; })(), "
    "custBizTags: (() => { const pf = ((this._zbCustDetail() || {}).profile) || {}; "
    "return [pf.industry, pf.market, pf.size, pf.hq].filter(Boolean)"
    ".map((x) => ({ label: this._zbCut(x, 38) })); })(), "
    "custChannels: (() => { const pf = ((this._zbCustDetail() || {}).profile) || {}; "
    "return (pf.channels || []).concat(pf.techStack || [])"
    ".map((x) => ({ name: this._zbCut(x, 46) })); })(), "
    "custFacts: ((this._zbCustDetail() || {}).facts || []).map((f) => { "
    "const v = f.verdict || 'ai'; "
    "const CH = { ok: ['✓ Đã xác minh', 'color:#12A150;background:#E7F8EF'], "
    "ai: ['AI suy luận', 'color:#E8A13C;background:#FDF3E3'], "
    "rejected: ['Đã loại bỏ', 'color:#D9483B;background:#FDECEA'] }; "
    "const ch = CH[v] || CH.ai; "
    "return { name: f.topic, desc: f.summary, "
    "src: [this._zbHost(f.sourceUrl) || 'Deep Research', "
    "f.confidence ? f.confidence + '% tin cậy' : ''].filter(Boolean).join(' · '), "
    "icon: 'width:20px;height:20px;flex:none;margin-top:2px;background:url(' "
    "+ (window.__resources['pptx/media/image' + (f.isOpportunity ? '74' : '102') + '.png'] "
    "|| '') + ') center/contain no-repeat', "
    "chip: ch[0], chipStyle: 'font:600 10px Roboto,sans-serif;border-radius:20px;"
    "padding:3px 9px;white-space:nowrap;' + ch[1], "
    "actWrap: v === 'ai' ? 'display:flex;gap:6px' : 'display:none', "
    "accept: () => this._zbCustSet(f, 'ok'), reject: () => this._zbCustSet(f, 'no') }; }), "
    "custSources: ((this._zbCustDetail() || {}).sources || []).map((sc) => ({ "
    "name: sc.title || sc.name || sc.url || 'Nguồn', "
    "sub: [this._zbHost(sc.url), sc.status === 'ok' ? '' : (sc.reason || sc.status)]"
    ".filter(Boolean).join(' · '), "
    "dot: 'width:8px;height:8px;flex:none;border-radius:50%;background:' "
    "+ (sc.status === 'ok' ? '#0068FF' : '#D9483B') })), "
    "custExtractLine: (() => { const fs = (this._zbCustDetail() || {}).facts || []; "
    "return fs.length + ' dữ kiện · ' "
    "+ fs.filter((f) => f.isOpportunity).length + ' cơ hội'; })(), "
    "custSrcLine: (() => { const ss = (this._zbCustDetail() || {}).sources || []; "
    "return ss.length + ' nguồn đã thu thập'; })(), "
    "custSrcTitle: 'Nguồn dữ liệu (' "
    "+ (((this._zbCustDetail() || {}).sources || []).length) + ')', "
    "custFactLine: (() => { const fs = (this._zbCustDetail() || {}).facts || []; "
    "const by = (v) => fs.filter((f) => (f.verdict || 'ai') === v).length; "
    "return fs.length + ' dữ kiện · ' + by('ai') + ' chờ duyệt'; })(), "
    "custRunLine: (() => { const r = ((this._zbCustDetail() || {}).research) || {}; "
    "return r.researchedAt ? 'Cập nhật ' + new Date(r.researchedAt).toLocaleString('vi-VN') "
    ": 'Chưa chạy Deep Research'; })(), "
)

SUMMARY_HEAD = (
    '<div style=\\"font:13px/1.7 Roboto,sans-serif;color:#33415e;margin-top:10px\\">'
    "{{ rName }} là doanh nghiệp F&amp;B"
)
SUMMARY_TAIL = "Membership<\\u002Fspan><\\u002Fdiv>"
SUMMARY_NEW = (
    '<div style=\\"font:13px/1.7 Roboto,sans-serif;color:#33415e;margin-top:10px\\">'
    "{{ rSummary }}<\\u002Fdiv>"
    '<div style=\\"display:flex;gap:8px;margin-top:14px;flex-wrap:wrap\\">'
    '<sc-for list=\\"{{ rTags }}\\" as=\\"tg\\" hint-placeholder-count=\\"3\\">'
    '<span style=\\"font:600 11px Roboto,sans-serif;color:#1A30A0;background:#E8F1FF;'
    'border-radius:20px;padding:5px 12px\\">{{ tg.label }}<\\u002Fspan>'
    "<\\u002Fsc-for><\\u002Fdiv>"
)

SUMMARY_PROPS_ANCHOR = "rSrcCount:"
SUMMARY_PROPS = (
    "rSummary: (() => { const d = this.state.rDetail || {}; const pf = d.profile || {}; "
    "return pf.marketPosition || d.notes || (this._zbFacts().length "
    "? 'Deep Research đã thu thập ' + this._zbFacts().length + ' dữ kiện từ ' "
    "+ this._zbSources().length + ' nguồn. Xem chi tiết bên dưới.' "
    ": 'Chưa có tóm tắt — chạy Deep Research để dựng hồ sơ.'); })(), "
    "rTags: (() => { const pf = (this.state.rDetail || {}).profile || {}; "
    "const cut = (x) => String(x).length > 42 ? String(x).slice(0, 40) + '…' : String(x); "
    "return [pf.industry, pf.market, pf.size].filter(Boolean)"
    ".map((x) => ({ label: cut(x) })); })(), "
)

FAIL_PANEL_HEAD = "rFailCode: this.state.netDown ? 'RESEARCH-503'"
FAIL_PANEL_TAIL = "'LinkedIn công ty ở chế độ riêng tư.'],"
FAIL_PANEL_NEW = (
    "rFailCode: this.state.rFailMsg ? 'RESEARCH-JOB' : 'RESEARCH-424', "
    "rFailBody: this.state.rFailMsg "
    "|| ('Deep Research dừng giữa chừng cho ' + (this.state.createdWeb || 'khách hàng này') "
    "+ '. Dữ kiện đã lưu trước đó vẫn giữ nguyên.'), "
    "rFailWhyTitle: 'Vì sao dừng', "
    "rFailReasons: (() => { const ss = this._zbSources(); "
    "const bad = ss.filter((x) => x.status !== 'ok'); "
    "const out = []; "
    "if (this.state.rStage) out.push('Dừng ở bước: ' + this.state.rStage); "
    "out.push(ss.length ? (ss.length - bad.length) + '/' + ss.length + ' nguồn lấy được' "
    ": 'Chưa lấy được nguồn nào'); "
    "bad.slice(0, 3).forEach((x) => out.push((x.name || x.url) + ': ' "
    "+ (x.reason || x.status))); "
    "return out; })(),"
)

FAIL_BODY_OLD = "rFailSrcLine: '2/5 nguồn lấy được — không đủ để dựng hồ sơ',"
FAIL_BODY_NEW = (
    "rFailSrcLine: (() => { const ss = this._zbSources(); "
    "const ok = ss.filter((x) => x.status === 'ok').length; "
    "return ss.length ? ok + '/' + ss.length + ' nguồn lấy được' "
    ": 'Chưa lấy được nguồn nào'; })(),"
)

FAIL_SOURCES_HEAD = "rFailSources: (() => {\\n        const kb = this._rsKbSel().join(', ');"
FAIL_SOURCES_TAIL = "}));\\n      })(),"
FAIL_SOURCES_NEW = (
    "rFailSources: this._zbSources().map((sc) => { const ok = sc.status === 'ok'; "
    "return { name: sc.title || sc.name || sc.url || 'Nguồn', "
    "st: ok ? 'Đã lấy' : (sc.reason || sc.status), mark: ok ? '✓' : '!', "
    "dot: 'width:16px;height:16px;flex:none;border-radius:50%;display:flex;"
    "align-items:center;justify-content:center;font:700 9px Montserrat,Roboto,sans-serif;"
    "color:#fff;background:' + (ok ? '#12A150' : '#D9483B'), "
    "chip: 'flex:none;font:600 9.5px Roboto,sans-serif;border-radius:20px;padding:3px 9px;"
    "color:' + (ok ? '#12A150' : '#B93A2D') + ';background:' "
    "+ (ok ? '#E7F8EF' : '#FDECEA') }; }),"
)

DASH_BTN_HEAD = (
    '<div sc-camel-on-click=\\"{{ openCreate }}\\" style=\\"border:1px solid #0068FF;color:#0068FF'
)
DASH_BTN_TAIL = "Tạo khách hàng mới"
DASH_BTN_NEW = (
    '<div sc-camel-on-click=\\"{{ openCreate }}\\" '
    'style=\\"display:flex;align-items:center;gap:8px;background:#0068FF;color:#fff;'
    "border-radius:8px;padding:9px 14px;font:600 12.5px Roboto,sans-serif;"
    'white-space:nowrap;flex:none;cursor:pointer;transition:all .22s cubic-bezier(.22,1,.36,1)\\" '
    'style-hover=\\"background:#1A30A0\\">'
    '<img src=\\"b3c96036-41da-4f8f-8be0-953abbc070d9\\" style=\\"width:15px;height:15px\\">'
    "Chạy Deep Research khách hàng mới"
)


RESEARCH_PATCHES: tuple[tuple[str, str, str, str], ...] = (
    (
        "runResearch",
        RESEARCH_OLD_RUN_HEAD,
        RESEARCH_OLD_RUN_TAIL,
        RESEARCH_NEW_RUN + "\\n  _runGen() {",
    ),
    ("createCustomer", CREATE_HEAD, CREATE_TAIL, CREATE_NEW),
    (
        "researchSteps",
        RESEARCH_STEPS_HEAD,
        RESEARCH_STEPS_TAIL,
        RESEARCH_STEPS_NEW + "\\n      researchLog:",
    ),
    (
        "researchLog",
        RESEARCH_LOG_HEAD,
        RESEARCH_LOG_TAIL,
        RESEARCH_LOG_NEW + "\\n      custName:",
    ),
    ("rSources", SOURCES_HEAD, SOURCES_TAIL, SOURCES_NEW + "\\n      rFacts:"),
    ("rFacts", FACTS_HEAD, FACTS_TAIL, FACTS_NEW + "\\n      rFactLine:"),
    ("rFactLine", FACT_LINE_HEAD, FACT_LINE_TAIL, FACT_LINE_NEW),
    ("rFailSources", FAIL_SOURCES_HEAD, FAIL_SOURCES_TAIL, FAIL_SOURCES_NEW),
    ("rFailPanel", FAIL_PANEL_HEAD, FAIL_PANEL_TAIL, FAIL_PANEL_NEW),
    ("rSummary", SUMMARY_HEAD, SUMMARY_TAIL, SUMMARY_NEW),
    ("_CRM", CRM_HEAD, CRM_TAIL, CRM_NEW),
    ("crmVals", CRMVALS_HEAD, CRMVALS_TAIL, CRMVALS_NEW),
    ("profileTab", PROFILE_TAB_HEAD, PROFILE_TAB_TAIL, PROFILE_TAB_NEW),
    ("reportRow", REPORT_ROW_HEAD, REPORT_ROW_TAIL, REPORT_ROW_NEW),
    ("auditRows", AUDIT_ROWS_HEAD, AUDIT_ROWS_TAIL, AUDIT_ROWS_NEW),
    ("auditDet", AUDIT_DET_HEAD, AUDIT_DET_TAIL, AUDIT_DET_NEW),
    ("ctSummary", CT_SUMMARY_HEAD, CT_SUMMARY_TAIL, CT_SUMMARY_NEW),
    ("ctChips", CT_CHIPS_HEAD, CT_CHIPS_TAIL, CT_CHIPS_NEW),
    ("ctCards", CT_CARDS_HEAD, CT_CARDS_TAIL, CT_CARDS_NEW),
    ("ctFacts", CT_FACTS_HEAD, CT_FACTS_TAIL, CT_FACTS_NEW),
    ("ctSources", CT_SRCS_HEAD, CT_SRCS_TAIL, CT_SRCS_NEW),
    ("dashButton", DASH_BTN_HEAD, DASH_BTN_TAIL, DASH_BTN_NEW),
)

RESEARCH_REPLACEMENTS: tuple[tuple[str, str, str], ...] = (
    ("rSrcCount", SRC_COUNT_OLD, SUMMARY_PROPS + SRC_COUNT_NEW),
    ("rToggleRun", TOGGLE_OLD, TOGGLE_NEW),
    ("rRetry", RETRY_OLD, RETRY_NEW),
    ("rFailSrcLine", FAIL_BODY_OLD, FAIL_BODY_NEW),
    ("rerunResearch", RERUN_OLD, RERUN_NEW),
    ("ctRunLine", CT_RUNLINE_OLD, CT_RUNLINE_NEW),
    ("ctSrcLine", CT_SRCLINE_OLD, CT_SRCLINE_NEW),
    ("ctSrcTitle", CT_SRCTITLE_OLD, CT_SRCTITLE_NEW),
    ("ctSumStep", CT_SUMSTEP_OLD, CT_SUMSTEP_NEW),
    ("ctExtract", CT_EXTRACT_OLD, CT_EXTRACT_NEW),
    ("reportSub", REPORT_SUB_OLD, REPORT_SUB_NEW),
    ("reportMap", REPORT_MAP_OLD, REPORT_MAP_NEW),
    ("reportSubProp", REPORT_SUB_ANCHOR, REPORT_SUB_PROP + REPORT_SUB_ANCHOR),
    ("auditFilters", AUDIT_FILTERS_OLD, AUDIT_FILTERS_NEW),
    ("auditClick", AUDIT_CLICK_OLD, AUDIT_CLICK_NEW),
    ("auditRange", AUDIT_RANGE_OLD, AUDIT_RANGE_NEW),
    ("auditMore", AUDIT_MORE_OLD, AUDIT_MORE_NEW),
    ("auditExp", AUDIT_EXP_OLD, AUDIT_EXP_NEW),
    ("ctRunNo", CT_RUNNO_OLD, CT_RUNNO_NEW),
    ("crmOwner", CRM_OWNER_OLD, CRM_OWNER_NEW),
    ("crmMarket", CRM_MARKET_OLD, CRM_MARKET_NEW),
    ("ctProps", CT_PROPS_ANCHOR, CT_PROPS + CRM_EXTRA_PROPS + CT_PROPS_ANCHOR),
)

HTML_PATCHES = frozenset(
    {
        "dashButton",
        "rSummary",
        "ctSummary",
        "ctChips",
        "ctCards",
        "ctFacts",
        "ctSources",
        "profileTab",
        "reportRow",
        "crmOwner",
        "crmMarket",
    }
)

RESEARCH_HIT_COUNT = len(RESEARCH_PATCHES) + len(RESEARCH_REPLACEMENTS)


def _patch_research(src: str) -> tuple[str, int]:
    hits = 0
    for name, head, tail, new in RESEARCH_PATCHES:
        html_patch = name in HTML_PATCHES
        src, ok = _splice(src, head, tail, new if html_patch else _jsafe(new, name))
        hits += int(ok)
    for name, old, new in RESEARCH_REPLACEMENTS:
        if old in src:
            src = src.replace(old, new if name in HTML_PATCHES else _jsafe(new, name), 1)
            hits += 1
    return src, hits


SCREEN_MARK = "/assets/zbs-mark.png"

SCREEN_TITLES = (
    r"Chào \{\{ userName \}\} 👋",
    "Khách hàng",
    "Proposal",
    "Quản trị",
    "Thông báo",
    "Playbook &amp; Skill",
    "Template Library",
    "Product Knowledge Hub",
)

SCREEN_TITLE_RE = re.compile(
    r'(<div style=\\"font:700 (18|21)px Montserrat,Roboto,sans-serif;color:#021F4E\\">)'
    r"(" + "|".join(SCREEN_TITLES) + r")"
    r"(<\\u002Fdiv>)"
)

SCREEN_TITLE_COUNT = 12


def _patch_screen_mark(src: str) -> tuple[str, int]:
    if SCREEN_MARK in src:
        return src, SCREEN_TITLE_COUNT

    def mark(m: re.Match[str]) -> str:
        big = m.group(2) == "21"
        img = (
            f'<img src=\\"{SCREEN_MARK}\\" alt=\\"\\" '
            f'style=\\"height:{22 if big else 19}px;width:auto;'
            f'vertical-align:-{4 if big else 3}px;margin-right:9px\\">'
        )
        return m.group(1) + img + m.group(3) + m.group(4)

    return SCREEN_TITLE_RE.subn(mark, src)


def _patch_proposals(src: str, nl: str) -> tuple[str, bool]:
    anchor = f"_PROPOSALS() {{{nl}    return (this.state.newProps || []).concat(["
    if anchor not in src:
        return src, False
    replacement = (
        f"_PROPOSALS() {{{nl}"
        f"    const __zb = window.__ZB;{nl}"
        f"    if (__zb && __zb.ready && __zb.proposals) "
        f"return (this.state.newProps || []).concat(__zb.proposals);{nl}"
        f"    return (this.state.newProps || []).concat(["
    )
    return src.replace(anchor, replacement, 1), True


def _patch_reqs(src: str, nl: str) -> tuple[str, bool]:
    anchor = f"_REQS() {{ const me = this._me(); return (this.state.reqExtra || []).concat([{nl}"
    if anchor not in src:
        return src, False
    replacement = (
        f"_REQS() {{ const me = this._me(); const __zb = window.__ZB; "
        f"if (__zb && __zb.ready && __zb.reqs) "
        f"return (this.state.reqExtra || []).concat(__zb.reqs); "
        f"return (this.state.reqExtra || []).concat([{nl}"
    )
    return src.replace(anchor, replacement, 1), True


def _patch_user_rows(src: str) -> tuple[str, bool]:
    head = "userRows: ["
    tail = "].concat(this.state.invited || [])"
    start = src.find(head)
    if start < 0:
        return src, False
    end = src.find(tail, start)
    if end < 0:
        return src, False

    live = "userRows: ((window.__ZB && window.__ZB.ready && window.__ZB.users) || ["
    seed = src[start + len(head) : end]
    patched = live + seed + "])" + tail[1:]
    return src[:start] + patched + src[end + len(tail) :], True


def _patch_title(src: str) -> tuple[str, int]:
    hits = 0
    for old in OLD_TITLES:
        for pattern in (f"<title>{old}</title>", f"<title>{old}<\\u002Ftitle>"):
            if pattern in src:
                src = src.replace(pattern, pattern.replace(old, PAGE_TITLE))
                hits += 1
    return src, hits


def _patch_inner_icon(src: str) -> tuple[str, bool]:
    src, n = INNER_ICON_RE.subn(r"\g<1>/favicon.ico\g<2>", src)
    return src, n > 0


def _patch_cust_scope(src: str, nl: str) -> tuple[str, int]:
    hits = 0

    rows_old = (
        f"      const sc = this.state.custScope || 0;{nl}"
        f"      if (sc === 0 ? !!c.owner : !c.owner) return false;"
    )
    rows_new = (
        f"      const sc = this.state.custScope === undefined ? 2 : this.state.custScope;{nl}"
        f"      if (sc !== 2 && (sc === 0 ? !!c.owner : !c.owner)) return false;"
    )
    if rows_old in src:
        src = src.replace(rows_old, rows_new, 1)
        hits += 1

    chips_old = (
        "custFilters: [['Của tôi', 0], ['Được chia sẻ', 1]].map(([label, v]) => { "
        "const sel = (this.state.custScope || 0) === v; "
        "const n = this._LIVECUST().filter((c) => v === 0 ? !c.owner : !!c.owner).length "
        "+ (v === 0 && this.state.createdName ? 1 : 0);"
    )
    chips_new = (
        "custFilters: [['Tất cả', 2], ['Của tôi', 0], ['Được chia sẻ', 1]]"
        ".map(([label, v]) => { "
        "const sel = (this.state.custScope === undefined ? 2 : this.state.custScope) === v; "
        "const n = this._LIVECUST()"
        ".filter((c) => v === 2 ? true : (v === 0 ? !c.owner : !!c.owner)).length "
        "+ (v !== 1 && this.state.createdName ? 1 : 0);"
    )
    if chips_old in src:
        src = src.replace(chips_old, chips_new, 1)
        hits += 1

    clear_old = (
        "custClear: () => this.setState({ custQ: '', custSt: null, custInd: null, custPage: 0 })"
    )
    clear_new = (
        "custClear: () => this.setState({ custQ: '', custSt: null, custInd: null, "
        "custPage: 0, custScope: 2 })"
    )
    if clear_old in src:
        src = src.replace(clear_old, clear_new, 1)
        hits += 1

    return src, hits


def _patch_create_flows(src: str) -> tuple[str, int]:
    hits = 0

    kb_old = "kbCreate: () => { const nm = (this.state.kbName || '').trim(); if (!nm) return;"
    kb_new = (
        "kbCreate: () => { const nm = (this.state.kbName || '').trim(); if (!nm) return; "
        "const __zb = window.__ZB; "
        "if (__zb && __zb.ready) { "
        "const __shared = (this.state.kbScope2 || 0) === 1; "
        "const __desc = (this.state.kbDesc || '').trim() || null; "
        "const __isSkill = (this.state.libTab || 'kb') === 'skill'; "
        "const __p = __isSkill "
        "? __zb.createSkill({ name: nm, description: __desc, shared: __shared }) "
        ": __zb.createKb({ name: nm, description: __desc, shared: __shared, "
        "url: (this.state.kbUrl || '').trim() }); "
        "this.setState({ kbNew: false, kbName: '', kbDesc: '', kbUrl: '', kbScope2: 0, "
        "kbNewQ: 0, libScope: 0 }); "
        "__p.then((r) => this._toast(r.ok "
        "? ((__isSkill ? 'Đã lưu skill “' : 'Đã lưu Knowledge Base “') + nm "
        "+ '” vào database') "
        ": ('Không lưu được: ' + (r.message || 'lỗi backend')))); "
        "return; } "
    )
    if kb_old in src:
        src = src.replace(kb_old, _jsafe(kb_new, "kbCreate"), 1)
        hits += 1

    pick_old = (
        "kbNewPick: () => { const n = (this.state.kbNewQ || 0) + 1; "
        "this.setState({ kbNewQ: n }); this._toast('Đã chọn ' + n + ' tài liệu cho kho mới'); }"
    )
    pick_new = (
        "kbNewPick: () => { const __zb = window.__ZB; "
        "if (__zb && __zb.ready && (this.state.libTab || 'kb') === 'skill') { "
        "__zb.pickSkillFile().then((r) => { "
        "if (r.cancelled) return; "
        "if (!r.ok) { this._toast('Nhập .skill thất bại: ' "
        "+ (r.message || 'lỗi backend')); return; } "
        "const bad = (r.items || []).filter((x) => x.status === 'failed'); "
        "this.setState({ kbNew: false, kbName: '', kbDesc: '', kbUrl: '', kbNewQ: 0, "
        "libTab: 'skill', libScope: 0 }); "
        "this._toast('Đã nhập ' + r.created + ' skill vào database' "
        "+ (bad.length ? ' — ' + bad.length + ' file lỗi: ' + bad[0].message : '')); "
        "}); return; } "
        "const n = (this.state.kbNewQ || 0) + 1; this.setState({ kbNewQ: n }); "
        "this._toast('Đã chọn ' + n + ' tài liệu cho kho mới'); }"
    )
    if pick_old in src:
        src = src.replace(pick_old, _jsafe(pick_new, "kbNewPick"), 1)
        hits += 1

    drop_old = (
        "kbNewDropText: (this.state.kbNewQ || 0) ? 'Đã chọn ' + this.state.kbNewQ "
        "+ ' file — bấm để thêm nữa' "
        ": 'Kéo thả tài liệu (PDF, DOCX, XLSX…) hoặc bấm để chọn file',"
    )
    drop_new = (
        "kbNewDropText: (this.state.libTab || 'kb') === 'skill' "
        "? 'Chọn file .skill để nhập skill có sẵn (JSON hoặc Markdown frontmatter)' "
        ": ((this.state.kbNewQ || 0) ? 'Đã chọn ' + this.state.kbNewQ "
        "+ ' file — bấm để thêm nữa' "
        ": 'Kéo thả tài liệu (PDF, DOCX, XLSX…) hoặc bấm để chọn file'),"
    )
    if drop_old in src:
        src = src.replace(drop_old, _jsafe(drop_new, "kbNewDropText"), 1)
        hits += 1

    prop_old = "openNewSkill: () => this.setState({ screen: 'skilledit', skill: null }),"
    prop_new = (
        "openNewSkill: () => this.setState({ screen: 'skilledit', skill: null }), "
        "zbImportSkill: () => { const __zb = window.__ZB; "
        "if (!__zb || !__zb.ready) { this._toast('Chưa kết nối backend'); return; } "
        "__zb.pickSkillFile().then((r) => { "
        "if (r.cancelled) return; "
        "if (!r.ok) { this._toast('Nhập .skill thất bại: ' "
        "+ (r.message || 'lỗi backend')); return; } "
        "const bad = (r.items || []).filter((x) => x.status === 'failed'); "
        "this.setState({ libTab: 'skill', libScope: 0 }); "
        "this._toast('Đã nhập ' + r.created + ' skill vào database' "
        "+ (bad.length ? ' — ' + bad.length + ' file lỗi: ' + bad[0].message : '')); "
        "}); }, "
        "zbImportSkillShow: (this.state.libTab || 'kb') === 'skill',"
    )
    if prop_old in src:
        src = src.replace(prop_old, _jsafe(prop_new, "zbImportSkill"), 1)
        hits += 1

    btn_old = (
        'sc-camel-on-click=\\"{{ createInLib }}\\">{{ createLibLabel }}<\\u002Fdiv><\\u002Fdiv>'
    )
    btn_new = (
        'sc-camel-on-click=\\"{{ createInLib }}\\">{{ createLibLabel }}<\\u002Fdiv>'
        '<sc-if value=\\"{{ zbImportSkillShow }}\\" hint-placeholder-val=\\"{{ false }}\\">'
        '<div style=\\"margin-left:8px;border:1.5px solid #0068FF;color:#0068FF;'
        "border-radius:8px;padding:8px 13px;font:600 12.5px Roboto,sans-serif;"
        "white-space:nowrap;flex:none;cursor:pointer;"
        'transition:all .22s cubic-bezier(.22,1,.36,1)\\" '
        'style-hover=\\"background:#E8F1FF\\" '
        'sc-camel-on-click=\\"{{ zbImportSkill }}\\">Nhập .skill<\\u002Fdiv><\\u002Fsc-if>'
        "<\\u002Fdiv>"
    )
    if btn_old in src:
        src = src.replace(btn_old, btn_new, 1)
        hits += 1

    save_old = (
        "saveSkill: () => { const cur = this.state.skVer "
        "|| ((this.state.skill || {}).ver || 'v1.0');"
    )
    save_new = (
        "saveSkill: () => { const __zb = window.__ZB; "
        "const __ins = document.querySelectorAll('input'); "
        "const __ta = document.querySelector('textarea'); "
        "if (__zb && __zb.ready && __ins.length >= 3 && __ta) { "
        "const __nm = (__ins[0].value || '').trim(); "
        "const __ds = (__ins[2].value || '').trim(); "
        "const __pr = (__ta.value || '').trim(); "
        "if (!__nm) { this._toast('Cần đặt tên skill trước khi lưu'); return; } "
        "const __sh = (this.state.skill || {}).scope === 'Shared'; "
        "const __id = (this.state.skill || {}).__id; "
        "__zb.saveSkill({ id: __id, name: __nm, description: __ds, prompt: __pr, "
        "shared: __sh }).then((r) => { "
        "if (!r.ok) { this._toast('Không lưu được: ' + (r.message || 'lỗi backend')); return; } "
        "this.setState({ skDirty: false, skVer: r.version }); "
        "this._toast('Đã lưu ' + r.version + ' vào database'); }); return; } "
        "const cur = this.state.skVer || ((this.state.skill || {}).ver || 'v1.0');"
    )
    if save_old in src:
        src = src.replace(save_old, _jsafe(save_new, "saveSkill"), 1)
        hits += 1

    return src, hits


RES_HEAD = "window.__resources = ' +"
RES_TAIL = "';</' + 'script>';"

RES_FALLBACK = (
    "window.__resources = (function(m){"
    "try{return new Proxy(m,{get:function(t,k){"
    "var v=t[k];"
    "if(v!==undefined)return v;"
    'return (typeof k==="string"&&k.indexOf("pptx/")===0)?"/"+k:v;'
    "}});}catch(e){return m;}})(' +"
)


def _patch_resource_fallback(src: str) -> tuple[str, bool]:
    start = src.find(RES_HEAD)
    if start < 0:
        return src, False
    end = src.find(RES_TAIL, start)
    if end < 0:
        return src, False
    middle = src[start + len(RES_HEAD) : end]
    patched = RES_FALLBACK + middle + "')" + RES_TAIL[1:]
    return src[:start] + patched + src[end + len(RES_TAIL) :], True


def _patch_subscribe(src: str, nl: str) -> tuple[str, bool]:
    anchor = f"componentDidMount() {{{nl}"
    if anchor not in src:
        return src, False
    guard = (
        f"{anchor}"
        f"    if (window.__ZB && window.__ZB.subscribe) "
        f"this._zbOff = window.__ZB.subscribe(() => {{ "
        f"const __zb = window.__ZB; "
        f"const __s = this.state.screen ?? this.props.startScreen ?? 'login'; "
        f"if (__zb.ready && __zb.me && __s === 'login') "
        # email trong state là email ở form đăng nhập (đã điền sẵn LOGIN_DEFAULT_EMAIL).
        # Khôi phục session bằng token thì phải đồng bộ lại, không thì header hiển thị
        # sai người: token của A mà tên hiện ra là email điền sẵn.
        f"this.setState({{ screen: __zb.me.needsOnboarding ? 'onboarding' : 'dashboard', "
        f"onboarded: !__zb.me.needsOnboarding, email: __zb.me.email || this.state.email }}); "
        f"else this.forceUpdate(); }});{nl}"
    )
    return src.replace(anchor, guard, 1), True


def _patch_customer_map(src: str) -> tuple[str, bool]:
    old = (
        "_byKey(k) { if (!this.__cmap) { this.__cmap = {}; "
        "this._CUST().forEach((c) => { this.__cmap[c.key] = c; }); } "
        "return this.__cmap[k] || this._CUST()[0]; }"
    )
    if old not in src:
        return src, False
    new = (
        "_byKey(k) { const __src = this._CUST(); "
        "if (this.__cmapSrc !== __src) { this.__cmap = {}; "
        "__src.forEach((c) => { this.__cmap[c.key] = c; }); this.__cmapSrc = __src; } "
        "return this.__cmap[k] || __src[0] || this._zbBlank(); }"
        f"{BLANK_CUST_METHOD}"
    )
    return src.replace(old, new, 1), True


def _patch_blank_cust(src: str) -> tuple[str, bool]:
    old = "const cust = this.state.cust || this._CUST()[0]"
    if old not in src:
        return src, False
    new = "const cust = this.state.cust || this._CUST()[0] || this._zbBlank()"
    return src.replace(old, new, 1), True


def _patch_login_email(src: str) -> tuple[str, bool]:
    """Prototype ghi cứng email prefill của tác giả. Lấy theo LOGIN_DEFAULT_EMAIL để
    người dùng bấm Gửi mã là xong, không phải gõ lại mỗi lần reload."""
    default = settings.login_default_email.strip()
    if not default:
        return src, False
    old = "loginStep: 'email', email: 'khanhpp@vng.com.vn'"
    new = f"loginStep: 'email', email: '{default}'"
    if old not in src:
        return src, False
    return src.replace(old, _jsafe(new, "loginEmail"), 1), True


def _patch_login(src: str, nl: str) -> tuple[str, int]:
    hits = 0

    known_old = "if (known.indexOf(handle) < 0) { this.setState({ loginErr: 'unknown' }); return; }"
    known_new = (
        "if (!(window.__ZB) && known.indexOf(handle) < 0) "
        "{ this.setState({ loginErr: 'unknown' }); return; }"
    )
    if known_old in src:
        src = src.replace(known_old, known_new, 1)
        hits += 1

    net_guard = (
        "        if (this.state.netDown) "
        "{ this.setState({ sending: false, loginErr: 'net' }); return; }"
    )
    mock_to_otp = (
        "        this.setState({ sending: false, loginStep: 'otp', otp: '', otpErr: 0, "
        "otpLeft: 90, otpCool: 30, otpExpired: false, otpLocked: false, otpLockLeft: 0, "
        "otpBad: false });"
    )
    send_old = (
        f"      this._sendT = setTimeout(() => {{{nl}"
        f"{net_guard}{nl}{mock_to_otp}{nl}"
        f"      }}, 620);"
    )
    send_new = (
        f"      const __toOtp = (ttl, cool) => this.setState({{ sending: false, "
        f"loginStep: 'otp', otp: '', otpErr: 0, otpLeft: ttl || 90, otpCool: cool || 30, "
        f"otpExpired: false, otpLocked: false, otpLockLeft: 0, otpBad: false }});{nl}"
        f"      if (window.__ZB) {{{nl}"
        f"        window.__ZB.requestOtp(em).then((r) => {{{nl}"
        f"          if (r.ok && r.direct) this.setState({{ sending: false, "
        f"screen: (this.state.onboarded || !r.needsOnboarding) ? 'dashboard' : 'onboarding', "
        f"otp: '', otpBad: false, otpErr: 0, loginErr: null }});{nl}"
        f"          else if (r.ok) __toOtp(r.ttl, r.resendAfter);{nl}"
        f"          else this.setState({{ sending: false, loginErr: r.kind }});{nl}"
        f"        }});{nl}"
        f"        return;{nl}"
        f"      }}{nl}"
        f"      this._sendT = setTimeout(() => {{{nl}{net_guard}{nl}"
        f"        __toOtp();{nl}"
        f"      }}, 620);"
    )
    if send_old in src:
        src = src.replace(send_old, send_new, 1)
        hits += 1

    return src, hits


def _patch_otp_verify(src: str, nl: str) -> tuple[str, bool]:
    old = (
        f"        this._otpT = setTimeout(() => {{{nl}"
        f"          if (v === '111111') {{{nl}"
        f"            const n = (this.state.otpErr || 0) + 1;{nl}"
        f"            if (n >= 3) this.setState({{ otpErr: n, otp: '', otpBad: false, "
        f"otpLocked: true, otpLockLeft: 900 }});{nl}"
        f"            else this.setState({{ otpErr: n, otp: '', otpBad: true }});{nl}"
        f"            return;{nl}"
        f"          }}{nl}"
        f"          this.setState({{ screen: this.state.onboarded ? 'dashboard' : 'onboarding', "
        f"otp: '', otpBad: false, otpErr: 0 }});{nl}"
        f"        }}, 700);"
    )
    if old not in src:
        return src, False
    new = (
        f"        const __enter = (needs) => this.setState({{ screen: "
        f"(this.state.onboarded || !needs) ? 'dashboard' : 'onboarding', otp: '', "
        f"otpBad: false, otpErr: 0 }});{nl}"
        f"        const __otpFail = (kind) => {{{nl}"
        f"          const n = (this.state.otpErr || 0) + 1;{nl}"
        f"          if (kind === 'locked') this.setState({{ otpErr: n, otp: '', "
        f"otpBad: false, otpLocked: true, otpLockLeft: 900 }});{nl}"
        f"          else if (kind === 'expired') this.setState({{ otpErr: n, otp: '', "
        f"otpBad: false, otpExpired: true }});{nl}"
        f"          else this.setState({{ otpErr: n, otp: '', otpBad: true }});{nl}"
        f"        }};{nl}"
        f"        if (window.__ZB) {{{nl}"
        f"          window.__ZB.verifyOtp(this.state.email, v).then((r) => {{{nl}"
        f"            if (r.ok) __enter(r.needsOnboarding); else __otpFail(r.kind);{nl}"
        f"          }});{nl}"
        f"          return;{nl}"
        f"        }}{nl}"
        f"        this._otpT = setTimeout(() => {{{nl}"
        f"          if (v === '111111') {{ __otpFail((this.state.otpErr || 0) + 1 >= 3 "
        f"? 'locked' : 'bad'); return; }}{nl}"
        f"          __enter(false);{nl}"
        f"        }}, 700);"
    )
    return src.replace(old, new, 1), True


def _patch_logout(src: str) -> tuple[str, int]:
    old = "run: () => this.setState({ screen: 'login', otp: '', loginStep: 'email', email: ''"
    new = (
        "run: () => { if (window.__ZB) window.__ZB.logout(); "
        "return this.setState({ screen: 'login', otp: '', loginStep: 'email', email: ''"
    )
    if old not in src:
        return src, 0
    hits = src.count(old)
    src = src.replace(old, new)
    src = src.replace(
        "otpBad: false, otpExpired: false, otpLocked: false }) })",
        "otpBad: false, otpExpired: false, otpLocked: false }); } })",
    )
    return src, hits


def patch_prototype(html: str) -> tuple[str, PatchReport]:
    report = PatchReport()
    original = html
    nl = "\\n" if "_CUST() {\n" not in html else "\n"

    for method, key in BLOCK_SOURCES.items():
        html, ok = _patch_block(html, method, key, nl)
        _mark(report, method, ok)

    for method, key in ONELINE_SOURCES.items():
        html, ok = _patch_oneline(html, method, key)
        _mark(report, method, ok)

    for method in EMPTY_SOURCES:
        html, ok = _patch_empty(html, method)
        _mark(report, method, ok)

    html, ok = _patch_prods(html, nl)
    _mark(report, "_PRODS", ok)

    html, hits = _patch_prod_hub_sub(html)
    _mark(report, f"prodHubSub({hits})", hits == 2)

    html, hits = _patch_onboarding_done(html)
    _mark(report, f"onboardingDone({hits})", hits == 3)

    html, hits = _patch_notifs(html)
    _mark(report, f"notifs({hits})", hits == 10)

    html, ok = _patch_top_prods(html)
    _mark(report, "topProds", ok)

    html, hits = _patch_empty_states(html)
    _mark(report, f"emptyStates({hits})", hits == 3)

    html, hits = _patch_screen_mark(html)
    _mark(report, f"screenMark({hits})", hits == SCREEN_TITLE_COUNT)

    html, hits = _patch_research(html)
    _mark(report, f"research({hits})", hits == RESEARCH_HIT_COUNT)

    html, hits = _patch_skill_versions(html)
    _mark(report, f"skillVersions({hits})", hits == 7)

    html, ok = _patch_kb_icon(html)
    _mark(report, "kbIcon", ok)

    html, ok = _patch_skill_tools(html)
    _mark(report, "skTools", ok)

    html, ok = _patch_skill_tools_save(html)
    _mark(report, "skToolsSave", ok)

    html, hits = _patch_cust_columns(html)
    _mark(report, f"custCols({hits})", hits == 7)

    html, hits = _patch_cust_status(html)
    _mark(report, f"custStatus({hits})", hits == 2)

    html, hits = _patch_pdf_only(html)
    _mark(report, f"pdfOnly({hits})", hits == 21)

    html, hits = _patch_template_upload(html)
    _mark(report, f"tplUpload({hits})", hits == 3)

    html, hits = _patch_template_preview(html)
    _mark(report, f"tplPreview({hits})", hits == 8)

    html, ok = _patch_no_save_template(html)
    _mark(report, "noSaveTpl", ok)

    html, hits = _patch_template_meta(html)
    _mark(report, f"tplMeta({hits})", hits == 6)

    html, hits = _patch_template_versions(html)
    _mark(report, f"tplVersions({hits})", hits == 5)

    html, ok = _patch_proposals(html, nl)
    _mark(report, "_PROPOSALS", ok)

    html, ok = _patch_customer_map(html)
    _mark(report, "_byKey", ok)

    html, ok = _patch_blank_cust(html)
    _mark(report, "blankCust", ok)

    html, ok = _patch_reqs(html, nl)
    _mark(report, "_REQS", ok)

    html, ok = _patch_user_rows(html)
    _mark(report, "userRows", ok)

    html, hits = _patch_cust_scope(html, nl)
    _mark(report, f"custScope({hits})", hits == 3)

    html, hits = _patch_create_flows(html)
    _mark(report, f"createFlows({hits})", hits == 6)

    html, ok = _patch_login_email(html)
    _mark(report, "loginEmail", ok)

    html, hits = _patch_login(html, nl)
    _mark(report, f"login({hits})", hits == 2)

    html, ok = _patch_otp_verify(html, nl)
    _mark(report, "otpVerify", ok)

    html, hits = _patch_logout(html)
    _mark(report, f"logout({hits})", hits > 0)

    if BRIDGE_TAG not in html:
        if "</head>" in html:
            html = html.replace("</head>", f"  {BRIDGE_TAG}\n</head>", 1)
            _mark(report, "bridgeTag", True)
        else:
            _mark(report, "bridgeTag", False)

    html, ok = _patch_subscribe(html, nl)
    _mark(report, "subscribe", ok)

    html, ok = _patch_resource_fallback(html)
    _mark(report, "resFallback", ok)

    if 'rel="icon"' not in html:
        if "</head>" in html:
            html = html.replace("</head>", f"  {ICON_TAG}\n</head>", 1)
            _mark(report, "iconTag", True)
        else:
            _mark(report, "iconTag", False)

    html, ok = _patch_inner_icon(html)
    _mark(report, "innerIcon", ok)

    html, hits = _patch_title(html)
    _mark(report, f"title({hits})", hits == 2)

    for problem in check_balance(original, html):
        _mark(report, f"balance[{problem}]", False)

    return html, report


class PrototypeCache:
    def __init__(self) -> None:
        self._key: tuple[str, int, float] | None = None
        self._html: str | None = None
        self._report: PatchReport | None = None

    def render(self, path: Path) -> str:
        stat = path.stat()
        key = (str(path), stat.st_size, stat.st_mtime)
        if self._key == key and self._html is not None:
            return self._html

        raw = path.read_text(encoding="utf-8", errors="replace")
        html, report = patch_prototype(raw)
        self._key = key
        self._html = html
        self._report = report

        if report.ok:
            log.info("prototype_patched", file=path.name, applied=report.applied)
        else:
            log.warning(
                "prototype_patched_partial",
                file=path.name,
                applied=report.applied,
                missed=report.missed,
                detail="Seam nào thiếu thì phần đó giữ mock.",
            )
        return html

    @property
    def report(self) -> PatchReport | None:
        return self._report


cache = PrototypeCache()
