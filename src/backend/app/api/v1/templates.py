import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, File, Query, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, SessionDep
from app.core.config import settings
from app.core.errors import FileSizeError, FileTypeError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.security import content_hash
from app.core.text import next_version, normalize, search_term, slugify
from app.db.models.enums import Permission, ResourceType, Scope
from app.db.models.library import ProposalTemplate, TemplateSlide, TemplateVersion
from app.schemas.requests import TemplatePatch, TemplateUpsert
from app.schemas.serializers import (
    template_detail,
    template_row,
)
from app.schemas.serializers import (
    template_slide as serialize_slide,
)
from app.schemas.serializers import (
    template_version as serialize_version,
)
from app.services import audit, deckmeta
from app.services.shares import apply_visibility, purge_links, require, resolve
from app.services.storage import storage

log = get_logger(__name__)
router = APIRouter(tags=["templates"])


@router.get("/templates", summary="Danh sách proposal template")
async def list_templates(
    user: CurrentUser,
    session: SessionDep,
    q: Annotated[str | None, Query()] = None,
    scope: Annotated[str | None, Query(description="mine | shared")] = None,
    tone: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    stmt = apply_visibility(select(ProposalTemplate), ResourceType.TEMPLATE, user).options(
        selectinload(ProposalTemplate.owner)
    )
    if scope == "mine":
        stmt = stmt.where(ProposalTemplate.owner_id == user.id)
    elif scope == "shared":
        stmt = stmt.where(ProposalTemplate.scope == Scope.SHARED)
    if tone:
        stmt = stmt.where(ProposalTemplate.tone == tone)
    if q:
        stmt = stmt.where(ProposalTemplate.name_norm.ilike(search_term(q)))

    rows = (
        (
            await session.execute(
                stmt.order_by(ProposalTemplate.used_count.desc(), ProposalTemplate.name_norm)
            )
        )
        .unique()
        .scalars()
        .all()
    )
    items: list[dict[str, Any]] = []
    for row in rows:
        access = await resolve(session, user, ResourceType.TEMPLATE, row.id, row)
        items.append(template_row(row, locked=not access.allowed))
    return {"items": items, "total": len(items)}


@router.post("/templates", status_code=status.HTTP_201_CREATED, summary="Tạo template")
async def create_template(
    body: TemplateUpsert, user: CurrentUser, session: SessionDep, request: Request
) -> dict[str, Any]:
    item = ProposalTemplate(
        id=ProposalTemplate.make_id(),
        name=body.name,
        name_norm=normalize(body.name),
        subtitle=body.subtitle,
        scope=body.scope,
        version="v1.0",
        tone=body.tone,
        required_slides=body.requiredSlides,
        slide_count=body.slideCount or len(body.slides),
        cover_url=body.coverUrl,
        theme=body.theme,
        owner_id=user.id,
    )
    session.add(item)
    await session.flush()

    for index, entry in enumerate(body.slides, start=1):
        session.add(
            TemplateSlide(
                id=TemplateSlide.make_id(),
                template_id=item.id,
                index=int(entry.get("index") or index),
                title=entry.get("title") or f"Slide {index}",
                layout=entry.get("layout", "content"),
                image_url=entry.get("imageUrl"),
                placeholders=entry.get("placeholders") or [],
                guidance=entry.get("guidance"),
                optional=bool(entry.get("optional", False)),
            )
        )
    if body.slides:
        item.slide_count = len(body.slides)

    session.add(
        TemplateVersion(
            id=TemplateVersion.make_id(),
            template_id=item.id,
            version="v1.0",
            note="Phiên bản đầu tiên",
            author_id=user.id,
            snapshot=_snapshot_of(item, body.slides),
        )
    )
    await audit.record(
        session,
        action="template.create",
        summary=f"{user.name} tạo template {item.name}",
        actor=user,
        resource_type="template",
        resource_id=item.id,
        resource_label=item.name,
        request=request,
    )
    await session.commit()
    await session.refresh(item)
    return template_detail(item, await _slides(session, item.id))


def _snapshot_of(item: ProposalTemplate, slides: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "template": {
            "name": item.name,
            "subtitle": item.subtitle,
            "tone": item.tone,
            "requiredSlides": item.required_slides,
            "theme": item.theme,
            "slideCount": item.slide_count,
        },
        "slides": slides,
    }


@router.get("/templates/{template_id}", summary="Chi tiết template")
async def get_template(template_id: str, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    item, access = await require(session, user, ResourceType.TEMPLATE, template_id, Permission.VIEW)
    slides = await _slides(session, template_id)
    return {
        **template_detail(item, slides),
        "myPermission": str(access.permission),
    }


async def _slides(session: SessionDep, template_id: str) -> list[TemplateSlide]:
    return list(
        (
            await session.execute(
                select(TemplateSlide)
                .where(TemplateSlide.template_id == template_id)
                .order_by(TemplateSlide.index)
            )
        )
        .scalars()
        .all()
    )


async def _renderable_pdf(item: ProposalTemplate) -> bytes | None:
    """Template chỉ nhận PDF nên file gốc render trực tiếp; preview_key chỉ còn ý
    nghĩa với vài bản upload PPTX từ trước khi bỏ hỗ trợ định dạng đó."""
    key = item.object_key if (item.theme or {}).get("kind") == "pdf" else item.preview_key
    if not key:
        return None
    try:
        return await storage.get(settings.bucket_templates, key)
    except Exception as exc:
        log.warning("template_pdf_missing", template=item.id, key=key, error=str(exc))
        return None


@router.get("/templates/{template_id}/pages", summary="Số trang xem trước được")
async def template_pages(
    template_id: str, user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    item, _ = await require(session, user, ResourceType.TEMPLATE, template_id, Permission.VIEW)
    data = await _renderable_pdf(item)
    total = deckmeta.page_count(data) if data else 0
    return {
        "templateId": item.id,
        "total": total,
        "renderable": total > 0,
        "reason": None
        if total
        else (
            "Bản upload cũ định dạng PPTX — hệ thống chỉ hỗ trợ PDF"
            if (item.theme or {}).get("kind") == "pptx"
            else "Không đọc được file"
        ),
        "urls": [
            f"{settings.api_prefix}/templates/{item.id}/pages/{n}" for n in range(1, total + 1)
        ],
    }


@router.get("/templates/{template_id}/pages/{index}", summary="Ảnh một trang/slide")
async def template_page(
    template_id: str, index: int, user: CurrentUser, session: SessionDep
) -> Response:
    item, _ = await require(session, user, ResourceType.TEMPLATE, template_id, Permission.VIEW)
    if index < 1:
        raise ValidationError("index phải từ 1 trở lên", field="index")

    cache_key = storage.template_key(item.id, f"pages/{index}.png")
    try:
        cached = await storage.get(settings.bucket_templates, cache_key)
        return Response(
            content=cached,
            media_type="image/png",
            headers={"Cache-Control": "private, max-age=86400"},
        )
    except Exception:
        pass

    data = await _renderable_pdf(item)
    if data is None:
        raise NotFoundError("Bản PDF để render trang")

    rendered = await asyncio.to_thread(deckmeta.render_page, data, index)
    if rendered is None:
        raise NotFoundError(f"Trang {index}")

    await storage.put(settings.bucket_templates, cache_key, rendered, content_type="image/png")
    return Response(
        content=rendered,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.get("/templates/{template_id}/thumbnail", summary="Ảnh bìa — trang/slide đầu")
async def template_thumbnail(template_id: str, user: CurrentUser, session: SessionDep) -> Response:
    item, _ = await require(session, user, ResourceType.TEMPLATE, template_id, Permission.VIEW)
    key = (item.theme or {}).get("coverKey")
    if not key:
        raise NotFoundError("Ảnh bìa template")
    try:
        data = await storage.get(settings.bucket_templates, key)
    except Exception as exc:
        raise NotFoundError("Ảnh bìa template") from exc
    ext = key.rsplit(".", 1)[-1].lower()
    return Response(
        content=data,
        media_type=f"image/{'jpeg' if ext == 'jpg' else ext}",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.get("/templates/{template_id}/file", summary="Link xem file gốc đã upload")
async def template_file(
    template_id: str,
    user: CurrentUser,
    session: SessionDep,
    download: Annotated[bool, Query(description="Tải về thay vì xem inline")] = False,
) -> dict[str, Any]:
    item, _ = await require(session, user, ResourceType.TEMPLATE, template_id, Permission.VIEW)
    if not item.object_key:
        raise NotFoundError("File gốc của template")
    filename = item.source_filename or f"{item.name}.pdf"

    # Xem trước thì dùng bản PDF đã convert (browser render được); tải về thì đưa
    # đúng file gốc người dùng đã upload.
    key = item.object_key if download else (item.preview_key or item.object_key)
    if key == item.preview_key:
        filename = f"{slugify(item.name) or 'template'}.pdf"

    url = await storage.presigned_url(
        settings.bucket_templates,
        key,
        download_name=item.source_filename if download else None,
    )
    return {
        "url": url,
        "filename": filename,
        "sizeBytes": item.size_bytes,
        "kind": (item.theme or {}).get("kind"),
        "previewKind": (item.theme or {}).get("previewKind"),
        "inlineViewable": bool(item.preview_key) or (item.theme or {}).get("kind") == "pdf",
        "expiresInSeconds": settings.presigned_url_ttl_seconds,
    }


@router.get("/templates/{template_id}/preview", summary="Ảnh preview từng slide")
async def preview_template(
    template_id: str, user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    item, _ = await require(session, user, ResourceType.TEMPLATE, template_id, Permission.VIEW)
    slides = await _slides(session, template_id)
    return {
        "templateId": item.id,
        "name": item.name,
        "version": item.version,
        "coverUrl": item.cover_url,
        "slides": [serialize_slide(s) for s in slides],
        "total": len(slides),
    }


@router.patch("/templates/{template_id}", summary="Cập nhật template — tăng version")
async def update_template(
    template_id: str,
    body: TemplatePatch,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> dict[str, Any]:
    item, _ = await require(session, user, ResourceType.TEMPLATE, template_id, Permission.EDIT)

    existing = await _slides(session, template_id)
    structural = body.slides is not None or body.requiredSlides is not None

    if structural:
        item.version = next_version(item.version)

    if body.name is not None:
        item.name = body.name
        item.name_norm = normalize(body.name)
    for field, value in (
        ("subtitle", body.subtitle),
        ("scope", body.scope),
        ("tone", body.tone),
        ("required_slides", body.requiredSlides),
        ("cover_url", body.coverUrl),
        ("theme", body.theme),
    ):
        if value is not None:
            setattr(item, field, value)

    if body.slides is not None:
        for s in existing:
            await session.delete(s)
        await session.flush()
        for index, entry in enumerate(body.slides, start=1):
            session.add(
                TemplateSlide(
                    id=TemplateSlide.make_id(),
                    template_id=template_id,
                    index=int(entry.get("index") or index),
                    title=entry.get("title") or f"Slide {index}",
                    layout=entry.get("layout", "content"),
                    image_url=entry.get("imageUrl"),
                    placeholders=entry.get("placeholders") or [],
                    guidance=entry.get("guidance"),
                    optional=bool(entry.get("optional", False)),
                )
            )
        item.slide_count = len(body.slides)
    elif body.slideCount is not None:
        item.slide_count = body.slideCount

    if structural:
        await session.flush()
        session.add(
            TemplateVersion(
                id=TemplateVersion.make_id(),
                template_id=template_id,
                version=item.version,
                note=body.note,
                author_id=user.id,
                snapshot=_snapshot_of(
                    item,
                    [
                        {
                            "index": s.index,
                            "title": s.title,
                            "layout": s.layout,
                            "imageUrl": s.image_url,
                            "placeholders": s.placeholders,
                            "guidance": s.guidance,
                            "optional": s.optional,
                        }
                        for s in await _slides(session, template_id)
                    ],
                ),
            )
        )

    await audit.record(
        session,
        action="template.update",
        summary=f"{user.name} cập nhật template {item.name} ({item.version})",
        actor=user,
        resource_type="template",
        resource_id=template_id,
        resource_label=item.name,
        meta={"version": item.version},
        request=request,
    )
    await session.commit()
    await session.refresh(item)
    return template_detail(item, await _slides(session, template_id))


@router.delete(
    "/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Xoá template"
)
async def delete_template(
    template_id: str, user: CurrentUser, session: SessionDep, request: Request
) -> None:
    item, _ = await require(session, user, ResourceType.TEMPLATE, template_id, Permission.EDIT)
    if item.is_builtin:
        raise ValidationError("Template mặc định của hệ thống không xoá được.")
    label = item.name
    files = await storage.delete_prefix(settings.bucket_templates, f"templates/{template_id}/")
    purged = await purge_links(session, ResourceType.TEMPLATE, template_id)
    await audit.record(
        session,
        action="template.delete",
        summary=f"{user.name} xoá template {label}",
        meta={"files": files, **purged},
        actor=user,
        resource_type="template",
        resource_id=template_id,
        resource_label=label,
        request=request,
    )
    await session.delete(item)
    await session.commit()


@router.post(
    "/templates/import",
    status_code=status.HTTP_201_CREATED,
    summary="Nhập template từ PDF",
)
async def import_template(
    user: CurrentUser,
    session: SessionDep,
    request: Request,
    file: Annotated[UploadFile, File(description="PDF — tối đa 50 MB")],
    name: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    filename = file.filename or "template.pdf"
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    if ext not in TEMPLATE_EXTENSIONS:
        raise FileTypeError(list(TEMPLATE_EXTENSIONS), filename)

    data = await file.read()
    await file.close()
    if len(data) > settings.max_upload_bytes:
        raise FileSizeError(settings.max_upload_mb, filename)

    digest = content_hash(data)
    existing = (
        await session.execute(
            select(ProposalTemplate).where(ProposalTemplate.content_hash == digest)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return {**template_detail(existing), "deduplicated": True}

    template_name = name or filename.rsplit(".", 1)[0]
    item = ProposalTemplate(
        id=ProposalTemplate.make_id(),
        name=template_name,
        name_norm=normalize(template_name),
        subtitle=f"Nhập từ {filename}",
        scope=Scope.MINE,
        version="v1.0",
        owner_id=user.id,
        source_filename=filename,
        size_bytes=len(data),
        content_hash=digest,
    )
    session.add(item)
    await session.flush()

    key = storage.template_key(item.id, filename)
    await storage.put(
        settings.bucket_templates,
        key,
        data,
        content_type=TEMPLATE_EXTENSIONS[ext],
    )
    item.object_key = key

    meta = deckmeta.from_pdf(data)
    item.theme = {**meta.as_theme(), "previewKind": "pdf"}

    if meta.thumbnail:
        thumb_key = storage.template_key(item.id, f"cover.{meta.thumbnail_ext}")
        await storage.put(
            settings.bucket_templates,
            thumb_key,
            meta.thumbnail,
            content_type=f"image/{meta.thumbnail_ext}",
        )
        item.cover_url = f"{settings.api_prefix}/templates/{item.id}/thumbnail"
        item.theme = {**item.theme, "coverKey": thumb_key}

    parsed = _parse_pdf(data)
    for entry in parsed:
        session.add(
            TemplateSlide(
                id=TemplateSlide.make_id(),
                template_id=item.id,
                index=entry["index"],
                title=entry["title"],
                layout=entry["layout"],
                placeholders=entry["placeholders"],
            )
        )
    item.slide_count = len(parsed)
    item.required_slides = [e["title"] for e in parsed[:6]]

    session.add(
        TemplateVersion(
            id=TemplateVersion.make_id(),
            template_id=item.id,
            version="v1.0",
            note=f"Nhập từ {filename}",
            author_id=user.id,
            snapshot=_snapshot_of(item, parsed),
        )
    )
    await audit.record(
        session,
        action="template.import",
        summary=f"{user.name} nhập template {template_name} từ {filename} ({len(parsed)} slide)",
        actor=user,
        resource_type="template",
        resource_id=item.id,
        resource_label=template_name,
        request=request,
    )
    await session.commit()
    await session.refresh(item)
    return template_detail(item, await _slides(session, item.id))


TEMPLATE_EXTENSIONS: dict[str, str] = {"pdf": "application/pdf"}


def _parse_pdf(data: bytes) -> list[dict[str, Any]]:
    """PDF không có khái niệm layout/placeholder như PPTX, nên mỗi trang thành một
    slide với tiêu đề lấy từ dòng đầu đọc được — thiếu thì đánh số trang."""
    import io

    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise ValidationError(f"Không đọc được file PDF: {exc}", field="file") from exc

    out: list[dict[str, Any]] = []
    for index, page in enumerate(reader.pages, start=1):
        title = ""
        try:
            text = (page.extract_text() or "").strip()
            for line in text.splitlines():
                line = line.strip()
                if len(line) >= 3:
                    title = line[:120]
                    break
        except Exception:
            title = ""
        out.append(
            {
                "index": index,
                "title": title or f"Trang {index}",
                "layout": "pdf-page",
                "placeholders": [],
            }
        )
    return out


@router.get("/templates/{template_id}/versions", summary="Lịch sử phiên bản")
async def list_versions(template_id: str, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    item, _ = await require(session, user, ResourceType.TEMPLATE, template_id, Permission.VIEW)
    rows = (
        (
            await session.execute(
                select(TemplateVersion)
                .where(TemplateVersion.template_id == template_id)
                .options(selectinload(TemplateVersion.author))
                .order_by(TemplateVersion.created_at.desc())
            )
        )
        .unique()
        .scalars()
        .all()
    )
    return {
        "items": [serialize_version(v) for v in rows],
        "current": item.version,
        "total": len(rows),
    }


async def _find_version(session: SessionDep, template_id: str, version: str) -> TemplateVersion:
    row = (
        await session.execute(
            select(TemplateVersion)
            .where(TemplateVersion.template_id == template_id, TemplateVersion.version == version)
            .options(selectinload(TemplateVersion.author))
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"Phiên bản {version}")
    return row


@router.get(
    "/templates/{template_id}/versions/{version_a}/diff/{version_b}",
    summary="So sánh hai phiên bản",
)
async def diff_versions(
    template_id: str,
    version_a: str,
    version_b: str,
    user: CurrentUser,
    session: SessionDep,
) -> dict[str, Any]:
    await require(session, user, ResourceType.TEMPLATE, template_id, Permission.VIEW)
    a = await _find_version(session, template_id, version_a)
    b = await _find_version(session, template_id, version_b)

    left = {s["index"]: s for s in (a.snapshot.get("slides") or [])}
    right = {s["index"]: s for s in (b.snapshot.get("slides") or [])}

    changes: list[dict[str, Any]] = []
    for index in sorted(set(left) | set(right)):
        lo, ro = left.get(index), right.get(index)
        if lo is None:
            changes.append({"index": index, "change": "added", "title": ro.get("title")})
        elif ro is None:
            changes.append({"index": index, "change": "removed", "title": lo.get("title")})
        elif lo != ro:
            changes.append(
                {
                    "index": index,
                    "change": "modified",
                    "title": ro.get("title"),
                    "before": lo,
                    "after": ro,
                }
            )
        else:
            changes.append({"index": index, "change": "unchanged", "title": ro.get("title")})

    return {"from": version_a, "to": version_b, "slides": changes}


@router.post("/templates/{template_id}/versions/{version}/restore", summary="Khôi phục phiên bản")
async def restore_version(
    template_id: str,
    version: str,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> dict[str, Any]:
    item, _ = await require(session, user, ResourceType.TEMPLATE, template_id, Permission.EDIT)
    target = await _find_version(session, template_id, version)

    existing = await _slides(session, template_id)
    item.version = next_version(item.version)

    meta = target.snapshot.get("template") or {}
    if meta.get("name"):
        item.name = meta["name"]
        item.name_norm = normalize(meta["name"])
    item.subtitle = meta.get("subtitle", item.subtitle)
    item.tone = meta.get("tone", item.tone)
    item.required_slides = meta.get("requiredSlides", item.required_slides)
    item.theme = meta.get("theme", item.theme)

    for s in existing:
        await session.delete(s)
    await session.flush()

    restored = target.snapshot.get("slides") or []
    for index, entry in enumerate(restored, start=1):
        session.add(
            TemplateSlide(
                id=TemplateSlide.make_id(),
                template_id=template_id,
                index=int(entry.get("index") or index),
                title=entry.get("title") or f"Slide {index}",
                layout=entry.get("layout", "content"),
                image_url=entry.get("imageUrl"),
                placeholders=entry.get("placeholders") or [],
                guidance=entry.get("guidance"),
                optional=bool(entry.get("optional", False)),
            )
        )
    item.slide_count = len(restored)

    await session.flush()
    session.add(
        TemplateVersion(
            id=TemplateVersion.make_id(),
            template_id=template_id,
            version=item.version,
            note=f"Khôi phục từ {version}",
            author_id=user.id,
            snapshot=_snapshot_of(
                item,
                [
                    {
                        "index": s.index,
                        "title": s.title,
                        "layout": s.layout,
                        "imageUrl": s.image_url,
                        "placeholders": s.placeholders,
                        "guidance": s.guidance,
                        "optional": s.optional,
                    }
                    for s in await _slides(session, template_id)
                ],
            ),
        )
    )

    await audit.record(
        session,
        action="template.restore",
        summary=f"{user.name} khôi phục template {item.name} về {version}",
        actor=user,
        resource_type="template",
        resource_id=template_id,
        resource_label=item.name,
        meta={"restoredFrom": version, "newVersion": item.version},
        request=request,
    )
    await session.commit()
    await session.refresh(item)
    return {
        **template_detail(item, await _slides(session, template_id)),
        "restoredFrom": version,
    }
