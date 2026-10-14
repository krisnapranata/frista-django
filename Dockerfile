FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CACHE_LOCATION=/app/data/cache \
    LOG_FILE=/app/data/frista.log \
    PORT=8010 \
    GUNICORN_WORKERS=3

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# SECRET_KEY sementara hanya untuk collectstatic saat build; yang asli dari .env
RUN mkdir -p /app/data/cache \
    && DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput \
    && useradd --system --uid 1000 --home /app frista \
    && chown -R frista /app/data

USER frista
VOLUME ["/app/data"]
EXPOSE 8010

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request as u;u.urlopen(u.Request('http://127.0.0.1:%s/health' % os.environ['PORT'], headers={'Host': 'localhost'}), timeout=4)" || exit 1

CMD ["sh", "-c", "exec gunicorn config.wsgi -b 0.0.0.0:${PORT} -w ${GUNICORN_WORKERS} --timeout 60 --access-logfile -"]
