# 목업 하드코딩 목록

- 목적: 프레임 목업에 넣었던 **가짜 값과 가짜 동작**을 추적한다. API 연결이 끝나면 한 줄씩 지운다.
- 상태: **2026-09-21 전부 교체 완료.** 아래 표는 무엇이 무엇으로 바뀌었는지 남기는 기록이다.
- 확인 명령: `rg "MOCK M-" apps/web/src` → 결과 0건이어야 한다.

## 교체 결과

| ID | 목업이 하던 일 | 무엇으로 바뀌었나 |
|---|---|---|
| M-01 | 예시 질문 칩 (`lib/mock/data.ts`) | `lib/examples.ts`의 제품 문구로 승격. 목업 아님 |
| M-02 · M-02b | 파이프라인별 고정 답변 | SSE `run_done.answer` |
| M-03 | 검색된 근거 청크 | SSE `run_done.trace.retrievals[].chunks` (서버가 ChromaDB·그래프에서 찾은 실제 청크) |
| M-04 | 라우터·분쟁 대상·Critic 판정 | SSE `run_done.trace` |
| M-05 | 지연·토큰 수치 | SSE `run_done.metrics` (실행 중 실제로 측정) |
| M-06 | 브라우저에서 비용 계산 | **서버가 계산한 `metrics.costUsd`** 를 그대로 표시. 단가는 서버 설정(`price_version`)에 있고 실행 레코드에 함께 저장 |
| M-07 | 타이머로 흉내 낸 SSE 엔진 | `lib/api.ts`의 `streamChat()` — `fetch` 스트림으로 실제 SSE 파싱 |
| M-08 | 키 형식만 확인 | `POST /api/key/validate` (OpenAI 모델 목록 조회) |
| M-09 | 검수용 시나리오 전환 패널 | **파일 삭제.** 상태는 실제 실행 결과로 나타남 |
| M-10 | 워밍업 타이머 | `GET /api/health` |
| M-11 | 소개 페이지 지식베이스 출처 | `GET /api/kb/info` (청크 수·그래프 노드 수도 함께 표시) |
| M-12 | 대시보드 집계 | `GET /api/dashboard?period=` |
| M-13 | 브라우저에서 비밀번호 비교 | `POST /api/admin/login` → **서버 세션 쿠키**. 브라우저에는 로그인 표시만 남김 |
| M-14 | 로그 목록·상세 | `GET /api/admin/logs`, `GET /api/admin/logs/{requestId}` |

삭제한 파일: `lib/mock/data.ts`, `lib/mock/engine.ts`, `lib/mock/dashboard.ts`, `lib/mock/logs.ts`, `components/chat/mock-controls.tsx`

## 교체하며 실제로 챙긴 것

1. **비용 계산을 화면에서 걷어냈다** (M-06). 서버가 캐시된 입력 토큰까지 반영해 계산한다.
2. **관리자 인증을 서버로 옮겼다** (M-13). 목업 때 경고로 적어 둔 위험이 해소됐다.
3. **로그 필터가 서버 쿼리로 바뀌었다.** 브라우저에서 배열을 거르지 않는다.
4. **실행 ID를 서버가 매긴다.** `request_created` 이벤트로 받은 ID로 교체해서 중지·로그 추적이 맞물린다.
5. **폴백 재사용은 서버가 처리한다.** 전체 보기에서 Agentic이 폴백하면 같은 요청의 Native 결과를 재사용한다.

## 남은 설정값

| 값 | 위치 | 비고 |
|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | `apps/web/.env.local` | 로컬 `http://localhost:8100`, 배포 시 Cloud Run 주소 |
| 예시 질문 문구 | `lib/examples.ts` | 제품 문구. 바꾸려면 여기서 |
