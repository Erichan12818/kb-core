FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

COPY kb /app/kb
COPY bin/kb /usr/local/bin/kb
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /usr/local/bin/kb /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["api"]
