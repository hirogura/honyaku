#!/usr/bin/env bash
# 受付翻訳 UI を 127.0.0.1:3374 でのみ起動する。
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "エラー: .venv がありません。先に以下を実行してください:" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

exec .venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 3374
