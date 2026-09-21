# 논문 파이프라인 프롬프트 역추론

## 근거 자료와 표기

| 표기 | 출처 |
|---|---|
| **[논문]** | `ref/논문.docx` 본문 (부록 D, 3.3·3.4·4.2·4.4·4.5절) |
| **[구현]** | Notion 「챗봇 구현 문서 (1)」: 스키마와 시스템 프롬프트 요약이 명시됨 |
| **[관찰]** | Notion 「챗봇 테스트 결과 (1)」(2026-08-14, 테스트 케이스 5건): 실제 출력과 실행 로그 |
| **[추론]** | 위 셋을 근거로 복원한 부분. 원문은 공개되지 않았음 |

[구현]에 있는 프롬프트도 **요약본**입니다. 원문 전체가 공개된 프롬프트는 Vanilla 하나뿐입니다.

---

## 0. 관찰 요약

| 파이프라인 | 관찰된 동작 | 추론되는 지시 |
|---|---|---|
| Vanilla | 맛집 질문에도 식당 목록으로 답함. 번호 목록 형식의 일반론. 조항 번호는 거의 없음 | 도메인 제한이 없는 한 줄짜리 역할 지시 |
| Native RAG | "[참조 2]에 따르면…" 식으로 인용함. 근거가 없으면 "정보가 없습니다. 답변을 제공할 수 없습니다"로 끝냄(TV 파손 건까지 거절) | 참조 문서만 쓰라는 강한 제약. 대안·절차 지시는 없음 |
| Agentic | "김철수님", "주문하신 스마트 TV 75인치"처럼 고객과 주문을 언급함. "언제든지 말씀해 주세요"로 끝나는 CS 말투. 한국소비자원을 안내함. 조항 번호는 드물게만 인용 | 쇼핑몰 CS 역할, 고객·주문 정보 주입, 절차 포함(Critic `is_complete`) |
| Agentic (거절) | 고정 문구로 응답 (L2 채점 0.02초) | LLM 호출 없는 고정 응답 |
| Agentic (로그) | `쿼리 강화: '청약철회 {원 질문}'` | 검색 쿼리 = `{dispute_type} {question}` |

---

## 1. Vanilla LLM

[구현] 원문이 공개됨. 모델은 GPT-4o-mini(구현) / GPT-4o 등 비교 대상 모델(논문). **재현: `gpt-4o-mini`**

```text
[system]
당신은 소비자 분쟁 전문가입니다. 다음 질문에 답변해주세요.

[user]
{question}
```

- [관찰] 도메인 밖 질문도 거절하지 않음 → 범위 제한 지시 없음

---

## 2. Native RAG

[논문] 부록 D 4-B: 검색된 소비자분쟁해결기준을 정제 없이 전량 프롬프트에 주입. 4.5절: 문서 검색만 수행하고 자율 검증 순환은 제외
[구현] 검색 = 벡터(ChromaDB, 코사인) + 그래프(NetworkX, LLM 엔티티 추출) → RRF 병합

```text
[system]                                                        ← [추론]
당신은 소비자 분쟁 상담 어시스턴트입니다.
아래 [참조]로 제공된 문서의 내용만을 근거로 질문에 답변하세요.
참조 문서에 질문과 관련된 정보가 없으면, 정보가 없다고 답하세요.

[user]
[참조 1] (페이지 {page}, 유사도 {score:.2f})                    ← [관찰] 머리글 형식
{chunk_text}

[참조 2] (페이지 {page}, 유사도 {score:.2f})
{chunk_text}
...

질문: {question}
```

- [관찰] 검색 로그: 벡터 20개 + 그래프 → 결합 29개 → LLM 리랭킹 → 최종 10개
- [추론] "정보가 없으면 없다고 답하라"는 지시 때문에 거절이 과다함 → Native의 낮은 L2 점수(0.47)

---

## 3. Agentic RAG

### 3-1. Stateful Memory Bank

[구현] 스키마와 시스템 프롬프트 요약

```python
class DisputeTarget(BaseModel):
    order_id: Optional[str] = Field(description="현재 분쟁 대상 주문 번호")
    product_name: Optional[str] = Field(description="분쟁 대상 상품명")
    dispute_type: Optional[str] = Field(description="분쟁 유형 (청약철회/환불/교환/가격보상/기타)")
```

```text
[system]
당신은 쇼핑몰 CS 시스템의 상태 추적 모듈입니다. 사용자의 질문에서 현재 분쟁 대상을 추출하세요.
이전 분쟁 대상이 있으면 그것을 기반으로 업데이트하세요.

[user]                                                          ← [추론]
이전 분쟁 대상: {previous_dispute_target_json}
질문: {question}
```

- 최종 구성에서는 주문 DB가 없으므로 `order_id`는 항상 `None`

### 3-2. Intent Router

[구현] 스키마와 시스템 프롬프트 요약. 모델 GPT-4o-mini, Structured Output

```python
class IntentResult(BaseModel):
    route: Literal["policy_inquiry", "system_action", "out_of_domain"] = Field(...)
    reason: str = Field(description="분류 이유 한 줄 설명")
```

