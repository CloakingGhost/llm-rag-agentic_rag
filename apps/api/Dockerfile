# Cloud Run용 API 이미지 (docs/07_deployment.md)
#
# 벡터 색인(chroma, 56MB)은 저장소에 두지 않으므로 **로컬에서 빌드해 푸시**한다.
# 지식베이스를 다시 만들면 KB_BUILD_ID를 바꿔서 다시 빌드한다.
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

# 의존성을 먼저 깔아 캐시를 살린다 (코드가 바뀌어도 이 층은 그대로)
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY cli ./cli
COPY kb_build ./kb_build
COPY config ./config

# 활성 빌드 하나만 넣는다
ARG KB_BUILD_ID=kb_20260920_2240
COPY kb_artifacts/${KB_BUILD_ID} ./kb_artifacts/${KB_BUILD_ID}

RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    KB_BUILD_ID=${KB_BUILD_ID} \
    PORT=8080

EXPOSE 8080

# Cloud Run이 PORT를 주입한다
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
