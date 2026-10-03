// k6 시나리오 공용 유틸. 질문 풀 · 키 로테이션 · ID 생성.

// 소비자 분쟁 도메인 내 질문 — 실제 트래픽처럼 보이게 VU마다 랜덤 선택한다.
// 전부 똑같은 질문만 보내면 (캐싱은 없지만) 실제 사용 패턴과 달라진다.
export const QUESTIONS = [
  '노트북에 물을 부었는데 받을 수 있는 조치가 무엇이 있나요?',
  '배달로 받은 마우스가 파손되어 있어요.',
  '온라인으로 주문한 옷이 사이즈가 안 맞아서 환불하고 싶어요.',
  '구독 서비스를 해지했는데 다음 달 요금이 또 결제됐어요.',
  '같은 상품인데 카드가 두 번 결제됐어요. 환불받을 수 있나요?',
  '중고로 산 노트북이 설명과 다르게 고장나 있었어요.',
  '해외 직구한 물건이 세관에 걸려서 안 오고 있는데 환불 가능한가요?',
  '배송이 2주 넘게 안 와서 취소하고 싶은데 판매자가 연락이 안 돼요.',
  '정품이라고 해서 샀는데 가짜 상품이 왔어요.',
  'A/S 맡긴 가전제품이 수리 후에도 똑같은 고장이 반복돼요.',
  '예약한 숙소가 갑자기 취소됐는데 위약금을 받을 수 있나요?',
  '무료체험 기간인 줄 알고 가입했는데 바로 유료 결제가 됐어요.',
];

export function pickQuestion() {
  return QUESTIONS[Math.floor(Math.random() * QUESTIONS.length)];
}

// K6_OPENAI_KEY_1/2를 VU별로 번갈아 쓴다 — 한 키에 몰리면 우리 서버가 아니라
// OpenAI 레이트리밋에 먼저 걸려서 "서버 다운"으로 오판하게 된다
export function pickKey() {
  const keys = [__ENV.K6_OPENAI_KEY_1, __ENV.K6_OPENAI_KEY_2].filter((k) => k && k.length > 0);
  if (keys.length === 0) {
    throw new Error('K6_OPENAI_KEY_1/2가 비어 있습니다. load/k6/.env를 확인하세요.');
  }
  return keys[__VU % keys.length];
}

// "k6-" 접두사: 부하테스트 트래픽을 DB(clients)·LangFuse(userId)에서 가려내고 지우기 쉽게 한다
export function newClientId() {
  return `k6-${__VU}-${__ITER}-${Date.now()}`;
}

export function newConversationId() {
  return `conv_${__VU}_${__ITER}_${Date.now()}`.slice(0, 40);
}
