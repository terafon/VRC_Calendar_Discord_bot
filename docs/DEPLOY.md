# VRC Calendar Discord Bot - デプロイガイド

このガイドでは、VRC Calendar Discord Botをデプロイする手順を説明します。

## 目次

1. [デプロイ方式の選択](#デプロイ方式の選択)
2. [方式A: OCI Always Free + GCP（推奨・完全無料）](#方式a-oci-always-free--gcp推奨完全無料)
3. [方式B: Cloud Run単体（シンプル・低コスト）](#方式b-cloud-run単体シンプル低コスト)
4. [共通設定](#共通設定)
5. [トラブルシューティング](#トラブルシューティング)

---

## デプロイ方式の選択

Discord Botは**常時WebSocket接続を維持**する必要があるため、サーバーレス環境との相性に注意が必要です。

### 方式比較

| 方式 | 月額コスト | 常時稼働 | 複雑さ | おすすめ |
|------|-----------|----------|--------|----------|
| **A: OCI + GCP** | 完全無料 | ◎ | やや複雑 | 常時稼働が必要な場合 |
| **B: Cloud Run** | ~$0.20 | △ | シンプル | たまに使う程度の場合 |

### 構成図

```
【方式A: OCI Always Free + GCP】
┌─────────────────────────────────────────────────────────────────┐
│  Oracle Cloud Infrastructure (Always Free)                      │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  VM.Standard.E2.1.Micro / Ampere A1                       │  │
│  │  ┌─────────────────┐  ┌─────────────────────────────────┐ │  │
│  │  │ Discord Bot     │  │ Flask Server (週次通知受信)    │ │  │
│  │  │ (常時WebSocket) │  │ localhost:8080                 │ │  │
│  │  └─────────────────┘  └─────────────────────────────────┘ │  │
│  │           │                        ▲                      │  │
│  │           │                        │ Cloudflare Tunnel    │  │
│  └───────────┼────────────────────────┼──────────────────────┘  │
└─────────────┼────────────────────────┼──────────────────────────┘
              │                        │
              ▼                        │
┌─────────────────────────────────────────────────────────────────┐
│  Google Cloud Platform                                          │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────────────────┐ │
│  │ Calendar API │ │ Gemini API   │ │ Cloud Storage (backup)   │ │
│  └──────────────┘ └──────────────┘ └──────────────────────────┘ │
│  ┌──────────────┐ ┌──────────────┐                              │
│  │ Scheduler    │→│ Pub/Sub      │→ HTTPS Push                  │
│  │ (月曜9:00)   │ │ (トリガー)   │                              │
│  └──────────────┘ └──────────────┘                              │
└─────────────────────────────────────────────────────────────────┘

【方式B: Cloud Run単体】
┌─────────────────────────────────────────────────────────────────┐
│  Google Cloud Platform                                          │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  Cloud Run (min-instances=0)                              │  │
│  │  ┌─────────────────┐  ┌─────────────────────────────────┐ │  │
│  │  │ Discord Bot     │  │ Flask Server                    │ │  │
│  │  │ (コールドスタート│  │ (HTTPエンドポイント)           │ │  │
│  │  │  で起動)        │  │                                 │ │  │
│  │  └─────────────────┘  └─────────────────────────────────┘ │  │
│  └───────────────────────────────────────────────────────────┘  │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────────────────┐ │
│  │ Calendar API │ │ Gemini API   │ │ Cloud Storage (backup)   │ │
│  └──────────────┘ └──────────────┘ └──────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

---

## 方式A: OCI Always Free + GCP（推奨・完全無料）

常時稼働が必要なDiscord BotをOCI Always Free VMで動かし、GCPのAPIサービスをバックエンドとして利用する構成です。

### なぜこの構成が推奨されるのか

1. **完全無料**: OCI Always Freeは期限なしで無料、GCPも無料枠内で収まる
2. **常時稼働**: VMは24時間稼働するため、Botが常にオンライン
3. **コールドスタートなし**: Cloud Runと違い、起動待ちがない
4. **安定性**: WebSocket接続が切断されにくい

### A-1. OCI（Oracle Cloud）のセットアップ

#### A-1.1 OCIアカウントの作成

1. [Oracle Cloud Free Tier](https://www.oracle.com/cloud/free/)にアクセス
2. 「無料で始める」をクリック
3. 必要情報を入力（クレジットカード登録が必要だが課金されない）
4. **ホームリージョン**を選択（後から変更不可。東京推奨）

> **重要**: Always Free VMはホームリージョンでのみ作成可能です

#### A-1.2 VMインスタンスの作成

1. OCIコンソールにログイン
2. 「コンピュート」→「インスタンス」→「インスタンスの作成」

**推奨スペック（Always Free対象）**:

| 項目 | AMD (E2.1.Micro) | Arm (A1.Flex) |
|------|------------------|---------------|
| OCPU | 1/8 | 最大4 |
| メモリ | 1GB | 最大24GB |
| ストレージ | 最大200GB | 最大200GB |
| 推奨度 | 軽量Bot向け | 余裕あり |

**設定手順**:

```
1. シェイプの選択
   - AMD: VM.Standard.E2.1.Micro（Always Free対象）
   - Arm: VM.Standard.A1.Flex（Always Free対象、1-4 OCPUを選択）

2. イメージの選択
   - Ubuntu 22.04（推奨）またはOracle Linux 8

3. ネットワーキング
   - 新規VCNを作成、またはデフォルトVCNを使用
   - 「パブリックIPv4アドレスの割当て」を有効化

4. SSHキーの追加
   - 自分の公開鍵をアップロード、または新規生成
```

#### A-1.3 セキュリティリストの設定

OCIコンソールで「ネットワーキング」→「仮想クラウド・ネットワーク」→ VCN選択 →「セキュリティ・リスト」

**イングレス・ルールを追加**:

| ソースCIDR | プロトコル | 宛先ポート | 説明 |
|------------|-----------|-----------|------|
| 0.0.0.0/0 | TCP | 22 | SSH |
| 0.0.0.0/0 | TCP | 80 | HTTP（Cloudflare Tunnel用） |
| 0.0.0.0/0 | TCP | 443 | HTTPS（Cloudflare Tunnel用） |

#### A-1.4 VMへのSSH接続

```bash
# SSHで接続
ssh -i ~/.ssh/your_private_key ubuntu@<VM_PUBLIC_IP>

# 初回接続時にパッケージを更新
sudo apt update && sudo apt upgrade -y
```

#### A-1.5 必要なパッケージのインストール

```bash
# Python環境とGitをインストール
sudo apt install -y python3 python3-venv python3-pip git

# バージョン確認
python3 --version  # 3.10以上を確認
```

#### A-1.6 プロジェクトのセットアップ

```bash
# プロジェクトをクローン
cd ~
git clone https://github.com/your-username/VRC_Calendar_Discord_bot.git
cd VRC_Calendar_Discord_bot

# 仮想環境を作成・有効化
python3 -m venv .venv
source .venv/bin/activate

# 依存関係をインストール
pip install --upgrade pip
pip install -r requirements.txt
```

#### A-1.7 環境変数ファイルの作成

```bash
# .envファイルを作成
cp .env.example .env
nano .env
```

**.envファイルの内容**:

```bash
# Discord
DISCORD_BOT_TOKEN=your_discord_bot_token_here
DISCORD_NOTIFICATION_CHANNEL_ID=your_channel_id_here

# Google Cloud
GCP_PROJECT_ID=your-gcp-project-id
GOOGLE_CALENDAR_ID=your_calendar_id@group.calendar.google.com
GOOGLE_APPLICATION_CREDENTIALS=/home/ubuntu/VRC_Calendar_Discord_bot/credentials.json
GCS_BUCKET_NAME=your-bucket-name

# Gemini API
GEMINI_API_KEY=your_gemini_api_key_here

# Server
PORT=8080
```

> **注意**: 値にクォートは不要です。スペースを含む値のみ`"`で囲んでください。

#### A-1.8 GCPサービスアカウント鍵の配置

GCPからダウンロードしたサービスアカウントJSONをVMに転送:

```bash
# ローカルマシンから転送
scp -i ~/.ssh/your_private_key credentials.json ubuntu@<VM_PUBLIC_IP>:/home/ubuntu/VRC_Calendar_Discord_bot/
```

または、GCPコンソールでJSON内容をコピーして直接作成:

```bash
nano /home/ubuntu/VRC_Calendar_Discord_bot/credentials.json
# JSONをペースト、Ctrl+X → Y → Enter で保存
```

#### A-1.9 動作テスト

```bash
# 仮想環境を有効化
source .venv/bin/activate

# テスト起動
python main.py
```

ログに以下が表示されれば成功:
```
Logged in as VRC Calendar Bot#1234
```

`Ctrl+C`で停止。

#### A-1.10 systemdサービスの設定（常駐化）

```bash
sudo nano /etc/systemd/system/vrc-calendar-bot.service
```

**サービスファイルの内容**:

```ini
[Unit]
Description=VRC Calendar Discord Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/VRC_Calendar_Discord_bot
EnvironmentFile=/home/ubuntu/VRC_Calendar_Discord_bot/.env
ExecStart=/home/ubuntu/VRC_Calendar_Discord_bot/.venv/bin/python main.py
Restart=always
RestartSec=10

# ログ設定
StandardOutput=journal
StandardError=journal
SyslogIdentifier=vrc-calendar-bot

[Install]
WantedBy=multi-user.target
```

**サービスの有効化と起動**:

```bash
# デーモンをリロード
sudo systemctl daemon-reload

# 自動起動を有効化
sudo systemctl enable vrc-calendar-bot

# サービスを起動
sudo systemctl start vrc-calendar-bot

# ステータス確認
sudo systemctl status vrc-calendar-bot
```

**ログの確認**:

```bash
# リアルタイムログ
sudo journalctl -u vrc-calendar-bot -f

# 最近のログ（100行）
sudo journalctl -u vrc-calendar-bot -n 100

# エラーのみ
sudo journalctl -u vrc-calendar-bot -p err
```

### A-2. 週次通知の設定

週次通知には2つの方法があります。

#### 方法1: cronジョブ（シンプル・推奨）

OCI VM上で直接cronを使う最もシンプルな方法:

```bash
# cronジョブを編集
crontab -e

# 以下を追加（毎週月曜9:00 JST）
0 9 * * 1 curl -X POST http://localhost:8080/weekly-notification -H "Content-Type: application/json" -d '{"message":{"data":""}}'
```

> **注意**: サーバーのタイムゾーンがJSTでない場合は調整が必要です。

```bash
# タイムゾーンをJSTに設定
sudo timedatectl set-timezone Asia/Tokyo
```

#### 方法2: Cloud Scheduler + Pub/Sub + Cloudflare Tunnel

GCPのCloud Schedulerを使う方法。外部からのHTTPSアクセスが必要なため、Cloudflare Tunnelを使用します。

##### Cloudflare Tunnelのセットアップ

**1. Cloudflareアカウントの準備**

1. [Cloudflare](https://cloudflare.com)でアカウント作成（無料）
2. ドメインをCloudflareに追加（無料ドメインでも可）
3. 「Zero Trust」→「Networks」→「Tunnels」にアクセス

**2. OCI VMにcloudflaredをインストール**

```bash
# Ubuntu/Debian
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o cloudflared.deb
sudo dpkg -i cloudflared.deb

# または Arm VM の場合
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb -o cloudflared.deb
sudo dpkg -i cloudflared.deb
```

**3. Tunnelの作成と認証**

```bash
# Cloudflareにログイン（ブラウザが開く）
cloudflared tunnel login

# Tunnelを作成
cloudflared tunnel create vrc-calendar-bot

# 作成されたTunnel IDを確認
cloudflared tunnel list
```

**4. DNSルーティングの設定**

```bash
# サブドメインをTunnelにルーティング
cloudflared tunnel route dns vrc-calendar-bot bot.yourdomain.com
```

**5. 設定ファイルの作成**

```bash
mkdir -p /home/ubuntu/.cloudflared
nano /home/ubuntu/.cloudflared/config.yml
```

```yaml
tunnel: vrc-calendar-bot
credentials-file: /home/ubuntu/.cloudflared/<TUNNEL_ID>.json

ingress:
  - hostname: bot.yourdomain.com
    service: http://127.0.0.1:8080
  - service: http_status:404
```

> `<TUNNEL_ID>`は`cloudflared tunnel list`で確認したIDに置き換えてください。

**6. cloudflaredのサービス化**

```bash
sudo cloudflared service install
sudo systemctl enable --now cloudflared

# 状態確認
sudo systemctl status cloudflared
```

**7. GCP側のPub/Sub設定**

```bash
# Pub/Subサブスクリプションを作成（Push先をCloudflare Tunnelに）
gcloud pubsub subscriptions create weekly-notification-sub \
  --topic=weekly-notification-trigger \
  --push-endpoint="https://bot.yourdomain.com/weekly-notification" \
  --push-auth-service-account=calendar-bot-sa@YOUR_PROJECT.iam.gserviceaccount.com
```

### A-3. バックアップの自動化

```bash
# バックアップスクリプトを作成
nano /home/ubuntu/VRC_Calendar_Discord_bot/backup.sh
```

```bash
#!/bin/bash
source /home/ubuntu/VRC_Calendar_Discord_bot/.venv/bin/activate
cd /home/ubuntu/VRC_Calendar_Discord_bot
python -c "from storage_backup import StorageBackup; sb = StorageBackup('$GCS_BUCKET_NAME', 'calendar.db'); sb.backup_to_cloud()"
```

```bash
# 実行権限を付与
chmod +x /home/ubuntu/VRC_Calendar_Discord_bot/backup.sh

# cronで定期実行（6時間ごと）
crontab -e
# 以下を追加
0 */6 * * * /home/ubuntu/VRC_Calendar_Discord_bot/backup.sh >> /var/log/backup.log 2>&1
```

---

## 方式B: Cloud Run単体（シンプル・低コスト）

Cloud Runのみを使用するシンプルな構成です。コールドスタートがあるため、常時使用には向きませんが、設定は簡単です。

### B-1. GCPプロジェクトのセットアップ

```bash
# プロジェクト作成
gcloud projects create vrc-calendar-bot --name="VRC Calendar Bot"
gcloud config set project vrc-calendar-bot

# 課金アカウントをリンク（必須）
gcloud billing accounts list
gcloud billing projects link vrc-calendar-bot --billing-account=BILLING_ACCOUNT_ID
```

### B-2. 必要なAPIの有効化

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  cloudscheduler.googleapis.com \
  pubsub.googleapis.com \
  secretmanager.googleapis.com \
  calendar-json.googleapis.com \
  storage.googleapis.com \
  artifactregistry.googleapis.com
```

### B-3. サービスアカウントの作成

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

# ローカル開発用にキーをダウンロード
gcloud iam service-accounts keys create credentials.json \
  --iam-account=calendar-bot-sa@vrc-calendar-bot.iam.gserviceaccount.com
```

### B-4. Secret Managerへのシークレット登録

```bash
# Discord Bot Token
echo -n "YOUR_DISCORD_BOT_TOKEN" | gcloud secrets create DISCORD_BOT_TOKEN --data-file=-

# Gemini API Key
echo -n "YOUR_GEMINI_API_KEY" | gcloud secrets create GEMINI_API_KEY --data-file=-

# アクセス権を付与
gcloud secrets add-iam-policy-binding DISCORD_BOT_TOKEN \
  --member="serviceAccount:calendar-bot-sa@vrc-calendar-bot.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

gcloud secrets add-iam-policy-binding GEMINI_API_KEY \
  --member="serviceAccount:calendar-bot-sa@vrc-calendar-bot.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

### B-5. Cloud Storageバケットの作成

```bash
gcloud storage buckets create gs://vrc-calendar-bot-backup \
  --location=asia-northeast1 \
  --uniform-bucket-level-access

gcloud storage buckets add-iam-policy-binding gs://vrc-calendar-bot-backup \
  --member="serviceAccount:calendar-bot-sa@vrc-calendar-bot.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"
```

### B-6. Cloud Runへのデプロイ

```bash
# Artifact Registryリポジトリ作成
gcloud artifacts repositories create calendar-bot-repo \
  --repository-format=docker \
  --location=asia-northeast1

# ビルドとプッシュ
gcloud builds submit \
  --tag asia-northeast1-docker.pkg.dev/vrc-calendar-bot/calendar-bot-repo/calendar-bot:latest

# デプロイ
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

### B-7. 週次通知（Cloud Scheduler + Pub/Sub）

```bash
# サービスURL取得
SERVICE_URL=$(gcloud run services describe calendar-bot --region=asia-northeast1 --format='value(status.url)')

# Pub/Subトピック作成
gcloud pubsub topics create weekly-notification-trigger

# Pushサブスクリプション作成
gcloud pubsub subscriptions create weekly-notification-sub \
  --topic=weekly-notification-trigger \
  --push-endpoint="${SERVICE_URL}/weekly-notification" \
  --push-auth-service-account=calendar-bot-sa@vrc-calendar-bot.iam.gserviceaccount.com

# Cloud Schedulerジョブ作成（毎週月曜9:00 JST）
gcloud scheduler jobs create pubsub weekly-notification-job \
  --schedule="0 9 * * 1" \
  --time-zone="Asia/Tokyo" \
  --topic=weekly-notification-trigger \
  --message-body="weekly notification trigger"
```

---

## 共通設定

### Discord Botのセットアップ

#### 1. Discord Developer Portalでアプリケーション作成

1. [Discord Developer Portal](https://discord.com/developers/applications)にアクセス
2. 「New Application」をクリック
3. アプリケーション名を入力（例: VRC Calendar Bot）
4. 左メニュー「Bot」→「Add Bot」をクリック
5. 「Reset Token」でトークンを取得（**一度しか表示されないのでコピー保存**）

#### 2. Bot設定

**Privileged Gateway Intents**（必須）:
- [x] MESSAGE CONTENT INTENT

**Bot Permissions**:
- Send Messages
- Embed Links
- Read Message History
- Use Slash Commands

#### 3. サーバーへの招待

1. 左メニュー「OAuth2」→「URL Generator」
2. **SCOPES**: `bot`, `applications.commands`
3. **BOT PERMISSIONS**: 上記の権限を選択
4. 生成されたURLをブラウザで開いて招待

### Googleカレンダーの設定

#### 1. カレンダーの共有設定

1. [Googleカレンダー](https://calendar.google.com)を開く
2. 左サイドバーで対象カレンダーの「⋮」→「設定と共有」
3. 「特定のユーザーとの共有」セクション
4. 「ユーザーを追加」でサービスアカウントのメールアドレスを入力:
   ```
   calendar-bot-sa@vrc-calendar-bot.iam.gserviceaccount.com
   ```
5. 権限: 「変更および共有の管理権限」を選択

#### 2. カレンダーIDの取得

1. カレンダーの設定画面を開く
2. 「カレンダーの統合」セクションまでスクロール
3. 「カレンダーID」をコピー

形式例:
- メインカレンダー: `primary`
- 追加カレンダー: `abc123xyz@group.calendar.google.com`

### Gemini APIキーの取得

1. [Google AI Studio](https://aistudio.google.com/)にアクセス
2. 「Get API Key」をクリック
3. 「Create API Key」でキーを生成
4. GCPプロジェクトを選択（Optional: 統合管理する場合）

---

## トラブルシューティング

### よくある問題と解決策

| 症状 | 原因 | 解決策 |
|------|------|--------|
| Botがオフラインのまま | トークンが無効 | Discord Developer Portalで新しいトークンを生成 |
| スラッシュコマンドが表示されない | コマンド未同期 | Botを再起動、または1時間待つ |
| カレンダー登録エラー | 権限不足 | サービスアカウントのカレンダー共有を確認 |
| 「曜日を特定できませんでした」 | NLP解析失敗 | 「毎週水曜14時に〜」など明確に指定 |
| DBがリセットされる | バックアップ未設定 | GCSバケットの権限とバックアップスクリプトを確認 |
| Cloud Runでタイムアウト | コールドスタート | `--min-instances=1`に変更（コスト増） |

### ログの確認方法

**OCI VM（systemd）**:
```bash
# リアルタイムログ
sudo journalctl -u vrc-calendar-bot -f

# 最近のエラー
sudo journalctl -u vrc-calendar-bot -p err --since "1 hour ago"
```

**Cloud Run**:
```bash
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=calendar-bot" --limit=50
```

### サービスの再起動

**OCI VM**:
```bash
sudo systemctl restart vrc-calendar-bot
```

**Cloud Run**:
```bash
# 新しいリビジョンをデプロイ（再起動と同等）
gcloud run services update calendar-bot --region=asia-northeast1
```

---

## コスト比較

### 方式A: OCI + GCP（推奨）

| サービス | 月額 | 備考 |
|----------|------|------|
| OCI VM | $0 | Always Free対象 |
| GCP Calendar API | $0 | 無料枠内 |
| GCP Storage | $0 | 5GB未満は無料 |
| GCP Secret Manager | $0 | 6シークレットまで無料 |
| Cloudflare Tunnel | $0 | Freeプラン |
| **合計** | **$0** | **完全無料** |

### 方式B: Cloud Run

| サービス | 月額 | 備考 |
|----------|------|------|
| Cloud Run | ~$0.10 | min-instances=0の場合 |
| Cloud Storage | ~$0.01 | 5GB未満 |
| Cloud Scheduler | $0 | 3ジョブまで無料 |
| Pub/Sub | $0 | 10GB/月まで無料 |
| Secret Manager | $0 | 6シークレットまで無料 |
| **合計** | **~$0.20** | **約30円/月** |

---

## リソースのクリーンアップ

### OCI

```bash
# VMインスタンスを終了（OCIコンソールから）
# または oci CLI を使用
oci compute instance terminate --instance-id <INSTANCE_ID>
```

### GCP

```bash
# Cloud Runサービス削除
gcloud run services delete calendar-bot --region=asia-northeast1

# Schedulerジョブ削除
gcloud scheduler jobs delete weekly-notification-job

# Pub/Sub削除
gcloud pubsub subscriptions delete weekly-notification-sub
gcloud pubsub topics delete weekly-notification-trigger

# シークレット削除
gcloud secrets delete DISCORD_BOT_TOKEN
gcloud secrets delete GEMINI_API_KEY

# バケット削除
gcloud storage rm -r gs://vrc-calendar-bot-backup

# プロジェクト全体を削除
gcloud projects delete vrc-calendar-bot
```
