from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, SessionDep
from app.db.models.customer import Customer, CustomerSolution
from app.db.models.enums import CustomerStatus, ProposalStatus, ResourceType
from app.db.models.product import Product
from app.db.models.proposal import Proposal
from app.schemas.serializers import customer_row, proposal_row
from app.services.shares import apply_visibility

router = APIRouter(tags=["dashboard"])

TOP_PRODUCTS = 3


async def _top_products(user: CurrentUser, session: SessionDep) -> list[dict[str, Any]]:
    per_product = func.count(func.distinct(CustomerSolution.customer_id))

    def scoped(stmt: Any) -> Any:
        return apply_visibility(
            stmt.join(Customer, Customer.id == CustomerSolution.customer_id).where(
                Customer.archived.is_(False)
            ),
            ResourceType.CUSTOMER,
            user,
        )

    total = int(
        (
            await session.execute(
                scoped(
                    select(func.count(func.distinct(CustomerSolution.customer_id))).select_from(
                        CustomerSolution
                    )
                )
            )
        ).scalar_one()
        or 0
    )
    if not total:
        return []

    rows = (
        await session.execute(
            scoped(
                select(Product.id, Product.name, per_product.label("customers"))
                .select_from(CustomerSolution)
                .join(Product, Product.id == CustomerSolution.product_id)
            )
            .group_by(Product.id, Product.name)
            .order_by(per_product.desc(), Product.name)
            .limit(TOP_PRODUCTS)
        )
    ).all()

    return [
        {
            "id": product_id,
            "name": name,
            "customerCount": int(customers or 0),
            "pct": round(int(customers or 0) * 100 / total),
        }
        for product_id, name, customers in rows
    ]


@router.get("/dashboard", summary="Số liệu + hoạt động gần đây cho trang chủ")
async def dashboard(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    owned = (Customer.owner_id == user.id) & (Customer.archived.is_(False))

    async def count_customers(*where: Any) -> int:
        stmt = select(func.count()).select_from(Customer).where(owned, *where)
        return int((await session.execute(stmt)).scalar_one() or 0)

    async def count_proposals(*where: Any) -> int:
        stmt = (
            select(func.count())
            .select_from(Proposal)
            .where(Proposal.owner_id == user.id, Proposal.archived.is_(False), *where)
        )
        return int((await session.execute(stmt)).scalar_one() or 0)

    stats = {
        "customersOwned": await count_customers(),
        "researching": await count_customers(Customer.status == CustomerStatus.RESEARCHING),
        "researched": await count_customers(Customer.status == CustomerStatus.RESEARCHED),
        "proposalsDraft": await count_proposals(Proposal.status == ProposalStatus.DRAFT),
        "customersInTalks": await count_customers(Customer.status == CustomerStatus.IN_TALKS),
        "proposalsDone": await count_proposals(Proposal.status == ProposalStatus.DONE),
    }

    recent_customers = (
        (
            await session.execute(
                apply_visibility(
                    select(Customer).where(Customer.archived.is_(False)),
                    ResourceType.CUSTOMER,
                    user,
                )
                .options(selectinload(Customer.owner))
                .order_by(Customer.updated_at.desc())
                .limit(3)
            )
        )
        .unique()
        .scalars()
        .all()
    )

    recent_proposals = (
        (
            await session.execute(
                apply_visibility(
                    select(Proposal).where(Proposal.archived.is_(False)),
                    ResourceType.PROPOSAL,
                    user,
                )
                .options(selectinload(Proposal.owner), selectinload(Proposal.customer))
                .order_by(Proposal.updated_at.desc())
                .limit(3)
            )
        )
        .unique()
        .scalars()
        .all()
    )

    return {
        "stats": stats,
        "recentCustomers": [customer_row(c) for c in recent_customers],
        "recentProposals": [proposal_row(p) for p in recent_proposals],
        "topProducts": await _top_products(user, session),
    }
