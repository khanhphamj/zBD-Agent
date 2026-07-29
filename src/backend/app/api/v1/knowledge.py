from typing import Annotated, Any

from fastapi import APIRouter, File, Query, Request, UploadFile, status
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.core.config import settings
from app.core.errors import (
    FileSizeError,
    FileTypeError,
    NotFoundError,
    StorageError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.security import content_hash
from app.core.text import normalize, search_term
from app.db.models.enums import (
    FileStatus,
    JobKind,
    JobStatus,
    Permission,
    ResourceType,
    Scope,
)
from app.db.models.job import Job
from app.db.models.knowledge import KnowledgeBase, KnowledgeFile, KnowledgeSource
from app.schemas.requests import (
    KnowledgeBasePatch,
    KnowledgeBaseUpsert,
    SourceCreate,
)
from app.schemas.serializers import (
    knowledge_base as serialize_kb,
)
from app.schemas.serializers import (
    knowledge_file as serialize_file,
)
from app.schemas.serializers import (
    knowledge_source as serialize_source,
)
from app.services import audit
from app.services.queue import enqueue
from app.services.shares import apply_visibility, require, resolve
from app.services.storage import storage

log = get_logger(__name__)
router = APIRouter(tags=["knowledge"])


def _check_upload(filename: str, size: int) -> str:
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    if ext not in settings.allowed_upload_extensions:
        raise FileTypeError(settings.allowed_upload_extensions, filename)
    if size > settings.max_upload_bytes:
        raise FileSizeError(settings.max_upload_mb, filename)
    return ext


@router.get("/knowledge-bases", summary="Danh sách kho tri thức")
async def list_knowledge_bases(
    user: CurrentUser,
    session: SessionDep,
    q: Annotated[str | None, Query()] = None,
    scope: Annotated[str | None, Query(description="mine | shared")] = None,
) -> dict[str, Any]:
    stmt = apply_visibility(select(KnowledgeBase), ResourceType.KNOWLEDGE_BASE, user)
    if scope == "mine":
        stmt = stmt.where(KnowledgeBase.owner_id == user.id)
    elif scope == "shared":
        stmt = stmt.where(KnowledgeBase.scope == Scope.SHARED)
    if q:
        stmt = stmt.where(KnowledgeBase.name_norm.ilike(search_term(q)))

    rows = (
        (await session.execute(stmt.order_by(KnowledgeBase.updated_at.desc())))
        .unique()
        .scalars()
        .all()
    )

    items: list[dict[str, Any]] = []
    for row in rows:
        access = await resolve(session, user, ResourceType.KNOWLEDGE_BASE, row.id, row)
        sources = list(
            (await session.execute(select(KnowledgeSource).where(KnowledgeSource.kb_id == row.id)))
            .scalars()
            .all()
        )
        items.append(serialize_kb(row, locked=not access.allowed, sources=sources))

    return {"items": items, "total": len(items)}


@router.post("/knowledge-bases", status_code=status.HTTP_201_CREATED, summary="Tạo kho tri thức")
async def create_knowledge_base(
    body: KnowledgeBaseUpsert, user: CurrentUser, session: SessionDep, request: Request
) -> dict[str, Any]:
    item = KnowledgeBase(
        id=KnowledgeBase.make_id(),
        name=body.name,
        name_norm=normalize(body.name),
        description=body.description,
        scope=body.scope,
        owner_id=user.id,
        tags=body.tags,
        restricted=body.restricted,
        icon=body.icon,
    )
    session.add(item)
    await audit.record(
        session,
        action="kb.create",
        summary=f"{user.name} tạo kho tri thức {item.name}",
        actor=user,
        resource_type="knowledge_base",
        resource_id=item.id,
        resource_label=item.name,
        request=request,
    )
    await session.commit()
    await session.refresh(item)
    return serialize_kb(item, sources=[])


@router.get("/knowledge-bases/{kb_id}", summary="Chi tiết kho tri thức")
async def get_knowledge_base(kb_id: str, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    item, access = await require(session, user, ResourceType.KNOWLEDGE_BASE, kb_id, Permission.VIEW)
    files = list(
        (
            await session.execute(
                select(KnowledgeFile)
                .where(KnowledgeFile.kb_id == kb_id)
                .order_by(KnowledgeFile.created_at.desc())
            )
        )
        .unique()
        .scalars()
        .all()
    )
    sources = list(
        (await session.execute(select(KnowledgeSource).where(KnowledgeSource.kb_id == kb_id)))
        .scalars()
        .all()
    )
    return {
        **serialize_kb(item, sources=sources),
        "files": [serialize_file(f) for f in files],
        "sourceDetails": [serialize_source(s) for s in sources],
        "myPermission": str(access.permission),
    }


@router.patch("/knowledge-bases/{kb_id}", summary="Cập nhật kho tri thức")
async def update_knowledge_base(
    kb_id: str,
    body: KnowledgeBasePatch,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> dict[str, Any]:
    item, _ = await require(session, user, ResourceType.KNOWLEDGE_BASE, kb_id, Permission.EDIT)
    if body.name is not None:
        item.name = body.name
        item.name_norm = normalize(body.name)
    for field, value in (
        ("description", body.description),
        ("scope", body.scope),
        ("tags", body.tags),
        ("restricted", body.restricted),
        ("icon", body.icon),
    ):
        if value is not None:
            setattr(item, field, value)

    await audit.record(
        session,
        action="kb.update",
        summary=f"{user.name} cập nhật kho tri thức {item.name}",
        actor=user,
        resource_type="knowledge_base",
        resource_id=kb_id,
        resource_label=item.name,
        request=request,
    )
    await session.commit()
    await session.refresh(item)
    sources = list(
        (await session.execute(select(KnowledgeSource).where(KnowledgeSource.kb_id == kb_id)))
        .scalars()
        .all()
    )
    return serialize_kb(item, sources=sources)


@router.delete(
    "/knowledge-bases/{kb_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Xoá kho tri thức",
)
async def delete_knowledge_base(
    kb_id: str, user: CurrentUser, session: SessionDep, request: Request
) -> None:
    item, _ = await require(session, user, ResourceType.KNOWLEDGE_BASE, kb_id, Permission.EDIT)
    label = item.name

    files = (
        (await session.execute(select(KnowledgeFile).where(KnowledgeFile.kb_id == kb_id)))
        .scalars()
        .all()
    )
    for f in files:
        await storage.delete(settings.bucket_knowledge, f.object_key)

    await audit.record(
        session,
        action="kb.delete",
        summary=f"{user.name} xoá kho tri thức {label}",
        actor=user,
        resource_type="knowledge_base",
        resource_id=kb_id,
        resource_label=label,
        request=request,
    )
    await session.delete(item)
    await session.commit()


@router.get("/knowledge-bases/{kb_id}/files", summary="Tài liệu trong kho")
async def list_files(kb_id: str, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    await require(session, user, ResourceType.KNOWLEDGE_BASE, kb_id, Permission.VIEW)
    rows = (
        (
            await session.execute(
                select(KnowledgeFile)
                .where(KnowledgeFile.kb_id == kb_id)
                .order_by(KnowledgeFile.created_at.desc())
            )
        )
        .unique()
        .scalars()
        .all()
    )
    return {"items": [serialize_file(f) for f in rows], "total": len(rows)}


@router.post("/knowledge-bases/{kb_id}/files", summary="Tải tài liệu lên — kết quả theo từng file")
async def upload_files(
    kb_id: str,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
    files: Annotated[list[UploadFile], File(description="PDF, DOCX, XLSX, PPTX — tối đa 50 MB")],
) -> dict[str, Any]:
    kb, _ = await require(session, user, ResourceType.KNOWLEDGE_BASE, kb_id, Permission.EDIT)

    results: list[dict[str, Any]] = []
    ok_count = 0
    failed_count = 0
    stored_ids: list[str] = []

    for upload in files:
        name = upload.filename or "unnamed"
        try:
            data = await upload.read()
            ext = _check_upload(name, len(data))

            digest = content_hash(data)
            existing = (
                await session.execute(
                    select(KnowledgeFile).where(
                        KnowledgeFile.kb_id == kb_id, KnowledgeFile.content_hash == digest
                    )
                )
            ).scalar_one_or_none()
            if existing:
                results.append(
                    {
                        "name": name,
                        "status": "ok",
                        "fileId": existing.id,
                        "deduplicated": True,
                    }
                )
                ok_count += 1
                continue

            file_id = KnowledgeFile.make_id()
            key = storage.knowledge_key(kb_id, file_id, name)
            await storage.put(
                settings.bucket_knowledge,
                key,
                data,
                content_type=upload.content_type or "application/octet-stream",
                metadata={"kb": kb_id, "uploader": user.id},
            )

            row = KnowledgeFile(
                id=file_id,
                kb_id=kb_id,
                name=name,
                ext=ext,
                mime_type=upload.content_type,
                size_bytes=len(data),
                content_hash=digest,
                object_key=key,
                status=FileStatus.UPLOADED,
                uploaded_by_id=user.id,
            )
            session.add(row)
            await session.flush()

            stored_ids.append(file_id)
            results.append({"name": name, "status": "ok", "fileId": file_id})
            ok_count += 1

        except (FileTypeError, FileSizeError) as exc:
            results.append(
                {"name": name, "status": "failed", "code": exc.code, "message": exc.message}
            )
            failed_count += 1
        except StorageError as exc:
            results.append(
                {"name": name, "status": "failed", "code": exc.code, "message": exc.message}
            )
            failed_count += 1
        except Exception as exc:
            log.warning("upload_failed", kb=kb_id, file=name, error=str(exc))
            results.append({"name": name, "status": "failed", "code": "STORAGE_500"})
            failed_count += 1
        finally:
            await upload.close()

    kb.doc_count = int(
        (
            await session.execute(
                select(__import__("sqlalchemy").func.count())
                .select_from(KnowledgeFile)
                .where(KnowledgeFile.kb_id == kb_id)
            )
        ).scalar_one()
        or 0
    )

    await audit.record(
        session,
        action="knowledge.upload",
        summary=f"{user.name} tải {ok_count} tài liệu lên {kb.name}",
        actor=user,
        resource_type="knowledge_base",
        resource_id=kb_id,
        resource_label=kb.name,
        meta={"ok": ok_count, "failed": failed_count},
        request=request,
    )
    await session.commit()

    for file_id in stored_ids:
        await enqueue("index_document", file_id)

    return {"results": results, "okCount": ok_count, "failedCount": failed_count}


@router.get("/knowledge-bases/{kb_id}/files/{file_id}", summary="Chi tiết tài liệu")
async def get_file(
    kb_id: str, file_id: str, user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    await require(session, user, ResourceType.KNOWLEDGE_BASE, kb_id, Permission.VIEW)
    row = await session.get(KnowledgeFile, file_id)
    if row is None or row.kb_id != kb_id:
        raise NotFoundError("Tài liệu")
    url = await storage.presigned_url(
        settings.bucket_knowledge, row.object_key, download_name=row.name
    )
    return {**serialize_file(row), "downloadUrl": url}


@router.delete(
    "/knowledge-bases/{kb_id}/files/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Xoá tài liệu",
)
async def delete_file(
    kb_id: str, file_id: str, user: CurrentUser, session: SessionDep, request: Request
) -> None:
    kb, _ = await require(session, user, ResourceType.KNOWLEDGE_BASE, kb_id, Permission.EDIT)
    row = await session.get(KnowledgeFile, file_id)
    if row is None or row.kb_id != kb_id:
        raise NotFoundError("Tài liệu")

    await storage.delete(settings.bucket_knowledge, row.object_key)
    await session.delete(row)
    kb.doc_count = max(0, kb.doc_count - 1)
    await audit.record(
        session,
        action="knowledge.delete_file",
        summary=f"{user.name} xoá tài liệu {row.name} khỏi {kb.name}",
        actor=user,
        resource_type="knowledge_base",
        resource_id=kb_id,
        resource_label=kb.name,
        request=request,
    )
    await session.commit()


@router.post(
    "/knowledge-bases/{kb_id}/sources",
    status_code=status.HTTP_201_CREATED,
    summary="Thêm nguồn đồng bộ",
)
async def add_source(
    kb_id: str, body: SourceCreate, user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    await require(session, user, ResourceType.KNOWLEDGE_BASE, kb_id, Permission.EDIT)

    duplicate = (
        await session.execute(
            select(KnowledgeSource).where(
                KnowledgeSource.kb_id == kb_id, KnowledgeSource.url == body.url
            )
        )
    ).scalar_one_or_none()
    if duplicate:
        raise ValidationError("Nguồn này đã có trong kho.", field="url")

    row = KnowledgeSource(id=KnowledgeSource.make_id(), kb_id=kb_id, url=body.url, label=body.label)
    session.add(row)
    await session.commit()
    return serialize_source(row)


@router.post("/knowledge-bases/{kb_id}/sync", summary="Đồng bộ nguồn")
async def sync_sources(
    kb_id: str, user: CurrentUser, session: SessionDep, request: Request
) -> dict[str, str]:
    kb, _ = await require(session, user, ResourceType.KNOWLEDGE_BASE, kb_id, Permission.EDIT)
    job = Job(
        id=Job.make_id(),
        kind=JobKind.KB_SYNC,
        status=JobStatus.QUEUED,
        kb_id=kb_id,
        created_by_id=user.id,
    )
    session.add(job)
    await audit.record(
        session,
        action="kb.sync",
        summary=f"{user.name} đồng bộ nguồn của {kb.name}",
        actor=user,
        resource_type="knowledge_base",
        resource_id=kb_id,
        resource_label=kb.name,
        request=request,
    )
    await session.commit()
    await enqueue("run_kb_sync", job.id)
    return {"jobId": job.id}


@router.delete(
    "/knowledge-bases/{kb_id}/sources/{source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Xoá nguồn đồng bộ",
)
async def delete_source(kb_id: str, source_id: str, user: CurrentUser, session: SessionDep) -> None:
    await require(session, user, ResourceType.KNOWLEDGE_BASE, kb_id, Permission.EDIT)
    row = await session.get(KnowledgeSource, source_id)
    if row is None or row.kb_id != kb_id:
        raise NotFoundError("Nguồn")
    await session.delete(row)
    await session.commit()
