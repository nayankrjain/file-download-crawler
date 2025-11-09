FROM python:3.12-slim

# 1) System deps for Playwright & build
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget curl ca-certificates gnupg \
    fonts-liberation libnss3 libatk-bridge2.0-0 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 \
    libasound2 libxshmfence1 libgtk-3-0 libpangocairo-1.0-0 libpango-1.0-0 libcairo2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 2) Install Poetry (no virtualenv inside container)
ENV POETRY_VERSION=1.8.3 \
    POETRY_VIRTUALENVS_CREATE=false \
    PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir "poetry==$POETRY_VERSION"

# 3) Copy only dependency files first to leverage Docker layer caching
# (Assumes pyproject.toml and poetry.lock are in repo root)
COPY ../pyproject.toml ../poetry.lock* /app/

# 4) Install project dependencies
RUN poetry install --no-interaction --no-ansi --no-root

# 5) Install Playwright browser + deps
RUN python -m playwright install --with-deps chromium

# 6) Copy the rest of your app (everything at repo root) into /app
# NOTE: use a .dockerignore to avoid copying large/unnecessary stuff
COPY .. /app/

# 7) Ensure mount points for downloads/state
VOLUME ["/downloads", "/state"]

# 8) Default command
# (Your entry file is main.py, not crawler.py)
CMD ["python", "-u", "main.py"]
