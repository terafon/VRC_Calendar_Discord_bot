# VRC Calendar Discord Bot - デプロイガイド

このガイドでは、VRC Calendar Discord Botをデプロイする手順を説明します。

## 目次

1. [構成概要](#構成概要)
2. [OCI Always Free + GCP のセットアップ](#oci-always-free--gcp-のセットアップ)
3. [共通設定](#共通設定)
4. [OAuth 2.0 ユーザー認証の設定](#oauth-20-ユーザー認証の設定)
5. [トラブルシューティング](#トラブルシューティング)
6. [コスト](#コスト)
7. [リソースのクリーンアップ](#リソースのクリーンアップ)

---

## 構成概要

OCI Always Free VMでDiscord Botを常時稼働させ、GCPのAPIサービスをバックエンドとして利用する構成です。完全無料で運用できます。

### 構成図

```
                        ┌───────────────┐
                        │  Discord API  │
                        └───────┬───────┘
                          WebSocket
                           (常時接続)
                                │
┌───────────────────────────────┼─────────────────────────────────────┐
│  Oracle Cloud Infrastructure (Always Free)                          │
│  ┌────────────────────────────┼────────────────────────────────┐    │
│  │  VM.Standard.E2.1.Micro / Ampere A1 (Ubuntu)               │    │
│  │                            │                                │    │
│  │  ┌────────────────────┐    │    ┌────────────────────────┐  │    │
│  │  │ Discord Bot        │◄───┘    │ Flask Server (:8080)   │  │    │
│  │  │ (discord.py)       │         │ ┌──────────────────┐   │  │    │
│  │  │                    │         │ │ /weekly-          │   │  │    │
│  │  │ - スラッシュコマンド│         │ │   notification    │   │  │    │
│  │  │ - 予定CRUD         │         │ │ /oauth/callback   │   │  │    │
│  │  │ - Gemini NLP連携   │         │ └──────────────────┘   │  │    │
│  │  └────────┬───────────┘         └───────────▲────────────┘  │    │
│  │           │                                 │               │    │
│  │  ┌────────┴───────────┐          ┌──────────┴────────────┐  │    │
│  │  │ cron               │          │ cloudflared           │  │    │
│  │  │ - 週次通知 (月曜9時)│          │ (Cloudflare Tunnel)   │  │    │
│  │  │ - バックアップ(6h) │          └──────────▲────────────┘  │    │
│  │  └────────────────────┘                     │               │    │
│  └─────────────────────────────────────────────┼───────────────┘    │
└─────────────────────────────────────────────────┼───────────────────┘
                                                  │
                                    HTTPS (bot.yourdomain.com)
                                                  │
┌─────────────────────────────────────────────────┼───────────────────┐
│  Cloudflare (Free)                              │                   │
│  ┌──────────────────────────────────────────────┴────────────────┐  │
│  │ DNS + Tunnel                                                  │  │
│  │ bot.yourdomain.com ──► OCI VM :8080                           │  │
│  └───────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
          ▲                         ▲
          │ OAuth リダイレクト       │ Google認証
          │                         │
      ┌───┴────┐             ┌──────┴──────┐
      │ ユーザー│────────────►│ Google 認証 │
      │ブラウザ │  ログイン   │ 画面        │
      └────────┘             └─────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  Google Cloud Platform (無料枠)                                      │
│                                                                     │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────┐ │
│  │ Calendar API    │  │ Gemini API      │  │ Cloud Firestore     │ │
│  │                 │  │                 │  │                     │ │
│  │ 予定の作成/更新 │  │ 自然言語解析    │  │ 予定・設定・タグ    │ │
│  │ /削除/取得      │  │ (1.5 Flash)     │  │ OAuthトークン       │ │
│  └─────────────────┘  └─────────────────┘  └─────────────────────┘ │
│                                                                     │
│  ┌─────────────────┐  ┌─────────────────┐                          │
│  │ Cloud Storage   │  │ Secret Manager  │                          │
│  │ (US リージョン) │  │                 │                          │
│  │ Firestoreの     │  │ Bot Token 等の  │                          │
│  │ 自動バックアップ│  │ シークレット管理│                          │
│  └─────────────────┘  └─────────────────┘                          │
└─────────────────────────────────────────────────────────────────────┘
```

---

## OCI Always Free + GCP のセットアップ

### 1. OCI（Oracle Cloud）のセットアップ

#### 1.1 OCIアカウントの作成

1. [Oracle Cloud Free Tier](https://www.oracle.com/cloud/free/)にアクセス
2. 「無料で始める」をクリック
3. 必要情報を入力（クレジットカード登録が必要だが課金されない）
4. **ホームリージョン**を選択（後から変更不可。東京推奨）

> **重要**: Always Free VMはホームリージョンでのみ作成可能です

#### 1.2 VMインスタンスの作成

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

#### 1.3 セキュリティリストの設定

OCIコンソールで「ネットワーキング」→「仮想クラウド・ネットワーク」→ VCN選択 →「セキュリティ・リスト」

**イングレス・ルールを追加**:

| ソースCIDR | プロトコル | 宛先ポート | 説明 |
|------------|-----------|-----------|------|
| 0.0.0.0/0 | TCP | 22 | SSH |
| 0.0.0.0/0 | TCP | 80 | HTTP（Cloudflare Tunnel用） |
| 0.0.0.0/0 | TCP | 443 | HTTPS（Cloudflare Tunnel用） |

#### 1.4 VMへのSSH接続

```bash
# [ローカルマシンで実行] SSHで接続
ssh -i ~/.ssh/your_private_key ubuntu@<VM_PUBLIC_IP>

# [OCI VM上で実行] 初回接続時にパッケージを更新
sudo apt update && sudo apt upgrade -y
```

#### 1.5 必要なパッケージのインストール

```bash
# [OCI VM上で実行]
# Python環境とGitをインストール
sudo apt install -y python3 python3-venv python3-pip git

# バージョン確認
python3 --version  # 3.13以上を推奨
```

> **Python 3.13未満の場合**: `requirements.txt`に含まれる`audioop-lts`はPython 3.13以上が必要です。
> 3.12以下の場合は以下のいずれかの対応が必要です。
>
> **対応1: Python 3.13にアップグレード（推奨）**
>
> ```bash
> # [OCI VM上で実行（Ubuntu）]
> # deadsnakes PPA を追加
> sudo add-apt-repository ppa:deadsnakes/ppa -y
> sudo apt update
>
> # Python 3.13 をインストール
> sudo apt install python3.13 python3.13-venv python3.13-dev -y
>
> # 確認
> python3.13 --version
>
> # venv を Python 3.13 で作り直す
> rm -rf .venv
> python3.13 -m venv .venv
> source .venv/bin/activate
> pip install --upgrade pip
> pip install -r requirements.txt
> ```
>
> **対応2: audioop-lts を requirements.txt から削除**
>
> Python 3.12以下には`audioop`モジュールが組み込まれているため、`audioop-lts`は不要です。
> ```bash
> # [OCI VM上で実行]
> sed -i '/audioop-lts/d' requirements.txt
> pip install -r requirements.txt
> ```

#### 1.6 プロジェクトのセットアップ

```bash
# [OCI VM上で実行]
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

#### 1.7 環境変数ファイルの作成

.envファイルは、Botが動作するために必要な設定値をまとめたファイルです。以下の手順で各値を取得し、設定してください。

```bash
# [OCI VM上で実行]
# .envファイルを作成
cp .env.example .env
nano .env
```

---

##### 各環境変数の取得方法

###### 1. DISCORD_BOT_TOKEN（Discord Botトークン）

**取得場所**: [Discord Developer Portal](https://discord.com/developers/applications)

**手順**:
1. Discord Developer Portalにログイン
2. 左メニューから対象のアプリケーションを選択（なければ「New Application」で作成）
3. 左メニュー「Bot」をクリック
4. 「Token」セクションの「Reset Token」をクリック
5. 表示されたトークンをコピー（**一度しか表示されないので必ずコピー**）

```
例: MTIzNDU2Nzg5MDEyMzQ1Njc4OQ.ABcDeF.abcdefghijklmnopqrstuvwxyz123456
```

> **注意**: トークンは絶対に公開しないでください。GitHubにpushするとBotが乗っ取られる可能性があります。

---

###### 2. GCP_PROJECT_ID（GCPプロジェクトID）

**取得場所**: [Google Cloud Console](https://console.cloud.google.com/)

**手順**:
1. Google Cloud Consoleにログイン
2. 画面上部のプロジェクト選択ドロップダウンをクリック
3. 使用するプロジェクトの「ID」列をコピー（名前ではなくID）

```
例: vrc-calendar-bot-12345
```

**プロジェクトがない場合**:
```bash
# [ローカルマシンで実行]
# gcloud CLIで新規作成
gcloud projects create vrc-calendar-bot --name="VRC Calendar Bot"
```

---

###### 3. GOOGLE_CALENDAR_ID（GoogleカレンダーID）【オプション】

> **サーバーごとに異なるカレンダーを使う場合**: Discord上で`/カレンダー追加`と`/カレンダー使用`コマンドで設定できます。この環境変数はデフォルト値として使われます。

**取得場所**: [Googleカレンダー](https://calendar.google.com/)

**手順**:
1. Googleカレンダーを開く
2. 左サイドバーで使用するカレンダーの「⋮」（3点メニュー）をクリック
3. 「設定と共有」をクリック
4. 下にスクロールして「カレンダーの統合」セクションを見つける
5. 「カレンダーID」をコピー

```
例（メインカレンダー）: primary
例（追加カレンダー）: abc123xyz@group.calendar.google.com
```

---

###### 4. GOOGLE_APPLICATION_CREDENTIALS（サービスアカウントJSONのパス）

これはファイルのパスを指定します。実際のJSONファイルは別途配置する必要があります（次のセクションで説明）。

**OCI VMの場合**:
```
/home/ubuntu/VRC_Calendar_Discord_bot/credentials.json
```

**ローカル開発の場合**:
```
./credentials.json
```

---

###### 5. GCS_BUCKET_NAME（Cloud Storageバケット名）

Firestoreデータの自動バックアップに使用するGCSバケットです。

**取得場所**: [Google Cloud Console > Cloud Storage](https://console.cloud.google.com/storage/browser)

**手順（バケットがある場合）**:
1. Cloud Storageのブラウザを開く
2. 使用するバケット名をコピー

**手順（バケットを新規作成する場合）**:
```bash
# [ローカルマシンで実行]
# gcloud CLIでバケット作成（無料枠: US リージョン 5GB）
gcloud storage buckets create gs://your-bucket-name \
  --location=us-central1 \
  --uniform-bucket-level-access
```

```
例: vrc-calendar-bot-backup
```

> **注意**: バケット名はグローバルで一意である必要があります。既に使われている名前は使用できません。
> **無料枠**: US リージョン（`us-east1`, `us-west1`, `us-central1`）に作成すれば 5GB まで無料です。

---

###### 6. GEMINI_API_KEY（Gemini APIキー）

**取得場所**: [Google AI Studio](https://aistudio.google.com/)

**手順**:
1. Google AI Studioにログイン
2. 左メニューの「Get API Key」をクリック
3. 「Create API Key」をクリック
4. プロジェクトを選択（任意）して「Create」
5. 表示されたAPIキーをコピー

```
例: AIzaSyAbCdEfGhIjKlMnOpQrStUvWxYz123456
```

---

###### 7. GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET / OAUTH_REDIRECT_URI（OAuth認証用）

OAuth 2.0 ユーザー認証で使用する値です。取得手順は [OAuth 2.0 ユーザー認証の設定](#oauth-20-ユーザー認証の設定) を参照してください。

- `GOOGLE_OAUTH_CLIENT_ID`: GCPで作成したOAuthクライアントID
- `GOOGLE_OAUTH_CLIENT_SECRET`: OAuthクライアントシークレット
- `OAUTH_REDIRECT_URI`: Cloudflare Tunnel で公開したコールバックURL（例: `https://bot.yourdomain.com/oauth/callback`）

---

###### 8. PORT（サーバーポート）

Flask/HTTPサーバーが使用するポート番号です。通常は変更不要。

```
デフォルト: 8080
```

---

##### 設定例（すべて入力後）

```bash
# Discord
DISCORD_BOT_TOKEN=MTIzNDU2Nzg5MDEyMzQ1Njc4OQ.ABcDeF.abcdefghijklmnopqrstuvwxyz123456

# Google Cloud
GCP_PROJECT_ID=vrc-calendar-bot-12345
GOOGLE_APPLICATION_CREDENTIALS=/home/ubuntu/VRC_Calendar_Discord_bot/credentials.json

# Googleカレンダー（オプション: サーバーごとに /カレンダー追加 と /カレンダー使用 で上書き可能）
GOOGLE_CALENDAR_ID=abc123xyz@group.calendar.google.com

# Cloud Storage（Firestoreバックアップ用）
GCS_BUCKET_NAME=vrc-calendar-bot-backup

# Gemini API
GEMINI_API_KEY=AIzaSyAbCdEfGhIjKlMnOpQrStUvWxYz123456

# Google OAuth（カレンダー認証用）
GOOGLE_OAUTH_CLIENT_ID=xxxxxxxxxxxx.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=GOCSPX-xxxxxxxxxx
OAUTH_REDIRECT_URI=https://bot.yourdomain.com/oauth/callback

# Server
PORT=8080
```

> **重要**:
> - 値にクォート（`"`や`'`）は**不要**です
> - 値の前後にスペースを入れないでください
> - `=`の前後にもスペースを入れないでください

**保存**: `Ctrl+X` → `Y` → `Enter`

---

#### 1.8 GCPサービスアカウント鍵の配置（credentials.json）

`.env`で指定したパスに、GCPサービスアカウントのJSONファイルを配置する必要があります。

##### サービスアカウントJSONの取得方法

**方法1: gcloud CLIで取得（推奨）**

```bash
# [ローカルマシンで実行]
# サービスアカウントがなければ作成
gcloud iam service-accounts create calendar-bot-sa \
  --display-name="Calendar Bot Service Account"

# JSONキーをダウンロード
gcloud iam service-accounts keys create credentials.json \
  --iam-account=calendar-bot-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com
```

**方法2: GCPコンソールから取得**

1. [IAMと管理 > サービスアカウント](https://console.cloud.google.com/iam-admin/serviceaccounts)を開く
2. 対象のサービスアカウントをクリック
3. 「キー」タブをクリック
4. 「鍵を追加」→「新しい鍵を作成」
5. 「JSON」を選択して「作成」
6. ファイルが自動的にダウンロードされる

##### VMへの転送方法

**方法1: SCPで転送（推奨）**

```bash
# [ローカルマシンで実行]
scp -i ~/.ssh/your_private_key credentials.json ubuntu@<VM_PUBLIC_IP>:/home/ubuntu/VRC_Calendar_Discord_bot/
```

**方法2: 手動でコピー&ペースト**

1. ローカルでJSONファイルを開いてすべてコピー
2. VMで以下を実行:
```bash
# [OCI VM上で実行]
nano /home/ubuntu/VRC_Calendar_Discord_bot/credentials.json
```
3. JSONをペースト
4. `Ctrl+X` → `Y` → `Enter` で保存

##### サービスアカウントへの権限付与

サービスアカウントには以下の権限が必要です:

```bash
# [ローカルマシンで実行]
# Secret Managerへのアクセス権（GCP Secret Managerを使う場合）
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:calendar-bot-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

# Cloud Storageへのアクセス権（バックアップ用）
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:calendar-bot-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"
```

#### 1.9 動作テスト

```bash
# [OCI VM上で実行]
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

#### 1.10 systemdサービスの設定（常駐化）

```bash
# [OCI VM上で実行]
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
# [OCI VM上で実行]
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
# [OCI VM上で実行]
# リアルタイムログ
sudo journalctl -u vrc-calendar-bot -f

# 最近のログ（100行）
sudo journalctl -u vrc-calendar-bot -n 100

# エラーのみ
sudo journalctl -u vrc-calendar-bot -p err
```

### 2. 週次通知の設定

週次通知には2つの方法があります。

#### 方法1: cronジョブ（シンプル・推奨）

OCI VM上で直接cronを使う最もシンプルな方法:

```bash
# [OCI VM上で実行]
# cronジョブを編集
crontab -e

# 以下を追加（毎週月曜9:00 JST）
0 9 * * 1 curl -X POST http://localhost:8080/weekly-notification -H "Content-Type: application/json" -d '{"message":{"data":""}}'
```

> **注意**: サーバーのタイムゾーンがJSTでない場合は調整が必要です。

```bash
# [OCI VM上で実行]
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
# [OCI VM上で実行]
# Ubuntu/Debian
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o cloudflared.deb
sudo dpkg -i cloudflared.deb

# または Arm VM の場合
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb -o cloudflared.deb
sudo dpkg -i cloudflared.deb
```

**3. Tunnelの作成と認証**

```bash
# [OCI VM上で実行]
# Cloudflareにログイン（ブラウザが開く）
cloudflared tunnel login

# Tunnelを作成
cloudflared tunnel create vrc-calendar-bot

# 作成されたTunnel IDを確認
cloudflared tunnel list
```

**4. DNSルーティングの設定**

```bash
# [OCI VM上で実行]
# サブドメインをTunnelにルーティング
cloudflared tunnel route dns vrc-calendar-bot bot.yourdomain.com
```

**5. 設定ファイルの作成**

```bash
# [OCI VM上で実行]
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
# [OCI VM上で実行]
sudo cloudflared service install
sudo systemctl enable --now cloudflared

# 状態確認
sudo systemctl status cloudflared
```

**7. GCP側のPub/Sub設定**

```bash
# [ローカルマシンで実行]
# Pub/Subサブスクリプションを作成（Push先をCloudflare Tunnelに）
gcloud pubsub subscriptions create weekly-notification-sub \
  --topic=weekly-notification-trigger \
  --push-endpoint="https://bot.yourdomain.com/weekly-notification" \
  --push-auth-service-account=calendar-bot-sa@YOUR_PROJECT.iam.gserviceaccount.com
```

### 3. バックアップの自動化

FirestoreのデータをJSON形式でGCSに自動バックアップします。

#### 3.1 GCSバケットの作成

まだバケットを作成していない場合は、以下のコマンドで作成します:

```bash
# [ローカルマシンで実行]
gcloud storage buckets create gs://your-bucket-name \
  --location=us-central1 \
  --uniform-bucket-level-access
```

> **無料枠**: US リージョン（`us-east1`, `us-west1`, `us-central1`）に作成すれば 5GB まで無料です。

#### 3.2 サービスアカウントへのGCS権限付与

```bash
# [ローカルマシンで実行]
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:calendar-bot-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"
```

#### 3.3 環境変数の設定

`.env` に `GCS_BUCKET_NAME` を設定してください:

```bash
GCS_BUCKET_NAME=your-bucket-name
```

#### 3.4 手動でバックアップをテスト

```bash
# [OCI VM上で実行]
source .venv/bin/activate
cd /home/ubuntu/VRC_Calendar_Discord_bot
python firestore_backup.py
```

成功すると `gs://your-bucket-name/firestore_backup/YYYYMMDD_HHMMSS.json` にアップロードされます。

#### 3.5 cronで自動バックアップを設定

```bash
# [OCI VM上で実行]
# バックアップスクリプトを作成
nano /home/ubuntu/VRC_Calendar_Discord_bot/backup.sh
```

```bash
#!/bin/bash
source /home/ubuntu/VRC_Calendar_Discord_bot/.venv/bin/activate
cd /home/ubuntu/VRC_Calendar_Discord_bot
python firestore_backup.py
```

```bash
# [OCI VM上で実行]
# 実行権限を付与
chmod +x /home/ubuntu/VRC_Calendar_Discord_bot/backup.sh

# cronで定期実行（6時間ごと）
crontab -e
# 以下を追加
0 */6 * * * /home/ubuntu/VRC_Calendar_Discord_bot/backup.sh >> /var/log/backup.log 2>&1
```

> バックアップは最新30件を保持し、古いものは自動削除されます。

#### 3.6 リストア（復元）

```bash
# [OCI VM上で実行]
source .venv/bin/activate
cd /home/ubuntu/VRC_Calendar_Discord_bot

# バックアップファイル一覧を確認
gsutil ls gs://your-bucket-name/firestore_backup/

# リストア実行（既存データを上書きします）
python firestore_backup.py --restore firestore_backup/20240101_120000.json
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

### Gemini APIキーの取得

1. [Google AI Studio](https://aistudio.google.com/)にアクセス
2. 「Get API Key」をクリック
3. 「Create API Key」でキーを生成
4. GCPプロジェクトを選択（Optional: 統合管理する場合）

---

## OAuth 2.0 ユーザー認証の設定

OAuth 2.0 を使ってユーザー自身のGoogleカレンダーに直接アクセスします。
サービスアカウントへのカレンダー共有設定は不要です。

### 前提条件

OAuth 認証では、Google がユーザーのブラウザを Bot の Flask サーバー（`/oauth/callback`）にリダイレクトします。
そのため、**Flask サーバーに外部から HTTPS でアクセスできる環境**が必要です。

OAuth を使うには **Cloudflare Tunnel の設定が必要**です（週次通知を cron で運用している場合でも必須）。
Tunnel の設定手順は [Cloudflare Tunnelのセットアップ](#cloudflare-tunnelのセットアップ) を参照してください。

### OAuth の認証フロー

```
1. ユーザーが Discord で /カレンダー認証 を実行
2. Bot が OAuth認証URL を ephemeral メッセージで送信
3. ユーザーがブラウザで Google認証 → カレンダーアクセスを許可
4. Google が Flask の /oauth/callback にリダイレクト
5. コールバックで state 検証 → コードをトークンに交換 → Firestore に保存
6. ブラウザに「認証成功」ページを表示
7. 以降、Bot はそのトークンでユーザーのカレンダーを操作
```

### O-1. OAuth同意画面の設定

> **重要**: OAuthクライアントIDを作成する前に、まず同意画面の設定が必要です。
> 同意画面が未設定の場合、認証情報の作成時にエラーになります。

1. [Google Cloud Console > APIとサービス > OAuth同意画面](https://console.cloud.google.com/apis/credentials/consent) にアクセス
2. **User Type**: 「外部」を選択して「作成」をクリック
3. **アプリ情報**を入力:
   - **アプリ名**: VRC Calendar Bot（ユーザーに表示される名前）
   - **ユーザーサポートメール**: 自分のメールアドレスを選択
   - **アプリのロゴ**: 省略可
4. **アプリのドメイン**: 省略可（テスト段階では不要）
5. **デベロッパーの連絡先情報**: 自分のメールアドレスを入力
6. 「保存して次へ」をクリック
7. **スコープ**画面:
   - 「スコープを追加または削除」をクリック
   - フィルタで `calendar` を検索
   - `https://www.googleapis.com/auth/calendar`（Google Calendar API）にチェック
   - 「更新」→「保存して次へ」
8. **テストユーザー**画面:
   - 「ADD USERS」をクリック
   - カレンダー認証に使用するGoogleアカウントのメールアドレスを追加
   - 「保存して次へ」
9. **概要**を確認して「ダッシュボードに戻る」

> **注意**: OAuth同意画面が「テスト」モードの場合、テストユーザーとして追加されたGoogleアカウントのみ認証が可能です。
> 一般公開する場合はGoogleの審査が必要です。

### O-2. GCPコンソールでOAuthクライアントIDを作成

1. [Google Cloud Console > APIとサービス > 認証情報](https://console.cloud.google.com/apis/credentials) にアクセス
2. 「認証情報を作成」→「OAuthクライアントID」をクリック
3. **アプリケーションの種類**: 「ウェブアプリケーション」を選択
4. **名前**: 任意（例: `VRC Calendar Bot OAuth`）
5. **承認済みのリダイレクトURI**: 「URIを追加」をクリックし、Bot がアクセス可能な URL を入力
   ```
   https://bot.yourdomain.com/oauth/callback
   ```
   > Cloudflare Tunnel 使用時は `https://` が必要です
6. 「作成」をクリック
7. 表示された **クライアントID** と **クライアントシークレット** を控える

### O-3. 環境変数の設定

`.env` ファイルに以下を追加:

```bash
# Google OAuth（カレンダー認証用）
GOOGLE_OAUTH_CLIENT_ID=xxxxxxxxxxxx.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=GOCSPX-xxxxxxxxxx
OAUTH_REDIRECT_URI=https://bot.yourdomain.com/oauth/callback
```

> **OAUTH_REDIRECT_URI** は O-2 で設定した「承認済みのリダイレクトURI」と完全に一致する必要があります。

### O-4. Discordでの使い方

| コマンド | 説明 | 必要権限 |
|---------|------|---------|
| `/カレンダー認証` | OAuth認証URLを取得（ephemeral） | manage_guild |
| `/カレンダー認証解除` | OAuth認証を解除 | manage_guild |
| `/カレンダー認証状態` | 認証方式・状態を確認 | manage_guild |

### O-5. 認証の優先順位

Bot は以下の優先順位でカレンダーにアクセスします:

1. **OAuth トークン**（`/カレンダー認証` で設定）
2. **サービスアカウント**（`/カレンダー使用` で設定）
3. **デフォルト**（環境変数のサービスアカウント）

OAuth が設定されている場合、サービスアカウントよりも優先されます。
OAuth トークンが失効した場合はサービスアカウントにフォールバックします。

---

## トラブルシューティング

### よくある問題と解決策

| 症状 | 原因 | 解決策 |
|------|------|--------|
| Botがオフラインのまま | トークンが無効 | Discord Developer Portalで新しいトークンを生成 |
| スラッシュコマンドが表示されない | コマンド未同期 | Botを再起動、または1時間待つ |
| カレンダー登録エラー | 権限不足 | `/カレンダー認証状態` で認証状態を確認、必要に応じて `/カレンダー認証` で再認証 |
| 「曜日を特定できませんでした」 | NLP解析失敗 | 「毎週水曜14時に〜」など明確に指定 |
| `audioop-lts`のインストールエラー | Python 3.13未満 | Python 3.13にアップグレードするか、`audioop-lts`を`requirements.txt`から削除（[1.5参照](#15-必要なパッケージのインストール)） |
| バックアップが失敗する | GCS権限不足 | サービスアカウントに`roles/storage.objectAdmin`を付与、`GCS_BUCKET_NAME`が正しいか確認 |
| OAuth認証で「redirect_uri_mismatch」 | リダイレクトURI不一致 | GCPコンソールの承認済みURIと`OAUTH_REDIRECT_URI`が完全一致しているか確認 |
| OAuth認証で「access_denied」 | 同意画面のテストユーザー未追加 | OAuth同意画面でテストユーザーにGoogleアカウントを追加 |
| OAuth認証後にカレンダー操作エラー | トークン期限切れ | `/カレンダー認証` で再認証するか、Google側でアクセスを取消していないか確認 |

### ログの確認方法

```bash
# [OCI VM上で実行]
# リアルタイムログ
sudo journalctl -u vrc-calendar-bot -f

# 最近のエラー
sudo journalctl -u vrc-calendar-bot -p err --since "1 hour ago"
```

### サービスの再起動

```bash
# [OCI VM上で実行]
sudo systemctl restart vrc-calendar-bot
```

---

## コスト

| サービス | 月額 | 備考 |
|----------|------|------|
| OCI VM | $0 | Always Free対象 |
| GCP Calendar API | $0 | 無料枠内 |
| GCP Firestore | $0 | 無料枠（1GiB, 50K reads/日）内 |
| GCP Storage | $0 | 5GB未満は無料 |
| GCP Secret Manager | $0 | 6シークレットまで無料 |
| Cloudflare Tunnel | $0 | Freeプラン（OAuth利用時は必須） |
| **合計** | **$0** | **完全無料** |

---

## リソースのクリーンアップ

### OCI

```bash
# [ローカルマシンで実行]
# VMインスタンスを終了（OCIコンソールから）
# または oci CLI を使用
oci compute instance terminate --instance-id <INSTANCE_ID>
```

### GCP

```bash
# [ローカルマシンで実行]
# シークレット削除
gcloud secrets delete DISCORD_BOT_TOKEN
gcloud secrets delete GEMINI_API_KEY

# バケット削除
gcloud storage rm -r gs://vrc-calendar-bot-backup

# プロジェクト全体を削除
gcloud projects delete vrc-calendar-bot
```
