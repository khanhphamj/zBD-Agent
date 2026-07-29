import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from app.api.deps import CurrentUser, SessionDep
from app.core.errors import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.security import now
from app.core.text import truncate
from app.db.models.customer import CustomerFact
from app.db.models.enums import Permission, ResourceType
from app.db.models.product import Product
from app.db.models.proposal import (
    AiChatMessage,
    AiSuggestedChange,
    Proposal,
    ProposalSlide,
)
from app.db.session import session_scope
from app.integrations.maas import MaasError, Message, maas
from app.schemas.requests import AiApplyBody, AiChatBody
from app.schemas.serializers import slide as serialize_slide
from app.services import audit
from app.services.shares import require

log = get_logger(__name__)
router = APIRouter(tags=["proposal-ai"])

SYSTEM = """\
Bạn là trợ lý biên tập proposal của đội Business Development Zalo.
Nhiệm vụ: giúp người dùng viết và chỉnh sửa nội dung slide bằng tiếng Việt,
văn phong doanh nghiệp, ngắn gọn, có số liệu khi có dữ kiện.

Quy tắc:
- Chỉ dùng dữ kiện được cung cấp trong CONTEXT. Không bịa số liệu, không bịa tên khách hàng.
- Nếu thiếu dữ kiện để trả lời, nói rõ là thiếu và đề nghị bổ sung.
- Khi người dùng yêu cầu sửa slide, kết thúc câu trả lời bằng một khối JSON
  đặt trong ```json ... ``` theo đúng dạng:
  {"changes":[{"kind":"rewrite","slideId":"<id>","label":"<mô tả ngắn>",
               "patch":{"title":"...","blocks":[...]}}]}
  Nếu không có đề xuất sửa nào, bỏ hẳn khối JSON.
"""


def _chat_kwargs() -> dict[str, Any]:
    from app.integrations.harness import resolve

    p = resolve("editor.chat")
    return {
        "model": p.model,
        "max_tokens": p.max_tokens,
        "temperature": p.temperature,
        "top_p": p.top_p,
    }


async def _build_context(
    session: SessionDep, proposal: Proposal, slide_id: str | None, scopes: list[str]
) -> str:
    parts: list[str] = [
        f"KHÁCH HÀNG: {proposal.customer.name if proposal.customer else '—'}",
        f"PROPOSAL: {proposal.title} ({proposal.version}, {proposal.status})",
    ]

    slides = list(
        (
            await session.execute(
                select(ProposalSlide)
                .where(ProposalSlide.proposal_id == proposal.id)
                .order_by(ProposalSlide.index)
            )
        )
        .scalars()
        .all()
    )
    parts.append(
        "DÀN Ý HIỆN TẠI:\n" + "\n".join(f"  {s.index}. [{s.id}] {s.title}" for s in slides)
    )

    if slide_id:
        focus = next((s for s in slides if s.id == slide_id), None)
        if focus:
            parts.append(
                f"SLIDE ĐANG SỬA [{focus.id}] — {focus.title}\n"
                f"{json.dumps(focus.blocks, ensure_ascii=False)[:3000]}"
            )

    if "research" in scopes and proposal.customer_id:
        facts = (
            (
                await session.execute(
                    select(CustomerFact)
                    .where(CustomerFact.customer_id == proposal.customer_id)
                    .order_by(CustomerFact.confidence.desc())
                    .limit(25)
                )
            )
            .scalars()
            .all()
        )
        if facts:
            parts.append(
                "DỮ KIỆN NGHIÊN CỨU:\n"
                + "\n".join(
                    f"  - [{f.topic}] {truncate(f.summary, 220)} "
                    f"(tin cậy {f.confidence}%, {f.verdict})"
                    for f in facts
                )
            )

    if "products" in scopes:
        products = (
            (await session.execute(select(Product).where(Product.status == "active").limit(20)))
            .scalars()
            .all()
        )
        if products:
            parts.append(
                "SẢN PHẨM ZALO:\n"
                + "\n".join(
                    f"  - {p.name} ({p.family}): {truncate(p.description or '', 160)}"
                    for p in products
                )
            )

    return "\n\n".join(parts)


