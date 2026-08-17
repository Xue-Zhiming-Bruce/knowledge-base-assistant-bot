# syntax=docker/dockerfile:1.7

ARG PYTHON_VERSION=3.12.13
ARG UV_VERSION=0.11.7
ARG TEMPO_VERSION=v1.11.0
ARG TEMPO_WALLET_VERSION=0.6.7
ARG TEMPO_REQUEST_VERSION=0.6.7

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM debian:trixie-slim AS tempo-tools

ARG TEMPO_VERSION
ARG TEMPO_WALLET_VERSION
ARG TEMPO_REQUEST_VERSION

ENV HOME=/tempo-build

RUN apt-get -o Acquire::Retries=3 update \
    && apt-get -o Acquire::Retries=3 install \
        --yes --no-install-recommends ca-certificates curl gnupg \
    && rm -rf /var/lib/apt/lists/*

RUN curl --fail --silent --show-error --location \
        --retry 5 --retry-all-errors \
        https://tempo.xyz/install --output /tmp/install-tempo

RUN bash /tmp/install-tempo --install "${TEMPO_VERSION}" \
    && /tempo-build/.tempo/bin/tempo add wallet "${TEMPO_WALLET_VERSION}" \
    && /tempo-build/.tempo/bin/tempo add request "${TEMPO_REQUEST_VERSION}"

FROM python:${PYTHON_VERSION}-slim-trixie AS builder

ENV PATH="/opt/venv/bin:${PATH}" \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /build

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --frozen --no-dev --no-editable


FROM builder AS test

ENV COVERAGE_FILE=/tmp/.coverage \
    PYTEST_ADDOPTS="-p no:cacheprovider" \
    XDG_CACHE_HOME=/tmp/.cache \
    HOME=/tmp

COPY tests ./tests
COPY data ./data
COPY Dockerfile compose.yaml .dockerignore ./

RUN uv sync --frozen --extra dev --extra orchestration --no-editable

CMD ["pytest", "--cov=knowledge_assistant", "--cov-report=term-missing", "-q"]


FROM builder AS orchestration

ARG APP_UID=10001
ARG APP_GID=10001

ENV PATH="/opt/venv/bin:${PATH}" \
    HOME=/home/knowledge-assistant \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN uv sync --frozen --no-dev --extra orchestration --no-editable \
    && groupadd --gid "${APP_GID}" knowledge-assistant \
    && useradd \
        --uid "${APP_UID}" \
        --gid "${APP_GID}" \
        --create-home \
        --home-dir /home/knowledge-assistant \
        --shell /usr/sbin/nologin \
        knowledge-assistant \
    && mkdir -p /app \
    && chown -R knowledge-assistant:knowledge-assistant \
        /app /home/knowledge-assistant

WORKDIR /app

USER knowledge-assistant

ENTRYPOINT ["knowledge-assistant"]
CMD ["check-config"]


FROM python:${PYTHON_VERSION}-slim-trixie AS runtime

ARG APP_UID=10001
ARG APP_GID=10001

ENV PATH="/opt/venv/bin:${PATH}" \
    HOME=/home/knowledge-assistant \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates libusb-1.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${APP_GID}" knowledge-assistant \
    && useradd \
        --uid "${APP_UID}" \
        --gid "${APP_GID}" \
        --create-home \
        --home-dir /home/knowledge-assistant \
        --shell /usr/sbin/nologin \
        knowledge-assistant \
    && mkdir -p /app /data/vault /home/knowledge-assistant/.tempo \
    && chown -R knowledge-assistant:knowledge-assistant \
        /app /data/vault /home/knowledge-assistant

WORKDIR /app

COPY --from=builder --chown=knowledge-assistant:knowledge-assistant /opt/venv /opt/venv
COPY --from=tempo-tools /tempo-build/.tempo/bin/tempo-request /usr/local/bin/tempo-request
COPY --from=tempo-tools /tempo-build/.tempo/bin/tempo-wallet /usr/local/bin/tempo-wallet

USER knowledge-assistant

ENTRYPOINT ["knowledge-assistant"]
CMD ["check-config"]
