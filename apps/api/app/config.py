"""설정. 모델·단가·제한값은 모두 여기(환경변수)로 모은다 (04_system_design.md)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class ModelSpec:
    """논문 6장에서 비교한 3개 모델. 단가는 USD per 1M tokens."""

    id: str
    label: str
    input: float
    cached_input: float
    output: float
    note: str
    # GPT-5.6 계열은 temperature를 기본값(1)만 허용한다
    supports_temperature: bool = True
    # GPT-5.6 계열은 chat.completions에서 함수 도구를 쓰려면 reasoning_effort='none'이어야 한다
    tools_need_reasoning_none: bool = False


MODEL_CATALOG: dict[str, ModelSpec] = {
    "gpt-5.6-luna": ModelSpec(
        id="gpt-5.6-luna",
        label="GPT-5.6 Luna",
        input=0.20,
        cached_input=0.10,
        output=1.20,
        note="경량. 논문 6.2절 기준 GPT-4o 대비 약 92% 저렴",
        supports_temperature=False,
        tools_need_reasoning_none=True,
    ),
    "gpt-5.6-terra": ModelSpec(
        id="gpt-5.6-terra",
        label="GPT-5.6 Terra",
        input=2.00,
        cached_input=1.00,
        output=12.00,
        note="고성능. 논문 7.2절에서 Agentic 결합 시 최고 점수",
        supports_temperature=False,
        tools_need_reasoning_none=True,
    ),
    "gpt-4o": ModelSpec(
        id="gpt-4o",
        label="GPT-4o",
        input=2.50,
        cached_input=1.25,
        output=10.00,
        note="이전 세대 기준선. 논문 7.2절의 '인지 부하 역설'이 나타난 모델",
    ),
}

DEFAULT_MODEL = "gpt-5.6-luna"


def get_model(model_id: str | None) -> ModelSpec:
    return MODEL_CATALOG.get(model_id or DEFAULT_MODEL, MODEL_CATALOG[DEFAULT_MODEL])


def sampling_args(model_id: str | None) -> dict:
    """모델이 받아 주는 샘플링 인자만 돌려준다.

    재현성을 위해 temperature=0을 쓰고 싶지만 GPT-5.6 계열은 기본값만 허용한다.
    """
    return {"temperature": 0} if get_model(model_id).supports_temperature else {}


def tool_sampling_args(model_id: str | None) -> dict:
    """도구 호출용 인자.

    GPT-5.6 계열은 `chat.completions`에서 함수 도구를 쓰려면 추론을 꺼야 한다.
        Function tools with reasoning_effort are not supported ... set reasoning_effort to 'none'
    """
    args = sampling_args(model_id)
    if get_model(model_id).tools_need_reasoning_none:
        args["reasoning_effort"] = "none"
    return args


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: str = ""
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/cdq"
    allowed_origins: str = "http://localhost:3100"

    admin_id: str = "admin"
    admin_pw: str = "admin"

    max_concurrent_runs: int = 20
    run_timeout_sec: int = 60

    # 모델 (논문 7.1절의 3개 실험군과 같은 구성)
    chat_model: str = "gpt-5.6-luna"
    embed_model: str = "text-embedding-3-large"

    # 단가 (USD per 1M tokens). 바뀌면 price_version을 올린다.
    price_version: str = "2026-09"
    price_embedding: float = 0.13

    # 검색
    retrieve_top_k: int = 20
    rerank_top_k: int = 5
    critic_max_attempts: int = 3

    # Native RAG 주입 방식
    #   paper      : 논문 부록 4-B 그대로. 검색 결과를 정제 없이 전량 주입한다 (평균 1만 토큰)
    #   compressed : 리랭킹 상위 5개만 주입한다 (우리가 먼저 만든 방식. 토큰은 1/3)
    native_mode: str = "paper"
    native_top_k: int = 20

    # 세션 메모리 (논문 3.4 MemorySaver). 프로세스 메모리에만 두므로 상한이 필요하다
    session_max: int = 500
    session_ttl_min: int = 120

    # 도구 호출 (논문 3.5 Lightweight ReAct + 9.3 Mock SQLite DB).
    # 논문 4.1절이 최종 평가에서 이 모듈을 껐으므로 기본값도 꺼 둔다.
    # 켜면 라우터가 3분기(policy_inquiry / system_action / out_of_domain)가 된다
    tools_enabled: bool = False
    tool_max_steps: int = 3
    mock_db_path: str = "mock_store.db"

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def cost_usd(tokens_in: int, tokens_out: int, cached_in: int = 0, model_id: str | None = None) -> float:
    """모델별 단가로 계산한다. 캐시된 입력은 단가가 절반이다 (data_feasibility.md §1)."""
    spec = get_model(model_id)
    fresh_in = max(tokens_in - cached_in, 0)
    return (fresh_in * spec.input + cached_in * spec.cached_input + tokens_out * spec.output) / 1_000_000
