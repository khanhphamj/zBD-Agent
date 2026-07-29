from dataclasses import dataclass
from typing import Any

from fastapi import Query
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings


class Page[T](BaseModel):
    items: list[T]
    page: int
    size: int
    total: int
    totalPages: int


@dataclass(slots=True)
class PageParams:
    page: int
    size: int
    q: str | None
    sort: str | None

    @property
    def offset(self) -> int:
        return self.page * self.size


def page_params(
    page: int = Query(0, ge=0, description="Zero-based page index"),
    size: int = Query(
        settings.default_page_size,
        ge=1,
        le=settings.max_page_size,
        description="Rows per page; the UI uses 5",
    ),
    q: str | None = Query(None, description="Diacritic-insensitive keyword"),
    sort: str | None = Query(None, description="field:asc|desc"),
) -> PageParams:
    return PageParams(page=page, size=size, q=q, sort=sort)


def parse_sort(
    sort: str | None,
    allowed: dict[str, Any],
    default: Any,
) -> Any:
    if not sort:
        return default
    field, _, direction = sort.partition(":")
    column = allowed.get(field.strip())
    if column is None:
        return default
    return column.asc() if direction.strip().lower() == "asc" else column.desc()


async def paginate(
    session: AsyncSession,
    stmt: Select[Any],
    params: PageParams,
    serializer: Any,
) -> dict[str, Any]:
    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = int((await session.execute(count_stmt)).scalar_one() or 0)

    rows = (
        (await session.execute(stmt.offset(params.offset).limit(params.size)))
        .unique()
        .scalars()
        .all()
    )

    total_pages = (total + params.size - 1) // params.size if params.size else 0
    return {
        "items": [serializer(row) for row in rows],
        "page": params.page,
        "size": params.size,
        "total": total,
        "totalPages": total_pages,
    }
