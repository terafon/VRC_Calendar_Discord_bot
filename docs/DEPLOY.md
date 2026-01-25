# VRC Calendar Discord Bot - デプロイガイド

このガイドでは、VRC Calendar Discord BotをGoogle Cloud Runにデプロイする手順を説明します。

## 前提条件

- Google Cloud Platform（GCP）アカウント
- gcloud CLIがインストール済み
- Discordアカウントと開発者ポータルへのアクセス
- Googleカレンダー（個人用またはGoogle Workspace）

## 0. 低コスト運用（推奨）: OCI Always Free + GCPバックエンド

常時稼働が必要なDiscord BotはCloud Runの`min-instances=0`だと停止しやすいため、
**計算基盤をOCI Always Free VMに移し、GCPはバックエンドを維持**する構成が最も低コストです。

### 構成イメージ
- OCI VM: Discord Bot（discord.py）常時稼働
- GCP: Calendar API / Secret Manager / GCS / (任意でCloud Scheduler + Pub/Sub)

### 0.1 OCI VMを作成（Always Free）
1. OCIのホームリージョンでAlways Free VM（Arm/AMD）を作成
2. セキュリティリスト/NSGで以下を開放
   - 22 (SSH)
   - 80/443 (HTTPS終端用。Pub/SubのPushを使う場合は必須)

### 0.2 依存パッケージの導入
```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
```

### 0.3 ソース配置と仮想環境
```bash
git clone <your-repo-url>
cd VRC_Calendar_Discord_bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 0.4 GCPサービスアカウント鍵の用意（OCI上で利用）
Cloud Runを使わないため、OCIからGCP APIを呼び出すには
**サービスアカウントJSON鍵**が必要です。

```bash
gcloud iam service-accounts keys create credentials.json \
  --iam-account=calendar-bot-sa@vrc-calendar-bot.iam.gserviceaccount.com
```

OCI VM上に`credentials.json`を配置し、以下の環境変数を設定します。

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/credentials.json
export GCP_PROJECT_ID=vrc-calendar-bot
export GOOGLE_CALENDAR_ID=YOUR_CALENDAR_ID
export GCS_BUCKET_NAME=vrc-calendar-bot-backup
```

### 0.5 systemdで常駐起動
`/etc/systemd/system/vrc-calendar-bot.service`:
```ini
[Unit]
Description=VRC Calendar Discord Bot
After=network.target

[Service]
WorkingDirectory=/home/ubuntu/VRC_Calendar_Discord_bot
Environment=GOOGLE_APPLICATION_CREDENTIALS=/home/ubuntu/VRC_Calendar_Discord_bot/credentials.json
Environment=GCP_PROJECT_ID=vrc-calendar-bot
Environment=GOOGLE_CALENDAR_ID=YOUR_CALENDAR_ID
Environment=GCS_BUCKET_NAME=vrc-calendar-bot-backup
Environment=DISCORD_BOT_TOKEN=YOUR_DISCORD_BOT_TOKEN
Environment=GEMINI_API_KEY=YOUR_GEMINI_API_KEY
ExecStart=/home/ubuntu/VRC_Calendar_Discord_bot/.venv/bin/python /home/ubuntu/VRC_Calendar_Discord_bot/main.py
Restart=always
User=ubuntu

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now vrc-calendar-bot
```

### 0.6 Pub/Sub Pushを使う場合（HTTPS必須・最小コスト構成）
Cloud Scheduler + Pub/Sub Push を使う場合、OCI側にHTTPSエンドポイントが必要です。
**最小構成・無料運用**のため、ここでは **Cloudflare Tunnel（Freeプラン）** を使います。

#### 0.6.1 Cloudflare側の準備（無料）
1. Cloudflareにアカウント作成
2. 自分のドメインをCloudflareに追加（無料枠でOK）
3. Zero Trust（Free）にアクセスしてTunnelを作成

#### 0.6.2 OCI側でcloudflaredを導入
```bash
# Debian/Ubuntuの場合
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o cloudflared.deb
sudo dpkg -i cloudflared.deb
```

#### 0.6.3 Tunnel作成とルーティング
```bash
cloudflared tunnel login
cloudflared tunnel create vrc-calendar-bot

# hostnameを割り当て（例: bot.example.com）
cloudflared tunnel route dns vrc-calendar-bot bot.example.com
```

`/etc/cloudflared/config.yml` を作成:
```yaml
tunnel: vrc-calendar-bot
credentials-file: /home/ubuntu/.cloudflared/<TUNNEL_ID>.json

ingress:
  - hostname: bot.example.com
    service: http://127.0.0.1:8080
  - service: http_status:404
```

#### 0.6.4 cloudflaredを常駐化
```bash
sudo cloudflared service install
sudo systemctl enable --now cloudflared
```

Pushエンドポイント例:
```
https://bot.example.com/weekly-notification
```

> Quick Tunnel（trycloudflare.com）は短期テスト用途のため、常時運用には不向きです。

