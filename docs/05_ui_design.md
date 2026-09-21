# UI 디자인 (5차)

- 시작일: 2026-09-20
- 진행 방식: 검수 요청 ↔ 피드백 ↔ 수정 반복
- 선행 문서: [03_ux_ia.md](03_ux_ia.md), [04_system_design.md](04_system_design.md)
- 목업 하드코딩 목록: [mock_inventory.md](mock_inventory.md)

## 5차 결정 사항

| 항목 | 결정 |
|---|---|
| 이번 단계 범위 | **프론트(`apps/web`)만** 구성. 백엔드는 6차 |
| 목업 데이터 | 프론트 가짜 데이터. **하드코딩 자리를 `mock_inventory.md`에 기록**하고 `// MOCK M-xx:` 주석을 단다 |
| 테마 | shadcn 기본 테마(neutral) + 라이트/다크 토글 |
| 목업 순서 | 챗봇 → 소개 → 대시보드 → 로그 |

## 프로젝트 구성 결과

```text
apps/web/
  src/app/layout.tsx                      테마·API 키 프로바이더, 공통 헤더
  src/app/page.tsx                        소개
  src/app/chat/page.tsx                   챗봇 4모드
  src/app/dashboard/page.tsx              대시보드
  src/app/admin/page.tsx                  관리자 로그인 (숨긴 주소)
  src/app/admin/logs/page.tsx             로그 목록
  src/app/admin/logs/[requestId]/page.tsx 로그 상세
  src/components/                         site-header · theme-toggle · api-key-panel · pipeline-diagram
  src/components/chat/                    run-card · trace-view · answer-markdown · mock-controls
  src/components/admin/                   admin-guard
  src/components/ui/                      shadcn 컴포넌트 19개 (chart 포함)
  src/lib/types.ts                        API 계약 타입 (04 문서 §4-2와 1:1)
  src/lib/api-key.tsx                     키 보관(localStorage) · 익명 브라우저 ID
  src/lib/admin-session.ts                관리자 세션 (목업)
  src/lib/mock/                           목업 데이터·엔진·대시보드·로그
```

버전은 `create-next-app`이 설치한 Next 16.3.5 · React 19.2.8 · Tailwind 4.3.3 · TypeScript 5.9.3입니다. 4차에서 적어 둔 TypeScript 7은 **적용하지 않았습니다.** shadcn과 ESLint 도구가 아직 5.x 기준이라, 4차 문서의 대비책대로 프론트만 5.x로 둡니다.

## 실행

```bash
pnpm --dir apps/web dev --port 3100
```

## 화면별 목업 상태

| 화면 | 경로 | 상태 |
|---|---|---|
| 소개 | `/` | 파이프라인 차이 그림, 사용 방법·비용, 지식베이스 출처 |
| 챗봇 | `/chat` | 4모드, 전체 보기 3열/모바일 탭, 처리 과정, 상태 7종 |
| 대시보드 | `/dashboard` | 기간 필터, 파이프라인 요약 3장, 평균 토큰 차트, 노드별 소요 시간 차트, 경로 분포, 표본 부족 경고 |
| 관리자 로그인 | `/admin` | 하드코딩 계정, 실패 메시지 |
| 로그 목록 | `/admin/logs` | 표·필터(키워드·모드·결과), 결과 배지, 빈 상태 |
| 로그 상세 | `/admin/logs/:id` | 답변 3열 비교, 노드 타임라인, 검색 결과 원문 스냅샷 |

## 챗봇 목업에서 확인할 수 있는 것

| 항목 | 구현 |
|---|---|
| 모드 4개 | 상단 탭. 탭마다 대화 기록을 따로 유지. 실행 중에는 탭 잠금 |
| 전체 보기 | 데스크톱 3열, 모바일 파이프라인 탭(상태 점 표시). 끝나는 순서대로 채워짐 |
| 진행 표시 | 노드 이름과 회차 표시(`검색 2/3`), 답변은 확정 후 한 번에 |
| 처리 과정 | 라우터 판정 · 검색 근거(경로·벡터 점수·그래프 여부·리랭킹 순위) · Critic 판정. 전체 보기는 펼침, 단일 모드는 접힘 |
| 답변 수치 | 카드 하단에 `9.2초 · 5,580 토큰 · $0.0011` |
| 상태 | 정상 · 도메인 밖 거절 · 폴백 · 키 무효 · 한도 초과 · 시간 초과 · 중지 |
| API 키 | 키가 없으면 입력창 자리에 인라인 안내, 헤더 ⚙에서 변경·삭제 |
| 서버 깨우기 | 진입 시 워밍업 안내 표시 |
| 검수 패널 | 좌측 하단에서 시나리오를 강제 전환 (목업 전용, M-09) |

## 검수 이력

| 회차 | 요청 사항 | 반영 |
|---|---|---|
| 1 | 목업 패널이 입력창을 가림 / 탭에 스크롤바 / 개발 표시기가 겹침 | 검수 패널을 탭 아래 인라인 줄로 이동, 탭은 줄바꿈 방식으로 변경, `devIndicators: false` |
| 2 | 답변의 마크다운 기호가 그대로 노출됨 | `react-markdown` + `remark-gfm`으로 렌더링 (`components/chat/answer-markdown.tsx`). 볼드·번호 목록·인용을 화면 서식으로 표시 |
| 2 | 전체 보기 카드가 너무 길고 높이가 제각각 | 전체 보기 카드 본문을 `max-h-[26rem]`으로 묶고 내부 스크롤. 검색 근거·Critic 판정도 각각 `max-h-64` 스크롤. 3열은 위쪽 정렬 |
| 3 | 예시 질문 문구·다크 모드 가독성은 충분 | 변경 없음 |
| 3 | **진행도는 표시하지 말 것** (처리 과정은 트레이스로 볼 수 있으므로) | 실행 중 표시를 로딩 스피너만 남기고 단계 이름·회차 제거. 02·03·04 문서의 관련 규칙도 함께 수정 |
