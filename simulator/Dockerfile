# syntax=docker/dockerfile:1
# Build from the repository root:  docker build -f simulator/Dockerfile .
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN groupadd --system app && useradd --system --gid app --home /app app
WORKDIR /app
COPY gateway/requirements.txt gateway-requirements.txt
RUN pip install -r gateway-requirements.txt
COPY gateway/pyproject.toml gateway/pyproject.toml
COPY gateway/gpsgw gateway/gpsgw
COPY simulator/pyproject.toml simulator/pyproject.toml
COPY simulator/gpssim simulator/gpssim
RUN pip install --no-deps ./gateway ./simulator
USER app
# Sends the real Teltonika protocol to the gateway; drives the demo fleet from seed_demo by default.
ENTRYPOINT ["python", "-m", "gpssim", "--host", "gateway", "--port", "5027"]
CMD ["--scenario", "normal_day", "--steps", "360", "--speedup", "1"]
