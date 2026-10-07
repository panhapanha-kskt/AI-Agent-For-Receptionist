FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first for better layer caching.
COPY pyproject.toml ./
COPY src ./src
RUN pip install ".[voice]"

COPY skills ./skills
COPY web ./web

# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 app && mkdir -p /app/data /app/models \
    && chown -R app:app /app/data /app/models
USER app

ENV DATABASE_URL=sqlite:////app/data/receptionist.db \
    ENVIRONMENT=production \
    HF_HOME=/app/models

EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["uvicorn", "receptionist.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
