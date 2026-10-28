#!/usr/bin/env bash
# 리눅스/맥 서버용 기동 스크립트. 사용: ./start_server.sh   (외부 접속 허용 시 BACKEND_HOST=0.0.0.0 ./start_server.sh)
# 필요: python3 -m pip install -r requirements-server.txt
cd "$(dirname "$0")" || exit 1
export PYTHONUTF8=1
export BACKEND_HOST="${BACKEND_HOST:-0.0.0.0}"
export BACKEND_PORT="${BACKEND_PORT:-8000}"
exec python3 app.py
