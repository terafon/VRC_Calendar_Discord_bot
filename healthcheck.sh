#!/usr/bin/env bash
# =============================================================
# VRC Calendar Discord Bot - ヘルスチェックスクリプト
# 使い方: bash healthcheck.sh
# =============================================================
set -uo pipefail

# ---------- 色定義 ----------
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

# ---------- カウンタ ----------
TOTAL=0
PASS=0

ok() {
    TOTAL=$((TOTAL + 1))
    PASS=$((PASS + 1))
    printf "${GREEN}[OK]${NC} %s\n" "$1"
}

ng() {
    TOTAL=$((TOTAL + 1))
    printf "${RED}[NG]${NC} %s\n" "$1"
    printf "     → %s\n" "$2"
}

warn() {
    printf "${YELLOW}[WARN]${NC} %s\n" "$1"
}

# ---------- プロジェクトルート ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "=== VRC Calendar Bot ヘルスチェック ==="
echo ""

# ==========================================================
# 1. Bot サービス稼働状態
# ==========================================================
if systemctl is-active --quiet vrc-calendar-bot 2>/dev/null; then
    ok "Bot サービス: 稼働中"
else
    ng "Bot サービス: 停止中" \
       "sudo systemctl start vrc-calendar-bot && sudo journalctl -u vrc-calendar-bot -f"
fi

# ==========================================================
# 2. Cloudflare Tunnel 稼働状態
# ==========================================================
if systemctl is-active --quiet cloudflared 2>/dev/null; then
    ok "Cloudflare Tunnel: 稼働中"
else
    ng "Cloudflare Tunnel: 停止中" \
       "sudo systemctl start cloudflared && sudo journalctl -u cloudflared -f"
fi

# ==========================================================
# 3. Flask ヘルスエンドポイント（ローカル）
# ==========================================================
PORT="${PORT:-8080}"
if [ -f .env ]; then
    ENV_PORT=$(grep -E '^PORT=' .env | cut -d'=' -f2 | tr -d '[:space:]')
    if [ -n "$ENV_PORT" ]; then
        PORT="$ENV_PORT"
    fi
fi

HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://localhost:${PORT}/health" 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
    ok "Flask ヘルスエンドポイント（ローカル）: 200 OK"
else
    ng "Flask ヘルスエンドポイント（ローカル）: HTTP ${HTTP_CODE}" \
       "Bot サービスが起動しているか確認: sudo systemctl status vrc-calendar-bot"
fi

# ==========================================================
# 4. Flask ヘルスエンドポイント（Tunnel 経由）
# ==========================================================
TUNNEL_DOMAIN=""
if [ -f .env ]; then
    REDIRECT_URI=$(grep -E '^OAUTH_REDIRECT_URI=' .env | cut -d'=' -f2- | tr -d '[:space:]')
    if [ -n "$REDIRECT_URI" ]; then
        # https://bot.example.com/oauth/callback -> bot.example.com
        TUNNEL_DOMAIN=$(echo "$REDIRECT_URI" | sed -E 's|https?://([^/]+).*|\1|')
    fi
fi

if [ -n "$TUNNEL_DOMAIN" ]; then
    HTTP_CODE=$(curl -L -s -o /dev/null -w '%{http_code}' --max-time 10 "https://${TUNNEL_DOMAIN}/health" 2>/dev/null || echo "000")
    if [ "$HTTP_CODE" = "200" ]; then
        ok "Flask ヘルスエンドポイント（Tunnel: ${TUNNEL_DOMAIN}）: 200 OK"
    else
        ng "Flask ヘルスエンドポイント（Tunnel: ${TUNNEL_DOMAIN}）: HTTP ${HTTP_CODE}" \
           "cloudflared の状態を確認: sudo systemctl status cloudflared"
    fi
else
    warn "Tunnel チェックをスキップ: .env に OAUTH_REDIRECT_URI が設定されていません"
fi

# ==========================================================
# 5. .env 必須変数の存在確認
# ==========================================================
REQUIRED_VARS=(
    DISCORD_BOT_TOKEN
    GCP_PROJECT_ID
    GOOGLE_APPLICATION_CREDENTIALS
    GCS_BUCKET_NAME
    GEMINI_API_KEY
    GOOGLE_OAUTH_CLIENT_ID
    GOOGLE_OAUTH_CLIENT_SECRET
    OAUTH_REDIRECT_URI
    PORT
)

