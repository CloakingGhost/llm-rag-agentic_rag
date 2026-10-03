"""LangFuse에 트레이스가 실제로 도착했는지 REST API로 확인한다 (docs/10_verification_commands.md 3단계).

- 최근 트레이스 목록 (이름·태그·세션·지연·관측 개수)
- 가장 최근 agentic 트레이스의 노드 구조 (memory → router → embed → extract_entities → rerank → generate → critic ...)
- BYOK OpenAI 키가 트레이스 본문 어디에도 없는지 (apps/api/.env의 OPENAI_API_KEY, load/k6/.env의 K6_OPENAI_KEY_*)

실행:
    cd apps/api
    uv run python eval/langfuse_traces/check.py                     # 최근 트레이스 10개
    uv run python eval/langfuse_traces/check.py --user lf-verify-1  # clientId로 거르기
    uv run python eval/langfuse_traces/check.py --environment loadtest   # 배포 테스트 서비스의 트레이스만
"""

from __future__ import annotations

import argparse
import base64
import json
import urllib.parse
import urllib.request
from pathlib import Path

from dotenv import dotenv_values

API_ENV = Path(__file__).resolve().parents[2] / ".env"
K6_ENV = Path(__file__).resolve().parents[4] / "load" / "k6" / ".env"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", help="clientId(= LangFuse userId)로 거른다")
    parser.add_argument("--environment", help="LangFuse environment로 거른다 (배포 테스트 서비스는 loadtest)")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    env = dotenv_values(API_ENV)
    base = env["LANGFUSE_BASE_URL"].rstrip("/")
    token = base64.b64encode(f"{env['LANGFUSE_PUBLIC_KEY']}:{env['LANGFUSE_SECRET_KEY']}".encode()).decode()

    def get(path: str) -> dict:
        req = urllib.request.Request(base + path, headers={"Authorization": f"Basic {token}"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)

    query = {"limit": args.limit}
    if args.user:
        query["userId"] = args.user
    if args.environment:
        query["environment"] = args.environment
    traces = get("/api/public/traces?" + urllib.parse.urlencode(query))["data"]
    print(f"트레이스 {len(traces)}개 ({base})")
    for t in traces:
        print(
            f"- {t['name']:16s} tags={t.get('tags')} session={t.get('sessionId')} "
            f"latency={t.get('latency')}s observations={len(t.get('observations', []))}"
        )

    agentic = next((t for t in traces if t["name"] == "chat:agentic"), None)
    if agentic is None:
        print(
            "\nchat:agentic 트레이스가 목록에 없다 — mode=all 또는 agentic으로 질문을 보낸 뒤 10초쯤 기다려 다시 실행"
        )
        return

    detail = get(f"/api/public/traces/{agentic['id']}")
    obs = sorted(detail["observations"], key=lambda o: o["startTime"])
    print(
        f"\nagentic 트레이스 구조 ({len(obs)}개 관측, totalCost=${detail.get('totalCost')}, latency={detail.get('latency')}s)"
    )
    for o in obs:
        extra = ""
        if o["type"] == "GENERATION":
            usage = o.get("usageDetails") or {}
            extra = f" model={o.get('model')} in={usage.get('input')} out={usage.get('output')}"
        indent = "" if o["type"] == "SPAN" else "  "
        print(f"{indent}[{o['type'][:3]}] {o['name']}{extra}")

    blob = json.dumps(detail, ensure_ascii=False)
    secrets = {"OPENAI_API_KEY (apps/api/.env)": dotenv_values(API_ENV).get("OPENAI_API_KEY")}
    if K6_ENV.exists():
        k6 = dotenv_values(K6_ENV)
        secrets.update({name: k6.get(name) for name in ("K6_OPENAI_KEY_1", "K6_OPENAI_KEY_2")})
    print("\nBYOK 키 유출 점검 (False여야 정상)")
    for name, value in secrets.items():
        if value:
            print(f"  {name} 가 트레이스에 포함됨?: {value in blob}")
    print(f"  'sk-proj' 문자열 포함?: {'sk-proj' in blob}")


if __name__ == "__main__":
    main()