```text
[system]
당신은 쇼핑몰 CS 챗봇의 의도 분류기입니다. 사용자 질문을 세 경로—policy_inquiry(규정·법률 안내),
system_action(시스템 액션), out_of_domain(무관한 질문)—중 하나로 분류하세요.
{퓨샷 예시: 실제 불만 접수 패턴 → 의도 클래스}                    ← [논문] 4.4절 결정론적 퓨샷

[user]
{question}
```

- **최종 구성**: [논문] 4.2.2절은 `policy_inquiry` / `out_of_domain` 두 경로만 둠 → `system_action` 제거
- [관찰] reason 예: "청약철회 가능 여부에 대한 문의로, 규정 안내로 해결할 수 있는 질문입니다."

### 3-3. Reject Responder (out_of_domain)

[관찰] LLM 호출이 없는 고정 응답

```text
저는 쇼핑몰 CS 분쟁 해결을 위한 AI 어시스턴트입니다. 쇼핑몰 분쟁과 관련 없는 질문에는 답변해 드릴 수 없습니다.
```

### 3-4. Retrieve

```python
query = f"{dispute_target.dispute_type} {question}"     # [관찰] 1회차
query = rewrite_query(question, critic.feedback)          # [추론] retry_retrieve 시
```

- [논문] 4.2.3절: 1회차 실패 시 개선 의견을 바탕으로 재검색

### 3-5. Generate

[논문] 부록 D 2-C: 주입된 고객 정보, 주문 내역, 추적된 분쟁 대상, 검색 문서를 바탕으로 생성. **정보 외 내용 생성 및 추가 안내를 엄격히 금지.** 4.4절: `<retrieved_documents>` 등 XML 태그로 맥락 격리
[구현] 검색 문서, 사용자 프로필, 분쟁 대상을 주입하여 답변 생성

```text
[system]                                                        ← [추론]
당신은 쇼핑몰 CS 분쟁 해결을 위한 AI 어시스턴트입니다.

규칙:
1. <retrieved_documents> 안의 내용만을 근거로 답변합니다. 문서에 없는 내용을 만들어내지 마세요.
2. 제공된 정보 외의 추가 안내는 하지 마세요.
3. 고객이 실제로 취할 수 있는 구체적인 해결 절차를 포함하세요.
4. 친절하고 정중한 상담원 말투로 답변하세요.

[user]
<customer_info>{user_profile}</customer_info>              ← 최종 구성에서 제외 (Context Injector)
<order_history>{recent_orders}</order_history>              ← 최종 구성에서 제외
<dispute_target>{dispute_target_json}</dispute_target>
<retrieved_documents>
{reranked_chunks}
</retrieved_documents>
<critic_feedback>{feedback}</critic_feedback>               ← retry_generate 시에만
<question>{question}</question>
```

- [관찰] "현재 주문하신 무선 청소기에 대해"(질문에 없던 상품) → 가상 주문 DB가 질문과 어긋나서 생긴 환각. [논문] 4.1절이 이 모듈을 최종 평가에서 뺀 이유와 일치함
- [관찰] 조항 번호를 거의 인용하지 않음 → 인용 형식 지시는 없는 것으로 추론 (L2 법적 근거 0.60 수준)

### 3-6. Self-RAG Critic

[구현] 스키마. 모델 GPT-4o-mini

```python
class CriticResult(BaseModel):
    is_grounded: bool = Field(description="답변이 참조 문서에 기반하는지 여부 (환각 없음)")
    is_relevant: bool = Field(description="답변이 사용자 질문에 적절히 대답하는지 여부")
    is_complete: bool = Field(description="사용자의 문제를 해결할 수 있는 구체적인 실행 방안이 포함되었는지 여부")
    feedback: str = Field(description="개선이 필요한 경우 구체적 피드백")
```

```text
[system]                                                        ← [추론]
당신은 답변 검증관입니다. 생성된 답변을 다음 세 기준으로 엄격히 평가하세요.
- is_grounded: 답변의 모든 주장이 <retrieved_documents>에 근거하는가
- is_relevant: 답변이 사용자 질문에 적절히 대답하는가
- is_complete: 사용자의 문제를 해결할 구체적인 실행 방안이 포함되었는가
기준 미달이면 feedback에 개선점을 구체적으로 적으세요.

[user]
<question>{question}</question>
<retrieved_documents>{docs}</retrieved_documents>
<answer>{answer}</answer>
```

- 전이 규칙 [논문]: 세 기준 모두 통과 → END / 1회차 실패 → `retry_retrieve` / 2회차 실패 → `retry_generate` / 최대 3회
- **불일치**: [논문] 4.4절은 판정 전에 feedback을 먼저 쓰게 했다(CoT)고 하지만, [구현] 스키마는 feedback이 마지막 필드임. Structured Output은 필드 순서대로 생성되므로 재현할 때는 **논문 서술대로 feedback을 첫 필드로** 두는 것을 기본값으로 함
- **[결정 사항]** 최대 재시도를 넘기면 Native RAG 결과로 폴백하고 '폴백됨'을 표시함

