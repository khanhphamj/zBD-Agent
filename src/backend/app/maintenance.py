import argparse
import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import setup_logging
from app.db.models.collab import Notification, Share, ShareLink
from app.db.models.enums import ResourceType
from app.db.models.user import AccessRequest
from app.db.session import session_scope
from app.services.shares import model_for, target_url
from app.services.storage import storage

_URL_TO_TYPE = {target_url(rt, "X").rsplit("/", 1)[0]: rt for rt in ResourceType}


async def _live_ids(session: AsyncSession, rt: ResourceType) -> set[str]:
    model = model_for(rt)
    rows = (await session.execute(select(model.id))).scalars().all()
    return set(rows)


def _parse_target(url: str | None) -> tuple[ResourceType, str] | None:
    if not url:
        return None
    prefix, _, ident = url.rpartition("/")
    rt = _URL_TO_TYPE.get(prefix)
    if rt is None or not ident:
        return None
    return rt, ident


async def find_orphans(session: AsyncSession) -> dict[str, list[tuple[str, str, str]]]:
    cache: dict[ResourceType, set[str]] = {}

    async def alive(rt: ResourceType, ident: str) -> bool:
        if rt not in cache:
            cache[rt] = await _live_ids(session, rt)
        return ident in cache[rt]

    found: dict[str, list[tuple[str, str, str]]] = {
        "shares": [],
        "share_links": [],
        "access_requests": [],
        "notifications": [],
    }

    for label, model in (
        ("shares", Share),
        ("share_links", ShareLink),
        ("access_requests", AccessRequest),
    ):
        for row in (await session.execute(select(model))).scalars().all():
            rt_raw = getattr(row, "resource_type", None)
            rid = getattr(row, "resource_id", None)
            if not rt_raw or not rid:
                continue
            try:
                rt = ResourceType(str(rt_raw))
            except ValueError:
                continue
            if not await alive(rt, rid):
                found[label].append((row.id, str(rt), rid))

    for note in (await session.execute(select(Notification))).scalars().all():
        parsed = _parse_target(note.target_url)
        if parsed is None:
            continue
        rt, ident = parsed
        if not await alive(rt, ident):
            found["notifications"].append((note.id, str(rt), ident))

    return found


async def _cmd_orphans(apply: bool) -> int:
    async with session_scope() as session:
        found = await find_orphans(session)
        total = sum(len(v) for v in found.values())

        if total == 0:
            print("Không có bản ghi mồ côi.")
            return 0

        for label, rows in found.items():
            if not rows:
                continue
            print(f"{label}: {len(rows)} mồ côi")
            for rid, rt, target in rows[:20]:
                print(f"    {rid}  -> {rt}/{target} (không còn tồn tại)")
            if len(rows) > 20:
                print(f"    … thêm {len(rows) - 20}")

        if not apply:
            print(f"\nTổng {total}. Chạy lại với --apply để xoá.")
            return 0

        models = {
            "shares": Share,
            "share_links": ShareLink,
            "access_requests": AccessRequest,
            "notifications": Notification,
        }
        for label, rows in found.items():
            model = models[label]
            for rid, _, _ in rows:
                obj = await session.get(model, rid)
                if obj is not None:
                    await session.delete(obj)
        await session.commit()
        print(f"\nĐã xoá {total} bản ghi mồ côi.")
    return 0


async def _cmd_storage(apply: bool) -> int:
    async with session_scope() as session:
        from app.db.models.knowledge import KnowledgeFile
        from app.db.models.library import Skill
        from app.db.models.proposal import ProposalExport

        skill_keys: set[str] = set()
        for bundle in (await session.execute(select(Skill.bundle))).scalars().all():
            for entry in (bundle or {}).get("files", []):
                key = entry.get("objectKey")
                if key:
                    skill_keys.add(key)

        keys: dict[str, set[str]] = {
            settings.bucket_knowledge: set(
                (await session.execute(select(KnowledgeFile.object_key))).scalars().all()
            ),
            settings.bucket_exports: set(
                (await session.execute(select(ProposalExport.object_key))).scalars().all()
            ),
            settings.bucket_skills: skill_keys,
        }

    client = storage.client
    total = 0
    for bucket, known in keys.items():
        try:
            objects = list(client.list_objects(bucket, recursive=True))
        except Exception as exc:
            print(f"{bucket}: không đọc được ({exc})")
            continue
        stray = [o for o in objects if o.object_name not in known and o.object_name is not None]
        print(f"{bucket}: {len(objects)} object, {len(stray)} không có bản ghi trong DB")
        for o in stray[:20]:
            print(f"    {o.object_name}  ({o.size} byte)")
        if len(stray) > 20:
            print(f"    … thêm {len(stray) - 20}")
        if apply:
            for o in stray:
                client.remove_object(bucket, o.object_name)
        total += len(stray)

    if total and not apply:
        print(f"\nTổng {total} object. Chạy lại với --apply để xoá.")
    elif apply:
        print(f"\nĐã xoá {total} object.")
    return 0


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    parser = argparse.ArgumentParser(
        prog="python -m app.maintenance",
        description=(
            "Dọn dữ liệu mồ côi. Mặc định chỉ liệt kê (dry-run); phải thêm --apply mới thực sự xoá."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    orphans = sub.add_parser(
        "orphans",
        help="Share / share_link / access_request / notification trỏ tới resource đã bị xoá",
    )
    orphans.add_argument("--apply", action="store_true")

    stray = sub.add_parser("storage", help="Object trên MinIO không còn bản ghi tương ứng trong DB")
    stray.add_argument("--apply", action="store_true")

    args = parser.parse_args(argv)
    if args.cmd == "orphans":
        return asyncio.run(_cmd_orphans(args.apply))
    return asyncio.run(_cmd_storage(args.apply))


if __name__ == "__main__":
    sys.exit(main())
