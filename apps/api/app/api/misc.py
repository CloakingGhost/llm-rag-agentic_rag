"""키 검증 · 지식베이스 정보 · 헬스 체크 · 대시보드 · 관리자 로그."""

from __future__ import annotations

import base64
import hmac
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Cookie, Header, HTTPException, Response
from openai import APIStatusError, AsyncOpenAI
from pydantic import BaseModel
from sqlalchemy import func, select

from app.config import DEFAULT_MODEL, MODEL_CATALOG, get_settings
from app.db.models import Run, Step
from app.db.repository import get_request_detail, list_requests
from app.db.session import session_scope
from app.kb.store import get_kb

router = APIRouter(prefix="/api", tags=["misc"])

SESSION_COOKIE = "cdq_admin"


@router.get("/health")
async def health() -> dict[str, str]:
    kb = get_kb()
    return {"status": "ok", "kb": "ok" if kb.available else "unavailable", "buildId": kb.build_id}


@router.get("/kb/info")
async def kb_info() -> dict[str, Any]:
    kb = get_kb()
    return {
        "buildId": kb.build_id,
        "status": kb.status,
        "reason": kb.reason,
        "chunkCount": kb.chunk_count,
        "graphNodes": kb.graph.number_of_nodes(),
        "documents": [
            {"name": "소비자분쟁해결기준", "detail": "공정거래위원회고시 제2025-14호 · 2025.12.18 시행"},
            {"name": "전자상거래 등에서의 소비자보호에 관한 법률", "detail": "법률 제21312호 · 2026.7.21 시행"},
        ],
    }


@router.post("/key/validate")
async def validate_key(x_openai_key: str = Header(default="")) -> dict[str, Any]:
    """비용이 들지 않는 모델 목록 조회로 키를 확인한다."""
    if not x_openai_key:
        return {"valid": False, "reason": "키가 비어 있습니다."}
    client = AsyncOpenAI(api_key=x_openai_key)
    try:
        await client.models.list()
        return {"valid": True}
    except APIStatusError as exc:
        if exc.status_code == 401:
            return {"valid": False, "reason": "유효하지 않은 키입니다."}
        return {"valid": False, "reason": "확인에 실패했습니다."}
    except Exception:  # noqa: BLE001
        return {"valid": False, "reason": "확인에 실패했습니다."}


@router.get("/models")
async def models() -> dict[str, Any]:
    """선택 가능한 모델 목록 (논문 7.1절의 3개 실험군)."""
    return {
        "default": DEFAULT_MODEL,
        "models": [
            {
                "id": spec.id,
                "label": spec.label,
                "note": spec.note,
                "pricePer1M": {"input": spec.input, "output": spec.output},
            }
            for spec in MODEL_CATALOG.values()
        ],
    }


