# 시스템 구조 설계와 기능 명세

- 작성일: 2026-09-20
- 방법: 운영자(나) 인터뷰, 5단계 (구조 설계 → API 명세 → 배치 명세 → I/F 명세 → 안정성 대비)
- 선행 문서: [01_problem_definition.md](01_problem_definition.md), [02_business_data_analysis.md](02_business_data_analysis.md), [03_ux_ia.md](03_ux_ia.md)
- 표기: **[결정]** 인터뷰에서 정함 / **[기본값]** Claude가 정함. 이견이 있으면 바꿀 수 있음

---

## 4-1. 구조 설계

### 기술 스택 [결정]

| 구성 | 선택 | 이유 |
|---|---|---|
| 프론트 | **Next.js (React)** + TailwindCSS + shadcn/ui | 5차 UI 방침 |
| 백엔드 | **FastAPI (Python)** | LangGraph·ChromaDB·NetworkX·pdfplumber가 파이썬 생태계 |
| DB | **PostgreSQL** (Neon 무료) | 무기한 보관과 대시보드 집계. 무료 한도 확인 결과 Railway 내장은 불가 (§4-1-2) |
| 실시간 전달 | **SSE** | 단계 표시가 단방향이라 충분 |
| 배포 | 프론트 **Vercel Hobby**, 백엔드 **Google Cloud Run**, DB **Neon** | 모두 무료 한도 안에서 운영 가능 (§4-1-2) |
| 지식베이스 산출물 | **컨테이너 이미지에 포함** | 재배포해도 디스크가 초기화되지 않음. 개정 때만 다시 배포 |
| 버전 정책 | **최신 메이저 고정** | 아래 표의 버전으로 고정하고, 올릴 때는 의도적으로 올림 |

### 4-1-2. 무료 계정 기준 배포 제약 (2026-09-20 확인) [결정]

| 후보 | 무료 사양 | 판단 |
|---|---|---|
| Vercel Hobby | 전송 100GB/월, 비상업적 용도만 | **채택** (프론트). 이 프로젝트는 연구·전시 목적이라 조건에 맞음 |
| **Google Cloud Run** | 월 200만 요청, 180k vCPU-초, 360k GiB-초 (us-central1 등), 0까지 축소 | **채택** (백엔드). 카드 등록은 필요하지만 한도 안에서는 과금 없음 |
| **Neon Free** | 저장 0.5GB, 월 100 CU-시간, 5분 뒤 자동 절전 | **채택** (DB) |
| Railway Free | RAM 0.5GB, **월 $1 크레딧**, 이미지 4GB | 탈락. 백엔드와 DB 상시 가동을 $1로 감당 못 함 |
| Render Free | RAM 512MB, 15분 뒤 슬립, **무료 Postgres 30일 만료** | 탈락. 로그 무기한 보관과 충돌 |
| Koyeb Free | 512MB, 1시간 뒤 축소, 무료 DB는 월 5시간 | 탈락 (DB 한도) |
| Fly.io | 2026년 현재 무료 한도 없음 (체험 2 VM-시간) | 탈락 |

**Cloud Run 설정값** [기본값]

| 항목 | 값 | 이유 |
|---|---|---|
| 리전 | `us-central1` | 무료 한도 적용 리전 |
| 메모리 / CPU | **1 GiB / 1 vCPU** | ChromaDB·그래프·LangChain을 올리면 512MB는 빠듯함 |
| 최소 인스턴스 | 0 | 무료 한도를 지키려면 상시 가동 금지 |
| 최대 인스턴스 | 3 | 폭주 시 한도 초과 방지 |
| 요청 타임아웃 | 120초 | 실행 예산 60초보다 여유 |
| 동시 요청 | 20 | 서버 동시 실행 상한과 동일 |

**한도 환산** [기본값]: 요청 1건이 1 vCPU·1GiB를 10초 쓴다고 보면 월 18,000건(vCPU 기준) / 36,000건(메모리 기준)까지 무료입니다. Neon 0.5GB는 검색 스냅샷을 포함해 요청 약 1만 건 분량입니다.