if [ -f .env ]; then
    MISSING_VARS=()
    for VAR in "${REQUIRED_VARS[@]}"; do
        if ! grep -qE "^${VAR}=.+" .env; then
            MISSING_VARS+=("$VAR")
        fi
    done

    if [ ${#MISSING_VARS[@]} -eq 0 ]; then
        ok "環境変数: 必須変数すべて設定済み"
    else
        ng "環境変数: 未設定の変数あり" \
           "不足: ${MISSING_VARS[*]}"
    fi
else
    ng "環境変数: .env ファイルが見つかりません" \
       ".env.example を参考に .env を作成してください"
fi

# ==========================================================
# 6. credentials.json の存在確認
# ==========================================================
CRED_PATH="credentials.json"
if [ -f .env ]; then
    ENV_CRED=$(grep -E '^GOOGLE_APPLICATION_CREDENTIALS=' .env | cut -d'=' -f2 | tr -d '[:space:]')
    if [ -n "$ENV_CRED" ]; then
        CRED_PATH="$ENV_CRED"
    fi
fi

if [ -f "$CRED_PATH" ]; then
    ok "credentials.json: 存在確認済み（${CRED_PATH}）"
else
    ng "credentials.json: ファイルが見つかりません（${CRED_PATH}）" \
       "GCP サービスアカウントキーを配置してください（DEPLOY.md 参照）"
fi

# ==========================================================
# 7. Firestore 接続テスト
# ==========================================================
VENV_PYTHON=""
if [ -f .venv/bin/python ]; then
    VENV_PYTHON=".venv/bin/python"
elif [ -f venv/bin/python ]; then
    VENV_PYTHON="venv/bin/python"
fi

if [ -n "$VENV_PYTHON" ]; then
    # .env を読み込んで環境変数としてエクスポート（コメント・空行を除外、\r を除去）
    FIRESTORE_RESULT=$(
        if [ -f .env ]; then
            while IFS='=' read -r key value; do
                key=$(echo "$key" | tr -d '[:space:]' | tr -d '\r')
                value=$(echo "$value" | sed 's/\r$//')
                # コメントや空行をスキップ、KEY=VALUE 形式のみ export
                if [[ -n "$key" && "$key" != \#* && -n "$value" ]]; then
                    export "$key=$value"
                fi
            done < .env
        fi
        "$VENV_PYTHON" -c "
from firestore_manager import FirestoreManager
import os
try:
    fm = FirestoreManager(os.environ.get('GCP_PROJECT_ID'))
    fm.get_setting('healthcheck_test')
    print('OK')
except Exception as e:
    print(f'ERROR: {e}')
" 2>&1
    )

    if [ "$FIRESTORE_RESULT" = "OK" ]; then
        ok "Firestore 接続: 正常"
    else
        ng "Firestore 接続: 失敗" \
           "${FIRESTORE_RESULT}"
    fi
else
    ng "Firestore 接続: Python 仮想環境が見つかりません" \
       "python -m venv .venv && pip install -r requirements.txt"
fi

# ==========================================================
# 8. crontab エントリの確認
# ==========================================================
CRONTAB_CONTENT=$(crontab -l 2>/dev/null || echo "")

if echo "$CRONTAB_CONTENT" | grep -q "firestore_backup"; then
    ok "crontab: バックアップジョブ確認済み"
else
    ng "crontab: バックアップジョブが未登録" \
       "DEPLOY.md のバックアップ設定を参照して crontab -e で追加してください"
fi

if echo "$CRONTAB_CONTENT" | grep -q "weekly_notify\|週次通知\|send_weekly"; then
    ok "crontab: 週次通知ジョブ確認済み"
else
    ng "crontab: 週次通知ジョブが未登録" \
       "DEPLOY.md の通知設定を参照して crontab -e で追加してください"
fi

# ==========================================================
# 9. ディスク使用量
# ==========================================================
DISK_USAGE=$(df -h / | awk 'NR==2 {print $5}' | tr -d '%')
if [ "$DISK_USAGE" -lt 80 ]; then
    ok "ディスク使用量: ${DISK_USAGE}% 使用中"
elif [ "$DISK_USAGE" -lt 90 ]; then
    TOTAL=$((TOTAL + 1))
    PASS=$((PASS + 1))
    warn "ディスク使用量: ${DISK_USAGE}% 使用中（注意: 80% 超過）"
else
    ng "ディスク使用量: ${DISK_USAGE}% 使用中" \
       "不要なファイルを削除してディスク容量を確保してください"
fi

# ==========================================================
# 結果サマリー
# ==========================================================
echo ""
echo "-------------------------------------------"
if [ "$PASS" -eq "$TOTAL" ]; then
    printf "結果: ${GREEN}%d/%d 項目が正常${NC}\n" "$PASS" "$TOTAL"
else
    printf "結果: ${YELLOW}%d/%d 項目が正常${NC}\n" "$PASS" "$TOTAL"
fi
echo ""
