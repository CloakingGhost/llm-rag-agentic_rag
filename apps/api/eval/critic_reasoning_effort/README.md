# Critic reasoning_effort 품질 비교

`docs/06_build_progress.md` 2026-10-02 "Critic 판정 품질 비교" 섹션의 근거 스크립트. "Self-RAG의
자기 판단을 reasoning_effort="none"으로 제한하면 역설 아니냐"는 질문에서 출발해, 실제
Critic 프롬프트·스키마 그대로 effort 생략(=medium)/none/low를 비교했다.

## 보는 법

- **프롬프트·케이스 원문**: [run.py](run.py)의 `CASES` 리스트 — question/references/answer/gold
  전부 여기 있다. `critic_system`/`critic_user`는 `app/pipelines/agentic.py`가 쓰는 것과
  완전히 같은 걸 `load_prompts()`로 그대로 가져온다(재구성 아님).
- **54건 전체 원문(요청에 들어간 system/user, 실제 응답 JSON, gold 대비 정답 여부, 지연,
  reasoning_tokens)**: `results/<날짜>.json`
- **요약 수치**: 실행할 때마다 콘솔에 찍히고, `docs/06_build_progress.md`에도 한 번 옮겨
  적어뒀다. LLM 샘플링이라 재실행하면 정확한 %는 달라질 수 있다 — "none이 가장 빠르고
  정확하고 안정적"이라는 **방향**은 두 번의 실행(2026-10-02) 모두 동일했다.

## 재실행

```bash
cd apps/api
TEST_KEY=$(grep '^K6_OPENAI_KEY_1=' ../../load/k6/.env | cut -d= -f2-) \
    uv run python eval/critic_reasoning_effort/run.py
```

`load/k6/.env`의 부하테스트 전용 BYOK 키를 그대로 쓴다 (gpt-5.6-luna, 54회 호출 기준 비용은
1센트 미만 수준).