**완전 무료는 아닌 부분**: 컨테이너 이미지(약 1GB)를 Artifact Registry에 두는 비용이 월 $0.1 안팎 발생할 수 있습니다. Cloud Run 사용료는 한도 안에서 0원입니다.

**콜드 스타트** [결정]: 안 쓰면 0으로 축소되므로 첫 요청이 느립니다. 화면이 열리면 곧바로 `/api/health`를 한 번 호출해 **미리 깨우고**, 아직 준비 중이면 "서버를 깨우는 중"을 표시합니다.

### 4-1-3. 관측 도구(LangSmith·Langfuse) 판단 [결정, 2026-09-20]

**결론: LangSmith·Langfuse를 쓰지 않는다. 자체 계측만으로 간다.** 필요해지면 IMP-16에서 다시 검토한다.

정확도 때문에 도입할 이유는 없습니다. 두 도구가 보여주는 토큰도 결국 `usage_metadata`에서 오고, 비용도 각자의 단가표로 계산한 값입니다. 우리가 직접 기록하는 값보다 더 정확해지지 않습니다. 실제 이득은 **누락 없는 자동 수집과 트레이스 디버깅 화면**입니다.

| 우려 | 내용 |
|---|---|
| 개인정보 이전 | 공개 URL로 받은 **분쟁 질문 원문과 답변이 제3자 SaaS로 전송**됨. 로그를 관리자 전용으로 막은 결정과 충돌 |
| Cloud Run과의 상성 | 기본 설정(요청 기반 과금)은 **응답을 보낸 뒤 CPU가 스로틀**됨. 트레이스를 백그라운드로 보내면 유실될 수 있음. 막으려면 응답 전에 flush(지연 증가)하거나 인스턴스 기반 과금(무료 한도 빨리 소진) |
| 무료 한도·보관 기간 | LangSmith Developer는 월 5,000 트레이스·14일 보관, 초과 시 수집 중단. Langfuse Hobby는 월 50k 유닛·30일 보관. 우리는 **무기한 보관**이 요구사항이라 어차피 자체 DB가 원본이어야 함 |
| 자체 호스팅 비용 | Langfuse v3+는 ClickHouse·Redis·S3·Postgres가 필요하고 RAM 4GB 이상을 권장. 무료 배포 계획과 맞지 않음 |
| 이중 출처 | 대시보드·로그 화면을 외부 API에서 읽으면 결합도·레이트리밋·지연이 생김. 화면이 요구하는 열(경로·벡터·그래프·사용)은 그쪽 스키마와도 다름 |

**적용 방식**: 관측 도구 의존성을 아예 넣지 않습니다. 디버깅은 노드마다 남기는 자체 로그(요청·실행·단계·검색 결과)와 개발 중 콘솔 출력으로 합니다. 이 로그는 관리자 로그 화면에서 그대로 볼 수 있으므로, 별도 트레이스 화면 없이도 어느 노드에서 무엇이 오갔는지 추적할 수 있습니다.

### 라이브러리와 버전 (2026-09-20 기준 최신, 고정) [결정]

**백엔드 `apps/api` — Python 3.13, 패키지 관리 `uv` 0.8**

| 라이브러리 | 버전 | 용도 |
|---|---|---|
| fastapi | 0.141 | REST + SSE |
| uvicorn[standard] | 0.53 | ASGI 서버 |
| langgraph | 1.2 | Agentic RAG 순환 그래프 (StateGraph, 조건부 분기) |
| langchain-openai | 1.6 | LLM 호출과 구조화 출력. 논문이 LangChain·LangGraph 기반이라 동일하게 씀 |
| openai | 3.16 | 키 검증(모델 목록 조회)과 임베딩 직접 호출 |
| chromadb | 1.5 | 벡터 저장소. 런타임은 산출물을 **읽기 전용**으로 로드 |
| networkx | 3.6 | 지식 그래프. Louvain 군집은 내장 `louvain_communities` 사용 |
| pdfplumber | 0.11 | 별표 PDF 좌표 파싱 |
| sqlalchemy | 2.0 | ORM (async) |
| asyncpg | 0.31 | PostgreSQL 드라이버 |
| alembic | 1.20 | 마이그레이션 |
| pydantic | 2.13 | 노드 스키마(`IntentResult`·`DisputeTarget`·`CriticResult`), API 모델 |
| pydantic-settings | 2.15 | 환경변수 설정 |
| pyyaml | 6.0 | 프롬프트 세트 YAML (DC-2) |
| tenacity | 9.1 | 429·5xx 지수 백오프 재시도 |
| typer | 0.27 | `kb` CLI |
| orjson | 3.12 | SSE·로그 직렬화 |
| ruff | 0.16 | 린트·포매터 |
| pytest / pytest-asyncio / httpx | 9.1 / 1.4 / 0.28 | 테스트 |

