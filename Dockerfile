# Imagem única para os dois serviços (api + inference). Cada container roda um
# `command` diferente no docker-compose — separados para escalar/trocar
# independentemente (a inference é o alvo de uma futura reimplementação em Rust).
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/.hf \
    RECOMENDAI_DEVICE=cpu

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt "uvicorn[standard]"

COPY . .

# Artefatos de modelo vêm por volume (dvc pull no host) ou bind-mount — ver
# docker-compose.yml. movies.db idem.
EXPOSE 8000 9000

# Default: a API. O serviço de inference sobrescreve o command no compose.
# 1 worker + threads: o rate limit (Flask-Limiter) usa storage em memória, que
# só vale por processo — com N workers o limite fica N× mais frouxo. O trabalho
# pesado de ML está no serviço `inference` à parte. Para escalar de verdade,
# aponte RATELIMIT_STORAGE_URI para Redis e volte a subir o nº de workers.
CMD ["gunicorn", "-b", "0.0.0.0:8000", "-w", "1", "--threads", "8", "-k", "gthread", "--timeout", "120", "app:app"]
