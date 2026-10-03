# LangFuse SDK 오버헤드 측정

`docs/10_observability_load_test_plan.md` 3단계의 근거 스크립트. 실제 LLM 호출은 지연이 수 초씩 흔들려서
수 ms짜리 SDK 비용이 묻히므로, **응답이 즉시 오는 로컬 스텁 서버**를 상대로 같은 호출을 plain/traced 두
클라이언트로 반복해 차이만 잰다. `langfuse.openai`는 import하는 순간 openai SDK를 전역 패치하므로 두 arm은
별도 프로세스로 돌린다.

- 요청 1건 = 부모 span + OpenAI 호출 12번 (agentic 한 번이 12~15호출), 입력 약 2만 자(Native 전량 주입 크기)
- 순차 25요청(300호출) + 동시 20요청 x 5라운드(1200호출), plain/traced를 번갈아 3쌍
- traced arm의 트레이스는 실제 LangFuse Cloud로 나간다 — `environment="overhead-bench"`로 구분되니 대시보드에서 필터로 제외하면 된다.
  **1회 실행 ≈ 트레이스 380개 + 관측 5천 개 정도를 보내므로 Cloud 무료 사용량을 꽤 쓴다** (반복 실행 주의)
- 결과 원문: `results/<날짜>.json` (쌍별 수치 포함)

```bash
cd apps/api
uv run python eval/langfuse_overhead/run.py     # 약 2분, OpenAI 비용 0
```

읽는 법: 스텁은 프로세스 CPU가 한계까지 차는 최악 조건이다. 실제 서비스는 호출이 수 초 걸리고 초당 8호출
안팎이라, "동시 처리량 감소"는 비율이 아니라 **호출당 추가 CPU(ms)**로 환산해서 본다.
