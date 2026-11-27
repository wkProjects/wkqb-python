# Build: the locked dependencies and the bot, installed into /app/.venv
FROM python:3.14-alpine AS build
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY README.md ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

# Runtime: Python and the virtualenv only (same base image, so the venv's python link fits)
FROM python:3.14-alpine
RUN apk add --no-cache tini
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER nobody
ENTRYPOINT ["/sbin/tini", "--"]
CMD ["wkqb"]