---

## 1. GCPプロジェクトのセットアップ

### 1.1 プロジェクト作成

```bash
# 新規プロジェクト作成
gcloud projects create vrc-calendar-bot --name="VRC Calendar Bot"

# プロジェクトを選択
gcloud config set project vrc-calendar-bot
```

### 1.2 必要なAPIの有効化

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  cloudscheduler.googleapis.com \
  pubsub.googleapis.com \
  secretmanager.googleapis.com \
  calendar-json.googleapis.com \
  storage.googleapis.com
```

### 1.3 サービスアカウントの作成

```bash
# サービスアカウント作成
gcloud iam service-accounts create calendar-bot-sa \
  --display-name="Calendar Bot Service Account"

# 必要な権限を付与
gcloud projects add-iam-policy-binding vrc-calendar-bot \
  --member="serviceAccount:calendar-bot-sa@vrc-calendar-bot.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

gcloud projects add-iam-policy-binding vrc-calendar-bot \
  --member="serviceAccount:calendar-bot-sa@vrc-calendar-bot.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"

# 認証キーのダウンロード（ローカル開発用）
gcloud iam service-accounts keys create credentials.json \
  --iam-account=calendar-bot-sa@vrc-calendar-bot.iam.gserviceaccount.com
```

## 2. Discord Botのセットアップ

### 2.1 Discord Developer Portalでアプリケーション作成

1. [Discord Developer Portal](https://discord.com/developers/applications)にアクセス
2. 「New Application」をクリック
3. アプリケーション名を入力（例: VRC Calendar Bot）
4. 「Bot」タブで「Add Bot」をクリック
5. 「Reset Token」でトークンを取得（後で使用）

### 2.2 Bot設定

**Privileged Gateway Intents**で以下を有効化：
- [x] MESSAGE CONTENT INTENT

### 2.3 BotをDiscordサーバーに招待

OAuth2 > URL Generatorで以下を選択：
- **SCOPES**: `bot`, `applications.commands`
- **BOT PERMISSIONS**: `Send Messages`, `Embed Links`, `Read Message History`

生成されたURLをブラウザで開いて、対象サーバーに招待。

## 3. Googleカレンダーの設定

### 3.1 カレンダーの共有設定

1. [Googleカレンダー](https://calendar.google.com)を開く
2. 対象カレンダーの設定 > 「特定のユーザーとの共有」
3. サービスアカウントのメールアドレスを追加：
   `calendar-bot-sa@vrc-calendar-bot.iam.gserviceaccount.com`
4. 権限: 「変更および共有の管理権限」

### 3.2 カレンダーIDの取得

カレンダーの設定 > 「カレンダーの統合」からカレンダーIDをコピー。
（例: `abc123@group.calendar.google.com`）

## 4. Secret Managerの設定

```bash
# Discord Bot Token
echo -n "YOUR_DISCORD_BOT_TOKEN" | gcloud secrets create DISCORD_BOT_TOKEN --data-file=-

# Gemini API Key
echo -n "YOUR_GEMINI_API_KEY" | gcloud secrets create GEMINI_API_KEY --data-file=-

# Cloud Runサービスアカウントにアクセス権を付与
gcloud secrets add-iam-policy-binding DISCORD_BOT_TOKEN \
  --member="serviceAccount:calendar-bot-sa@vrc-calendar-bot.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

gcloud secrets add-iam-policy-binding GEMINI_API_KEY \
  --member="serviceAccount:calendar-bot-sa@vrc-calendar-bot.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

## 5. Cloud Storageバケットの作成

```bash
# バケット作成（リージョンは東京）
gcloud storage buckets create gs://vrc-calendar-bot-backup \
  --location=asia-northeast1 \
  --uniform-bucket-level-access

# サービスアカウントに権限付与
gcloud storage buckets add-iam-policy-binding gs://vrc-calendar-bot-backup \
  --member="serviceAccount:calendar-bot-sa@vrc-calendar-bot.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"
```

## 6. Cloud Runへのデプロイ

### 6.1 Artifact Registryリポジトリの作成

```bash
gcloud artifacts repositories create calendar-bot-repo \
  --repository-format=docker \
  --location=asia-northeast1
```

### 6.2 Dockerイメージのビルドとプッシュ

```bash
# イメージをビルドしてプッシュ
gcloud builds submit --tag asia-northeast1-docker.pkg.dev/vrc-calendar-bot/calendar-bot-repo/calendar-bot:latest
```

### 6.3 Cloud Runサービスのデプロイ

```bash
gcloud run deploy calendar-bot \
  --image=asia-northeast1-docker.pkg.dev/vrc-calendar-bot/calendar-bot-repo/calendar-bot:latest \
  --platform=managed \
  --region=asia-northeast1 \
  --allow-unauthenticated \
  --service-account=calendar-bot-sa@vrc-calendar-bot.iam.gserviceaccount.com \
  --min-instances=0 \
  --max-instances=1 \
  --memory=512Mi \
  --cpu=1 \
  --timeout=300 \
  --set-env-vars="GCP_PROJECT_ID=vrc-calendar-bot,GOOGLE_CALENDAR_ID=YOUR_CALENDAR_ID,GCS_BUCKET_NAME=vrc-calendar-bot-backup"
```

