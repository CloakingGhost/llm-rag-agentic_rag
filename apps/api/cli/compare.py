"""CSV 표준답변과 챗봇 답변을 나란히 놓은 비교 파일을 만든다.

운영자가 눈으로 채점하기 위한 자료다. 자동 채점은 하지 않는다 (01_problem_definition.md 개정 4).

    uv run python -m cli.compare --per-category 2 --model gpt-5.6-luna
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pandas as pd

from app.config import DEFAULT_MODEL, MODEL_CATALOG, get_settings
from kb_build.paths import REF_DIR, REPO_DIR

CSV_NAME = "한국소비자원_소비자상담 표준답변_20250725.csv"
API = "http://localhost:8100"
PIPELINE_LABEL = {"vanilla": "Vanilla LLM", "native": "Native RAG", "agentic": "Agentic RAG"}

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


def render_markdown(rows: list[dict], model: str, started: str) -> str:
    spec = MODEL_CATALOG[model]
    out: list[str] = []
    out.append("# 표준답변 대조표")
    out.append("")
    out.append(f"- 생성: {started}")
    out.append(f"- 모델: **{spec.label}** (`{model}`)")
    out.append(f"- 질문 수: {len(rows)}개 (한국소비자원 표준답변 CSV에서 카테고리별로 추출)")
    out.append("- 각 질문마다 `전체 파이프라인 보기`와 같은 요청을 보내 3개 파이프라인 답변을 받았습니다.")
    out.append("- **정답은 CSV의 표준답변**입니다. 자동 채점은 하지 않았습니다. 눈으로 비교하십시오.")
    out.append("")

    total = {"vanilla": 0.0, "native": 0.0, "agentic": 0.0}
    for row in rows:
        for pipeline, run in row["runs"].items():
            total[pipeline] = total.get(pipeline, 0.0) + (run.get("metrics", {}).get("costUsd") or 0)

    out.append("## 비용 요약")
    out.append("")
    out.append("| 파이프라인 | 합계 비용 | 질문당 평균 |")
    out.append("|---|---|---|")
    for pipeline, label in PIPELINE_LABEL.items():
        amount = total.get(pipeline, 0.0)
        out.append(f"| {label} | ${amount:.4f} | ${amount / max(1, len(rows)):.5f} |")
    out.append("")
    out.append("---")
    out.append("")

    for index, row in enumerate(rows, start=1):
        out.append(f"## {index}. [{row['category']}] {row['item']}")
        out.append("")
        out.append(f"**질문**  \n{row['question']}")
        out.append("")
        out.append("### 정답 (한국소비자원 표준답변)")
        out.append("")
        out.append(f"> {row['answer'].replace(chr(10), chr(10) + '> ')}")
        out.append("")

        for pipeline, label in PIPELINE_LABEL.items():
            run = row["runs"].get(pipeline)
            if not run:
                continue
            metrics = run.get("metrics") or {}
            badge = {
                "answered": "답변",
                "rejected": "도메인 밖 거절",
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

            trace = run.get("trace") or {}
            retrievals = trace.get("retrievals") or []
            if retrievals:
                used = [c for c in retrievals[-1]["chunks"] if c.get("selected")]
                if used:
                    out.append("<details><summary>참고한 근거</summary>")
                    out.append("")
                    for chunk in used:
                        out.append(f"- `{chunk['path']}` — {chunk['textSnapshot'][:120]}")
                    out.append("")
                    out.append("</details>")
                    out.append("")

        out.append("---")
        out.append("")

    return "\n".join(out)


async def main_async(args: argparse.Namespace) -> None:
    settings = get_settings()
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY가 없습니다.")

    picked = pick_questions(args.per_category, args.min_rows)
    print(f"질문 {len(picked)}개 (카테고리 {picked['대분류'].nunique()}개), 모델 {args.model}")

    rows: list[dict] = []
    async with httpx.AsyncClient() as client:
        for index, (_, item) in enumerate(picked.iterrows(), start=1):
            started = time.monotonic()
            try:
                result = await ask(client, item["질문"], args.model, settings.openai_api_key)
            except Exception as exc:  # noqa: BLE001
                print(f"  [{index}/{len(picked)}] 실패: {exc}")
                continue
            rows.append(
                {
                    "category": item["대분류"],
                    "item": item["품목명"],
                    "question": item["질문"],
                    "answer": item["답변"],
                    "requestId": result["requestId"],
                    "runs": result["runs"],
                }
            )
            outcomes = " ".join(f"{p[0].upper()}:{r.get('outcome', '?')}" for p, r in result["runs"].items())
            print(f"  [{index}/{len(picked)}] {item['대분류']}/{item['품목명']} {time.monotonic() - started:.0f}초 {outcomes}")

    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M")
    out_dir = REPO_DIR / "compare"
    out_dir.mkdir(exist_ok=True)

    md_path = out_dir / f"대조표_{args.model}_{stamp}.md"
    md_path.write_text(
        render_markdown(rows, args.model, datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")), encoding="utf-8"
    )
    json_path = out_dir / f"대조표_{args.model}_{stamp}.json"
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n완료: {md_path}")
    print(f"      {json_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="표준답변 대조표 생성")
    parser.add_argument("--per-category", type=int, default=2, help="카테고리당 질문 수")
    parser.add_argument("--min-rows", type=int, default=10, help="이 건수 이상인 카테고리만 사용")
    parser.add_argument("--model", default=DEFAULT_MODEL, choices=list(MODEL_CATALOG))
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