@router.get("/dashboard")
async def dashboard(period: Literal["all", "7d", "30d"] = "all", model: str | None = None) -> dict[str, Any]:
    settings = get_settings()
    since = None
    if period != "all":
        days = 7 if period == "7d" else 30
        since = datetime.now(UTC) - timedelta(days=days)

    async with session_scope() as session:
        stmt = select(Run)
        if since is not None:
            stmt = stmt.where(Run.created_at >= since)
        all_runs = (await session.execute(stmt)).scalars().all()

        step_stmt = select(Step.node, func.avg(Step.latency_ms)).group_by(Step.node)
        if model:
            step_stmt = select(Step.node, func.avg(Step.latency_ms)).join(Run, Run.id == Step.run_id).where(
                Run.model == model
            ).group_by(Step.node)
        node_rows = (await session.execute(step_stmt)).all()

    runs = [r for r in all_runs if not model or r.model == model]

    # 모델별 비교 (논문 7.1절 9개 실험군과 같은 축)
    by_model: list[dict[str, Any]] = []
    for model_id, spec in MODEL_CATALOG.items():
        subset = [r for r in all_runs if r.model == model_id]
        if not subset:
            continue
        latencies = sorted(r.latency_ms for r in subset)
        by_model.append(
            {
                "model": model_id,
                "label": spec.label,
                "requests": len(subset),
                "latencyP50Ms": _percentile(latencies, 0.5),
                "avgTokens": round(sum(r.tokens_in + r.tokens_out for r in subset) / len(subset)),
                "avgCostUsd": round(sum(r.cost_usd for r in subset) / len(subset), 6),
                "byPipeline": [
                    {
                        "pipeline": pipeline,
                        "requests": len([r for r in subset if r.pipeline == pipeline]),
                        "avgCostUsd": round(
                            sum(r.cost_usd for r in subset if r.pipeline == pipeline)
                            / max(1, len([r for r in subset if r.pipeline == pipeline])),
                            6,
                        ),
                        "fallbackRate": _ratio(
                            [r for r in subset if r.pipeline == pipeline], lambda r: r.fallback_used
                        ),
                    }
                    for pipeline in ("vanilla", "native", "agentic")
                ],
            }
        )

    summaries = []
    for pipeline in ("vanilla", "native", "agentic"):
        subset = [r for r in runs if r.pipeline == pipeline]
        latencies = sorted(r.latency_ms for r in subset)
        summaries.append(
            {
                "pipeline": pipeline,
                "requests": len(subset),
                "latencyP50Ms": _percentile(latencies, 0.5),
                "latencyP95Ms": _percentile(latencies, 0.95),
                "avgTokens": round(sum(r.tokens_in + r.tokens_out for r in subset) / len(subset)) if subset else 0,
                "avgCostUsd": round(sum(r.cost_usd for r in subset) / len(subset), 6) if subset else 0.0,
            }
        )

    agentic = [r for r in runs if r.pipeline == "agentic"]
    native = [r for r in runs if r.pipeline == "native"]
    return {
        "period": period,
        "model": model,
        "priceVersion": settings.price_version,
        "totalRequests": len({r.request_id for r in runs}),
        "byModel": by_model,
        "summaries": summaries,
        "nodeLatency": [{"node": node, "ms": round(avg or 0)} for node, avg in node_rows],
        "routes": {
            "oodRejectRate": _ratio(agentic, lambda r: r.outcome == "rejected"),
            "fallbackRate": _ratio(agentic, lambda r: r.fallback_used),
            "nativeNoInfoRate": _ratio(native, lambda r: r.no_info_flag),
        },
    }


def _percentile(values: list[int], q: float) -> int:
    if not values:
        return 0
    index = min(len(values) - 1, int(round((len(values) - 1) * q)))
    return values[index]


def _ratio(rows: list[Run], predicate) -> float:
    return round(sum(1 for r in rows if predicate(r)) / len(rows), 4) if rows else 0.0


class LoginRequest(BaseModel):
    id: str
    pw: str


def _token() -> str:
    settings = get_settings()
    return base64.urlsafe_b64encode(f"{settings.admin_id}:{settings.admin_pw}".encode()).decode()


def require_admin(cdq_admin: str | None = Cookie(default=None)) -> None:
    if not cdq_admin or not hmac.compare_digest(cdq_admin, _token()):
        raise HTTPException(status_code=401, detail="관리자 로그인이 필요합니다.")


@router.post("/admin/login")
async def admin_login(payload: LoginRequest, response: Response) -> dict[str, bool]:
    settings = get_settings()
    ok = hmac.compare_digest(payload.id, settings.admin_id) and hmac.compare_digest(payload.pw, settings.admin_pw)
    if not ok:
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다.")
    # 세션 쿠키: 브라우저를 닫으면 만료된다 (04_system_design.md)
    response.set_cookie(SESSION_COOKIE, _token(), httponly=True, samesite="lax", secure=True)
    return {"ok": True}


@router.post("/admin/logout")
async def admin_logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@router.get("/admin/logs")
async def admin_logs(
    mode: str | None = None,
    pipeline: str | None = None,
    outcome: str | None = None,
    q: str | None = None,
    page: int = 1,
    cdq_admin: str | None = Cookie(default=None),
) -> dict[str, Any]:
    require_admin(cdq_admin)
    return await list_requests(mode=mode, pipeline=pipeline, outcome=outcome, q=q, page=page)


@router.get("/admin/logs/{request_id}")
async def admin_log_detail(request_id: str, cdq_admin: str | None = Cookie(default=None)) -> dict[str, Any]:
    require_admin(cdq_admin)
    detail = await get_request_detail(request_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="로그를 찾을 수 없습니다.")
    return detail
