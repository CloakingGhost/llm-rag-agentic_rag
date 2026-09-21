# 7. 배포

- 작성일: 2026-09-21
- 구성: **Vercel Hobby**(프런트) + **Google Cloud Run**(API) + **Neon Free**(DB). 근거는 [04_system_design.md](04_system_design.md) §4-1-2
- 이 문서는 **처음부터 끝까지 따라 하는 순서**다. 계정 생성과 결제 수단 등록은 본인이 직접 해야 한다.

## 0. 준비물

| 항목 | 확인 |
|---|---|
| Google Cloud 계정 + 결제 수단 등록 | Cloud Run 무료 한도 안에서는 청구되지 않지만 카드 등록은 필요하다 |
| `gcloud` CLI | `gcloud --version` |
| Docker Desktop 실행 중 | `docker version` |
| Neon 계정 | https://neon.tech |
| Vercel 계정 + GitHub 저장소 | 프런트는 GitHub 연동이 가장 간단하다 |

**왜 이미지를 로컬에서 만드나**: 벡터 색인(`chroma`, 56MB)을 저장소에 두지 않기 때문이다.
CI에는 그 파일이 없으므로 이미지는 **로컬에서 빌드해 푸시**한다. GitHub Actions는 검사만 한다.

## 1. Neon (DB)

1. 프로젝트를 만들고 리전은 **AWS US East (Ohio)** 처럼 Cloud Run `us-central1`과 가까운 곳으로 고른다
2. 연결 문자열을 복사한다. **Pooled connection**을 쓴다
3. 드라이버에 맞게 고친다

```text
postgresql://USER:PASSWORD@ep-xxx-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require
→ postgresql+asyncpg://USER:PASSWORD@ep-xxx-pooler.us-east-2.aws.neon.tech/neondb?ssl=require
```

- `postgresql://` → `postgresql+asyncpg://`
- `sslmode=require` → `ssl=require` (asyncpg는 `sslmode`를 모른다)
- 풀러 주소라서 준비문 캐시를 끈다. 코드에 이미 반영돼 있다 (`app/db/session.py`)

표는 기동할 때 만들어진다(`create_all` + 뒤늦게 추가된 열 보정). Alembic 전환은 IMP-17이다.

## 2. Cloud Run (API)

```bash
export PROJECT_ID=cdq-chatbot            # 본인 프로젝트 ID
export REGION=us-central1

gcloud auth login
gcloud config set project "$PROJECT_ID"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com
gcloud artifacts repositories create cdq --repository-format=docker --location="$REGION"
gcloud auth configure-docker "${REGION}-docker.pkg.dev"
```

프런트 주소를 아직 모르면 일단 아무 값으로 배포하고, 4단계 뒤에 다시 채운다.

```bash
export DATABASE_URL='postgresql+asyncpg://...?ssl=require'
export WEB_ORIGIN='https://<프로젝트>.vercel.app'
export ADMIN_ID=... ADMIN_PW=...          # 기본값 admin/admin은 공개 배포 전에 반드시 바꾼다 (IMP-10)

./deploy/deploy_api.sh
```

스크립트가 하는 일: 이미지 빌드 → Artifact Registry 푸시 → Cloud Run 배포 → URL 출력.

설정값(리전·메모리 1GiB·최소 0·최대 3·동시 20·타임아웃 120초)은 §4-1-2의 결정을 그대로 쓴다.

확인:

```bash
curl https://<cloud-run-url>/api/health
curl https://<cloud-run-url>/api/kb/info
```

`{"status":"ok","kb":"ok","buildId":"kb_20260920_2240"}`가 나와야 한다.

## 3. Vercel (프런트)

| 항목 | 값 |
|---|---|
| Root Directory | `apps/web` |
| Framework | Next.js (자동 인식) |
| Install Command | 기본값 (pnpm 자동 인식) |
| 환경변수 | `NEXT_PUBLIC_API_BASE_URL` = Cloud Run URL |

환경변수는 **Production·Preview 양쪽**에 넣는다. 값이 바뀌면 다시 배포해야 반영된다
(`NEXT_PUBLIC_` 변수는 빌드 시점에 박힌다).

## 4. 도메인 연결 마무리

프런트 주소가 정해지면 Cloud Run 환경변수를 다시 맞춘다.

```bash
gcloud run services update cdq-api --region us-central1 \
  --update-env-vars "^##^ALLOWED_ORIGINS=https://<프로젝트>.vercel.app##COOKIE_SAMESITE=none##COOKIE_SECURE=true"
```

- `ALLOWED_ORIGINS`가 틀리면 브라우저가 CORS로 막는다
- **`COOKIE_SAMESITE=none`이 없으면 관리자 로그인이 안 된다.** 프런트와 API의 도메인이 달라서
  `SameSite=Lax` 쿠키는 전송되지 않는다. 로컬(localhost끼리)에서는 `lax`가 맞다

## 5. 배포 후 점검

| 확인 | 방법 |
|---|---|
| 지식베이스 적재 | `/api/kb/info`의 `chunkCount`가 1,654 |
| 질문 1건 | 화면에서 키를 넣고 "전체 파이프라인 보기" 실행 |
| 로그 기록 | 관리자 로그인 → `/admin/logs`에 방금 질문이 보이는지 |
| 대시보드 | `/dashboard`에 모델별 집계가 나오는지 |
| 콜드 스타트 | 10분 쉰 뒤 첫 요청. 화면에 "서버를 깨우는 중"이 뜬다 |

## 6. 다시 배포할 때

| 상황 | 할 일 |
|---|---|
| 코드만 고침 | `./deploy/deploy_api.sh` 다시 실행 |
| 프런트만 고침 | GitHub에 푸시하면 Vercel이 자동 배포 |
| **지식베이스를 다시 만듦** | `apps/api/Dockerfile`의 `KB_BUILD_ID`, `apps/api/.dockerignore`의 빌드 ID, 루트 `.gitignore`의 빌드 ID를 **함께** 바꾼 뒤 다시 배포 |
| 되돌리기 | `gcloud run services update-traffic cdq-api --to-revisions <이전리비전>=100` |

## 7. 비용과 한도

| 항목 | 무료 한도 | 넘으면 |
|---|---|---|
| Cloud Run | 월 200만 요청 / 180k vCPU-초 / 360k GiB-초 | 요청 1건 10초 기준 **월 약 18,000건**까지 무료 |
| Artifact Registry | 0.5GB | 이미지가 약 1.2GB라 **월 $0.1 안팎 발생 가능**. 옛 이미지는 지운다 |
| Neon | 저장 0.5GB / 월 100 CU-시간 | 요청 약 1만 건 분량. 80%에서 정리 (IMP-14) |
| Vercel Hobby | 전송 100GB/월, 비상업적 용도 | 연구·전시 목적이라 조건에 맞는다 |
| OpenAI | — | **사용자가 각자 자기 키로 부담한다.** 서버는 키를 저장하지 않는다 |

옛 이미지 정리:

```bash
gcloud artifacts docker images list us-central1-docker.pkg.dev/$PROJECT_ID/cdq/api
gcloud artifacts docker images delete us-central1-docker.pkg.dev/$PROJECT_ID/cdq/api:<태그>
```

## 8. 아직 남은 것

- **관리자 계정이 `admin`/`admin`이다.** 공개 전에 `ADMIN_ID`·`ADMIN_PW`를 바꾼다 (IMP-10)
- DB 비밀번호가 Cloud Run 환경변수로 들어간다. Secret Manager 전환은 다음 과제다
- 스키마 변경은 `create_all` + `ALTER TABLE` 보정에 기대고 있다 (IMP-17)
