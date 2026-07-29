import time
from typing import Annotated, Any

from fastapi import APIRouter, File, Query, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, SessionDep
from app.core.config import settings
from app.core.errors import AppError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.text import next_version, normalize, search_term, slugify
from app.db.models.customer import CustomerFact
from app.db.models.enums import Permission, ResourceType, Scope
from app.db.models.library import Skill, SkillTestRun, SkillVersion
from app.integrations.maas import MaasError, maas
from app.schemas.requests import SkillPatch, SkillTest, SkillUpsert
from app.schemas.serializers import (
    skill_detail,
    skill_row,
)
from app.schemas.serializers import (
    skill_version as serialize_version,
)
from app.services import audit, skillfile
from app.services.shares import apply_visibility, purge_links, require, resolve
from app.services.storage import storage

log = get_logger(__name__)

router = APIRouter(tags=["skills"])


@router.get("/skills", summary="Danh sách skill")
async def list_skills(
    user: CurrentUser,
    session: SessionDep,
    q: Annotated[str | None, Query()] = None,
    scope: Annotated[str | None, Query(description="mine | shared")] = None,
    stage: Annotated[str | None, Query()] = None,
    detail: Annotated[bool, Query(description="Trả đủ prompt/model/inputSchema")] = False,
) -> dict[str, Any]:
    stmt = apply_visibility(select(Skill), ResourceType.SKILL, user).options(
        selectinload(Skill.owner)
    )
    if scope == "mine":
        stmt = stmt.where(Skill.owner_id == user.id)
    elif scope == "shared":
        stmt = stmt.where(Skill.scope == Scope.SHARED)
    if stage:
        stmt = stmt.where(Skill.stage == stage)
    if q:
        stmt = stmt.where(Skill.name_norm.ilike(search_term(q)))

    rows = (
        (await session.execute(stmt.order_by(Skill.stage, Skill.name_norm)))
        .unique()
        .scalars()
        .all()
    )
    items: list[dict[str, Any]] = []
    for row in rows:
        access = await resolve(session, user, ResourceType.SKILL, row.id, row)
        shaped = skill_row(row, locked=not access.allowed)
        if detail:
            shaped = {**skill_detail(row), "locked": not access.allowed}
        items.append(shaped)
    return {
        "items": items,
        "total": len(items),
        "stages": sorted({str(r.stage) for r in rows}),
    }


@router.post("/skills", status_code=status.HTTP_201_CREATED, summary="Tạo skill")
async def create_skill(
    body: SkillUpsert, user: CurrentUser, session: SessionDep, request: Request
) -> dict[str, Any]:
    item = Skill(
        id=Skill.make_id(),
        name=body.name,
        name_norm=normalize(body.name),
        description=body.description,
        stage=body.stage,
        scope=body.scope,
        version="v1.0",
        prompt=body.prompt,
        model=body.model,
        temperature=body.temperature,
        input_schema=body.inputSchema,
        tools=body.tools,
        owner_id=user.id,
    )
    session.add(item)
    await session.flush()

    session.add(
        SkillVersion(
            id=SkillVersion.make_id(),
            skill_id=item.id,
            version="v1.0",
            prompt=body.prompt,
            note="Phiên bản đầu tiên",
            author_id=user.id,
        )
    )
    await audit.record(
        session,
        action="skill.create",
        summary=f"{user.name} tạo skill {item.name}",
        actor=user,
        resource_type="skill",
        resource_id=item.id,
        resource_label=item.name,
        request=request,
    )
    await session.commit()
    await session.refresh(item)
    return skill_detail(item)


async def _unique_name(session: SessionDep, name: str) -> str:
    taken = set(
        (await session.execute(select(Skill.name).where(Skill.name.ilike(f"{name}%"))))
        .scalars()
        .all()
    )
    if name not in taken:
        return name
    for n in range(2, 100):
        candidate = f"{name} ({n})"
        if candidate not in taken:
            return candidate
    return f"{name} ({Skill.make_id()[-6:]})"


