# LangFuse 트레이스 도착 확인

`check.py`가 LangFuse REST API로 최근 트레이스를 조회한다. 사용법·기대 결과는
`docs/10_verification_commands.md` 3단계 (3-3).

```bash
cd apps/api
uv run python eval/langfuse_traces/check.py --user lf-verify-1
```

`apps/api/.env`의 `LANGFUSE_*` 키를 읽는다. BYOK OpenAI 키가 트레이스 본문에 섞이지 않았는지도 함께 점검한다.
