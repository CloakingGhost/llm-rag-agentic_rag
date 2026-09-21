"""세션 메모리 (논문 3.4 Stateful Memory Bank).

LangGraph `MemorySaver`를 대화 ID(thread_id) 단위로 쓴다.
같은 대화 안에서는 `DisputeTarget`이 턴을 넘어 이어지고, 대화가 끝나면 버린다.

논문 9.3은 "단일 세션 내에서의 상태 관리만 지원하며 세션 종료 시 대화 맥락이 초기화된다"를
한계로 적었다. 그 환경을 그대로 재현하므로 장기 기억(DB 적재)은 두지 않는다.
대화 로그 자체는 별도로 Postgres에 남지만, 그 기록이 다음 턴의 추론에 쓰이지는 않는다.
"""

from __future__ import annotations

import contextlib
import time
from collections import OrderedDict
from functools import lru_cache

from langgraph.checkpoint.memory import MemorySaver

from app.config import get_settings


class SessionMemory:
    """진행 중인 대화의 체크포인트를 들고 있는다. 프로세스가 내려가면 같이 사라진다."""

    def __init__(self) -> None:
        self.saver = MemorySaver()
        self._touched: OrderedDict[str, float] = OrderedDict()
        self._turns: dict[str, int] = {}

    def begin_turn(self, thread_id: str) -> int:
        """이번 턴 번호를 돌려주고 마지막 사용 시각을 갱신한다."""
        self._evict()
        turn = self._turns.get(thread_id, 0) + 1
        self._turns[thread_id] = turn
        self._touched[thread_id] = time.monotonic()
        self._touched.move_to_end(thread_id)
        return turn

    def config(self, thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id}}

    def end(self, thread_id: str) -> bool:
        """대화 종료. 체크포인트를 지운다 (논문의 '세션 종료 시 초기화')."""
        return self._drop(thread_id)

    @property
    def active(self) -> int:
        return len(self._touched)

    def _drop(self, thread_id: str) -> bool:
        self._turns.pop(thread_id, None)
        existed = self._touched.pop(thread_id, None) is not None
        with contextlib.suppress(Exception):
            self.saver.delete_thread(thread_id)
        return existed

    def _evict(self) -> None:
        """오래된 대화를 정리한다. 메모리에만 있으므로 상한이 필요하다."""
        settings = get_settings()
        deadline = time.monotonic() - settings.session_ttl_min * 60
        for thread_id, touched in list(self._touched.items()):
            if touched < deadline:
                self._drop(thread_id)
        while len(self._touched) > settings.session_max:
            self._drop(next(iter(self._touched)))


@lru_cache
def get_session_memory() -> SessionMemory:
    return SessionMemory()
