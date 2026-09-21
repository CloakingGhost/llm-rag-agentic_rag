"""가상 주문 DB (논문 9.3절의 Mock SQLite).

실제 상거래 플랫폼 연동은 범위 밖이므로 합성 데이터를 SQLite에 넣어 대신한다.
`config/mock_orders.json`이 원본이고, 이 파일에서 DB를 만든다. 지우면 다시 만들어진다.

주의: 이 데이터는 한국소비자원 상담 질문과 맥락이 맞지 않는다. 그 불일치가
논문 9.2절의 '도구 편향'을 일으키는 조건이므로 일부러 맞추지 않는다.
"""

from __future__ import annotations

import json
import sqlite3
from functools import lru_cache
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
SEED_PATH = CONFIG_DIR / "mock_orders.json"

SCHEMA = """
CREATE TABLE IF NOT EXISTS customer (
    customer_id TEXT PRIMARY KEY, name TEXT, grade TEXT, joined_at TEXT, phone_last4 TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY, product_name TEXT, category TEXT, amount INTEGER,
    ordered_at TEXT, delivered_at TEXT, status TEXT, payment TEXT
);
CREATE TABLE IF NOT EXISTS refunds (
    order_id TEXT PRIMARY KEY, requested_at TEXT, reason TEXT, status TEXT, refund_amount INTEGER
);
"""


@lru_cache
def _connect() -> sqlite3.Connection:
    from app.config import get_settings

    path = Path(get_settings().mock_db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed(conn)
    return conn


def _seed(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT count(*) FROM orders").fetchone()[0]:
        return
    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    customer = seed["customer"]
    conn.execute(
        "INSERT OR REPLACE INTO customer VALUES (:customer_id, :name, :grade, :joined_at, :phone_last4)",
        customer,
    )
    conn.executemany(
        "INSERT OR REPLACE INTO orders VALUES (:order_id, :product_name, :category, :amount,"
        " :ordered_at, :delivered_at, :status, :payment)",
        seed["orders"],
    )
    conn.executemany(
        "INSERT OR REPLACE INTO refunds VALUES (:order_id, :requested_at, :reason, :status, :refund_amount)",
        seed["refunds"],
    )
    conn.commit()


def _row(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def get_customer_profile() -> dict:
    """로그인한 고객 1명만 있는 단일 계정 환경이다."""
    profile = _row(_connect().execute("SELECT * FROM customer LIMIT 1").fetchone())
    return profile or {"error": "not_found", "message": "고객 정보가 없습니다."}


def list_orders(limit: int = 10) -> dict:
    rows = _connect().execute("SELECT * FROM orders ORDER BY ordered_at DESC LIMIT ?", (limit,)).fetchall()
    return {"count": len(rows), "orders": [dict(r) for r in rows]}


def get_order(order_id: str) -> dict:
    order = _row(_connect().execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone())
    if order is None:
        # 논문 9.2절이 관찰한 실패 경로. 여기서 모델이 '내역이 없다'를 답으로 삼으면 오답이 된다
        return {"error": "not_found", "message": f"주문번호 {order_id}를 찾을 수 없습니다."}
    return order


def get_refund_status(order_id: str) -> dict:
    refund = _row(_connect().execute("SELECT * FROM refunds WHERE order_id = ?", (order_id,)).fetchone())
    if refund is None:
        return {"error": "not_found", "message": f"주문번호 {order_id}의 환불 접수 내역이 없습니다."}
    return refund


def create_refund_request(order_id: str, reason: str) -> dict:
    """환불 접수. 가상 DB이므로 실제로 돈이 움직이지는 않는다."""
    conn = _connect()
    order = _row(conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone())
    if order is None:
        return {"error": "not_found", "message": f"주문번호 {order_id}를 찾을 수 없어 접수할 수 없습니다."}
    existing = _row(conn.execute("SELECT * FROM refunds WHERE order_id = ?", (order_id,)).fetchone())
    if existing is not None:
        return {"error": "already_exists", "message": "이미 접수된 환불 건입니다.", "refund": existing}

    from datetime import UTC, datetime

    record = {
        "order_id": order_id,
        "requested_at": datetime.now(UTC).strftime("%Y-%m-%d"),
        "reason": reason,
        "status": "접수완료",
        "refund_amount": order["amount"],
    }
    conn.execute(
        "INSERT INTO refunds VALUES (:order_id, :requested_at, :reason, :status, :refund_amount)", record
    )
    conn.commit()
    return record


def reset() -> None:
    """실험을 다시 돌릴 때 접수된 환불을 비운다."""
    conn = _connect()
    conn.execute("DELETE FROM refunds")
    conn.commit()
    _seed_refunds(conn)


def _seed_refunds(conn: sqlite3.Connection) -> None:
    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    conn.executemany(
        "INSERT OR REPLACE INTO refunds VALUES (:order_id, :requested_at, :reason, :status, :refund_amount)",
        seed["refunds"],
    )
    conn.commit()
