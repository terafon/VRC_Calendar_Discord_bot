# VRC Calendar Discord Bot - 仕様書

## 1. 概要

VRC Calendar Discord Botは、Discord上で自然言語を使って予定を管理できるBotです。Googleカレンダーと連携し、予定の登録・編集・削除・検索を行えます。また、毎週月曜日に週間予定を自動通知する機能を備えています。

## 2. 機能一覧

### 2.1 コア機能

| 機能 | 説明 |
|------|------|
| 予定追加 | 自然言語で予定を登録（Googleカレンダーに同期） |
| 予定編集 | 既存予定の時刻・内容を変更 |
| 予定削除 | 予定を削除（論理削除） |
| 予定検索 | 期間・タグ・名前で予定を検索 |
| 今週の予定 | 今週の予定一覧を表示 |
| 予定一覧 | 登録されている繰り返し予定のマスター一覧 |
| 週次通知 | 毎週月曜日の指定時刻に今週の予定を自動通知 |

### 2.2 繰り返しパターン

| パターン | 説明 | 例 |
|----------|------|-----|
| `weekly` | 毎週 | 毎週水曜日 |
| `biweekly` | 隔週 | 隔週金曜日 |
| `nth_week` | 第n週 | 第2・第4水曜日 |
| `irregular` | 不定期 | 個別に日時を指定 |

## 3. システムアーキテクチャ

```
┌─────────────────────────────────────────────────────────────────┐
│                         Cloud Run                               │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                      main.py                             │   │
│  │  ┌─────────────┐    ┌─────────────────────────────────┐  │   │
│  │  │   Flask     │    │     Discord Bot (discord.py)   │  │   │
│  │  │   Server    │    │                                 │  │   │
│  │  └──────┬──────┘    └──────────────┬──────────────────┘  │   │
│  └─────────┼──────────────────────────┼─────────────────────┘   │
│            │                          │                         │
└────────────┼──────────────────────────┼─────────────────────────┘
             │                          │
             │                          │
┌────────────▼────────────┐  ┌──────────▼───────────┐
│   Cloud Pub/Sub         │  │      Discord API     │
│   (週次通知トリガー)     │  │                      │
└─────────────────────────┘  └──────────────────────┘
             │                          │
┌────────────▼────────────┐             │
│   Cloud Scheduler       │             │
│   (毎週月曜 9:00 JST)   │             │
└─────────────────────────┘             │
                                        │
┌───────────────────────────────────────▼───────────────────────┐
│                    External Services                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐ │
│  │ Google       │  │ Gemini API   │  │ Cloud Storage        │ │
│  │ Calendar API │  │ (NLP処理)    │  │ (DBバックアップ)     │ │
│  └──────────────┘  └──────────────┘  └──────────────────────┘ │
└───────────────────────────────────────────────────────────────┘
```

## 4. データベース設計

> **重要**: すべてのデータはDiscordサーバー（guild）ごとに分離されています。
> あるサーバーのユーザーは他のサーバーのデータを閲覧・操作できません。

### 4.1 eventsテーブル（予定マスター）

| カラム名 | 型 | 説明 |
|----------|------|------|
| id | INTEGER | 主キー（自動採番） |
| **guild_id** | TEXT | **DiscordサーバーID（必須）** |
| event_name | TEXT | 予定名 |
| tags | TEXT (JSON) | タグ配列 |
| recurrence | TEXT | 繰り返しタイプ |
| nth_weeks | TEXT (JSON) | 第n週のリスト |
| event_type | TEXT | イベント種類 |
| time | TEXT | 開始時刻（HH:MM形式） |
| weekday | INTEGER | 曜日（0=月〜6=日） |
| duration_minutes | INTEGER | 所要時間（分） |
| description | TEXT | 説明 |
| color_name | TEXT | 色名（プリセット名） |
| urls | TEXT (JSON) | URL配列（Twitter等） |
| google_calendar_events | TEXT (JSON) | Googleカレンダーイベント情報 |
| discord_channel_id | TEXT | Discord通知先チャンネル |
| created_by | TEXT | 作成者のDiscord User ID |
| created_at | TIMESTAMP | 作成日時 |
| updated_at | TIMESTAMP | 更新日時 |
| is_active | BOOLEAN | 有効フラグ（論理削除用） |

### 4.2 irregular_eventsテーブル（不定期予定）

| カラム名 | 型 | 説明 |
|----------|------|------|
| id | INTEGER | 主キー |
| event_id | INTEGER | 予定マスターID（外部キー） |
| event_date | DATE | 予定日 |
| event_time | TEXT | 予定時刻 |
| google_calendar_event_id | TEXT | GoogleカレンダーイベントID |
| created_at | TIMESTAMP | 作成日時 |

### 4.3 settingsテーブル（設定情報）

| カラム名 | 型 | 説明 |
|----------|------|------|
| key | TEXT | 設定キー（主キー） |
| value | TEXT | 設定値 |
| updated_at | TIMESTAMP | 更新日時 |

### 4.4 color_presetsテーブル（色プリセット）

| カラム名 | 型 | 説明 |
|----------|------|------|
| **guild_id** | TEXT | **DiscordサーバーID（複合主キー）** |
| name | TEXT | 色名（複合主キー） |
| color_id | TEXT | Googleカレンダー colorId |
| description | TEXT | 説明 |

### 4.5 tag_groups / tags テーブル

#### tag_groups
| カラム名 | 型 | 説明 |
|----------|------|------|
| id | INTEGER | 主キー |
| **guild_id** | TEXT | **DiscordサーバーID（UNIQUE制約）** |
| name | TEXT | グループ名（guild_idとUNIQUE制約） |
| description | TEXT | 説明 |

