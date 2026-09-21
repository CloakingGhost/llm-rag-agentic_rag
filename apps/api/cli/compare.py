"""CSV 표준답변과 챗봇 답변을 나란히 놓은 비교 파일을 만든다.

운영자가 눈으로 채점하기 위한 자료다. 자동 채점은 하지 않는다 (01_problem_definition.md 개정 4).

논문 7.2절의 '토큰 42% 절감'은 라우터가 무관한 질의를 걸러 낸 100건 평균이다.
정책 질의만 모아서는 그 효과가 드러나지 않으므로 도메인 밖 질문을 함께 섞는다.

    uv run python -m cli.compare --per-category 2 --ood 6 --model gpt-5.6-luna
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime

import httpx
import pandas as pd

from app.config import DEFAULT_MODEL, MODEL_CATALOG, get_settings
from app.pipelines.base import load_prompts
from kb_build.paths import REF_DIR, REPO_DIR

CSV_NAME = "한국소비자원_소비자상담 표준답변_20250725.csv"
API = "http://localhost:8100"
PIPELINE_LABEL = {"vanilla": "Vanilla LLM", "native": "Native RAG", "agentic": "Agentic RAG"}
OOD_CATEGORY = "도메인 밖"
NEWLINE = chr(10)

CATEGORY_NORMALIZE = {
    "계약해지": "계약해제·해지",
    "계약해제": "계약해제·해지",
    "계약해지·해제": "계약해제·해지",
    "계약해지?해제": "계약해제·해지",
    "계약해제.해지": "계약해제·해지",
    "품질(물품.용역)": "품질",
    "품질(물품/용역)": "품질",
    "가격.요금": "가격·요금",
    "안전(제품/시설)": "안전",
    "법.제도": "법·제도",
}

# 라우터가 걸러 내야 하는 질문들. 논문 7.2절의 '무관한 질의 선제 차단'을 재현하기 위한 것이다
OOD_QUESTIONS = [
    "서울에서 가장 유명한 맛집을 추천해 주세요.",
    "오늘 서울 날씨 어때요?",
    "파이썬으로 리스트를 정렬하는 방법을 알려 주세요.",
    "주말에 아이와 갈 만한 여행지를 추천해 주세요.",
    "이번 주 로또 번호를 뽑아 주세요.",
    "감기에 걸렸는데 어떤 약을 먹어야 하나요?",
    "회사 연차는 1년에 며칠까지 쓸 수 있나요?",
    "요즘 인기 있는 드라마가 뭔가요?",
]


def pick_questions(per_category: int, min_rows: int) -> pd.DataFrame:
    df = pd.read_csv(REF_DIR / CSV_NAME)
    for column in ("품목명", "구분", "질문", "답변"):
        df[column] = df[column].astype(str).str.strip()
    df["대분류"] = df["구분"].str.split("_").str[0].str.strip().replace(CATEGORY_NORMALIZE)

    counts = df["대분류"].value_counts()
    categories = [c for c, n in counts.items() if n >= min_rows]

    picked = []
    for category in categories:
        subset = df[df["대분류"] == category]
        # 품목이 겹치지 않게 앞에서부터 고른다 (재현을 위해 무작위를 쓰지 않는다)
        seen: set[str] = set()
        for _, row in subset.iterrows():
            if len(seen) >= per_category:
                break
            if row["품목명"] in seen:
                continue
            seen.add(row["품목명"])
            picked.append(row)
    return pd.DataFrame(picked)


def build_plan(per_category: int, min_rows: int, ood: int) -> list[dict]:
    """정책 질의와 도메인 밖 질의를 한 목록으로 만든다."""
    reject = load_prompts()["agentic"]["reject_message"].strip()
    plan = [
        {
            "category": row["대분류"],
            "item": row["품목명"],
            "question": row["질문"],
            "answer": row["답변"],
            "domain": "policy",
        }
        for _, row in pick_questions(per_category, min_rows).iterrows()
    ]
    plan += [
        {
            "category": OOD_CATEGORY,
            "item": "-",
            "question": question,
            "answer": reject,
            "domain": "ood",
        }
        for question in OOD_QUESTIONS[:ood]
    ]
    return plan


async def ask(client: httpx.AsyncClient, question: str, model: str, api_key: str) -> dict:
    """전체 파이프라인 보기와 같은 요청을 보내고 SSE를 모은다."""
    body = {"mode": "all", "question": question, "clientId": "compare-batch", "model": model}
    runs: dict[str, dict] = {}
    request_id = ""

    async with client.stream(
        "POST", f"{API}/api/chat", json=body, headers={"X-OpenAI-Key": api_key}, timeout=420
    ) as response:
        response.raise_for_status()
        event = ""
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                event = line[7:].strip()
            elif line.startswith("data: "):
                payload = json.loads(line[6:])
                if event == "request_created":
                    request_id = payload["requestId"]
                elif event == "run_done":
                    runs[payload["pipeline"]] = payload
                elif event == "run_error":
                    runs[payload["pipeline"]] = {
                        "pipeline": payload["pipeline"],
                        "outcome": "failed",
                        "answer": f"(오류: {payload['message']})",
                        "metrics": {},
                        "trace": {},
                    }
    return {"requestId": request_id, "runs": runs}


def _totals(rows: list[dict], domain: str | None = None) -> dict[str, dict[str, float]]:
    scoped = [r for r in rows if domain is None or r["domain"] == domain]
    totals: dict[str, dict[str, float]] = {p: {"tokens": 0.0, "cost": 0.0, "count": 0} for p in PIPELINE_LABEL}
    for row in scoped:
        for pipeline, run in row["runs"].items():
            metrics = run.get("metrics") or {}
            bucket = totals.setdefault(pipeline, {"tokens": 0.0, "cost": 0.0, "count": 0})
            bucket["tokens"] += (metrics.get("tokensIn") or 0) + (metrics.get("tokensOut") or 0)
            bucket["cost"] += metrics.get("costUsd") or 0
            bucket["count"] += 1
    return totals


def _avg(bucket: dict[str, float], key: str) -> float:
    return bucket[key] / bucket["count"] if bucket["count"] else 0.0


def _quote(text: str) -> str:
    return "> " + text.replace(NEWLINE, NEWLINE + "> ")


def _summary_block(rows: list[dict], policy: list[dict], ood: list[dict]) -> list[str]:
    overall = _totals(rows)
    out: list[str] = ["## 토큰·비용 요약", ""]
    out.append("| 파이프라인 | 총 토큰 | 질문당 평균 토큰 | 총 비용 | 질문당 평균 비용 |")
    out.append("|---|---|---|---|---|")
    for pipeline, label in PIPELINE_LABEL.items():
        bucket = overall[pipeline]
        out.append(
            f"| {label} | {bucket['tokens']:,.0f} | {_avg(bucket, 'tokens'):,.0f} | "
            f"${bucket['cost']:.4f} | ${_avg(bucket, 'cost'):.5f} |"
        )
    out.append("")

    native_avg = _avg(overall["native"], "tokens")
    agentic_avg = _avg(overall["agentic"], "tokens")
    if native_avg:
        delta = (native_avg - agentic_avg) / native_avg * 100
        word = "절감" if delta > 0 else "초과"
        out.append(f"Agentic이 Native 대비 토큰 **{abs(delta):.0f}% {word}** — 논문 7.2절 보고치는 42% 절감입니다.")
        out.append("")

    out.append("### 구분별 평균 토큰")
    out.append("")
    out.append("| 구분 | 질문 수 | Vanilla | Native RAG | Agentic RAG |")
    out.append("|---|---|---|---|---|")
    for label, domain, count in (("정책 질의", "policy", len(policy)), ("도메인 밖", "ood", len(ood))):
        if not count:
            continue
        bucket = _totals(rows, domain)
        out.append(
            f"| {label} | {count} | {_avg(bucket['vanilla'], 'tokens'):,.0f} | "
            f"{_avg(bucket['native'], 'tokens'):,.0f} | {_avg(bucket['agentic'], 'tokens'):,.0f} |"
        )
    out.append("")
    out.append(
        "도메인 밖 질문에서 Agentic은 라우터가 즉시 거절하므로 검색을 하지 않습니다. "
        "Native RAG는 질문을 가리지 않고 검색해 문서를 전량 주입합니다 (논문 부록 4-B)."
    )
    out.append("")

    if ood:
        rejected = sum(1 for r in ood if (r["runs"].get("agentic") or {}).get("outcome") == "rejected")
        out.append(f"라우터 거절: 도메인 밖 {len(ood)}건 중 **{rejected}건** 차단")
        out.append("")
    return out


def render_markdown(rows: list[dict], model: str, started: str) -> str:
    spec = MODEL_CATALOG[model]
    policy = [r for r in rows if r["domain"] == "policy"]
    ood = [r for r in rows if r["domain"] == "ood"]

    out: list[str] = ["# 표준답변 대조표", ""]
    out.append(f"- 생성: {started}")
    out.append(f"- 모델: **{spec.label}** (`{model}`)")
    out.append(f"- 질문 수: **{len(rows)}개** (정책 질의 {len(policy)} + 도메인 밖 {len(ood)})")
    out.append("- 각 질문마다 `전체 파이프라인 보기`와 같은 요청을 보내 3개 파이프라인 답변을 받았습니다.")
    out.append("- **정답은 CSV의 표준답변**입니다. 자동 채점은 하지 않았습니다. 눈으로 비교하십시오.")
    out.append("- 도메인 밖 질문의 정답은 '거절'입니다. 논문 7.2절의 라우터 효과를 보려고 섞었습니다.")
    out.append("")
    out.extend(_summary_block(rows, policy, ood))
    out.append("---")
    out.append("")

    for index, row in enumerate(rows, start=1):
        out.append(f"## {index}. [{row['category']}] {row['item']}")
        out.append("")
        out.append(f"**질문**  {NEWLINE}{row['question']}")
        out.append("")
        out.append("### 정답 (한국소비자원 표준답변)" if row["domain"] == "policy" else "### 정답 (거절해야 함)")
        out.append("")
        out.append(_quote(row["answer"]))
        out.append("")

        for pipeline, label in PIPELINE_LABEL.items():
            run = row["runs"].get(pipeline)
            if not run:
                continue
            metrics = run.get("metrics") or {}
            badge = {
                "answered": "답변",
                "rejected": "도메인 밖 거절",
                "unverified": "검증 미통과 (Critic 3회 미달)",
                "fallback": "폴백 (Critic 미달)",
                "failed": "실패",
                "canceled": "중지",
            }.get(run.get("outcome", ""), run.get("outcome", ""))
            stat = (
                f"{metrics.get('latencyMs', 0) / 1000:.1f}초 · "
                f"{(metrics.get('tokensIn', 0) + metrics.get('tokensOut', 0)):,}토큰 · "
                f"${metrics.get('costUsd', 0):.5f}"
            )
            out.append(f"### {label} — {badge}")
            out.append("")
            out.append(f"<sub>{stat}</sub>")
            out.append("")
            out.append(run.get("answer", "").strip() or "(빈 답변)")
            out.append("")

            retrievals = (run.get("trace") or {}).get("retrievals") or []
            used = [c for c in retrievals[-1]["chunks"] if c.get("selected")] if retrievals else []
            if used:
                out.append(f"<details><summary>참고한 근거 {len(used)}건</summary>")
                out.append("")
                for chunk in used:
                    out.append(f"- `{chunk['path']}` — {chunk['textSnapshot'][:120]}")
                out.append("")
                out.append("</details>")
                out.append("")

        out.append("---")
        out.append("")

    return NEWLINE.join(out)


async def main_async(args: argparse.Namespace) -> None:
    settings = get_settings()
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY가 없습니다.")

    plan = build_plan(args.per_category, args.min_rows, args.ood)
    print(f"질문 {len(plan)}개 (도메인 밖 {args.ood}개 포함), 모델 {args.model}")

    rows: list[dict] = []
    async with httpx.AsyncClient() as client:
        for index, item in enumerate(plan, start=1):
            started = time.monotonic()
            try:
                result = await ask(client, item["question"], args.model, settings.openai_api_key)
            except Exception as exc:  # noqa: BLE001
                print(f"  [{index}/{len(plan)}] 실패: {exc}")
                continue
            rows.append({**item, "requestId": result["requestId"], "runs": result["runs"]})
            outcomes = " ".join(f"{p[0].upper()}:{r.get('outcome', '?')}" for p, r in result["runs"].items())
            elapsed = time.monotonic() - started
            print(f"  [{index}/{len(plan)}] {item['category']}/{item['item']} {elapsed:.0f}초 {outcomes}")

    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M")
    out_dir = REPO_DIR / "compare"
    out_dir.mkdir(exist_ok=True)

    md_path = out_dir / f"대조표_{args.model}_{stamp}.md"
    md_path.write_text(
        render_markdown(rows, args.model, datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")), encoding="utf-8"
    )
    json_path = out_dir / f"대조표_{args.model}_{stamp}.json"
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"{NEWLINE}완료: {md_path}")
    print(f"      {json_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="표준답변 대조표 생성")
    parser.add_argument("--per-category", type=int, default=2, help="카테고리당 질문 수")
    parser.add_argument("--min-rows", type=int, default=10, help="이 건수 이상인 카테고리만 사용")
    parser.add_argument("--ood", type=int, default=6, help="섞을 도메인 밖 질문 수 (라우터 효과 측정용)")
    parser.add_argument("--model", default=DEFAULT_MODEL, choices=list(MODEL_CATALOG))
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
