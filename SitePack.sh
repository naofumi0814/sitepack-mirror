#!/usr/bin/env bash
# ============================================================================
#  SitePack — Linux ダブルクリック起動ランチャー
# ----------------------------------------------------------------------------
#  ファイルマネージャによっては「実行可能ファイル」として起動を許可する
#  必要があります (Nautilus の「実行可能としてマーク」など)。
# ============================================================================
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY="python3"
elif command -v python >/dev/null 2>&1; then
    PY="python"
else
    if command -v zenity >/dev/null 2>&1; then
        zenity --error --text="Python が見つかりません。Python 3.11 以上をインストールしてください。"
    else
        echo "Python が見つかりません。Python 3.11 以上をインストールしてください。" >&2
    fi
    exit 1
fi

export PYTHONPATH="$DIR/src:${PYTHONPATH:-}"
exec "$PY" -m sitepack