> サーバーごとに最大3グループまで作成可能

#### tags
| カラム名 | 型 | 説明 |
|----------|------|------|
| id | INTEGER | 主キー |
| group_id | INTEGER | タググループID（外部キー） |
| name | TEXT | タグ名（group_idとUNIQUE制約） |
| description | TEXT | 説明 |

### 4.6 calendar_accounts / guild_settings テーブル

#### calendar_accounts
| カラム名 | 型 | 説明 |
|----------|------|------|
| id | INTEGER | 主キー |
| **guild_id** | TEXT | **DiscordサーバーID（UNIQUE制約）** |
| name | TEXT | アカウント名（guild_idとUNIQUE制約） |
| calendar_id | TEXT | GoogleカレンダーID |
| credentials_path | TEXT | 認証ファイルパス（省略時はデフォルト使用） |

#### guild_settings
| カラム名 | 型 | 説明 |
|----------|------|------|
| guild_id | TEXT | DiscordサーバーID（主キー） |
| calendar_account_id | INTEGER | 使用するカレンダーアカウントID |

## 5. API設計

### 5.1 Discord スラッシュコマンド

#### `/予定`
メインの予定管理コマンド。自然言語でアクションを指定。

| パラメータ | 説明 | 例 |
|-----------|------|-----|
| メッセージ | 予定操作の自然言語指示 | "毎週水曜14時に定例会議を追加" |

#### `/今週の予定`
今週の予定一覧をEmbed形式で表示。パラメータなし。

#### `/予定一覧`
登録されている繰り返し予定のマスター一覧を表示。パラメータなし。

### 5.2 HTTPエンドポイント

#### `GET /health`
ヘルスチェック用エンドポイント。

- レスポンス: `200 OK`

#### `POST /weekly-notification`
週次通知のPub/Subハンドラー。

- リクエスト: Pub/Subメッセージ形式
- レスポンス: `204 No Content`（成功時）

## 6. 自然言語処理

### 6.1 NLPスキーマ

Gemini APIを使用してユーザーメッセージを以下のJSONに変換：

```json
{
  "action": "add|edit|delete|search",
  "event_name": "予定名",
  "tags": ["タグ1", "タグ2"],
  "recurrence": "weekly|biweekly|nth_week|irregular",
  "nth_weeks": [2, 4],
  "event_type": "種類",
  "time": "14:00",
  "weekday": 2,
  "duration_minutes": 60,
  "description": "説明",
  "color_name": "色名",
  "urls": ["https://twitter.com/...", "https://example.com"],
  "search_query": {
    "date_range": "today|this_week|next_week|this_month",
    "tags": ["タグ"],
    "event_name": "部分一致文字列"
  }
}
```

### 6.2 曜日マッピング

| 曜日 | 値 |
|------|-----|
| 月曜 | 0 |
| 火曜 | 1 |
| 水曜 | 2 |
| 木曜 | 3 |
| 金曜 | 4 |
| 土曜 | 5 |
| 日曜 | 6 |

## 7. Googleカレンダー連携

### 7.1 認証方式

サービスアカウント認証を使用。カレンダーへの書き込み権限が必要。

### 7.2 スコープ

```
https://www.googleapis.com/auth/calendar
```

### 7.3 タグと色の対応

| タグ | 色ID | 色 |
|------|------|-----|
| 重要 | 11 | 赤 |
| チームミーティング | 9 | 青 |
| 個人 | 2 | 緑 |

## 8. バックアップ機構

### 8.1 バックアップタイミング

- **起動時**: Cloud Storageから最新のDBをダウンロード（リストア）
- **定期実行**: 6時間ごとにDBをCloud Storageにアップロード

### 8.2 保存先

```
gs://{GCS_BUCKET_NAME}/calendar.db
```

## 9. セキュリティ

### 9.1 シークレット管理

1. **Secret Manager（推奨）**: GCP Secret Managerに保存
2. **環境変数（フォールバック）**: .envファイルから読み込み

### 9.2 保護対象

- `DISCORD_BOT_TOKEN`
- `GEMINI_API_KEY`
- `GOOGLE_APPLICATION_CREDENTIALS`（サービスアカウントJSON）

## 10. 制約事項

### 10.1 技術的制約

- SQLiteはCloud Runのエフェメラルファイルシステム上で動作
- インスタンス終了時にデータ消失の可能性があるため、定期バックアップが必須
- コールドスタート時はDiscord Botの起動に数秒かかる

### 10.2 API制限

- **Gemini API**: 無料枠は15 RPM（リクエスト/分）
- **Google Calendar API**: 1,000,000クエリ/日（十分な余裕あり）
- **Discord API**: レート制限あり（通常使用では問題なし）

## 11. 将来の拡張予定

- [ ] 複数カレンダー対応
- [ ] リマインダー機能
- [ ] iCal形式でのエクスポート
- [ ] 予定の重複チェック
- [ ] ボタンUIでの予定選択
#### `/ヘルプ`
Botの使い方とコマンド説明を表示。

#### `/色一覧` `/色追加` `/色削除`
色プリセットの管理。

#### `/タググループ一覧` `/タググループ追加` `/タググループ削除` `/タグ追加` `/タグ削除`
タググループとタグの管理（最大3グループ）。

#### `/凡例更新`
色/タグの凡例イベントを更新。

#### `/カレンダー一覧` `/カレンダー追加` `/カレンダー使用`
カレンダーアカウントの管理。