SSE는 별도 라이브러리 없이 `StreamingResponse`로 직접 만듭니다 [기본값].

**프론트 `apps/web` — Node 22, 패키지 관리 `pnpm` 10**

| 라이브러리 | 버전 | 용도 |
|---|---|---|
| next | 16.3 | App Router |
| react / react-dom | 19.3 | |
| typescript | 7.0 | strict 모드 |
| tailwindcss | 4.3 | |
| shadcn (CLI) | 4.21 | 기본 테마 컴포넌트 추가 |
| lucide-react | 1.47 | 아이콘 (shadcn 기본) |
| @tanstack/react-query | 5.103 | 대시보드·로그 조회 |
| recharts | 3.10 | 대시보드 차트 (shadcn chart가 쓰는 라이브러리) |
| zod | 4.6 | API 응답 스키마 검증 |

챗봇 화면은 헤더에 키를 실어야 하므로 `fetch` 스트림으로 SSE를 직접 읽습니다. 나머지 조회는 TanStack Query를 씁니다 [기본값].

**개발·배포 도구**

| 도구 | 용도 |
|---|---|
| Docker 28 | 백엔드 이미지(지식베이스 산출물 포함), 로컬 Postgres |
| GitHub Actions | PR에서 ruff·pytest·tsc·eslint 실행 [기본값] |
| Vercel / Railway | GitHub 연결 자동 배포 |

### 환경변수 [기본값]

| 대상 | 이름 | 설명 |
|---|---|---|
| api | `DATABASE_URL` | Neon 연결 문자열. 풀링 주소를 쓰고 asyncpg는 `statement_cache_size=0`으로 설정 [기본값] |
| api | `ALLOWED_ORIGINS` | 프론트 도메인 |
| api | `ADMIN_ID` / `ADMIN_PW` | 기본 `admin` / `admin` (IMP-10에서 해시로 전환) |
| api | `MAX_CONCURRENT_RUNS` | 기본 20 |
| api | `RUN_TIMEOUT_SEC` | 기본 60 |
| api | `KB_BUILD_ID` | 비우면 매니페스트의 활성 빌드 |
| api | `NATIVE_MODE` | `paper`(기본, 논문 부록 4-B 전량 주입) 또는 `compressed` |
| api | `SESSION_MAX` / `SESSION_TTL_MIN` | 세션 메모리 상한. 기본 500개 / 120분 |
| api | `TOOLS_ENABLED` | 도구 호출·가상 주문 DB. **기본 `false`** (논문 4.1의 최종 구성) |
| api | `TOOL_MAX_STEPS` | ReAct 루프 상한. 기본 3 |
| cli (로컬) | `OPENAI_API_KEY` | 빌드 전용 운영자 키 |
| web | `NEXT_PUBLIC_API_BASE_URL` | 백엔드 주소 |

### 버전 관련 위험 [기본값]

TypeScript 7은 컴파일러가 바뀐 새 메이저라 shadcn CLI나 ESLint 플러그인이 아직 맞지 않을 수 있습니다. 5차 프로젝트 구성 단계에서 막히면 **프론트만 TypeScript 5.x로 낮추고** 나머지는 그대로 둡니다. 백엔드 스택에는 영향이 없습니다.

### 구성도

