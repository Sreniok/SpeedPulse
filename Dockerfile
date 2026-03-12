FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata ca-certificates curl gnupg \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://packagecloud.io/ookla/speedtest-cli/gpgkey \
        | gpg --dearmor -o /etc/apt/keyrings/ookla-speedtest.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/ookla-speedtest.gpg] https://packagecloud.io/ookla/speedtest-cli/debian/ bookworm main" \
        > /etc/apt/sources.list.d/ookla-speedtest.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends speedtest \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x /app/docker-entrypoint.sh

RUN groupadd --gid 1000 appgroup \
    && useradd --uid 1000 --gid appgroup --create-home appuser \
    && chown -R appuser:appgroup /app

EXPOSE 8000

USER appuser

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "web.app:APP", "--host", "0.0.0.0", "--port", "8000"]
