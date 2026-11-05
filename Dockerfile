FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y \
    lftp \
    openssh-client \
    cron \
    util-linux \
    --no-install-recommends && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY scripts/ /app/
COPY app/ /app/app/

RUN chmod +x /app/*.sh && \
    mkdir -p /var/log /var/lock /app/files /app/state /app/static

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
