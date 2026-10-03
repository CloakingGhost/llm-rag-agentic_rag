"""부하테스트 키의 OpenAI 레이트리밋을 확인한다 (큰 부하를 걸기 전에 돌리는 사전 점검).

우리 서버가 아니라 OpenAI 한도가 먼저 막히면 "서버 한계"로 오판한다. 한도는 계정 티어에 따라 바뀌므로 매번 읽는다.
모델마다 아주 작은 호출 1번(max_completion_tokens=16)씩만 보내 응답 헤더를 읽는다 — 비용은 무시할 수준이다.

    uv run --directory apps/api python ../../load/openai_limits.py

load/k6/.env 의 K6_OPENAI_KEY_1/2 를 읽는다 (키 값은 출력하지 않는다).
"""

from __future__ import annotations

from pathlib import Path

from dotenv import dotenv_values
from openai import OpenAI

ENV = Path(__file__).resolve().parent / "k6" / ".env"
CHAT_MODELS = ("gpt-5.6-luna", "gpt-4o-mini")  # 생성·판정(Luna), 리랭킹(고정 4o-mini)
EMBED_MODEL = "text-embedding-3-large"


def main() -> None:
    env = dotenv_values(ENV)
    keys = [(name, env.get(name)) for name in ("K6_OPENAI_KEY_1", "K6_OPENAI_KEY_2")]
    totals: dict[str, int] = {}
    for name, key in keys:
        if not key:
            print(f"{name}: 비어 있음 — 건너뜀")
            continue
        client = OpenAI(api_key=key)
        print(f"=== {name} ===")
        for model in CHAT_MODELS:
            h = client.chat.completions.with_raw_response.create(
                model=model, messages=[{"role": "user", "content": "hi"}], max_completion_tokens=16
            ).headers
            rpm, tpm = int(h["x-ratelimit-limit-requests"]), int(h["x-ratelimit-limit-tokens"])
            totals[model] = totals.get(model, 0) + rpm
            print(f"  {model:14s} {rpm:>6,} RPM  {tpm:>9,} TPM")
        h = client.embeddings.with_raw_response.create(model=EMBED_MODEL, input="hi").headers
        rpm = int(h["x-ratelimit-limit-requests"])
        totals[EMBED_MODEL] = totals.get(EMBED_MODEL, 0) + rpm
        print(f"  {EMBED_MODEL:14s} {rpm:>6,} RPM")

    print("\n키를 번갈아 쓰는 k6 전체 합계(RPM)")
    for model, rpm in totals.items():
        print(f"  {model:24s} {rpm:>7,}")
    print("\n참고: agentic 한 요청은 Luna를 5~11번 부른다. 합계 RPM / (호출 수 x 분당 요청 수)로 VU 상한을 가늠한다.")


if __name__ == "__main__":
    main()
