from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

BRAND_BLUE = "#0068FF"
BRAND_NAVY = "#021F4E"
BRAND_INDIGO = "#1A30A0"
BRAND_BORDER = "#DCE6F7"
BRAND_MUTED = "#8FA2C7"
BRAND_BG = "#F2F6FD"


class EmailError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(slots=True)
class SentEmail:
    id: str | None
    to: list[str]
    subject: str
    dry_run: bool = False
    redirected_from: list[str] | None = None

    @property
    def delivered(self) -> bool:
        return bool(self.id) and not self.dry_run


def _abs(url: str) -> str:
    if url.startswith(("http://", "https://", "mailto:")):
        return url
    return f"{settings.app_base_url.rstrip('/')}/{url.lstrip('/')}"


def _sandbox_banner(original: list[str]) -> str:
    return (
        f'<p style="margin:0 0 16px;padding:10px 14px;background:#FFF6DC;'
        f"border:1px solid #E6C566;border-radius:8px;"
        f'font:600 12px/1.55 Roboto,Arial,sans-serif;color:#6B4E00">'
        f"SANDBOX — email này lẽ ra được gửi tới <strong>{', '.join(original)}</strong>. "
        f"Nó chuyển về đây vì tài khoản Resend chưa verify domain gửi."
        f"</p>"
    )


def _shell(title: str, body_html: str, footer: str | None = None) -> str:
    footer_html = (
        f'<p style="margin:18px 0 0;font:400 12px/1.6 Roboto,Arial,sans-serif;color:{BRAND_MUTED}">'
        f"{footer}</p>"
        if footer
        else ""
    )
    return f"""\
<!doctype html>
<html lang="vi">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title></head>
<body style="margin:0;padding:24px 12px;background:{BRAND_BG}">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
       style="max-width:560px;margin:0 auto;background:#fff;border:1px solid {BRAND_BORDER};
              border-radius:14px;overflow:hidden">
  <tr>
    <td style="padding:20px 28px;background:{BRAND_NAVY}">
      <span style="font:800 16px/1.2 Montserrat,Arial,sans-serif;color:#fff;letter-spacing:.2px">
        {settings.app_name}
      </span>
    </td>
  </tr>
  <tr>
    <td style="padding:28px">
      <h1 style="margin:0 0 14px;font:700 19px/1.35 Montserrat,Arial,sans-serif;color:{BRAND_NAVY}">
        {title}
      </h1>
      {body_html}
      {footer_html}
    </td>
  </tr>
  <tr>
    <td style="padding:16px 28px;background:{BRAND_BG};border-top:1px solid {BRAND_BORDER}">
      <p style="margin:0;font:400 11px/1.6 Roboto,Arial,sans-serif;color:{BRAND_MUTED}">
        Email tự động từ hệ thống {settings.app_name} — vui lòng không trả lời.
      </p>
    </td>
  </tr>
</table>
</body>
</html>"""


def _button(label: str, url: str) -> str:
    return (
        f'<a href="{url}" style="display:inline-block;margin:18px 0 4px;padding:11px 22px;'
        f"background:{BRAND_BLUE};color:#fff;border-radius:9px;text-decoration:none;"
        f'font:600 14px/1 Roboto,Arial,sans-serif">{label}</a>'
    )


def _paragraph(text: str) -> str:
    return (
        f'<p style="margin:0 0 12px;font:400 14px/1.65 Roboto,Arial,sans-serif;'
        f'color:#33415e">{text}</p>'
    )


