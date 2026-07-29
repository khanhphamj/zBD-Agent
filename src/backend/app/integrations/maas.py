import asyncio
import base64
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

Role = Literal["system", "assistant", "user"]

# Lỗi tạm thời: pool model sập, gateway quá tải, timeout mạng. Đáng thử lại và đáng
# đổi sang model khác. Còn 400/401/403/404/422 là sai request hoặc sai tên model —
# thử lại chỉ tốn thời gian.
TRANSIENT_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


class MaasError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class MaasNotConfigured(MaasError):
    def __init__(self) -> None:
        super().__init__(
            "AI_PLATFORM_API_KEY chưa được cấu hình — không thể gọi MaaS.", status_code=503
        )


@dataclass(slots=True)
class Message:
    role: Role
    content: str | list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {"role": self.role, "content": self.content}


@dataclass(slots=True)
class Completion:
    text: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    finish_reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def as_json(self) -> Any:
        text = self.text.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1] if text.count("```") >= 2 else text.strip("`")
            text = text.removeprefix("json").strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            for opener, closer in (("{", "}"), ("[", "]")):
                start, end = text.find(opener), text.rfind(closer)
                if start != -1 and end > start:
                    try:
                        return json.loads(text[start : end + 1])
                    except json.JSONDecodeError:
                        continue
            raise MaasError(f"Model trả về nội dung không phải JSON hợp lệ: {text[:200]}") from None


class MaasClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: int | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.ai_platform_api_key
        self.base_url = (base_url or settings.maas_base_url).rstrip("/")
        self.timeout = timeout or settings.maas_timeout_seconds
        self._client: httpx.AsyncClient | None = None

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout, connect=15.0),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def aclose(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> "MaasClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    def _payload(
        self,
        messages: list[Message],
        model: str,
        max_tokens: int | None,
        temperature: float | None,
        top_p: float | None,
        response_format: dict[str, Any] | None,
        stream: bool,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": [m.to_dict() for m in messages],
            "max_tokens": max_tokens if max_tokens is not None else settings.maas_max_tokens,
            "temperature": temperature if temperature is not None else settings.maas_temperature,
            "top_p": top_p if top_p is not None else settings.maas_top_p,
        }
        if response_format:
            body["response_format"] = response_format
        if stream:
            body["stream"] = True
        return body

    def _attempt_plan(self, model: str) -> list[tuple[str, float]]:
        """(model, số giây chờ trước khi gọi). Thử lại model chính một lần cho các cú
        chớp nhoáng, rồi mới hạ xuống model dự phòng."""
        delays = [float(d) for d in settings.maas_retry_delays if str(d).strip()] or [2.0]
        plan: list[tuple[str, float]] = [(model, 0.0), (model, delays[0])]
        for index, alternative in enumerate(settings.maas_fallback_models):
            if alternative and alternative != model:
                plan.append((alternative, delays[min(index, len(delays) - 1)]))
        return plan

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        response_format: dict[str, Any] | None = None,
        timeout: int | None = None,  # noqa: ASYNC109
    ) -> Completion:
        if not self.configured:
            raise MaasNotConfigured

        wanted = model or settings.maas_reasoning_model
        client = await self._http()
        plan = self._attempt_plan(wanted)
        last: MaasError | None = None
        response: httpx.Response | None = None
        served_by = wanted

        for index, (candidate, delay) in enumerate(plan):
            if delay:
                await asyncio.sleep(delay)
            body = self._payload(
                messages, candidate, max_tokens, temperature, top_p, response_format, stream=False
            )
            try:
                attempt = await client.post(
                    "/chat/completions",
                    json=body,
                    timeout=httpx.Timeout(timeout or self.timeout, connect=15.0),
                )
            except httpx.HTTPError as exc:
                last = MaasError(f"Không gọi được MaaS: {exc}")
                log.warning("maas_unreachable", model=candidate, error=str(exc)[:200])
                continue

            if attempt.status_code < 400:
                response = attempt
                served_by = candidate
                if candidate != wanted:
                    log.warning("maas_fallback_used", wanted=wanted, served_by=candidate)
                break

            detail = attempt.text[:400]
            log.warning("maas_error", status=attempt.status_code, model=candidate, detail=detail)
            last = MaasError(
                f"MaaS trả về {attempt.status_code}: {detail}", status_code=attempt.status_code
            )
            if attempt.status_code not in TRANSIENT_STATUS:
                raise last
            if index == len(plan) - 1:
                raise MaasError(
                    f"MaaS không phục vụ được sau {len(plan)} lần thử "
                    f"({', '.join(dict.fromkeys(m for m, _ in plan))}): {last}",
                    status_code=attempt.status_code,
                )

        if response is None:
            raise last or MaasError("MaaS không phản hồi")

        model = served_by
        data = response.json()
        choice = (data.get("choices") or [{}])[0]
        usage = data.get("usage") or {}
        text = (choice.get("message") or {}).get("content") or ""

        if not text.strip():
            text = (choice.get("message") or {}).get("reasoning_content") or ""

        return Completion(
            text=text,
            model=data.get("model", model),
            tokens_in=int(usage.get("prompt_tokens") or 0),
            tokens_out=int(usage.get("completion_tokens") or 0),
            finish_reason=choice.get("finish_reason"),
            raw=data,
        )

    async def stream(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
    ) -> AsyncIterator[str]:
        if not self.configured:
            raise MaasNotConfigured

        model = model or settings.maas_reasoning_model
        body = self._payload(messages, model, max_tokens, temperature, top_p, None, stream=True)

        client = await self._http()
        async with client.stream("POST", "/chat/completions", json=body) as response:
            if response.status_code >= 400:
                raw = await response.aread()
                raise MaasError(
                    f"MaaS trả về {response.status_code}: {raw[:300].decode(errors='replace')}",
                    status_code=response.status_code,
                )
            async for line in response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    parsed = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                delta = ((parsed.get("choices") or [{}])[0].get("delta") or {}).get("content")
                if delta:
                    yield delta

    async def run(
        self,
        profile_key: str,
        system: str,
        user: str,
        *,
        admin: Any | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> Completion:
        from app.integrations.harness import resolve

        p = resolve(
            profile_key,
            admin=admin,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return await self.complete(
            [Message("assistant", system), Message("user", user)],
            model=p.model,
            max_tokens=p.max_tokens,
            temperature=p.temperature,
            top_p=p.top_p,
            timeout=p.timeout,
            response_format={"type": "json_object"} if p.json_mode else None,
        )

    async def run_stream(
        self,
        profile_key: str,
        messages: list[Message],
        *,
        admin: Any | None = None,
    ) -> AsyncIterator[str]:
        from app.integrations.harness import resolve

        p = resolve(profile_key, admin=admin)
        async for chunk in self.stream(
            messages,
            model=p.model,
            max_tokens=p.max_tokens,
            temperature=p.temperature,
            top_p=p.top_p,
        ):
            yield chunk

    async def look(
        self,
        profile_key: str,
        prompt: str,
        images: list[bytes | str],
        *,
        mime_type: str = "image/png",
        admin: Any | None = None,
    ) -> Completion:
        from app.integrations.harness import resolve

        p = resolve(profile_key, admin=admin)
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for image in images:
            if isinstance(image, bytes):
                url = f"data:{mime_type};base64,{base64.b64encode(image).decode()}"
            else:
                url = image
            content.append({"type": "image_url", "image_url": {"url": url}})
        return await self.complete(
            [Message("user", content)],
            model=p.model,
            max_tokens=p.max_tokens,
            temperature=p.temperature,
            top_p=p.top_p,
            timeout=p.timeout,
        )

    async def reason(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        as_json: bool = False,
    ) -> Completion:
        return await self.complete(
            [Message("assistant", system), Message("user", user)],
            model=settings.maas_reasoning_model,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format={"type": "json_object"} if as_json else None,
        )

    async def see(
        self,
        prompt: str,
        images: list[bytes | str],
        *,
        mime_type: str = "image/png",
        max_tokens: int | None = None,
    ) -> Completion:
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for image in images:
            if isinstance(image, bytes):
                encoded = base64.b64encode(image).decode()
                url = f"data:{mime_type};base64,{encoded}"
            else:
                url = image
            content.append({"type": "image_url", "image_url": {"url": url}})

        return await self.complete(
            [Message("user", content)],
            model=settings.maas_vision_model,
            max_tokens=max_tokens,
        )


maas = MaasClient()