@router.post(
    "/skills/import",
    status_code=status.HTTP_201_CREATED,
    summary="Nhập skill từ file .skill — kết quả theo từng file",
)
async def import_skills(
    user: CurrentUser,
    session: SessionDep,
    request: Request,
    files: Annotated[
        list[UploadFile], File(description=".skill — bundle ZIP chứa SKILL.md + references/")
    ],
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    created: list[dict[str, Any]] = []

    for upload in files:
        label = upload.filename or "unnamed.skill"
        try:
            skillfile.check_extension(label)
            doc = skillfile.parse(await upload.read(), label)
        except AppError as exc:
            results.append(
                {"name": label, "status": "failed", "code": exc.code, "message": exc.message}
            )
            continue

        name = await _unique_name(session, doc.name)
        skill_id = Skill.make_id()

        stored: list[dict[str, Any]] = []
        try:
            for entry in doc.files:
                key = storage.skill_key(skill_id, entry.path)
                await storage.put(
                    settings.bucket_skills,
                    key,
                    entry.data,
                    content_type="text/markdown"
                    if entry.path.endswith(".md")
                    else "application/octet-stream",
                    metadata={"skill": skill_id, "uploader": user.id},
                )
                stored.append({"path": entry.path, "objectKey": key, "bytes": entry.size})
        except Exception as exc:
            results.append(
                {
                    "name": label,
                    "status": "failed",
                    "code": "STORAGE_503",
                    "message": f"Không lưu được file kèm theo: {exc}",
                }
            )
            continue

        item = Skill(
            id=skill_id,
            name=name,
            name_norm=normalize(name),
            description=doc.description,
            stage=doc.stage,
            scope=doc.scope,
            version="v1.0",
            prompt=doc.prompt,
            model=doc.model,
            temperature=doc.temperature,
            input_schema=doc.input_schema,
            tools=doc.tools,
            bundle={
                "format": doc.source,
                "root": doc.root,
                "sourceFile": label,
                "files": stored,
                "meta": doc.meta,
            },
            owner_id=user.id,
        )
        session.add(item)
        await session.flush()
        session.add(
            SkillVersion(
                id=SkillVersion.make_id(),
                skill_id=item.id,
                version="v1.0",
                prompt=doc.prompt,
                note=f"Nhập từ {label}",
                author_id=user.id,
            )
        )
        await audit.record(
            session,
            action="skill.import",
            summary=f"{user.name} nhập skill {name} từ {label}",
            actor=user,
            resource_type="skill",
            resource_id=item.id,
            resource_label=name,
            meta={
                "file": label,
                "format": doc.source,
                "renamed": name != doc.name,
                "attachments": len(stored),
            },
            request=request,
        )
        results.append(
            {
                "name": label,
                "status": "ok",
                "skillId": item.id,
                "skillName": name,
                "renamed": name != doc.name,
                "format": doc.source,
                "attachments": [f["path"] for f in stored],
            }
        )
        created.append({"id": item.id, "name": name})

    if not created:
        await session.rollback()
        message = results[0]["message"] if results else "Không có file nào được nhập."
        raise ValidationError(str(message), "file")

    await session.commit()
    return {
        "items": results,
        "created": len(created),
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "skills": created,
    }


@router.get("/skills/{skill_id}/export", summary="Tải skill về dạng bundle .skill")
async def export_skill(skill_id: str, user: CurrentUser, session: SessionDep) -> Response:
    item, _ = await require(session, user, ResourceType.SKILL, skill_id, Permission.VIEW)

    files: list[skillfile.BundleFile] = []
    for entry in (item.bundle or {}).get("files", []):
        key = entry.get("objectKey")
        path = entry.get("path")
        if not key or not path:
            continue
        try:
            files.append(
                skillfile.BundleFile(path=path, data=await storage.get(settings.bucket_skills, key))
            )
        except Exception as exc:
            log.warning("skill_export_file_missing", skill=skill_id, key=key, error=str(exc))

    filename = f"{slugify(item.name) or 'skill'}.skill"
    return Response(
        content=skillfile.build_bundle(item, files),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/skills/{skill_id}", summary="Chi tiết skill")
async def get_skill(skill_id: str, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    item, access = await require(session, user, ResourceType.SKILL, skill_id, Permission.VIEW)
    return {**skill_detail(item), "myPermission": str(access.permission)}


@router.patch("/skills/{skill_id}", summary="Lưu prompt — mỗi lần lưu tăng version")
async def update_skill(
    skill_id: str,
    body: SkillPatch,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> dict[str, Any]:
    item, _ = await require(session, user, ResourceType.SKILL, skill_id, Permission.EDIT)

    prompt_changed = body.prompt is not None and body.prompt != item.prompt
    if prompt_changed:
        item.version = next_version(item.version)
        item.prompt = body.prompt or ""
        session.add(
            SkillVersion(
                id=SkillVersion.make_id(),
                skill_id=item.id,
                version=item.version,
                prompt=item.prompt,
                note=body.note,
                author_id=user.id,
            )
        )

    if body.name is not None:
        item.name = body.name
        item.name_norm = normalize(body.name)
    for field, value in (
        ("description", body.description),
        ("stage", body.stage),
        ("scope", body.scope),
        ("model", body.model),
        ("temperature", body.temperature),
        ("input_schema", body.inputSchema),
        ("tools", body.tools),
    ):
        if value is not None:
            setattr(item, field, value)

    await audit.record(
        session,
        action="skill.update",
        summary=f"{user.name} lưu skill {item.name} ({item.version})",
        actor=user,
        resource_type="skill",
        resource_id=skill_id,
        resource_label=item.name,
        meta={"version": item.version, "promptChanged": prompt_changed},
        request=request,
    )
    await session.commit()
    await session.refresh(item)
    return skill_detail(item)


@router.delete("/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Xoá skill")
async def delete_skill(
    skill_id: str, user: CurrentUser, session: SessionDep, request: Request
) -> None:
    item, _ = await require(session, user, ResourceType.SKILL, skill_id, Permission.EDIT)
    if item.is_builtin:
        raise ValidationError("Skill mặc định của hệ thống không xoá được.")
    label = item.name

    removed = 0
    for entry in (item.bundle or {}).get("files", []):
        key = entry.get("objectKey")
        if not key:
            continue
        try:
            await storage.delete(settings.bucket_skills, key)
            removed += 1
        except Exception as exc:
            log.warning("skill_file_cleanup_failed", skill=skill_id, key=key, error=str(exc))

    purged = await purge_links(session, ResourceType.SKILL, skill_id)
    await audit.record(
        session,
        action="skill.delete",
        summary=f"{user.name} xoá skill {label}",
        actor=user,
        resource_type="skill",
        resource_id=skill_id,
        resource_label=label,
        meta={"attachmentsRemoved": removed, "purged": purged},
        request=request,
    )
    await session.delete(item)
    await session.commit()


@router.post("/skills/{skill_id}/test", summary="Chạy thử skill")
async def test_skill(
    skill_id: str, body: SkillTest, user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    item, _ = await require(session, user, ResourceType.SKILL, skill_id, Permission.VIEW)

    context = ""
    customer_id = body.input.get("customerId")
    if customer_id:
        from app.db.models.customer import Customer

        customer = await session.get(Customer, customer_id)
        if customer is None:
            raise NotFoundError("Khách hàng")
        facts = (
            (
                await session.execute(
                    select(CustomerFact)
                    .where(CustomerFact.customer_id == customer_id)
                    .order_by(CustomerFact.confidence.desc())
                    .limit(20)
                )
            )
            .scalars()
            .all()
        )
        context = (
            f"KHÁCH HÀNG: {customer.name} ({customer.industry or 'chưa rõ ngành'})\n"
            f"WEBSITE: {customer.website or '—'}\n"
            "DỮ KIỆN:\n" + "\n".join(f"  - [{f.topic}] {f.summary}" for f in facts)
        )

    extra = {k: v for k, v in body.input.items() if k != "customerId"}
    if extra:
        context += f"\n\nTHAM SỐ:\n{extra}"

    started = time.monotonic()
    run = SkillTestRun(
        id=SkillTestRun.make_id(),
        skill_id=skill_id,
        version=item.version,
        input=body.input,
        run_by_id=user.id,
    )

    try:
        completion = await maas.run(
            "skill.test",
            item.prompt or "Bạn là trợ lý phân tích doanh nghiệp.",
            context or "Chạy thử không có dữ liệu đầu vào.",
            model=item.model,
            temperature=item.temperature,
        )
        run.output = completion.text
        run.status = "ok"
        run.tokens_in = completion.tokens_in
        run.tokens_out = completion.tokens_out
        run.model = completion.model
    except MaasError as exc:
        run.status = "failed"
        run.error = str(exc)

    run.latency_ms = int((time.monotonic() - started) * 1000)
    session.add(run)
    await session.commit()

    return {
        "id": run.id,
        "status": run.status,
        "output": run.output,
        "error": run.error,
        "latencyMs": run.latency_ms,
        "tokensIn": run.tokens_in,
        "tokensOut": run.tokens_out,
        "model": run.model,
        "version": run.version,
    }


@router.get("/skills/{skill_id}/versions", summary="Lịch sử phiên bản")
async def list_versions(skill_id: str, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    item, _ = await require(session, user, ResourceType.SKILL, skill_id, Permission.VIEW)
    rows = (
        (
            await session.execute(
                select(SkillVersion)
                .where(SkillVersion.skill_id == skill_id)
                .options(selectinload(SkillVersion.author))
                .order_by(SkillVersion.created_at.desc())
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


async def _find_version(session: SessionDep, skill_id: str, version: str) -> SkillVersion:
    row = (
        await session.execute(
            select(SkillVersion)
            .where(SkillVersion.skill_id == skill_id, SkillVersion.version == version)
            .options(selectinload(SkillVersion.author))
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"Phiên bản {version}")
    return row


@router.get(
    "/skills/{skill_id}/versions/{version_a}/diff/{version_b}", summary="So sánh hai phiên bản"
)
async def diff_versions(
    skill_id: str,
    version_a: str,
    version_b: str,
    user: CurrentUser,
    session: SessionDep,
) -> dict[str, Any]:
    await require(session, user, ResourceType.SKILL, skill_id, Permission.VIEW)
    a = await _find_version(session, skill_id, version_a)
    b = await _find_version(session, skill_id, version_b)

    import difflib

    left = (a.prompt or "").splitlines()
    right = (b.prompt or "").splitlines()
    diff = list(difflib.unified_diff(left, right, lineterm="", n=2))

    return {
        "from": version_a,
        "to": version_b,
        "promptBefore": a.prompt,
        "promptAfter": b.prompt,
        "diff": diff,
        "summary": {
            "added": sum(1 for line in diff if line.startswith("+") and not line.startswith("+++")),
            "removed": sum(
                1 for line in diff if line.startswith("-") and not line.startswith("---")
            ),
        },
    }


@router.post("/skills/{skill_id}/versions/{version}/restore", summary="Khôi phục phiên bản")
async def restore_version(
    skill_id: str,
    version: str,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> dict[str, Any]:
    item, _ = await require(session, user, ResourceType.SKILL, skill_id, Permission.EDIT)
    target = await _find_version(session, skill_id, version)

    item.version = next_version(item.version)
    item.prompt = target.prompt
    session.add(
        SkillVersion(
            id=SkillVersion.make_id(),
            skill_id=skill_id,
            version=item.version,
            prompt=item.prompt,
            note=f"Khôi phục từ {version}",
            author_id=user.id,
        )
    )

    await audit.record(
        session,
        action="skill.restore",
        summary=f"{user.name} khôi phục skill {item.name} về {version}",
        actor=user,
        resource_type="skill",
        resource_id=skill_id,
        resource_label=item.name,
        meta={"restoredFrom": version, "newVersion": item.version},
        request=request,
    )
    await session.commit()
    await session.refresh(item)
    return {**skill_detail(item), "restoredFrom": version}
