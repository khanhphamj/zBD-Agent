import io
import posixpath
import zipfile
from dataclasses import dataclass, field
from typing import Any

import yaml

from app.core.errors import ValidationError
from app.core.text import slugify
from app.db.models.enums import Scope, SkillStage

MANIFEST = "SKILL.md"
EXTENSIONS = ("skill", "zip", "md")
MAX_BYTES = 20 * 1024 * 1024
MAX_FILES = 60
MAX_FILE_BYTES = 5 * 1024 * 1024

_SKIP_PREFIXES = ("__MACOSX/", ".git/")
_SKIP_NAMES = (".DS_Store", "Thumbs.db")

_STAGE_ALIASES = {
    "research": SkillStage.RESEARCH,
    "nghien cuu": SkillStage.RESEARCH,
    "profile": SkillStage.PROFILE,
    "ho so": SkillStage.PROFILE,
    "matching": SkillStage.MATCHING,
    "phuong an": SkillStage.MATCHING,
    "proposal": SkillStage.PROPOSAL,
    "review": SkillStage.REVIEW,
    "kiem duyet": SkillStage.REVIEW,
}

TOOLS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("web_search", ("web search", "websearch", "search", "tim kiem")),
    ("crawler", ("crawler", "crawl", "web crawl", "ai web crawl")),
    ("product_kb", ("product knowledge hub", "product knowledge", "product kb", "internal kb")),
    ("template_library", ("template library", "template", "tpl")),
)

TOOL_LABELS = {
    "web_search": "Web Search",
    "crawler": "Crawler",
    "product_kb": "Product Knowledge Hub",
    "template_library": "Template Library",
}


_SCOPE_ALIASES = {
    "mine": Scope.MINE,
    "rieng": Scope.MINE,
    "private": Scope.MINE,
    "shared": Scope.SHARED,
    "dung chung": Scope.SHARED,
    "team": Scope.SHARED,
}


@dataclass(slots=True)
class BundleFile:
    path: str
    data: bytes

    @property
    def size(self) -> int:
        return len(self.data)


@dataclass(slots=True)
class SkillDoc:
    name: str
    prompt: str
    description: str | None = None
    stage: SkillStage = SkillStage.RESEARCH
    scope: Scope = Scope.MINE
    model: str | None = None
    temperature: float | None = None
    input_schema: list[dict[str, Any]] = field(default_factory=list)
    version: str | None = None
    tools: list[str] = field(default_factory=list)
    root: str = ""
    files: list[BundleFile] = field(default_factory=list)
    source: str = "bundle"
    meta: dict[str, Any] = field(default_factory=dict)


def _fold(value: str) -> str:
    from app.core.text import strip_accents

    return strip_accents(value).strip().lower()


def _stage(raw: Any) -> SkillStage:
    if raw in (None, ""):
        return SkillStage.RESEARCH
    text = str(raw)
    for stage in SkillStage:
        if text == str(stage):
            return stage
    hit = _STAGE_ALIASES.get(_fold(text))
    if hit is None:
        allowed = ", ".join(str(s) for s in SkillStage)
        raise ValidationError(f"stage “{text}” không hợp lệ. Chọn một trong: {allowed}.", "stage")
    return hit


def _scope(raw: Any) -> Scope:
    if raw in (None, ""):
        return Scope.MINE
    text = str(raw)
    for scope in Scope:
        if text == str(scope):
            return scope
    hit = _SCOPE_ALIASES.get(_fold(text))
    if hit is None:
        raise ValidationError(f"scope “{text}” không hợp lệ. Dùng Mine hoặc Shared.", "scope")
    return hit


def _tools(raw: Any) -> list[str]:
    if raw in (None, ""):
        return []
    items = raw if isinstance(raw, list) else [p.strip() for p in str(raw).split(",")]
    out: list[str] = []
    for item in items:
        folded = _fold(str(item))
        for key, aliases in TOOLS:
            if folded == key or folded in aliases:
                if key not in out:
                    out.append(key)
                break
    return out


def _temperature(raw: Any) -> float | None:
    if raw in (None, ""):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"temperature “{raw}” không phải số.", "temperature") from exc
    if not 0 <= value <= 2:
        raise ValidationError("temperature phải nằm trong khoảng 0–2.", "temperature")
    return value


def _input_schema(raw: Any) -> list[dict[str, Any]]:
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise ValidationError("inputSchema phải là một danh sách.", "inputSchema")
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValidationError(f"inputSchema[{i}] phải là một object.", "inputSchema")
    return list(raw)


