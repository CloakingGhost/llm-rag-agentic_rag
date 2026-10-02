"""Critic 판정 품질 비교: reasoning_effort 생략(=medium) vs none vs low.

docs/06_build_progress.md 2026-10-02 "Critic 판정 품질 비교" 섹션의 근거 스크립트.
app.pipelines.agentic와 완전히 같은 프롬프트·스키마·sampling_args()를 그대로 가져다 쓴다
(재구성이 아니라 실제 프로덕션 호출 경로를 그대로 재현) — 다른 건 effort 값뿐이다.

경계 사례 6개에 직접 매긴 "정답(gold)"을 두고, 각 case x effort 조합을 N회씩 반복해서
(a) gold 대비 정확도 (b) 같은 입력에 대한 판정 안정성(반복 간 일치율)을 본다.

실행:
    cd apps/api
    TEST_KEY=$(grep '^K6_OPENAI_KEY_1=' ../../load/k6/.env | cut -d= -f2-) \
        uv run python eval/critic_reasoning_effort/run.py

결과는 콘솔에 요약 출력하고, 54건(기본 REPEATS=3 기준) 전체 원문(프롬프트·응답·판정·지연·
reasoning_tokens)을 eval/critic_reasoning_effort/results/<날짜>.json에 남긴다.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from openai import OpenAI

from app.config import sampling_args
from app.pipelines.agentic import SCHEMAS
from app.pipelines.base import load_prompts

MODEL = "gpt-5.6-luna"
REPEATS = 3
EFFORTS = [None, "none", "low"]  # None = 생략 (공식 문서·실측 확인상 "medium"과 동일하게 거동)
RESULTS_DIR = Path(__file__).parent / "results"

FIELDS = ["is_grounded", "is_relevant", "is_complete"]

# 각 case: question/references/answer는 실제 critic_user 포맷 그대로. gold는 사람이 critic_system의
# 세 기준(grounded/relevant/complete)을 직접 적용해 미리 매긴 정답.
CASES = [
    {
        "name": "1_paraphrase_ok",
        "label": "정상 패러프레이즈",
        "question": "어제 산 이어폰에서 노이즈가 나는데 어떻게 해야 하나요?",
        "references": "[참조 1] (소비자분쟁해결기준, 유사도 0.91)\n전자제품 구입 후 7일 이내 발견된 하자는 제품 교환 또는 환불을 받을 수 있다.",
        "answer": "구입 후 7일 이내에 하자를 발견하셨다면 소비자분쟁해결기준에 따라 제품 교환이나 환불을 요청하실 수 있습니다. 판매처에 연락해 하자 내용을 알리고 교환 또는 환불을 신청하세요.",
        "gold": {"is_grounded": True, "is_relevant": True, "is_complete": True},
    },
    {
        "name": "2_subtle_hallucination",
        "label": "숫자 하나를 지어냄",
        "question": "온라인으로 산 옷을 입어봤는데 안 맞아서 반품하고 싶어요. 언제까지 가능한가요?",
        "references": "[참조 1] (전자상거래법, 유사도 0.88)\n전자상거래법상 청약철회는 상품 수령일로부터 7일 이내에 가능하다. 단, 소비자의 책임 있는 사유로 상품이 훼손된 경우 철회가 제한될 수 있다.",
        "answer": "전자상거래법에 따라 상품 수령일로부터 14일 이내에 청약철회가 가능합니다. 다만 상품에 손상이 있는 경우 반품이 제한될 수 있으니 택은 떼지 말고 보관하세요.",
        "gold": {"is_grounded": False, "is_relevant": True, "is_complete": True},
    },
    {
        "name": "3_off_topic_fabricated",
        "label": "문서와 무관한 내용을 지어냄",
        "question": "매달 자동결제되는 구독 서비스를 해지했는데 다음 달에 또 결제가 됐어요.",
        "references": "[참조 1] (전자상거래법, 유사도 0.80)\n정기구독 서비스는 해지 신청 후에도 이미 결제된 기간까지는 서비스가 유지되며, 환불은 원칙적으로 불가하다. 단, 서비스 미제공 기간에 대해서는 일할 계산하여 환불한다.",
        "answer": "가전제품은 구입 후 1년간 무상 A/S를 받으실 수 있습니다. 고장 시 가까운 서비스센터를 방문해 수리 접수를 하시면 됩니다.",
        "gold": {"is_grounded": False, "is_relevant": False, "is_complete": False},
    },
    {
        "name": "4_vague_missing_steps",
        "label": "구체적 절차 누락",
        "question": "택배로 받은 머그컵이 깨져서 왔어요. 어떻게 하면 되나요?",
        "references": "[참조 1] (소비자분쟁해결기준, 유사도 0.93)\n배송 중 파손된 상품은 수령 후 7일 이내 사진 증빙과 함께 판매자에게 교환 또는 환불을 요청할 수 있다. 판매자가 책임을 회피할 경우 소비자원에 분쟁조정을 신청할 수 있다.",
        "answer": "상품에 문제가 있으면 소비자분쟁해결기준에 따라 교환이나 환불을 받을 수 있습니다. 판매자에게 문의해보세요.",
        "gold": {"is_grounded": True, "is_relevant": True, "is_complete": False},
    },
    {
        "name": "5_fully_compliant",
        "label": "전부 통과 sanity",
        "question": "무료체험인 줄 알고 가입했는데 바로 유료 결제가 됐어요. 환불받을 수 있나요?",
        "references": "[참조 1] (전자상거래법, 유사도 0.95)\n무료체험 기간 중 해지하지 않고 그대로 두면 체험 종료 다음 날 자동으로 유료 결제가 전환된다. 체험 중 해지하면 결제되지 않는다. 이미 결제된 경우 결제일로부터 7일 이내 전액 환불이 가능하다.",
        "answer": "무료체험 기간이 끝난 뒤 자동으로 유료 결제가 전환된 것으로 보입니다. 결제일로부터 7일 이내라면 전액 환불을 요청하실 수 있으니, 고객센터에 결제 취소 및 환불을 신청해 보세요.",
        "gold": {"is_grounded": True, "is_relevant": True, "is_complete": True},
    },
    {
        "name": "6_retrieval_mismatch",
        "label": "검색 자체가 질문과 무관",
        "question": "환불도 안 해주고 연락도 안 되는 중고거래 사기를 당했어요. 어떻게 해야 하나요?",
        "references": "[참조 1] (숙박 이용약관, 유사도 0.42)\n반려동물 동반 숙박이 가능한 숙소는 예약 시 별도 표기되어 있다. 표기되지 않은 숙소에 반려동물을 데려가 발생한 문제는 숙소 측 책임이 아니다.",
        "answer": "반려동물 동반 가능 여부가 표기되지 않은 숙소라면 숙소 측에 책임을 묻기 어렵습니다. 예약 전 동반 가능 여부를 꼭 확인하세요.",
        "gold": {"is_grounded": True, "is_relevant": False, "is_complete": False},
    },
]


def effort_label(effort: str | None) -> str:
    return "omitted" if effort is None else effort


def call_once(client, critic_system: str, user: str, effort: str | None):
    kwargs = sampling_args(MODEL, effort=effort)
    t0 = time.monotonic()
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": critic_system}, {"role": "user", "content": user}],
        response_format={"type": "json_schema", "json_schema": SCHEMAS["critic"]},
        **kwargs,
    )
    dt = time.monotonic() - t0
    payload = json.loads(resp.choices[0].message.content or "{}")
    details = getattr(resp.usage, "completion_tokens_details", None)
    reasoning = getattr(details, "reasoning_tokens", None) if details else None
    return payload, dt, reasoning


def main() -> None:
    client = OpenAI(api_key=os.environ["TEST_KEY"])
    prompts = load_prompts()["agentic"]
    critic_system = prompts["critic_system"]
    critic_user_template = prompts["critic_user"]

    run_started = datetime.now(UTC).isoformat()
    rows: list[dict] = []

    for case in CASES:
        user = critic_user_template.format(
            question=case["question"], references=case["references"], answer=case["answer"]
        )
        for effort in EFFORTS:
            tuples = []
            for rep in range(REPEATS):
                payload, dt, reasoning = call_once(client, critic_system, user, effort)
                correct = {f: payload.get(f) == case["gold"][f] for f in FIELDS}
                row = {
                    "case": case["name"],
                    "case_label": case["label"],
                    "effort": effort_label(effort),
                    "rep": rep,
                    "prompt": {"system": critic_system, "user": user},
                    "gold": case["gold"],
                    "prediction": payload,
                    "correct_per_field": correct,
                    "all_correct": all(correct.values()),
                    "latency_sec": round(dt, 3),
                    "reasoning_tokens": reasoning,
                }
                rows.append(row)
                tuples.append(tuple(payload.get(f) for f in FIELDS))
            stable = len(set(tuples)) == 1
            print(f"[{case['name']:22s}] effort={effort_label(effort):8s} preds={tuples} stable={stable}")

    print()
    print("=" * 70)
    print("필드별 정확도 (gold 대비, effort별 전체 case x repeat 평균)")
    print("=" * 70)
    summary: dict[str, dict] = {}
    for effort in EFFORTS:
        key = effort_label(effort)
        subset = [r for r in rows if r["effort"] == key]
        field_acc = {f: sum(1 for r in subset if r["correct_per_field"][f]) / len(subset) for f in FIELDS}
        all_acc = sum(1 for r in subset if r["all_correct"]) / len(subset)
        avg_latency = sum(r["latency_sec"] for r in subset) / len(subset)
        avg_reasoning = sum((r["reasoning_tokens"] or 0) for r in subset) / len(subset)
        summary[key] = {
            "field_accuracy": field_acc,
            "all_correct_rate": all_acc,
            "avg_latency_sec": round(avg_latency, 2),
            "avg_reasoning_tokens": round(avg_reasoning, 1),
        }
        for f in FIELDS:
            print(f"  effort={key:8s} {f:12s} 정확도={field_acc[f]:.0%}")
        print(
            f"  effort={key:8s} 3필드 모두 정답={all_acc:.0%}"
            f"  평균지연={avg_latency:.2f}s  평균reasoning_tokens={avg_reasoning:.1f}"
        )
        print()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"{datetime.now(UTC).strftime('%Y-%m-%d_%H%M%S')}.json"
    out_path.write_text(
        json.dumps(
            {
                "run_started_utc": run_started,
                "model": MODEL,
                "repeats": REPEATS,
                "efforts": [effort_label(e) for e in EFFORTS],
                "cases": CASES,
                "summary": summary,
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"전체 원문(프롬프트 54건 + 응답 + 판정)을 저장했다: {out_path}")


if __name__ == "__main__":
    main()