---

## 4. 논문과 구현 문서의 불일치 (재현 기준값 결정 필요)

| 항목 | 논문 | 구현 문서 / 관찰 | 재현 기본값 |
|---|---|---|---|
| 임베딩 | text-embedding-3-large (3072차원) | text-embedding-3-small | 논문 (large) |
| 생성 모델 | Generator gpt-4o, Router·Critic gpt-4o-mini | 전 구성 gpt-4o-mini | **전 노드 gpt-4o-mini** (확정. 구현 문서와 같음) |
| 지식베이스 | 분쟁해결기준 + 전자상거래법 | 분쟁해결기준 + CSV 표준답변 | 논문 (인터뷰 결정) |
| 청킹 | pdfplumber, 500/100 | RecursiveCharacterTextSplitter 500/100 (문단→문장→공백) | 표 구조 보존 방식 ([kb_build_proposal.md](kb_build_proposal.md) §3~5) |
| 그래프 검색 | 전체 조항 엔티티·관계 그래프 + Louvain 군집 | 모든 질의에서 청크 0~4(개정 이력·부칙)만 반환 → **사실상 미작동** | 논문 방식(G1)으로 **지식베이스 전체**에서 다시 구축 ([kb_build_proposal.md](kb_build_proposal.md) §6) |
| Critic feedback 위치 | 판정 전 (CoT) | 마지막 필드 | 논문 |
| 리랭킹 후 청크 수 | 상위 5개 | 10개 | 논문 (5) |

### 4-1. 챗봇 노드별 모델명

| 노드 | 논문 | 논문 출처 | Notion 구현 문서 | 이 프로젝트 |
|---|---|---|---|---|
| Generate (답변 생성) | `gpt-4o`, `gpt-5.6-luna`, `gpt-5.6-terra` (모델 3종 × 파이프라인 3종 = 9개 실험군) | 5.5절 Table, 6.1~6.2절, 7.1절 | `gpt-4o-mini` | `gpt-4o-mini` |
| Vanilla | 위 Generate와 같은 3종 | 7.1절 Table 1 | `gpt-4o-mini` | `gpt-4o-mini` |
| Intent Router | `gpt-4o-mini` | 5.5절 Table | `gpt-4o-mini` | `gpt-4o-mini` |
| Self-RAG Critic | `gpt-4o-mini` | 5.5절 Table ('Evaluator: 품질 채점 및 피드백') | `gpt-4o-mini` | `gpt-4o-mini` |
| LLM Re-ranker | `gpt-4o-mini` | 3.6절 | 명시 없음 | `gpt-4o-mini` |
| 임베딩 | `text-embedding-3-large` | 4.3절 | `text-embedding-3-small` | `text-embedding-3-large` |

- 논문 안에서도 서술이 어긋나는 곳이 있음: 6.1절은 "GPT-4o를 주 생성기 및 **평가기**로 채택"이라고 하지만, 5.5절 Table은 평가(Critic) 노드를 `gpt-4o-mini`로 적음. 위 표는 노드별로 명시한 5.5절 Table을 따름
- 평가용 심사 모델(L1·L3 `gpt-4o-mini`, L2 `gpt-5.6-luna`)은 챗봇이 아니므로 이 표에서 뺐음

---

## 5. 판단 범위: 역추론 결론

추론한 논문 프롬프트가 허용하는 "챗봇이 말해도 되는 범위"는 다음과 같습니다.

- **Vanilla**: 제한 없음. 모델 지식으로 결론·절차를 자유롭게 말함
- **Native RAG**: 참조 문서 범위 안에서만 말함. 근거가 없으면 답하지 않음(결론·대안 모두 없음)
- **Agentic RAG**: 검색 문서 범위 안에서 **결론과 해결 절차**까지 말함(`is_complete`로 강제). 문서 밖의 추가 안내는 금지함. 면책 문구 지시는 없음

### 최종 사용자(소비자) 요구와의 차이

챗봇 화자는 **사업자(쇼핑몰 CS)**로 확정했습니다. 논문과 같습니다. 아래는 그 챗봇의 답을 받는 소비자의 요구와 비교한 것입니다.

| 소비자 요구 | 논문 프롬프트 | 차이 |
|---|---|---|
| ① 가능/불가 판단 | 명시적 지시 없음. Agentic은 결론을 내는 편이고 Native는 자주 거절함 | 결론을 강제하지 않음 |
| ② 불가 시 대안 | Agentic의 "추가 안내 금지"와 충돌할 수 있음 | **충돌 가능** |
| ③ 행동 방법 | Critic `is_complete`가 검사함 (Agentic만) | Vanilla·Native에는 지시 없음 |
| ④ 근거 | Native는 [참조 N], Agentic은 조항 번호를 드물게 인용 | 조항 단위 인용 지시 없음 |
| 화자 입장 | 쇼핑몰 CS(사업자 측) | **일치** (사업자 측으로 확정) |