def _extract_changes(text: str) -> list[dict[str, Any]]:
    if "```" not in text:
        return []
    segment = text.rsplit("```json", 1)[-1] if "```json" in text else text.rsplit("```", 2)[-2]
    segment = segment.split("```", 1)[0].strip()
    try:
        parsed = json.loads(segment)
    except json.JSONDecodeError:
        return []
    changes = parsed.get("changes") if isinstance(parsed, dict) else parsed
    return [c for c in (changes or []) if isinstance(c, dict)]


def _visible_answer(text: str) -> str:
    if "```json" in text:
        return text.split("```json", 1)[0].rstrip()
    return text


@router.post("/proposals/{proposal_id}/ai/chat", summary="Chat với AI trong trình soạn thảo")
async def ai_chat(
    proposal_id: str,
    body: AiChatBody,
    user: CurrentUser,
    session: SessionDep,
) -> Any:
    proposal, _ = await require(
        session, user, ResourceType.PROPOSAL, proposal_id, Permission.COMMENT
    )
    await session.refresh(proposal)
    context = await _build_context(session, proposal, body.slideId, body.context)

    session.add(
        AiChatMessage(
            id=AiChatMessage.make_id(),
            proposal_id=proposal_id,
            slide_id=body.slideId,
            user_id=user.id,
            role="user",
            content=body.message,
            context=body.context,
        )
    )
    await session.commit()

    messages = [
        Message("assistant", SYSTEM),
        Message("user", f"CONTEXT:\n{context}\n\nYÊU CẦU:\n{body.message}"),
    ]

    if not body.stream:
        try:
            completion = await maas.complete(
                messages,
                **_chat_kwargs(),
            )
        except MaasError as exc:
            raise ValidationError(f"Không gọi được model: {exc}") from exc
        answer = _visible_answer(completion.text)
        changes = await _persist(
            session,
            proposal_id,
            body.slideId,
            answer,
            completion,
            _extract_changes(completion.text),
        )
        return {"message": answer, "suggestedChanges": changes}

    proposal_title = proposal.title
    slide_id = body.slideId

    async def event_stream() -> AsyncIterator[dict[str, str]]:
        collected: list[str] = []
        try:
            async for delta in maas.run_stream("editor.chat", messages):
                collected.append(delta)

                if "```" not in "".join(collected[-6:]):
                    yield {
                        "event": "token",
                        "data": json.dumps({"delta": delta}, ensure_ascii=False),
                    }
        except MaasError as exc:
            yield {
                "event": "error",
                "data": json.dumps({"code": "AI_FAILED", "message": str(exc)}, ensure_ascii=False),
            }
            return

        full = "".join(collected)
        answer = _visible_answer(full)

        async with session_scope() as own:
            changes = await _persist(
                own, proposal_id, slide_id, answer, None, _extract_changes(full)
            )

        yield {
            "event": "done",
            "data": json.dumps(
                {"message": answer, "suggestedChanges": changes, "proposal": proposal_title},
                ensure_ascii=False,
            ),
        }

    return EventSourceResponse(
        event_stream(),
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


async def _persist(
    session: SessionDep,
    proposal_id: str,
    slide_id: str | None,
    answer: str,
    completion: Any,
    raw_changes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    message = AiChatMessage(
        id=AiChatMessage.make_id(),
        proposal_id=proposal_id,
        slide_id=slide_id,
        role="assistant",
        content=answer,
        tokens_in=getattr(completion, "tokens_in", None),
        tokens_out=getattr(completion, "tokens_out", None),
        model=getattr(completion, "model", None),
    )
    session.add(message)
    await session.flush()

    out: list[dict[str, Any]] = []
    for change in raw_changes:
        row = AiSuggestedChange(
            id=AiSuggestedChange.make_id(),
            proposal_id=proposal_id,
            message_id=message.id,
            slide_id=change.get("slideId") or slide_id,
            kind=change.get("kind", "rewrite"),
            label=change.get("label") or "Áp dụng thay đổi",
            patch=change.get("patch") or {},
        )
        session.add(row)
        out.append(
            {
                "id": row.id,
                "kind": row.kind,
                "label": row.label,
                "slideId": row.slide_id,
                "patch": row.patch,
            }
        )
    await session.commit()
    return out


@router.post("/proposals/{proposal_id}/ai/apply", summary="Áp dụng đề xuất của AI")
async def ai_apply(
    proposal_id: str,
    body: AiApplyBody,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> dict[str, Any]:
    proposal, _ = await require(session, user, ResourceType.PROPOSAL, proposal_id, Permission.EDIT)
    change = await session.get(AiSuggestedChange, body.changeId)
    if change is None or change.proposal_id != proposal_id:
        raise NotFoundError("Đề xuất")
    if change.applied:
        raise ValidationError("Đề xuất này đã được áp dụng.")

    patch = change.patch or {}
    touched: list[ProposalSlide] = []

    if change.kind in ("rewrite", "retitle"):
        if not change.slide_id:
            raise ValidationError("Đề xuất thiếu slideId.")
        target = await session.get(ProposalSlide, change.slide_id)
        if target is None or target.proposal_id != proposal_id:
            raise NotFoundError("Slide")
        if "title" in patch:
            target.title = patch["title"]
        if "blocks" in patch:
            target.blocks = patch["blocks"]
        if "speakerNotes" in patch:
            target.speaker_notes = patch["speakerNotes"]
        touched.append(target)

    elif change.kind == "add_slide":
        from sqlalchemy import func as sqlfunc

        highest = int(
            (
                await session.execute(
                    select(sqlfunc.coalesce(sqlfunc.max(ProposalSlide.index), 0)).where(
                        ProposalSlide.proposal_id == proposal_id
                    )
                )
            ).scalar_one()
            or 0
        )
        created = ProposalSlide(
            id=ProposalSlide.make_id(),
            proposal_id=proposal_id,
            index=int(patch.get("index") or highest + 1),
            title=patch.get("title") or "Slide mới",
            layout=patch.get("layout", "content"),
            blocks=patch.get("blocks") or [],
            speaker_notes=patch.get("speakerNotes"),
        )
        session.add(created)
        proposal.slide_count = highest + 1
        touched.append(created)

    elif change.kind == "delete_slide":
        target = await session.get(ProposalSlide, change.slide_id) if change.slide_id else None
        if target is None or target.proposal_id != proposal_id:
            raise NotFoundError("Slide")
        await session.delete(target)
        proposal.slide_count = max(0, proposal.slide_count - 1)

    else:
        raise ValidationError(f"Loại đề xuất chưa hỗ trợ: {change.kind}")

    change.applied = True
    change.applied_at = now()
    change.applied_by_id = user.id

    await audit.record(
        session,
        action="proposal.ai_apply",
        summary=f"{user.name} áp dụng đề xuất AI “{change.label}”",
        actor=user,
        resource_type="proposal",
        resource_id=proposal_id,
        resource_label=proposal.title,
        meta={"changeId": change.id, "kind": change.kind},
        request=request,
    )
    await session.commit()

    return {
        "applied": True,
        "changeId": change.id,
        "slides": [serialize_slide(s) for s in touched],
    }


@router.get("/proposals/{proposal_id}/ai/history", summary="Lịch sử chat")
async def ai_history(proposal_id: str, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    await require(session, user, ResourceType.PROPOSAL, proposal_id, Permission.VIEW)
    rows = (
        (
            await session.execute(
                select(AiChatMessage)
                .where(AiChatMessage.proposal_id == proposal_id)
                .order_by(AiChatMessage.created_at)
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "slideId": m.slide_id,
                "createdAt": m.created_at,
            }
            for m in rows
        ],
        "total": len(rows),
    }
