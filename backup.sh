#!/bin/bash
# =============================================================
# VRC Calendar Discord Bot - Firestore バックアップスクリプト
# cron から呼び出される想定
# =============================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/.venv/bin/activate"
cd "$SCRIPT_DIR"
python firestore_backup.py
