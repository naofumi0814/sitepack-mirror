#!/usr/bin/env bash
# ============================================================================
#  SitePack — macOS ダブルクリック起動ランチャー (.command)
# ----------------------------------------------------------------------------
#  使い方:
#    1. このファイルを Finder からダブルクリック
#    2. 初回のみ chmod +x SitePack.command が必要な場合あり
# ============================================================================
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# venv を優先
if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY="python3"
elif command -v python >/dev/null 2>&1; then
    PY="python"
else
    osascript -e 'display dialog "Python が見つかりません。Python 3.11 以上をインストールしてください。" buttons {"OK"} default button 1' || true
    exit 1
fi

export PYTHONPATH="$DIR/src:${PYTHONPATH:-}"
exec "$PY" -m sitepack
