import argparse
import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from app.core.text import domain_of, handle_of, initials, normalize
from app.db.models.collab import AuditLog
from app.db.models.enums import Role, UserStatus
from app.db.models.user import User, UserNotificationSettings
from app.db.session import session_scope
from app.services.provisioning import pick_avatar_color

log = get_logger(__name__)


async def upsert_user(
    session: AsyncSession,
    email: str,
    role: Role,
    name: str | None = None,
    team_id: str | None = None,
) -> tuple[User, str]:
    email = email.strip().lower()
    if domain_of(email) not in settings.allowed_email_domains:
        allowed = ", ".join("@" + d for d in settings.allowed_email_domains)
        raise ValueError(f"{email} không thuộc {allowed}")

    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()

    if user is None:
        display = handle_of(email)
        user = User(
            id=User.make_id(),
            email=email,
            handle=handle_of(email),
            name=display,
            name_norm=normalize(display),
            initials=initials(handle_of(email)),
            avatar_color=pick_avatar_color(email),
            role=role,
            status=UserStatus.ACTIVE,
            team_id=team_id,
            is_first_login=True,
        )
        session.add(user)
        await session.flush()
        session.add(UserNotificationSettings(user_id=user.id))
        return user, "created"

    changed = []
    if user.role != role:
        user.role = role
        changed.append(f"role={role.value}")
    if name and user.name != name:
        user.name = name
        user.name_norm = normalize(name)
        changed.append("name")
    if user.status == UserStatus.LOCKED:
        user.status = UserStatus.ACTIVE
        changed.append("unlocked")
    return user, ("updated: " + ", ".join(changed)) if changed else "unchanged"


async def ensure_bootstrap_admins(session: AsyncSession) -> list[str]:
    """Promote the emails in BOOTSTRAP_ADMIN_EMAILS to ADMIN.

    Only bootstraps the first administrators so an empty database is reachable.
    The DB stays the source of truth: everything afterwards goes through
    /admin/users, and removing an email here never demotes anyone.
    """
    touched: list[str] = []
    for email in settings.bootstrap_admin_emails:
        try:
            user, outcome = await upsert_user(session, email, Role.ADMIN)
        except ValueError as exc:
            log.warning("bootstrap_admin_rejected", email=email, error=str(exc))
            continue
        if outcome != "unchanged":
            touched.append(f"{user.email} ({outcome})")
    return touched


async def _cmd_grant(emails: list[str], role: Role, name: str | None) -> int:
    async with session_scope() as session:
        for email in emails:
            try:
                user, outcome = await upsert_user(session, email, role, name=name)
            except ValueError as exc:
                print(f"  BỎ QUA  {email}: {exc}")
                continue
            print(f"  {outcome.upper():<22} {user.email:<26} role={user.role.value}")
    return 0


async def _cmd_sync_names(apply: bool) -> int:
    async with session_scope() as session:
        rows = (await session.execute(select(User).order_by(User.email))).scalars().all()
        drift = [u for u in rows if u.name != handle_of(u.email)]
        stale_audit = await _stale_audit(session)

        if not drift and not stale_audit:
            print(
                f"{len(rows)} user, tên đã khớp handle email và audit log không còn "
                f"nhãn cũ — không có gì phải đổi."
            )
            return 0

        if drift:
            print(f"{len(drift)}/{len(rows)} user có tên không suy ra từ email:")
            for u in drift:
                print(f"  {u.email:<24} {u.name!r} -> {handle_of(u.email)!r}")
        if stale_audit:
            print(f"{len(stale_audit)} dòng audit log còn nhãn tên cũ, ví dụ:")
            for row in stale_audit[:5]:
                print(f"  {row.actor_label!r}: {(row.summary or '')[:60]}")

        if not apply:
            print("\nChạy lại với --apply để đổi.")
            return 0

        for u in drift:
            handle = handle_of(u.email)
            u.name = handle
            u.name_norm = normalize(handle)
            u.initials = initials(handle)

        touched = await _relabel_audit(session)
        await session.commit()
        print(f"\nĐã đổi {len(drift)} user, viết lại {touched} dòng audit log.")
    return 0


async def _stale_audit(session: AsyncSession) -> list[AuditLog]:
    handles = dict((await session.execute(select(User.id, User.handle))).all())
    rows = (
        (await session.execute(select(AuditLog).where(AuditLog.actor_id.isnot(None))))
        .scalars()
        .all()
    )
    return [
        r
        for r in rows
        if r.actor_label
        and handles.get(r.actor_id or "")
        and r.actor_label != handles[r.actor_id or ""]
    ]


async def _relabel_audit(session: AsyncSession) -> int:
    """Audit log lưu tên hiển thị vào actor_label và mở đầu summary bằng chính nó,
    nên đổi được chính xác mà không cần đoán chuỗi cũ."""
    rows = (
        (await session.execute(select(AuditLog).where(AuditLog.actor_id.isnot(None))))
        .scalars()
        .all()
    )
    handles = dict(
        (await session.execute(select(User.id, User.handle))).all()  # type: ignore[arg-type]
    )

    touched = 0
    for row in rows:
        handle = handles.get(row.actor_id or "")
        old_label = row.actor_label
        if not handle or not old_label or old_label == handle:
            continue
        if row.summary and old_label in row.summary:
            row.summary = row.summary.replace(old_label, handle)
        if row.resource_label and old_label in row.resource_label:
            row.resource_label = row.resource_label.replace(old_label, handle)
        row.actor_label = handle
        row.search_norm = normalize(
            " ".join(filter(None, [row.summary, row.resource_label, handle]))
        )
        touched += 1
    return touched


async def _cmd_list() -> int:
    async with session_scope() as session:
        rows = (await session.execute(select(User).order_by(User.role, User.email))).scalars().all()
        print(f"{len(rows)} user trong DB:")
        for u in rows:
            print(f"  {u.email:<26} {u.role.value:<7} {u.status.value:<8} {u.name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    parser = argparse.ArgumentParser(
        prog="python -m app.users",
        description="Quản lý user trong database (nguồn sự thật là bảng users).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    grant = sub.add_parser("grant-admin", help="Tạo user nếu chưa có và đặt role ADMIN")
    grant.add_argument("emails", nargs="+")
    grant.add_argument("--name", default=None)

    revoke = sub.add_parser("revoke-admin", help="Hạ user về MEMBER")
    revoke.add_argument("emails", nargs="+")

    sub.add_parser("list", help="Liệt kê user trong DB")

    sync = sub.add_parser(
        "sync-names",
        help="Đặt lại name/initials theo handle email (hệ thống không biết tên thật)",
    )
    sync.add_argument("--apply", action="store_true")

    args = parser.parse_args(argv)

    if args.cmd == "grant-admin":
        return asyncio.run(_cmd_grant(args.emails, Role.ADMIN, args.name))
    if args.cmd == "revoke-admin":
        return asyncio.run(_cmd_grant(args.emails, Role.MEMBER, None))
    if args.cmd == "sync-names":
        return asyncio.run(_cmd_sync_names(args.apply))
    return asyncio.run(_cmd_list())


if __name__ == "__main__":
    sys.exit(main())
