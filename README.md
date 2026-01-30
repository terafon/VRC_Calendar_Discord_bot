# VRC Calendar Discord Bot

自然言語で予定管理ができるDiscord Botです。Googleカレンダーと連携し、「第2・第4水曜日」などの複雑な繰り返しパターンに対応しています。

## 特徴

- **自然言語による操作**: `/予定 第2・第4水曜日の14時に定例MTGを追加` のように入力して登録可能
- **Googleカレンダー同期**: 登録された予定は自動的にGoogleカレンダーに同期
- **OAuth 2.0認証**: ユーザー自身のGoogleアカウントで認証し、カレンダーに直接アクセス
- **週次通知**: 毎週月曜日9時にその週の予定を自動通知
- **複雑な繰り返し対応**: 毎週、隔週、第n週の指定が可能
- **マルチサーバー対応**: 各Discordサーバーのデータは完全に分離
- **低コスト運用**: OCI Always Free + GCP無料枠で完全無料

## ドキュメント

| ドキュメント | 説明 |
|-------------|------|
| [使い方ガイド](docs/USAGE.md) | ユーザー向けのコマンド説明と使用例 |
| [デプロイガイド](docs/DEPLOY.md) | OCI VM + GCPへのデプロイ手順 |
| [仕様書](docs/SPECIFICATION.md) | 技術仕様・アーキテクチャ・Firestore設計 |

## クイックスタート

### 必要な準備

1. Discord Bot Token（[Discord Developer Portal](https://discord.com/developers/applications)）
2. Gemini API Key（[Google AI Studio](https://aistudio.google.com/)）
3. Google Cloud プロジェクト（Calendar API、Firestore、OAuth同意画面）
4. OCI Always Free VM（[Oracle Cloud](https://www.oracle.com/cloud/free/)）
5. Cloudflare Tunnel（OAuth コールバック用HTTPS公開）

### ローカル実行

```bash
# 1. リポジトリをクローン
git clone https://github.com/your-username/VRC_Calendar_Discord_bot.git
cd VRC_Calendar_Discord_bot

# 2. 環境変数を設定
cp .env.example .env
# .env を編集して各キーを設定

# 3. サービスアカウントJSONを配置
# credentials.json をプロジェクトルートに配置

# 4. 依存関係をインストールして実行
pip install -r requirements.txt
python main.py
```

### 基本コマンド

```
/予定 毎週水曜14時に定例会議を追加
/予定 第2・第4金曜日の20時にゲーム会
/今週の予定
/予定一覧
/カレンダー認証        # OAuth認証を開始
/カレンダー認証状態     # 認証状態を確認
```

詳しい使い方は[使い方ガイド](docs/USAGE.md)を参照してください。

## 技術スタック

| カテゴリ | 技術 |
|---------|------|
| 言語 | Python 3.13 |
| Discord | discord.py 2.3.2 |
| NLP | Google Gemini 1.5 Flash |
| カレンダー | Google Calendar API |
| 認証 | OAuth 2.0（ユーザー認証） / サービスアカウント（フォールバック） |
| データベース | Cloud Firestore |
| インフラ | OCI Always Free VM + GCP無料枠 |

## システム構成

```
┌─────────────────────────────────────────────────┐
│              OCI VM (Always Free)               │
│  ┌──────────────┐    ┌────────────────────────┐ │
│  │ Flask Server │    │ Discord Bot            │ │
│  │ (HTTP/OAuth) │    │ (常時WebSocket接続)    │ │
│  └──────────────┘    └────────────────────────┘ │
└─────────────────────────────────────────────────┘
         │                      │
         ▼                      ▼
┌─────────────────┐    ┌────────────────────────┐
│ Cloudflare      │    │ Google Calendar API    │
│ Tunnel (HTTPS)  │    │ Gemini API             │
│                 │    │ Cloud Firestore        │
└─────────────────┘    └────────────────────────┘
```

## デプロイ

詳細は[デプロイガイド](docs/DEPLOY.md)を参照してください。

### コスト

| サービス | 月額コスト |
|---------|-----------|
| OCI VM (Always Free) | $0 |
| GCP（Firestore、Calendar API、Gemini、GCS） | $0（無料枠内） |
| Cloudflare Tunnel | $0 |
| **合計** | **$0（完全無料）** |

## 環境変数

| 変数名 | 説明 | 必須 |
|--------|------|------|
| `DISCORD_BOT_TOKEN` | Discord Botトークン | Yes |
| `GEMINI_API_KEY` | Gemini APIキー | Yes |
| `GCP_PROJECT_ID` | GCPプロジェクトID | Yes |
| `GOOGLE_APPLICATION_CREDENTIALS` | サービスアカウントJSONパス | Yes |
| `GOOGLE_OAUTH_CLIENT_ID` | OAuth クライアントID | Yes |
| `GOOGLE_OAUTH_CLIENT_SECRET` | OAuth クライアントシークレット | Yes |
| `OAUTH_REDIRECT_URI` | OAuthリダイレクトURI | Yes |
| `GCS_BUCKET_NAME` | バックアップ用GCSバケット名 | Yes |
| `GOOGLE_CALENDAR_ID` | デフォルトのカレンダーID（サーバーごとに上書き可） | No |
| `PORT` | HTTPポート（デフォルト: 8080） | No |
