#!/usr/bin/env bash
# telegram-musicdown 启动脚本（Linux / Docker 内均可用）
#
# 用法：
#   ./run.sh              # 直接运行（python -m app）
#   ./run.sh docker       # docker compose 构建并后台运行
#   ./run.sh logs         # 查看容器日志
#   ./run.sh stop         # 停止容器
#
# 环境变量（可选）：
#   TGM_WEB_HOST  默认 0.0.0.0（远程可访问；此时必须设 TGM_WEB_LOGIN_SECRET，否则拒启）
#   TGM_WEB_PORT  默认 8787
#   TGM_BASE_DIR  默认脚本所在目录（数据布局：downloads/ data/sessions/ data/logs/ data/temp/；
#                 可用 config.yaml 的 save/session/temp_directory 或同名 TGM_* env 改为绝对路径）
set -euo pipefail
cd "$(dirname "$0")"

export TGM_WEB_HOST="${TGM_WEB_HOST:-0.0.0.0}"
export TGM_WEB_PORT="${TGM_WEB_PORT:-8787}"
export TGM_BASE_DIR="${TGM_BASE_DIR:-$PWD}"

case "${1:-run}" in
  run)
    # 前端产物缺失时先构建（需要 node/npm；已构建则跳过）
    if [ ! -f web/dist/index.html ] && command -v npm >/dev/null 2>&1; then
      echo "[run] building web frontend..."
      (cd web && npm install --no-audit --no-fund && npm run build)
    fi
    exec python3 -m app
    ;;
  docker)
    exec docker compose up -d --build
    ;;
  logs)
    exec docker compose logs -f
    ;;
  stop)
    exec docker compose down
    ;;
  *)
    echo "usage: ./run.sh [run|docker|logs|stop]" >&2
    exit 1
    ;;
esac
