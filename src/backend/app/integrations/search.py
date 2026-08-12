from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.core.text import clean_website, truncate

log = get_logger(__name__)

BlockReason = Literal["no_result", "timeout", "private", "http_error", "not_configured", "quota"]


@dataclass(slots=True)
class SearchHit:
    url: str
    title: str
    snippet: str = ""
    content: str = ""
    published_at: str | None = None
    score: float = 0.0
    provider: str = ""

    @property
    def host(self) -> str:
        return clean_website(self.url)

    def to_source_event(self) -> dict[str, Any]:
        return {"url": self.url, "title": truncate(self.title, 160), "status": "ok"}


@dataclass(slots=True)
class ProviderOutcome:
    provider: str
    ok: bool
    hits: list[SearchHit] = field(default_factory=list)
    reason: BlockReason | None = None
    http_status: int | None = None
    detail: str | None = None


class SearchProvider:
    name = "base"

    @property
    def configured(self) -> bool:
        raise NotImplementedError

    async def search(
        self,
        client: httpx.AsyncClient,
        query: str,
        limit: int,
        recency: str | None = None,
    ) -> ProviderOutcome:
        raise NotImplementedError


class LangSearchProvider(SearchProvider):
    name = "langsearch"

    @property
    def configured(self) -> bool:
        return bool(settings.langsearch_api_key)

    async def search(
        self,
        client: httpx.AsyncClient,
        query: str,
        limit: int,
        recency: str | None = None,
    ) -> ProviderOutcome:
        if not self.configured:
            return ProviderOutcome(self.name, ok=False, reason="not_configured")

        try:
            response = await client.post(
                f"{settings.langsearch_base_url.rstrip('/')}/web-search",
                headers={
                    "Authorization": f"Bearer {settings.langsearch_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "query": query,
                    "count": limit,
                    "summary": True,
                    "freshness": recency or "noLimit",
                    "language": settings.research_locale,
                    "country": settings.research_market,
                },
            )
        except httpx.TimeoutException:
            return ProviderOutcome(self.name, ok=False, reason="timeout")
        except httpx.HTTPError as exc:
            return ProviderOutcome(self.name, ok=False, reason="http_error", detail=str(exc))

        if response.status_code in (401, 403):
            return ProviderOutcome(
                self.name, ok=False, reason="not_configured", http_status=response.status_code
            )
        if response.status_code == 429:
            return ProviderOutcome(self.name, ok=False, reason="quota", http_status=429)
        if response.status_code >= 400:
            return ProviderOutcome(
                self.name,
                ok=False,
                reason="http_error",
                http_status=response.status_code,
                detail=response.text[:200],
            )

        payload = response.json()
        pages = (((payload.get("data") or {}).get("webPages") or {}).get("value")) or []
        hits = [
            SearchHit(
                url=item.get("url") or "",
                title=item.get("name") or item.get("url") or "",
                snippet=item.get("snippet") or "",
                content=item.get("summary") or item.get("snippet") or "",
                published_at=item.get("datePublished"),
                provider=self.name,
            )
            for item in pages
            if item.get("url")
        ]
        return ProviderOutcome(
            self.name, ok=bool(hits), hits=hits, reason=None if hits else "no_result"
        )


class TavilyProvider(SearchProvider):
    name = "tavily"

    @property
    def configured(self) -> bool:
        return bool(settings.tavily_api_key)

    async def search(
        self,
        client: httpx.AsyncClient,
        query: str,
        limit: int,
        recency: str | None = None,
    ) -> ProviderOutcome:
        if not self.configured:
            return ProviderOutcome(self.name, ok=False, reason="not_configured")

        try:
            response = await client.post(
                f"{settings.tavily_base_url.rstrip('/')}/search",
                headers={
                    "Authorization": f"Bearer {settings.tavily_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "query": query,
                    "max_results": limit,
                    "search_depth": "advanced",
                    "include_answer": False,
                    "include_raw_content": True,
                    "country": "vietnam",
                    "search_lang": settings.research_locale,
                    "exclude_domains": settings.research_excluded_domains,
                    **({"topic": "news", "days": 365} if recency else {}),
                },
            )
        except httpx.TimeoutException:
            return ProviderOutcome(self.name, ok=False, reason="timeout")
        except httpx.HTTPError as exc:
            return ProviderOutcome(self.name, ok=False, reason="http_error", detail=str(exc))

        if response.status_code in (401, 403):
            return ProviderOutcome(
                self.name, ok=False, reason="not_configured", http_status=response.status_code
            )
        if response.status_code == 429:
            return ProviderOutcome(self.name, ok=False, reason="quota", http_status=429)
        if response.status_code >= 400:
            return ProviderOutcome(
                self.name,
                ok=False,
                reason="http_error",
                http_status=response.status_code,
                detail=response.text[:200],
            )

        payload = response.json()
        hits = [
            SearchHit(
                url=item.get("url") or "",
                title=item.get("title") or item.get("url") or "",
                snippet=truncate(item.get("content") or "", 400),
                content=item.get("raw_content") or item.get("content") or "",
                published_at=item.get("published_date"),
                score=float(item.get("score") or 0.0),
                provider=self.name,
            )
            for item in (payload.get("results") or [])
            if item.get("url")
        ]
        return ProviderOutcome(
            self.name, ok=bool(hits), hits=hits, reason=None if hits else "no_result"
        )