**重要な設定値：**
- `--min-instances=0`: コスト削減のためアイドル時はインスタンス0（Discord Botの常時稼働には不向き）
- `--max-instances=1`: 同時実行を1に制限（SQLite競合防止）
- `--timeout=300`: Discord Botの起動時間を考慮

### 6.4 サービスURLの確認

```bash
gcloud run services describe calendar-bot --region=asia-northeast1 --format='value(status.url)'
```

## 7. 週次通知の設定（Cloud Scheduler + Pub/Sub）

### 7.1 Pub/Subトピックの作成

```bash
# トピック作成
gcloud pubsub topics create weekly-notification-trigger

# サブスクリプション作成（Cloud Run Push）
SERVICE_URL=$(gcloud run services describe calendar-bot --region=asia-northeast1 --format='value(status.url)')

gcloud pubsub subscriptions create weekly-notification-sub \
  --topic=weekly-notification-trigger \
  --push-endpoint="${SERVICE_URL}/weekly-notification" \
  --push-auth-service-account=calendar-bot-sa@vrc-calendar-bot.iam.gserviceaccount.com
```

### 7.2 Cloud Schedulerジョブの作成

```bash
# 毎週月曜日9:00（JST）に実行
gcloud scheduler jobs create pubsub weekly-notification-job \
  --schedule="0 9 * * 1" \
  --time-zone="Asia/Tokyo" \
  --topic=weekly-notification-trigger \
  --message-body="weekly notification trigger"
```

## 8. 動作確認

### 8.1 ヘルスチェック

```bash
curl ${SERVICE_URL}/health
# 期待される応答: OK
```

### 8.2 Discord Botの確認

1. Discordサーバーで `/予定` と入力
2. コマンドが表示されればBot起動成功

### 8.3 週次通知のテスト

```bash
# 手動でPub/Subメッセージを発行
gcloud pubsub topics publish weekly-notification-trigger --message="test"
```

## 9. トラブルシューティング

### 9.1 ログの確認

```bash
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=calendar-bot" --limit=50
```

### 9.2 よくある問題

| 問題 | 原因 | 対処法 |
|------|------|--------|
| Bot起動失敗 | トークン不正 | Secret Managerの値を確認 |
| カレンダー登録失敗 | 権限不足 | サービスアカウントのカレンダー共有を確認 |
| 週次通知が来ない | Pub/Sub設定不備 | サブスクリプションのpush-endpointを確認 |
| DBがリセットされる | バックアップ失敗 | GCSバケットの権限を確認 |

### 9.3 コールドスタートの遅延

Cloud Runの`--min-instances=0`設定では、リクエストがない時はインスタンスが停止します。最初のリクエスト時にコールドスタートが発生し、10-30秒程度かかる場合があります。

即時応答が必要な場合は `--min-instances=1` に変更しますが、月額コストが増加します。

## 10. コスト見積もり

| サービス | 月額見込み | 備考 |
|----------|-----------|------|
| Cloud Run | ~$0.10 | min-instances=0、低頻度アクセス |
| Cloud Storage | ~$0.01 | 5GB未満は実質無料 |
| Cloud Scheduler | 無料 | 3ジョブまで無料 |
| Pub/Sub | 無料 | 10GB/月まで無料 |
| Secret Manager | 無料 | 6シークレットまで無料 |
| **合計** | **~$0.20/月** | 約30円以下 |

## 11. 更新デプロイ

コードを更新後、再デプロイ：

```bash
# 再ビルド & デプロイ
gcloud builds submit --tag asia-northeast1-docker.pkg.dev/vrc-calendar-bot/calendar-bot-repo/calendar-bot:latest

gcloud run deploy calendar-bot \
  --image=asia-northeast1-docker.pkg.dev/vrc-calendar-bot/calendar-bot-repo/calendar-bot:latest \
  --region=asia-northeast1
```

## 12. クリーンアップ（削除時）

```bash
# Cloud Runサービス削除
gcloud run services delete calendar-bot --region=asia-northeast1

# Schedulerジョブ削除
gcloud scheduler jobs delete weekly-notification-job

# Pub/Subリソース削除
gcloud pubsub subscriptions delete weekly-notification-sub
gcloud pubsub topics delete weekly-notification-trigger

# シークレット削除
gcloud secrets delete DISCORD_BOT_TOKEN
gcloud secrets delete GEMINI_API_KEY

# バケット削除
gcloud storage rm -r gs://vrc-calendar-bot-backup

# プロジェクト削除（すべて削除する場合）
gcloud projects delete vrc-calendar-bot
```
