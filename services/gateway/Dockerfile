# Build from the repo root: docker build -f services/gateway/Dockerfile .
FROM python:3.13-slim

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir uv==0.11.32 \
    && useradd --create-home --uid 10001 scout

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY libs/contracts/ libs/contracts/
COPY services/gateway/ services/gateway/
RUN uv sync --frozen --no-dev --package gateway

USER scout
EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "gateway.app:app", "--host", "0.0.0.0", "--port", "8000"]