class ResendClient:
    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key if api_key is not None else settings.resend_api_key
        self.base_url = settings.resend_base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(20.0, connect=10.0),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def aclose(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1.5, min=1, max=10),
        reraise=True,
    )
    async def send(
        self,
        to: str | list[str],
        subject: str,
        html: str,
        *,
        text: str | None = None,
        tags: dict[str, str] | None = None,
    ) -> SentEmail:
        recipients = [to] if isinstance(to, str) else to

        if settings.email_dry_run or not self.configured:
            log.info(
                "email_dry_run",
                to=recipients,
                subject=subject,
                reason="EMAIL_DRY_RUN" if settings.email_dry_run else "no RESEND_API_KEY",
                preview=(text or "")[:400] or None,
            )
            return SentEmail(id=None, to=recipients, subject=subject, dry_run=True)

        delivered_to = recipients
        redirected_from: list[str] | None = None
        sandbox = settings.email_sandbox_to.strip().lower()
        if sandbox and [r.lower() for r in recipients] != [sandbox]:
            redirected_from = recipients
            delivered_to = [sandbox]
            subject = f"[→ {', '.join(recipients)}] {subject}"
            marker = f'<body style="margin:0;padding:24px 12px;background:{BRAND_BG}">'
            banner = _sandbox_banner(recipients)
            html = html.replace(marker, marker + banner, 1) if marker in html else banner + html
            if text:
                text = f"[SANDBOX] Người nhận gốc: {', '.join(recipients)}\n\n{text}"

        payload: dict[str, Any] = {
            "from": settings.resend_from_email,
            "to": delivered_to,
            "subject": subject,
            "html": html,
        }
        if text:
            payload["text"] = text
        if settings.resend_reply_to:
            payload["reply_to"] = settings.resend_reply_to
        if tags:
            payload["tags"] = [{"name": k, "value": v} for k, v in tags.items()]

        client = await self._http()
        try:
            response = await client.post("/emails", json=payload)
        except httpx.HTTPError as exc:
            raise EmailError(f"Không gửi được email: {exc}") from exc

        if response.status_code >= 400:
            detail = response.text[:300]
            log.warning(
                "resend_error",
                status=response.status_code,
                to=delivered_to,
                sender=settings.resend_from_email,
                detail=detail,
            )
            raise EmailError(
                f"Resend trả về {response.status_code}: {detail}",
                status_code=response.status_code,
            )

        body = response.json()
        log.info(
            "email_sent",
            to=delivered_to,
            original_to=redirected_from,
            subject=subject,
            id=body.get("id"),
        )
        return SentEmail(
            id=body.get("id"),
            to=delivered_to,
            subject=subject,
            redirected_from=redirected_from,
        )

    async def send_otp(self, to: str, code: str, ttl_seconds: int) -> SentEmail:
        minutes = max(1, round(ttl_seconds / 60))
        digits = "".join(
            f'<span style="display:inline-block;min-width:38px;padding:10px 0;margin:0 3px;'
            f"background:{BRAND_BG};border:1px solid {BRAND_BORDER};border-radius:8px;"
            f'font:700 22px/1 Montserrat,Arial,sans-serif;color:{BRAND_NAVY};text-align:center">'
            f"{ch}</span>"
            for ch in code
        )
        html = _shell(
            "Mã xác thực đăng nhập",
            _paragraph(f"Dùng mã dưới đây để đăng nhập vào {settings.app_name}:")
            + f'<div style="margin:18px 0;text-align:center">{digits}</div>',
            footer=(
                f"Mã có hiệu lực trong {minutes} phút. "
                "Nếu bạn không yêu cầu đăng nhập, hãy bỏ qua email này."
            ),
        )
        return await self.send(
            to,
            f"{code} là mã đăng nhập {settings.app_name} của bạn",
            html,
            text=f"Mã xác thực {settings.app_name}: {code} (hiệu lực {minutes} phút)",
            tags={"type": "otp"},
        )

    async def send_invite(
        self, to: str, inviter_name: str, role: str, accept_url: str, team: str | None = None
    ) -> SentEmail:
        accept_url = _abs(accept_url)
        where = f" vào nhóm <strong>{team}</strong>" if team else ""
        html = _shell(
            f"Bạn được mời vào {settings.app_name}",
            _paragraph(
                f"<strong>{inviter_name}</strong> đã mời bạn tham gia "
                f"{settings.app_name}{where} với vai trò <strong>{role}</strong>."
            )
            + _paragraph(
                "Nhấn vào nút bên dưới để kích hoạt tài khoản. Bạn sẽ đăng nhập bằng mã OTP "
                "gửi tới chính email này."
            )
            + _button("Kích hoạt tài khoản", accept_url),
            footer="Lời mời sẽ hết hiệu lực sau 7 ngày.",
        )
        return await self.send(
            to,
            f"{inviter_name} mời bạn dùng {settings.app_name}",
            html,
            text=(
                f"{inviter_name} mời bạn vào {settings.app_name} ({role}). Kích hoạt: {accept_url}"
            ),
            tags={"type": "invite"},
        )

    async def send_share(
        self,
        to: str,
        actor_name: str,
        resource_label: str,
        resource_kind: str,
        permission: str,
        url: str,
    ) -> SentEmail:
        url = _abs(url)
        perm = {"view": "xem", "comment": "bình luận", "edit": "chỉnh sửa"}.get(
            permission, permission
        )
        html = _shell(
            f"{actor_name} đã chia sẻ {resource_kind}",
            _paragraph(
                f"<strong>{actor_name}</strong> vừa chia sẻ {resource_kind} "
                f"<strong>{resource_label}</strong> với bạn, quyền <strong>{perm}</strong>."
            )
            + _button(f"Mở trong {settings.app_name}", url),
        )
        return await self.send(
            to,
            f"{actor_name} đã chia sẻ “{resource_label}” với bạn",
            html,
            text=f"{actor_name} chia sẻ {resource_label} ({perm}): {url}",
            tags={"type": "share"},
        )

    async def send_approval_request(
        self, to: str, requester_name: str, proposal_title: str, url: str, note: str | None = None
    ) -> SentEmail:
        url = _abs(url)
        extra = _paragraph(f"Ghi chú: <em>{note}</em>") if note else ""
        html = _shell(
            "Đề nghị duyệt proposal",
            _paragraph(
                f"<strong>{requester_name}</strong> đề nghị bạn duyệt proposal "
                f"<strong>{proposal_title}</strong>."
            )
            + extra
            + _button("Xem và duyệt", url),
        )
        return await self.send(
            to,
            f"[Cần duyệt] {proposal_title}",
            html,
            text=f"{requester_name} đề nghị duyệt {proposal_title}: {url}",
            tags={"type": "approval_request"},
        )

    async def send_approval_result(
        self,
        to: str,
        proposal_title: str,
        approved: bool,
        approver_name: str,
        url: str,
        reason: str | None = None,
    ) -> SentEmail:
        url = _abs(url)
        verdict = "đã được duyệt" if approved else "bị từ chối"
        colour = "#12A150" if approved else "#D9483B"
        body = _paragraph(
            f'Proposal <strong>{proposal_title}</strong> <span style="color:{colour};'
            f'font-weight:600">{verdict}</span> bởi <strong>{approver_name}</strong>.'
        )
        if reason:
            body += _paragraph(f"Lý do: <em>{reason}</em>")
        html = _shell(f"Proposal {verdict}", body + _button("Mở proposal", url))
        return await self.send(
            to,
            f"[{'Đã duyệt' if approved else 'Từ chối'}] {proposal_title}",
            html,
            text=f"{proposal_title} {verdict} bởi {approver_name}. {url}",
            tags={"type": "approval_result"},
        )

    async def send_research_done(
        self,
        to: str,
        customer_name: str,
        source_count: int,
        fact_count: int,
        opportunity_count: int,
        url: str,
    ) -> SentEmail:
        url = _abs(url)
        html = _shell(
            "Deep Research hoàn tất",
            _paragraph(
                f"Hồ sơ <strong>{customer_name}</strong> đã sẵn sàng: "
                f"<strong>{source_count}</strong> nguồn, <strong>{fact_count}</strong> dữ kiện, "
                f"<strong>{opportunity_count}</strong> cơ hội mới."
            )
            + _button("Xem hồ sơ khách hàng", url),
        )
        return await self.send(
            to,
            f"Deep Research hoàn tất — {customer_name}",
            html,
            text=(
                f"{customer_name}: {source_count} nguồn, {fact_count} dữ kiện, "
                f"{opportunity_count} cơ hội. {url}"
            ),
            tags={"type": "research_done"},
        )

    async def send_comment(
        self, to: str, actor_name: str, proposal_title: str, body_text: str, url: str
    ) -> SentEmail:
        url = _abs(url)
        html = _shell(
            "Bình luận mới",
            _paragraph(
                f"<strong>{actor_name}</strong> đã bình luận trên "
                f"<strong>{proposal_title}</strong>:"
            )
            + f'<blockquote style="margin:0 0 12px;padding:10px 14px;background:{BRAND_BG};'
            f"border-left:3px solid {BRAND_INDIGO};font:400 14px/1.6 Roboto,Arial,sans-serif;"
            f'color:{BRAND_NAVY}">{body_text}</blockquote>' + _button("Trả lời", url),
        )
        return await self.send(
            to,
            f"{actor_name} bình luận trên “{proposal_title}”",
            html,
            text=f"{actor_name}: {body_text} — {url}",
            tags={"type": "comment"},
        )

    async def send_access_request(
        self,
        to: str,
        requester_name: str,
        requester_email: str,
        resource_label: str,
        permission: str,
        reason: str | None,
        url: str,
    ) -> SentEmail:
        url = _abs(url)
        extra = _paragraph(f"Lý do: <em>{reason}</em>") if reason else ""
        html = _shell(
            "Yêu cầu cấp quyền truy cập",
            _paragraph(
                f"<strong>{requester_name}</strong> ({requester_email}) xin quyền "
                f"<strong>{permission}</strong> vào <strong>{resource_label}</strong>."
            )
            + extra
            + _button("Duyệt yêu cầu", url),
        )
        return await self.send(
            to,
            f"[Yêu cầu quyền] {resource_label}",
            html,
            text=f"{requester_name} xin quyền {permission} vào {resource_label}: {url}",
            tags={"type": "access_request"},
        )

    async def send_handover(
        self, to: str, from_name: str, customer_name: str, note: str | None, url: str
    ) -> SentEmail:
        url = _abs(url)
        extra = _paragraph(f"Ghi chú bàn giao: <em>{note}</em>") if note else ""
        html = _shell(
            "Bạn được bàn giao khách hàng",
            _paragraph(
                f"<strong>{from_name}</strong> đã bàn giao khách hàng "
                f"<strong>{customer_name}</strong> cho bạn."
            )
            + extra
            + _button("Nhận hồ sơ", url),
        )
        return await self.send(
            to,
            f"Bàn giao khách hàng — {customer_name}",
            html,
            text=f"{from_name} bàn giao {customer_name} cho bạn: {url}",
            tags={"type": "handover"},
        )


resend = ResendClient()
