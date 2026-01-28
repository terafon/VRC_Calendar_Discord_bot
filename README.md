# VRC Calendar Discord Bot

自然言語で予定管理ができるDiscord Botです。Googleカレンダーと連携し、「第2・第4水曜日」などの複雑な繰り返しパターンに対応しています。

## 特徴

- **自然言語による操作**: `/予定 第2・第4水曜日の14時に定例MTGを追加` のように入力して登録可能
- **Googleカレンダー同期**: 登録された予定は自動的にGoogleカレンダーに同期
- **週次通知**: 毎週月曜日9時にその週の予定を自動通知
- **複雑な繰り返し対応**: 毎週、隔週、第n週の指定が可能
- **マルチサーバー対応**: 各Discordサーバーのデータは完全に分離
- **低コスト運用**: OCI Always Free（完全無料）またはCloud Run（月額約30円）

## ドキュメント

| ドキュメント | 説明 |
|-------------|------|
| [使い方ガイド](docs/USAGE.md) | ユーザー向けのコマンド説明と使用例 |
| [デプロイガイド](docs/DEPLOY.md) | Cloud Runへのデプロイ手順 |
| [仕様書](docs/SPECIFICATION.md) | 技術仕様・アーキテクチャ・DB設計 |

## クイックスタート

### 必要な準備

1. Discord Bot Token（[Discord Developer Portal](https://discord.com/developers/applications)）
2. Gemini API Key（[Google AI Studio](https://aistudio.google.com/)）
3. Google Cloud プロジェクト（Calendar API、Cloud Run等）

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
```

詳しい使い方は[使い方ガイド](docs/USAGE.md)を参照してください。

## 技術スタック

| カテゴリ | 技術 |
|---------|------|
| 言語 | Python 3.11 |
| Discord | discord.py 2.3.2 |
| NLP | Google Gemini 1.5 Flash |
| カレンダー | Google Calendar API |
| データベース | SQLite |
| インフラ | OCI Always Free VM または Cloud Run |

## システム構成

### 方式A: OCI Always Free + GCP（推奨・完全無料）

```
┌─────────────────────────────────────────────────┐
│              OCI VM (Always Free)               │
│  ┌──────────────┐    ┌────────────────────────┐ │
│  │ Flask Server │    │ Discord Bot            │ │
│  │ (HTTP)       │    │ (常時WebSocket接続)    │ │
│  └──────────────┘    └────────────────────────┘ │
└─────────────────────────────────────────────────┘
         │                      │
         ▼                      ▼
┌─────────────────┐    ┌────────────────────────┐
│ cron または     │    │ Google Calendar API    │
│ Cloud Scheduler │    │ Gemini API             │
│ (週次通知)      │    └────────────────────────┘
└─────────────────┘
```

### 方式B: Cloud Run（シンプル・低コスト）

```
┌─────────────────────────────────────────────────┐
│                   Cloud Run                     │
│  ┌──────────────┐    ┌────────────────────────┐ │
│  │ Flask Server │    │ Discord Bot            │ │
│  │ (HTTP)       │    │ (コールドスタート)     │ │
│  └──────────────┘    └────────────────────────┘ │
└─────────────────────────────────────────────────┘
         │                      │
         ▼                      ▼
┌─────────────────┐    ┌────────────────────────┐
│ Cloud Scheduler │    │ Google Calendar API    │
│ + Pub/Sub       │    │ Gemini API             │
│ (週次通知)      │    │ Cloud Storage (backup) │
└─────────────────┘    └────────────────────────┘
```

## デプロイ

詳細は[デプロイガイド](docs/DEPLOY.md)を参照してください。

### 方式比較

| 方式 | 月額コスト | 常時稼働 | おすすめ |
|------|-----------|----------|----------|
| OCI + GCP | **$0（完全無料）** | ◎ | 常時稼働が必要な場合 |
| Cloud Run | ~$0.20（30円） | △ | たまに使う程度の場合 |

## 環境変数

| 変数名 | 説明 | 必須 |
|--------|------|------|
| `DISCORD_BOT_TOKEN` | Discord Botトークン | Yes |
| `GEMINI_API_KEY` | Gemini APIキー | Yes |
| `GCP_PROJECT_ID` | GCPプロジェクトID | Yes |
| `GOOGLE_APPLICATION_CREDENTIALS` | サービスアカウントJSONパス | Yes |
| `GOOGLE_CALENDAR_ID` | デフォルトのカレンダーID（サーバーごとに上書き可） | No |
| `GCS_BUCKET_NAME` | バックアップ用バケット名（OCI VMなら不要） | No |
| `PORT` | HTTPポート（デフォルト: 8080） | No |