```text
[브라우저]  Next.js (Vercel Hobby)
   │  REST + SSE,  헤더 X-OpenAI-Key (요청마다 전달, 저장 안 함)
   ▼
[FastAPI] (Cloud Run, us-central1)
   ├─ 파이프라인 3종 (LangGraph)  ── OpenAI API ◀ 사용자 키
   ├─ 지식베이스 로더 (읽기 전용) ── 이미지에 포함된 산출물
   │     ChromaDB(벡터) · NetworkX(그래프) · 청크 JSONL
   └─ 로그·집계 ─────────────────▶ PostgreSQL (Neon)

[운영자 CLI] (로컬) ── OpenAI API ◀ 운영자 키
   └─ 지식베이스 빌드 → 산출물 커밋 → 재배포
```

### 저장소 구조 [기본값]

```text
pnpm-workspace.yaml  프론트 워크스페이스
docker-compose.yml   로컬 Postgres + api
.github/workflows/   ruff · pytest · tsc · eslint
apps/web/            Next.js (pnpm)
apps/api/            FastAPI (uv, pyproject.toml)
  app/pipelines/     vanilla.py · native_rag.py · agentic/ (graph.py, nodes/)
  app/kb/            로더, 검색기(벡터·그래프·RRF·리랭커)
  app/api/           라우트 (chat, dashboard, admin, kb)
  app/db/            모델·마이그레이션
  cli/               kb 빌드 CLI
  kb_artifacts/      빌드 산출물 (이미지에 포함)
  config/            prompts/*.yaml · settings.yaml (모델·단가·타임아웃·상한)
docs/                기획 문서
ref/                 원문 자료
```

### 병렬 실행 [결정]

전체 파이프라인 보기는 **요청 1건으로 서버가 3개를 동시 실행**하고, 한 SSE 스트림에 파이프라인 꼬리표를 붙여 내보냅니다. Agentic이 폴백할 때는 같은 요청 안에서 돌고 있는 Native 결과를 기다렸다가 재사용하므로 추가 호출이 없습니다.

---

## 4-2. API 명세

공통: 오류는 `{ "error": { "type": ..., "message": ... } }` 형식. 사용자 키는 `X-OpenAI-Key` 헤더로만 받습니다.

### POST `/api/chat` (SSE)

```jsonc
// 요청 (헤더: X-OpenAI-Key)
{
  "mode": "all | vanilla | rag | agentic",
  "question": "...",
  "clientId": "익명 브라우저 ID",
  "model": "gpt-5.6-luna | gpt-5.6-terra | gpt-4o",   // 없으면 기본 모델
  "conversationId": "conv_... | null",                // 같은 대화의 후속 질문이면 앞 응답의 값
  "tools": null                                       // 도구 사용 여부. 생략하면 서버 기본값(꺼짐)
}
```

응답은 `text/event-stream`입니다. 브라우저는 헤더를 붙여야 하므로 `EventSource` 대신 `fetch` 스트림으로 읽습니다 [기본값].

| 이벤트 | 내용 |
|---|---|
| `request_created` | `{ requestId, conversationId, turn, tools, buildId, model, runs: [{ runId, pipeline }] }` — `conversationId`는 다음 질문에 그대로 실어 보낸다 |
| `run_step` | `{ runId, pipeline, node, attempt, elapsedMs }` — 화면은 이 이벤트로 **로딩 여부만** 판단하고 단계 이름은 표시하지 않음 (5차 검수에서 변경). 이벤트 자체는 로그 기록에 필요하므로 유지 |
| `run_done` | `{ runId, pipeline, outcome, answer, metrics: { latencyMs, tokensIn, tokensOut, costUsd }, trace }` — **검색 근거 원문과 Critic 판정은 여기에 한 번에** [결정] |
| `run_error` | `{ runId, pipeline, errorType, message }` |
| `done` | `{ requestId, conversationId, status }` |

`outcome`: `answered` / `rejected` / `fallback` / `failed` / `canceled`
`errorType`: `invalid_key` / `quota_exceeded` / `timeout` / `kb_unavailable` / `busy` / `unknown`

### 그 외

