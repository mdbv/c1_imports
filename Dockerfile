FROM debian:stable-slim

RUN apt-get update && apt-get install -y \
    smbclient \
    lftp \
    openssh-client \
    cron \
    --no-install-recommends && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY scripts/*  /app/

RUN chmod +x /app/*.sh && \
    mkdir -p /var/log

ENTRYPOINT ["/app/entrypoint.sh"]
