FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_DEV=1

# Dependencies first so they're cached between code changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --extra classifier

COPY . .
RUN uv sync --locked --extra classifier

CMD ["uv", "run", "--no-sync", "scripts/run_bot.py", "env=prod"]
