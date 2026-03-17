FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --gid 1001 appuser \
    && useradd --uid 1001 --gid 1001 --create-home --shell /usr/sbin/nologin appuser

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py ./
COPY prompts ./prompts

USER appuser

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import os,sys; required=['TELEGRAM_BOT_TOKEN','OPENROUTER_API_KEY','OPENROUTER_MODEL','GYM_DB_HOST','GYM_DB_NAME','GYM_DB_USER','GYM_DB_PASSWORD']; missing=[key for key in required if not os.getenv(key)]; sys.exit(1 if missing else 0)"

CMD ["python", "main.py"]
