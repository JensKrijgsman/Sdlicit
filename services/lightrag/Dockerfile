# Stage 1: Build frontend (WebUI with knowledge graph visualization)
FROM oven/bun:1 AS frontend-builder
WORKDIR /app
RUN git clone --depth 1 https://github.com/HKUDS/LightRAG.git /tmp/lightrag \
    && cp -r /tmp/lightrag/lightrag_webui ./lightrag_webui \
    && rm -rf /tmp/lightrag
RUN cd lightrag_webui \
    && bun install --frozen-lockfile \
    && bun run build

# Stage 2: Final image with API + WebUI
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "lightrag-hku[api]>=1.3.0"
RUN mkdir -p /app/data/rag_storage /app/data/inputs

# Copy built frontend into the location the server expects
COPY --from=frontend-builder /app/lightrag/api/webui /usr/local/lib/python3.12/site-packages/lightrag/api/webui

ENV WORKING_DIR=/app/data/rag_storage
ENV INPUT_DIR=/app/data/inputs
ENV HOST=0.0.0.0
ENV PORT=9621
EXPOSE 9621

ENTRYPOINT ["python", "-m", "lightrag.api.lightrag_server"]