_REGISTRY: dict[str, type[SearchProvider]] = {
    LangSearchProvider.name: LangSearchProvider,
    TavilyProvider.name: TavilyProvider,
}


class SearchClient:
    def __init__(self, provider_names: list[str] | None = None) -> None:
        names = provider_names or settings.search_providers
        self.providers: list[SearchProvider] = [
            _REGISTRY[name]() for name in names if name in _REGISTRY
        ]
        self._client: httpx.AsyncClient | None = None

    @property
    def configured(self) -> bool:
        return any(p.configured for p in self.providers)

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(settings.search_timeout_seconds, connect=15.0)
            )
        return self._client

    async def aclose(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def search(
        self, query: str, limit: int | None = None, recency: str | None = None
    ) -> ProviderOutcome:
        limit = limit or settings.research_max_sources
        client = await self._http()
        last = ProviderOutcome("none", ok=False, reason="not_configured")

        for provider in self.providers:
            outcome = await provider.search(client, query, limit, recency)
            log.debug(
                "search_attempt",
                provider=provider.name,
                query=query,
                ok=outcome.ok,
                hits=len(outcome.hits),
                reason=outcome.reason,
            )
            if outcome.ok:
                kept = [h for h in outcome.hits if not is_excluded(h)]
                dropped = len(outcome.hits) - len(kept)
                if dropped:
                    log.debug("search_filtered", query=query, dropped=dropped)
                kept.sort(key=vn_rank, reverse=True)
                return ProviderOutcome(
                    provider=outcome.provider,
                    ok=bool(kept),
                    hits=kept,
                    reason=None if kept else "no_result",
                    http_status=outcome.http_status,
                    detail=outcome.detail,
                )
            last = outcome
        return last

    async def multi_search(
        self, queries: list[str], limit_each: int = 5, recency: str | None = None
    ) -> list[SearchHit]:
        seen: set[str] = set()
        merged: list[SearchHit] = []
        for query in queries:
            outcome = await self.search(query, limit_each, recency)
            for hit in outcome.hits:
                key = hit.url.rstrip("/").lower()
                if key in seen:
                    continue
                seen.add(key)
                merged.append(hit)
        merged.sort(key=vn_rank, reverse=True)
        return merged


def is_excluded(hit: SearchHit) -> bool:
    host = hit.host
    return any(host == d or host.endswith(f".{d}") for d in settings.research_excluded_domains)


def vn_rank(hit: SearchHit) -> float:
    host = hit.host
    score = hit.score

    if any(host == d or host.endswith(f".{d}") for d in settings.research_preferred_domains):
        score += 3.0
    # Nguồn của nhóm ngành đang focus được cộng thêm — ưu tiên mềm, cộng dồn
    # lên điểm chung chứ không loại nguồn ngành khác.
    if settings.focus_industry and any(
        host == d or host.endswith(f".{d}") for d in settings.focus_preferred_domains
    ):
        score += 1.5
    if host.endswith(".vn"):
        score += 2.5
    if host.endswith(".com.vn") or host.endswith(".gov.vn"):
        score += 0.5

    text = f"{hit.title} {hit.snippet}"
    if any(ch in text for ch in "ăâđêôơưáàảãạấầẩẫậéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ"):
        score += 1.5
    lowered = text.lower()
    if "việt nam" in lowered or "vietnam" in lowered:
        score += 1.0

    return score


search_client = SearchClient()
