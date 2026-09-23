FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    LEADSGEN_HOST=0.0.0.0 LEADSGEN_PORT=8910 \
    LEADSGEN_DB=/data/leadsgen.sqlite3 LEADSGEN_WEB_DIST=/app/dist \
    XDG_CACHE_HOME=/tmp/cache
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY server ./server
COPY dist ./dist
RUN useradd --uid 10001 --create-home app && mkdir /data && chown app:app /data
ENV PYTHONPATH=/app/server
USER app
EXPOSE 8910
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8910/healthz')"
CMD ["python", "-m", "leadsgen.app"]
