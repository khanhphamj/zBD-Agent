from dataclasses import dataclass, replace
from typing import Literal

from app.core.config import settings

Kind = Literal["reasoning", "vision"]

CONTEXT_WINDOW: dict[Kind, int] = {
    "reasoning": 1_000_000,
    "vision": 256_000,
}

MAX_OUTPUT: dict[Kind, int] = {
    "reasoning": 131_072,
    "vision": 8_192,
}

CHARS_PER_TOKEN = 3.2
INPUT_SAFETY = 0.75


@dataclass(frozen=True, slots=True)
class ModelProfile:
    key: str
    kind: Kind
    max_tokens: int
    temperature: float
    top_p: float
    timeout: int
    json_mode: bool = False
    input_share: float = 0.25

    @property
    def model(self) -> str:
        if self.kind == "vision":
            return settings.maas_vision_model
        return settings.maas_reasoning_model

    @property
    def context_window(self) -> int:
        return CONTEXT_WINDOW[self.kind]

    @property
    def output_cap(self) -> int:
        return MAX_OUTPUT[self.kind]

    @property
    def effective_max_tokens(self) -> int:
        return min(self.max_tokens, self.output_cap)

    @property
    def input_budget_tokens(self) -> int:
        usable = int(self.context_window * INPUT_SAFETY) - self.effective_max_tokens
        return max(2_000, int(usable * self.input_share))

    @property
    def input_budget_chars(self) -> int:
        return int(self.input_budget_tokens * CHARS_PER_TOKEN)

    def merged(
        self,
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        timeout: int | None = None,
        json_mode: bool | None = None,
    ) -> "ResolvedProfile":
        return ResolvedProfile(
            key=self.key,
            kind=self.kind,
            model=model or self.model,
            max_tokens=min(
                max_tokens if max_tokens is not None else self.max_tokens,
                self.output_cap,
            ),
            temperature=temperature if temperature is not None else self.temperature,
            top_p=top_p if top_p is not None else self.top_p,
            timeout=timeout if timeout is not None else self.timeout,
            json_mode=json_mode if json_mode is not None else self.json_mode,
            context_window=self.context_window,
            input_budget_chars=self.input_budget_chars,
        )


@dataclass(frozen=True, slots=True)
class ResolvedProfile:
    key: str
    kind: Kind
    model: str
    max_tokens: int
    temperature: float
    top_p: float
    timeout: int
    json_mode: bool
    context_window: int
    input_budget_chars: int


PROFILES: dict[str, ModelProfile] = {
    "research.plan": ModelProfile(
        key="research.plan",
        kind="reasoning",
        max_tokens=4_096,
        temperature=0.35,
        top_p=0.9,
        timeout=180,
        json_mode=True,
        input_share=0.30,
    ),
    # max_tokens từng để 65_536: model reasoning sinh liên tục ~15 phút rồi gateway
    # MaaS cắt bằng 502. JSON extract thực tế chỉ cần vài nghìn token. timeout đặt
    # dưới ngưỡng ~900s của gateway để lỗi hiện đúng bản chất thay vì 502 mơ hồ.
    # input_share 0.30 ≈ 704K ký tự: đủ cho ~88 nguồn ở mức 8K/nguồn. Đầu vào lớn
    # không phải nguyên nhân 502 (đo được: prompt 700KB trả về sau 25,8s).
    "research.extract": ModelProfile(
        key="research.extract",
        kind="reasoning",
        max_tokens=16_384,
        temperature=0.2,
        top_p=0.9,
        timeout=720,
        json_mode=True,
        input_share=0.30,
    ),
    "matching.solutions": ModelProfile(
        key="matching.solutions",
        kind="reasoning",
        max_tokens=8_192,
        temperature=0.35,
        top_p=0.9,
        timeout=300,
        json_mode=True,
        input_share=0.35,
    ),
    "proposal.outline": ModelProfile(
        key="proposal.outline",
        kind="reasoning",
        max_tokens=8_192,
        temperature=0.4,
        top_p=0.92,
        timeout=300,
        json_mode=True,
        input_share=0.40,
    ),
    "proposal.slide": ModelProfile(
        key="proposal.slide",
        kind="reasoning",
        max_tokens=6_144,
        temperature=0.5,
        top_p=0.95,
        timeout=180,
        json_mode=True,
        input_share=0.30,
    ),
    "editor.chat": ModelProfile(
        key="editor.chat",
        kind="reasoning",
        max_tokens=8_192,
        temperature=0.6,
        top_p=0.95,
        timeout=180,
        input_share=0.35,
    ),
    "skill.test": ModelProfile(
        key="skill.test",
        kind="reasoning",
        max_tokens=8_192,
        temperature=0.4,
        top_p=0.95,
        timeout=300,
        input_share=0.35,
    ),
    "product.crawl": ModelProfile(
        key="product.crawl",
        kind="reasoning",
        max_tokens=12_288,
        temperature=0.3,
        top_p=0.9,
        timeout=300,
        json_mode=True,
        input_share=0.30,
    ),
    "kb.summary": ModelProfile(
        key="kb.summary",
        kind="reasoning",
        max_tokens=768,
        temperature=0.3,
        top_p=0.9,
        timeout=120,
        input_share=0.30,
    ),
    "vision.ocr": ModelProfile(
        key="vision.ocr",
        kind="vision",
        max_tokens=16_384,
        temperature=0.1,
        top_p=0.9,
        timeout=600,
        input_share=0.60,
    ),
    "vision.describe": ModelProfile(
        key="vision.describe",
        kind="vision",
        max_tokens=2_048,
        temperature=0.2,
        top_p=0.9,
        timeout=180,
        input_share=0.30,
    ),
}

DEFAULT = ModelProfile(
    key="default",
    kind="reasoning",
    max_tokens=4_096,
    temperature=0.4,
    top_p=0.95,
    timeout=180,
)


def profile(key: str) -> ModelProfile:
    return PROFILES.get(key, replace(DEFAULT, key=key))


def resolve(
    key: str,
    *,
    admin: object | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    timeout: int | None = None,
    json_mode: bool | None = None,
) -> ResolvedProfile:
    base = profile(key)

    if admin is not None and base.kind == "reasoning":
        admin_model = getattr(admin, "model", None)
        admin_max = getattr(admin, "max_tokens", None)
        if model is None and admin_model:
            model = str(admin_model)
        if max_tokens is None and admin_max:
            max_tokens = min(int(admin_max), base.max_tokens)
    elif admin is not None and base.kind == "vision":
        admin_vision = getattr(admin, "vision_model", None)
        if model is None and admin_vision:
            model = str(admin_vision)

    return base.merged(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        timeout=timeout,
        json_mode=json_mode,
    )


def describe() -> list[dict[str, object]]:
    return [
        {
            "key": p.key,
            "kind": p.kind,
            "model": p.model,
            "contextWindow": p.context_window,
            "outputCap": p.output_cap,
            "maxTokens": p.effective_max_tokens,
            "inputBudgetChars": p.input_budget_chars,
            "temperature": p.temperature,
            "topP": p.top_p,
            "timeoutSeconds": p.timeout,
            "jsonMode": p.json_mode,
        }
        for p in PROFILES.values()
    ]
