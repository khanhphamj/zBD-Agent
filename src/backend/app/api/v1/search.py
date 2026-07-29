from typing import Annotated, Any

from fastapi import APIRouter, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, SessionDep
from app.core import synonyms
from app.db.models.customer import Customer
from app.db.models.enums import ResourceType
from app.db.models.knowledge import KnowledgeBase
from app.db.models.library import ProposalTemplate, Skill
from app.db.models.product import Product
from app.db.models.proposal import Proposal
from app.services.shares import apply_visibility

router = APIRouter(tags=["search"])


@router.get("/search", summary="Tìm kiếm toàn cục")
async def search(
    user: CurrentUser,
    session: SessionDep,
    q: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=20)] = 6,
) -> dict[str, Any]:
    terms = synonyms.patterns(q) if q else []

    customer_stmt = apply_visibility(
        select(Customer).where(Customer.archived.is_(False)), ResourceType.CUSTOMER, user
    ).options(selectinload(Customer.owner))
    if terms:
        customer_stmt = customer_stmt.where(
            or_(*[or_(Customer.name_norm.ilike(t), Customer.website_norm.ilike(t)) for t in terms])
        )
    customers = (
        (await session.execute(customer_stmt.order_by(Customer.updated_at.desc()).limit(limit)))
        .unique()
        .scalars()
        .all()
    )

    product_stmt = select(Product).where(Product.status == "active")
    if terms:
        product_stmt = product_stmt.where(or_(*[Product.name_norm.ilike(t) for t in terms]))
    products = (
        (await session.execute(product_stmt.order_by(Product.sort_order).limit(limit)))
        .scalars()
        .all()
    )

    if not q:
        customers = customers[:2]
        products = products[:2]
        return {
            "customers": [_customer(c) for c in customers],
            "products": [_product(p) for p in products],
            "proposals": [],
            "library": [],
            "total": len(customers) + len(products),
            "suggested": True,
        }

    proposal_stmt = apply_visibility(
        select(Proposal).where(Proposal.archived.is_(False)), ResourceType.PROPOSAL, user
    ).options(selectinload(Proposal.customer))
    proposal_stmt = proposal_stmt.where(or_(*[Proposal.title_norm.ilike(t) for t in terms]))
    proposals = (
        (await session.execute(proposal_stmt.order_by(Proposal.updated_at.desc()).limit(limit)))
        .unique()
        .scalars()
        .all()
    )

    library: list[dict[str, Any]] = []

    kbs = (
        (
            await session.execute(
                apply_visibility(select(KnowledgeBase), ResourceType.KNOWLEDGE_BASE, user)
                .where(or_(*[KnowledgeBase.name_norm.ilike(t) for t in terms]))
                .order_by(KnowledgeBase.updated_at.desc())
                .limit(limit)
            )
        )
        .unique()
        .scalars()
        .all()
    )
    library += [
        {
            "id": kb.id,
            "kind": "kb",
            "name": kb.name,
            "subtitle": f"{kb.doc_count} tài liệu · {kb.scope}",
            "iconUrl": None,
        }
        for kb in kbs
    ]

    skills = (
        (
            await session.execute(
                apply_visibility(select(Skill), ResourceType.SKILL, user)
                .where(or_(*[Skill.name_norm.ilike(t) for t in terms]))
                .order_by(Skill.updated_at.desc())
                .limit(limit)
            )
        )
        .unique()
        .scalars()
        .all()
    )
    library += [
        {
            "id": s.id,
            "kind": "skill",
            "name": s.name,
            "subtitle": f"{s.stage} · {s.version}",
            "iconUrl": None,
        }
        for s in skills
    ]

    templates = (
        (
            await session.execute(
                apply_visibility(select(ProposalTemplate), ResourceType.TEMPLATE, user)
                .where(or_(*[ProposalTemplate.name_norm.ilike(t) for t in terms]))
                .order_by(ProposalTemplate.used_count.desc())
                .limit(limit)
            )
        )
        .unique()
        .scalars()
        .all()
    )
    library += [
        {
            "id": t.id,
            "kind": "template",
            "name": t.name,
            "subtitle": f"{t.slide_count} slide · {t.version}",
            "iconUrl": t.cover_url,
        }
        for t in templates
    ]
    library = library[:limit]

    return {
        "customers": [_customer(c) for c in customers],
        "products": [_product(p) for p in products],
        "proposals": [
            {
                "id": p.id,
                "name": p.title,
                "subtitle": (p.customer.name if p.customer else "") + f" · {p.status}",
            }
            for p in proposals
        ],
        "library": library,
        "total": len(customers) + len(products) + len(proposals) + len(library),
        "suggested": False,
    }


def _customer(item: Customer) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "subtitle": " · ".join(filter(None, [item.industry, str(item.status)])),
        "logoUrl": item.logo_url,
        "website": item.website,
    }


def _product(item: Product) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "subtitle": " · ".join(filter(None, [item.family, item.group_name])),
        "iconUrl": item.icon_url,
    }
