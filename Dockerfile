# telegram-musicdown：单进程 FastAPI + Kurigram（SDD §8 M4）
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TGM_WEB_HOST=0.0.0.0

WORKDIR /opt/app

# 依赖层（利用缓存）
COPY pyproject.toml ./
RUN pip install --no-cache-dir fastapi uvicorn pydantic kurigram tgcrypto mutagen pyyaml

# 代码与前端构建产物
COPY app ./app
COPY web/dist ./web/dist

# 卷：曲库、会话、临时文件、数据库（FR-OPS-02）
RUN useradd -m tgm && mkdir -p /data/downloads /data/sessions /data/temp /data/data \
    && chown -R tgm:tgm /opt/app /data
USER tgm

VOLUME ["/data/downloads", "/data/sessions", "/data/temp", "/data/data"]

EXPOSE 8787

CMD ["python", "-m", "app"]