| 메서드 | 경로 | 요청 | 응답 |
|---|---|---|---|
| POST | `/api/chat/{requestId}/cancel` | `{ clientId }` | `{ canceled: true }`. 연결이 끊겨도 서버가 중지함 [결정] |
| POST | `/api/chat/conversation/{conversationId}/end` | — | `{ closed: true }`. 세션 메모리를 버린다 (논문 3.4·9.3) |
| GET | `/api/models` | — | `{ default, models: [{ id, label, note, pricePer1M }] }` |
| POST | `/api/key/validate` | 헤더 키 | `{ valid, reason? }` — 모델 목록 조회, 비용 없음 |
| GET | `/api/kb/info` | — | `{ buildId, builtAt, status: "ok\|unavailable", documents: [{ name, effectiveDate }], chunkCount }` |
| GET | `/api/dashboard?period=all\|7d\|30d&model=...` | — | 파이프라인별·모델별 요약, 노드별 소요 시간, 경로 분포, 표본 수 |
| POST | `/api/admin/login` | `{ id, pw }` | 세션 쿠키 설정 |
| POST | `/api/admin/logout` | — | 쿠키 삭제 |
| GET | `/api/admin/logs` | 필터: `mode, pipeline, outcome, from, to, q, page` | 목록 + 전체 건수 |
| GET | `/api/admin/logs/{requestId}` | — | 요청 + 실행 + 단계 + 검색 결과(원문 스냅샷) |
| GET | `/api/health` | — | `{ status, kb: "ok\|unavailable", db: "ok" }` |

---

## 4-3. 배치 작업 명세 (W1 지식베이스 구축)

운영자가 로컬에서 실행하고, 산출물을 커밋해 재배포합니다. 운영자 키는 환경변수 `OPENAI_API_KEY`로 받습니다.

### 명령 [결정]

```bash
kb build                         # 전체 실행
kb build --only parse            # 한 단계만 다시 (parse | chunk | embed | graph)
kb build --no-cache              # 보정 캐시 무시
kb report                        # 마지막 빌드의 검증 결과 요약
kb activate <buildId>            # 활성 빌드 지정 (되돌리기 포함)
```

### 단계와 산출물

| 단계 | 하는 일 | 산출물 |
|---|---|---|
| parse | 별표 규칙 파싱 → 검증(V1~V6) → 실패한 표만 LLM 보정 → 재검증 | `tables.json`, `parse_report.json` |
| chunk | 행 단위 청크 + 경로 메타데이터, 법령 조문 청크 | `chunks.jsonl` |
| embed | `text-embedding-3-large`로 임베딩 | `chroma/` |
| graph | 청크에서 개념·관계 추출(G1) → NetworkX → Louvain | `graph.json` |
| finalize | 매니페스트 작성, 활성 빌드 기록 | `manifest.json` (빌드 ID, 원문 해시, 청크 수, 노드 수, 파일 해시) |

### 재구축 방식 [결정]

전체를 다시 만들되, **원문이 그대로인 표의 LLM 보정 결과는 캐시를 재사용**합니다. 캐시 키는 표 영역 원문의 해시입니다. `--no-cache`로 무시할 수 있습니다.

산출물은 `kb_artifacts/<buildId>/`에 쌓이고, `manifest.json`의 활성 빌드만 서버가 읽습니다. 이전 빌드를 남겨 두면 `kb activate`로 되돌릴 수 있습니다 [기본값].

**예상 비용** [기본값]: 표 보정과 그래프 구축을 합쳐 `gpt-4o-mini` 기준 1~2달러, 임베딩은 1달러 미만으로 추정합니다. 운영자 부담입니다.

---

## 4-4. I/F 명세

### 외부: OpenAI

| 항목 | 규격 |
|---|---|
| 사용 API | Chat Completions (Router·Memory·Critic은 JSON 스키마 강제), Embeddings |
| 모델 | 전 노드 `gpt-4o-mini`, 임베딩 `text-embedding-3-large` — **설정 파일** [기본값] |
| 키 | 실행 시점은 사용자 키(헤더), 빌드 시점은 운영자 키(환경변수) |
| 키 취급 | 메모리에서만 사용. DB·로그·오류 리포트에 기록 금지. 노출 가능 지점에서는 마스킹 [기본값] |
| 타임아웃 | 실행 1건 60초 (폴백 포함) |
| 오류 매핑 | 401 → `invalid_key` / 429 `insufficient_quota` → `quota_exceeded` / 429 rate limit → 재시도 / 5xx·연결 오류 → 재시도 / 초과 → `timeout` / 그 외 → `unknown` |

