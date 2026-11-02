# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN groupadd --system app && useradd --system --gid app --home /app app
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY --chown=app:app gpsproc ./gpsproc
USER app
EXPOSE 8082
HEALTHCHECK --interval=15s --timeout=5s --start-period=90s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8082/health',timeout=4).status==200 else 1)"
CMD ["python", "-m", "gpsproc.main"]
