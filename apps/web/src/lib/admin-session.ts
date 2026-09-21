/** 관리자 세션 표시. 실제 인증은 서버의 세션 쿠키가 한다 (브라우저를 닫으면 만료). */

const STORAGE = "cdq.admin";
const listeners = new Set<() => void>();

export const adminSession = {
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  getSnapshot: () => window.sessionStorage.getItem(STORAGE),
  getServerSnapshot: () => null,
  markLoggedIn() {
    window.sessionStorage.setItem(STORAGE, "1");
    listeners.forEach((l) => l());
  },
  clear() {
    window.sessionStorage.removeItem(STORAGE);
    listeners.forEach((l) => l());
  },
};