### 내부

| 구간 | 규격 |
|---|---|
| Next.js ↔ FastAPI | REST + SSE. CORS는 배포 도메인만 허용 [기본값] |
| FastAPI ↔ PostgreSQL | requests / runs / steps / retrieved_chunks (2차 정의). 인덱스: `requests.created_at`, `requests.client_id`, `runs.request_id`, `runs.pipeline`, `runs.outcome`, `steps.run_id` [기본값] |
| FastAPI ↔ 지식베이스 | 기동 시 매니페스트를 읽고 메모리에 로드. 런타임 쓰기 없음 |
| CLI ↔ 산출물 | 빌드 ID 폴더 단위로 기록, 매니페스트로 활성화 |

---

## 4-5. 안정성 대비

| 항목 | 정책 |
|---|---|
| **사용자·권한 관리** | 일반 사용자는 로그인 없음. 익명 브라우저 ID로만 구분. 관리자는 하드코딩 단일 계정(`admin`/`admin`), 숨긴 주소. 환경변수·해시로 옮기는 것은 IMP-10 |
| **인증 만료** | 관리자 세션은 **브라우저를 닫으면 만료** [결정] (세션 쿠키, HttpOnly·SameSite=Lax·Secure). 만료 후 접근하면 `/admin`으로 이동. 사용자 키가 중간에 무효가 되면 `invalid_key`로 끝내고 화면에서 키 재입력을 안내 |
| **API 변경** | 모델 ID·엔드포인트·토큰 단가를 설정 파일로 관리. 비용 계산에 쓴 **단가 버전을 실행 레코드에 함께 저장**해서 나중에 단가가 바뀌어도 과거 수치가 흔들리지 않게 함 [기본값] |
| **데이터 오류** | 기동 시 매니페스트 해시·청크 수·컬렉션 수를 점검. 문제가 있으면 **RAG·Agentic만 비활성**하고 Vanilla·대시보드·로그는 그대로 동작 [결정]. 화면에는 안내 배너를 띄우고 `/api/kb/info`가 `unavailable`을 반환. 검색 결과 스냅샷은 청크당 2,000자, 실행당 20개로 상한 [기본값] |
| **중복 실행** | 화면 잠금으로 충분 [결정]. 서버는 브라우저당 동시 1건 규칙으로 자연히 차단 |
| **동시 실행 상한** | 브라우저당 1건, 서버 전체 동시 실행 수는 설정값(기본 20) [결정]. 초과하면 최대 5초 대기 후 `busy` 반환 [기본값] |
| **rate limit (429)** | 지수 백오프로 **2회 재시도**(약 0.5초, 2초). 60초 예산을 넘기면 `timeout`으로 종료 [결정] |
| **실행 실패** | 실패도 로그에 남김(`outcome=failed`, 오류 유형 포함). 전체 보기에서는 실패한 칸만 오류 표시. 서버가 재시작되면 `running`으로 남은 레코드를 기동 시 `interrupted`로 정리 [기본값] |
| **중지** | 취소 API와 연결 끊김 둘 다로 중지 [결정]. 중지 시점까지의 토큰·비용은 기록 |
| **콜드 스타트** | 화면 진입 시 `/api/health`로 미리 깨움. 준비 전이면 "서버를 깨우는 중" 표시 [결정]. 지식베이스는 기동 시 로드하되, 로드 전이라도 Vanilla는 답할 수 있게 함 [기본값] |
| **무료 한도 초과** | Neon 저장 용량(0.5GB)이 80%를 넘으면 관리자 화면에 경고. 정리 정책은 IMP-14 [기본값] |

---

## 5차로 넘기는 항목

| 항목 |
|---|
| 프레임 목업 (소개·챗봇 4모드·대시보드·로그 목록·로그 상세) |
| 예시 질문 칩 문구, 파이프라인 차이 그림, 배지·상태 색상, 차트 형태 |
| 진행 단계 표시 문구 (`검색 2/3` 등)와 오류 안내 문구 |
