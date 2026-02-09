# VRC Calendar Discord Bot

VRChatイベント（集会、ワールド紹介、アバター試着会など）を自然言語で管理できるDiscord Botです。Googleカレンダーと連携し、「第2・第4土曜日21時にVRC集会」などの複雑な繰り返しパターンに対応しています。

## 特徴

- **自然言語による操作**: `/予定 毎週土曜21時にVRC集会を追加` のように入力して登録可能
- **対話型情報収集**: 不足情報があればスレッド内でGeminiとの対話で補完
- **色の自動割当**: 繰り返しタイプ（毎週/隔週/月1回/第n週/不定期）に応じて色を自動設定
- **タググループ別選択**: タグをグループごとに分類し、各グループから1つずつ選択
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
| [運用ガイド](docs/OPERATIONS.md) | デプロイ後のメンテナンス・トラブルシューティング |
| [認証情報・有効期限ガイド](docs/CREDENTIALS.md) | 各サービスの認証情報の有効期限と更新手順 |
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
/予定 毎週土曜21時にVRC集会を追加
/予定 第2・第4金曜日の22時にワールド紹介会
/予定 VRC集会を登録して          # 対話モードで情報収集
/今週の予定
/予定一覧
/カレンダー 認証        # OAuth認証を開始
/色 初期設定            # 繰り返しタイプごとのデフォルト色を設定
/カレンダー 認証状態     # 認証状態を確認
```

詳しい使い方は[使い方ガイド](docs/USAGE.md)を参照してください。

## 対話型予定登録

情報が不足している場合、Botはスレッドを作成してGeminiとの対話で必要な情報を収集します。色は繰り返しタイプに基づいてシステムが自動で割り当てるため、ユーザーが指定する必要はありません。

```
ユーザー: /予定 VRC集会を登録して

Bot: (スレッド「予定管理: VRC集会を登録して」を作成)
Bot: 開催頻度を教えてください。
     - 毎週
     - 隔週
     - 第n週（例: 第2・第4週）
     - 不定期

ユーザー: 毎週です

Bot: 何曜日に開催しますか？

ユーザー: 土曜日

Bot: 開催時刻は何時ですか？

ユーザー: 21時から2時間

Bot: (確認Embed表示)
     色: セージ（自動割当）
     [確定] [修正] [キャンセル]

ユーザー: (確定ボタンを押す)

Bot: ✅ 予定を登録しました！
```

## 技術スタック

| カテゴリ | 技術 |
|---------|------|
| 言語 | Python 3.13 |
| Discord | discord.py 2.3.2 |
| NLP | Google Gemini 2.0 Flash（マルチターン会話対応） |
| カレンダー | Google Calendar API |
| 認証 | OAuth 2.0（ユーザー認証） |
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
| `PORT` | HTTPポート（デフォルト: 8080） | No |
