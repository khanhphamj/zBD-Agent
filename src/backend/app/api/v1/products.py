from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, status
from sqlalchemy import or_, select

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.core.errors import NotFoundError
from app.core.text import normalize, search_term
from app.db.models.enums import JobKind, JobStatus
from app.db.models.job import Job
from app.db.models.product import Product
from app.schemas.requests import ProductPatch, ProductUpsert
from app.schemas.serializers import product_detail, product_row
from app.services import audit
from app.services.queue import enqueue

router = APIRouter(tags=["products"])


@router.get("/products", summary="Danh mục sản phẩm")
async def list_products(
    user: CurrentUser,
    session: SessionDep,
    q: Annotated[str | None, Query()] = None,
    group: Annotated[str | None, Query()] = None,
    family: Annotated[str | None, Query()] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    detail: Annotated[bool, Query(description="Trả đủ pricing/capabilities/documents")] = False,
) -> dict[str, Any]:
    stmt = select(Product)
    if group:
        stmt = stmt.where(Product.group_name == group)
    if family:
        stmt = stmt.where(Product.family == family)
    if status_filter:
        stmt = stmt.where(Product.status == status_filter)
    if q:
        term = search_term(q)
        stmt = stmt.where(or_(Product.name_norm.ilike(term), Product.description.ilike(f"%{q}%")))

    rows = (
        (await session.execute(stmt.order_by(Product.sort_order, Product.name_norm)))
        .scalars()
        .all()
    )

    shape = product_detail if detail else product_row

    families: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        families.setdefault(row.family or "Khác", []).append(shape(row))

    return {
        "items": [shape(r) for r in rows],
        "total": len(rows),
        "families": [{"name": name, "products": items} for name, items in families.items()],
        "groups": sorted({r.group_name for r in rows if r.group_name}),
    }


@router.get("/products/{product_id}", summary="Profile sản phẩm đầy đủ")
async def get_product(product_id: str, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    item = await session.get(Product, product_id)
    if item is None:
        raise NotFoundError("Sản phẩm")
    return product_detail(item)


@router.post(
    "/products",
    status_code=status.HTTP_201_CREATED,
    summary="Thêm sản phẩm (Admin)",
)
async def create_product(
    body: ProductUpsert, admin: AdminUser, session: SessionDep, request: Request
) -> dict[str, Any]:
    item = Product(
        id=Product.make_id(),
        name=body.name,
        name_norm=normalize(body.name),
        family=body.family,
        group_name=body.group,
        description=body.description,
        packages=body.packages,
        status=body.status,
        icon_url=body.iconUrl,
        positioning=body.positioning,
        use_cases=body.useCases,
        pricing=body.pricing,
        documents=body.documents,
        capabilities=body.capabilities,
        target_industries=body.targetIndustries,
        source_url=body.sourceUrl,
    )
    session.add(item)
    await audit.record(
        session,
        action="product.create",
        summary=f"{admin.name} thêm sản phẩm {item.name}",
        actor=admin,
        resource_type="product",
        resource_id=item.id,
        resource_label=item.name,
        request=request,
    )
    await session.commit()
    await session.refresh(item)
    return product_detail(item)


@router.patch("/products/{product_id}", summary="Cập nhật sản phẩm (Admin)")
async def update_product(
    product_id: str,
    body: ProductPatch,
    admin: AdminUser,
    session: SessionDep,
    request: Request,
) -> dict[str, Any]:
    item = await session.get(Product, product_id)
    if item is None:
        raise NotFoundError("Sản phẩm")

    if body.name is not None:
        item.name = body.name
        item.name_norm = normalize(body.name)
    for field, value in (
        ("family", body.family),
        ("group_name", body.group),
        ("description", body.description),
        ("packages", body.packages),
        ("status", body.status),
        ("icon_url", body.iconUrl),
        ("positioning", body.positioning),
        ("use_cases", body.useCases),
        ("pricing", body.pricing),
        ("documents", body.documents),
        ("capabilities", body.capabilities),
        ("target_industries", body.targetIndustries),
        ("source_url", body.sourceUrl),
    ):
        if value is not None:
            setattr(item, field, value)

    await audit.record(
        session,
        action="product.update",
        summary=f"{admin.name} cập nhật sản phẩm {item.name}",
        actor=admin,
        resource_type="product",
        resource_id=item.id,
        resource_label=item.name,
        request=request,
    )
    await session.commit()
    await session.refresh(item)
    return product_detail(item)


@router.delete(
    "/products/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Xoá sản phẩm (Admin)",
)
async def delete_product(
    product_id: str, admin: AdminUser, session: SessionDep, request: Request
) -> None:
    item = await session.get(Product, product_id)
    if item is None:
        raise NotFoundError("Sản phẩm")
    await audit.record(
        session,
        action="product.delete",
        summary=f"{admin.name} xoá sản phẩm {item.name}",
        actor=admin,
        resource_type="product",
        resource_id=product_id,
        resource_label=item.name,
        request=request,
    )
    await session.delete(item)
    await session.commit()


@router.post("/products/{product_id}/crawl", summary="Đồng bộ lại từ nguồn chính thức")
async def crawl_product(
    product_id: str, user: CurrentUser, session: SessionDep, request: Request
) -> dict[str, str]:
    item = await session.get(Product, product_id)
    if item is None:
        raise NotFoundError("Sản phẩm")

    job = Job(
        id=Job.make_id(),
        kind=JobKind.PRODUCT_CRAWL,
        status=JobStatus.QUEUED,
        created_by_id=user.id,
        params={"productId": product_id, "sourceUrl": item.source_url},
    )
    session.add(job)
    await audit.record(
        session,
        action="product.crawl",
        summary=f"{user.name} đồng bộ lại sản phẩm {item.name}",
        actor=user,
        resource_type="product",
        resource_id=product_id,
        resource_label=item.name,
        request=request,
    )
    await session.commit()
    await enqueue("run_product_crawl", job.id)
    return {"jobId": job.id}