def split_frontmatter(text: str, label: str) -> tuple[dict[str, Any], str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.lstrip().startswith("---"):
        raise ValidationError(
            f"{label}: {MANIFEST} phải mở đầu bằng khối frontmatter “---”.", "file"
        )

    body = normalized.lstrip()
    end = body.find("\n---", 3)
    if end < 0:
        raise ValidationError(f"{label}: frontmatter chưa được đóng bằng “---”.", "file")

    raw_meta = body[3:end]
    rest = body[end + 4 :].lstrip("\n")

    try:
        meta = yaml.safe_load(raw_meta) or {}
    except yaml.YAMLError as exc:
        raise ValidationError(
            f"{label}: frontmatter không phải YAML hợp lệ ({exc}).", "file"
        ) from exc
    if not isinstance(meta, dict):
        raise ValidationError(f"{label}: frontmatter phải là các cặp key: value.", "file")
    return meta, rest.strip()


def _keep(name: str) -> bool:
    if name.endswith("/"):
        return False
    if any(name.startswith(p) or f"/{p}" in name for p in _SKIP_PREFIXES):
        return False
    return posixpath.basename(name) not in _SKIP_NAMES


def _pick_manifest(names: list[str]) -> str:
    candidates = [n for n in names if posixpath.basename(n) == MANIFEST]
    if not candidates:
        raise ValidationError(
            f"Không tìm thấy {MANIFEST} trong bundle. Cấu trúc cần là "
            f"<tên-skill>/{MANIFEST} kèm thư mục references/ (tuỳ chọn).",
            "file",
        )
    return min(candidates, key=lambda n: (n.count("/"), len(n)))


def _doc_from_meta(
    meta: dict[str, Any], body: str, label: str, root: str, files: list[BundleFile], source: str
) -> SkillDoc:
    name = str(meta.get("name") or "").strip()
    if not name:
        raise ValidationError(f"{label}: thiếu “name” trong frontmatter.", "name")
    if len(name) > 255:
        raise ValidationError(f"{label}: name dài quá 255 ký tự.", "name")
    if not body.strip():
        raise ValidationError(f"{label}: {MANIFEST} không có nội dung sau frontmatter.", "prompt")

    description = meta.get("description")
    if description is not None:
        description = " ".join(str(description).split()) or None

    known = {
        "name",
        "description",
        "stage",
        "scope",
        "model",
        "temperature",
        "version",
        "inputschema",
        "input_schema",
        "tools",
    }
    extra = {k: v for k, v in meta.items() if k.lower() not in known}

    return SkillDoc(
        name=name,
        prompt=body.strip(),
        description=description,
        stage=_stage(meta.get("stage")),
        scope=_scope(meta.get("scope")),
        model=(str(meta["model"]).strip() or None) if meta.get("model") else None,
        temperature=_temperature(meta.get("temperature")),
        input_schema=_input_schema(meta.get("inputSchema") or meta.get("input_schema")),
        version=(str(meta["version"]).strip() or None) if meta.get("version") else None,
        tools=_tools(meta.get("tools")),
        root=root,
        files=files,
        source=source,
        meta=extra,
    )


def parse(data: bytes, filename: str | None = None) -> SkillDoc:
    label = filename or "file"
    if not data:
        raise ValidationError(f"{label}: file rỗng.", "file")
    if len(data) > MAX_BYTES:
        raise ValidationError(
            f"{label}: bundle tối đa {MAX_BYTES // 1024 // 1024} MB (đang {len(data) // 1024} KB).",
            "file",
        )

    if not zipfile.is_zipfile(io.BytesIO(data)):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError(
                f"{label}: không phải bundle .skill (ZIP) và cũng không đọc được như "
                f"{MANIFEST} UTF-8.",
                "file",
            ) from exc
        meta, body = split_frontmatter(text, label)
        return _doc_from_meta(meta, body, label, root="", files=[], source="markdown")

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        bad = zf.testzip()
        if bad is not None:
            raise ValidationError(f"{label}: file hỏng ở “{bad}”.", "file")

        names = [n for n in zf.namelist() if _keep(n)]
        if not names:
            raise ValidationError(f"{label}: bundle không có file nào.", "file")
        if len(names) > MAX_FILES:
            raise ValidationError(
                f"{label}: bundle tối đa {MAX_FILES} file (đang {len(names)}).", "file"
            )

        manifest_path = _pick_manifest(names)
        root = posixpath.dirname(manifest_path)

        try:
            manifest_text = zf.read(manifest_path).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError(f"{label}: {MANIFEST} phải là UTF-8.", "file") from exc

        meta, body = split_frontmatter(manifest_text, label)

        files: list[BundleFile] = []
        for name in sorted(names):
            if name == manifest_path:
                continue
            rel = posixpath.relpath(name, root) if root else name
            if rel.startswith(".."):
                continue
            info = zf.getinfo(name)
            if info.file_size > MAX_FILE_BYTES:
                raise ValidationError(
                    f"{label}: “{rel}” vượt {MAX_FILE_BYTES // 1024 // 1024} MB.", "file"
                )
            files.append(BundleFile(path=rel, data=zf.read(name)))

    return _doc_from_meta(meta, body, label, root=root, files=files, source="bundle")


def render_manifest(skill: Any) -> bytes:
    meta: dict[str, Any] = {"name": skill.name}
    if skill.description:
        meta["description"] = skill.description
    meta["stage"] = str(skill.stage)
    meta["scope"] = str(skill.scope)
    if skill.version:
        meta["version"] = skill.version
    if skill.model:
        meta["model"] = skill.model
    if skill.temperature is not None:
        meta["temperature"] = skill.temperature
    if skill.input_schema:
        meta["inputSchema"] = skill.input_schema
    if getattr(skill, "tools", None):
        meta["tools"] = [TOOL_LABELS.get(t, t) for t in skill.tools]
    for key, value in (getattr(skill, "bundle", None) or {}).get("meta", {}).items():
        meta.setdefault(key, value)

    head = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return f"---\n{head}---\n\n{skill.prompt or ''}\n".encode()


def build_bundle(skill: Any, files: list[BundleFile]) -> bytes:
    root = slugify(skill.name) or "skill"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{root}/{MANIFEST}", render_manifest(skill))
        for item in files:
            zf.writestr(f"{root}/{item.path}", item.data)
    return buffer.getvalue()


def check_extension(filename: str | None) -> str:
    name = filename or ""
    ext = (name.rsplit(".", 1)[-1] if "." in name else "").lower()
    if ext not in EXTENSIONS:
        raise ValidationError(
            f"{name or 'file'}: chỉ nhận .skill (bundle ZIP), .zip, hoặc {MANIFEST} rời (.md).",
            "file",
        )
    return ext


__all__ = [
    "EXTENSIONS",
    "MANIFEST",
    "TOOLS",
    "TOOL_LABELS",
    "BundleFile",
    "SkillDoc",
    "build_bundle",
    "check_extension",
    "parse",
    "render_manifest",
    "split_frontmatter",
]
