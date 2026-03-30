FROM python:3.12-slim AS builder

ENV POETRY_VERSION=2.1.3 \
    POETRY_HOME=/opt/poetry \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:/opt/poetry/bin:$PATH"

RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv "$POETRY_HOME" \
    && "$POETRY_HOME/bin/pip" install --no-cache-dir "poetry==$POETRY_VERSION" \
    && python -m venv "$VIRTUAL_ENV"

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY poetry.lock* /app/

RUN poetry install --no-root --only main

COPY src /app/src

RUN poetry install --only main

FROM python:3.12-slim

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PINBRIDGE_MCP_HOST=0.0.0.0 \
    PINBRIDGE_MCP_PORT=57289 \
    PINBRIDGE_MCP_STREAMABLE_HTTP_PATH=/

RUN useradd -m -u 1000 pinbridge

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app /app

RUN chown -R pinbridge:pinbridge /app

USER pinbridge

EXPOSE 57289

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:57289/healthz')"

CMD ["pinbridge-mcp", "--transport", "http", "--host", "0.0.0.0", "--port", "57289"]
