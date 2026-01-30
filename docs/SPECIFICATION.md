# VRC Calendar Discord Bot - 仕様書

## 1. 概要

VRC Calendar Discord Botは、Discord上で自然言語を使って予定を管理できるBotです。OAuth 2.0でユーザーのGoogleカレンダーと連携し、予定の登録・編集・削除・検索を行えます。また、毎週月曜日に週間予定を自動通知する機能を備えています。

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
| OAuth認証 | ユーザーのGoogleカレンダーにOAuth 2.0で直接アクセス |

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
│  Oracle Cloud Infrastructure (Always Free)                      │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  VM.Standard.E2.1.Micro / Ampere A1                       │  │
│  │  ┌─────────────────┐  ┌─────────────────────────────────┐ │  │
│  │  │ Discord Bot     │  │ Flask Server                    │ │  │
│  │  │ (常時WebSocket) │  │ (通知/OAuthコールバック:8080)   │ │  │
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
│  │ Firestore    │ │ Secret Mgr   │                              │
│  └──────────────┘ └──────────────┘                              │
└─────────────────────────────────────────────────────────────────┘
```

## 4. データベース設計（Firestore）

> **重要**: すべてのデータはDiscordサーバー（guild）ごとに分離されています。
> あるサーバーのユーザーは他のサーバーのデータを閲覧・操作できません。

### 4.1 Firestoreコレクション構造

```
(Root)
├── counters/{counter_name}                    # ID自動採番用カウンター
│     └── { current: number }
│
├── settings/{key}                             # グローバル設定
│     └── { value, updated_at }
│
├── oauth_states/{state}                       # OAuth CSRF state（一時的）
│     └── { guild_id, user_id, created_at }
│
└── guilds/{guild_id}/                         # サーバーごとのデータ
      ├── events/{event_id}                    # 予定マスター
      ├── irregular_events/{doc_id}            # 不定期予定の個別日時
      ├── color_presets/{name}                 # 色プリセット
      ├── tag_groups/{group_id}                # タググループ
      ├── tags/{tag_id}                        # タグ
      ├── calendar_accounts/{account_id}       # カレンダーアカウント（サービスアカウント用）
      ├── guild_settings/config                # サーバー設定
      └── oauth_tokens/google                  # OAuthトークン
```

### 4.2 events ドキュメント（予定マスター）

| フィールド | 型 | 説明 |
|----------|------|------|
| id | number | 予定ID（自動採番） |
| guild_id | string | DiscordサーバーID |
| event_name | string | 予定名 |
| tags | string (JSON) | タグ配列 |
| recurrence | string | 繰り返しタイプ |
| nth_weeks | string (JSON) | 第n週のリスト |
| event_type | string | イベント種類 |
| time | string | 開始時刻（HH:MM形式） |
| weekday | number | 曜日（0=月〜6=日） |
| duration_minutes | number | 所要時間（分） |
| description | string | 説明 |
| color_name | string | 色名（プリセット名） |
| urls | string (JSON) | URL配列 |
| google_calendar_events | string (JSON) | Googleカレンダーイベント情報 |
| discord_channel_id | string | Discord通知先チャンネル |
| created_by | string | 作成者のDiscord User ID |
| created_at | string | 作成日時（ISO 8601） |
| updated_at | string | 更新日時（ISO 8601） |
| is_active | boolean | 有効フラグ（論理削除用） |

### 4.3 oauth_tokens ドキュメント

パス: `guilds/{guild_id}/oauth_tokens/google`

| フィールド | 型 | 説明 |
|----------|------|------|
| access_token | string | Googleアクセストークン |
| refresh_token | string | リフレッシュトークン |
| token_expiry | string | トークン有効期限（ISO 8601） |
| calendar_id | string | 対象カレンダーID |
| authenticated_by | string | 認証したユーザーのDiscord User ID |
| authenticated_at | string | 認証日時（ISO 8601） |

### 4.4 oauth_states ドキュメント（一時的）

パス: `oauth_states/{state}`

| フィールド | 型 | 説明 |
|----------|------|------|
| guild_id | string | DiscordサーバーID |
| user_id | string | DiscordユーザーID |
| created_at | string | 作成日時（ISO 8601） |

### 4.5 その他のコレクション

#### color_presets
| フィールド | 型 | 説明 |
|----------|------|------|
| guild_id | string | DiscordサーバーID |
| name | string | 色名 |
| color_id | string | Googleカレンダー colorId |
| description | string | 説明 |

#### tag_groups
| フィールド | 型 | 説明 |
|----------|------|------|
| id | number | グループID |
| guild_id | string | DiscordサーバーID |
| name | string | グループ名 |
| description | string | 説明 |

> サーバーごとに最大3グループまで作成可能

#### tags
| フィールド | 型 | 説明 |
|----------|------|------|
| id | number | タグID |
| group_id | number | タググループID |
| group_name | string | タググループ名 |
| name | string | タグ名 |
| description | string | 説明 |

#### calendar_accounts（サービスアカウント用フォールバック）
| フィールド | 型 | 説明 |
|----------|------|------|
| id | number | アカウントID |
| guild_id | string | DiscordサーバーID |
| name | string | アカウント名 |
| calendar_id | string | GoogleカレンダーID |
| credentials_path | string | 認証ファイルパス |

## 5. API設計

### 5.1 Discord スラッシュコマンド

#### 予定管理

| コマンド | パラメータ | 説明 |
|---------|-----------|------|
| `/予定` | メッセージ（自然言語） | 予定の追加・編集・削除・検索 |
| `/今週の予定` | なし | 今週の予定一覧をEmbed形式で表示 |
| `/予定一覧` | なし | 登録されている繰り返し予定のマスター一覧 |
| `/ヘルプ` | なし | Botの使い方とコマンド説明を表示 |

#### 色・タグ管理

| コマンド | パラメータ | 説明 |
|---------|-----------|------|
| `/色一覧` | なし | 色プリセットとGoogleカラーIDを表示 |
| `/色追加` | 名前, color_id, 説明 | 色プリセットを追加/更新 |
| `/色削除` | 名前 | 色プリセットを削除 |
| `/タググループ一覧` | なし | タググループとタグの一覧 |
| `/タググループ追加` | 名前, 説明 | タググループを追加（最大3） |
| `/タググループ削除` | id | タググループを削除 |
| `/タグ追加` | group_id, 名前, 説明 | タグを追加 |
| `/タグ削除` | group_id, 名前 | タグを削除 |
| `/凡例更新` | なし | 色/タグの凡例イベントを更新 |

#### カレンダー管理

| コマンド | パラメータ | 必要権限 | 説明 |
|---------|-----------|---------|------|
| `/カレンダー一覧` | なし | なし | 登録済みカレンダーアカウントを表示 |
| `/カレンダー追加` | 名前, calendar_id, 認証ファイル | なし | カレンダーアカウントを追加 |
| `/カレンダー使用` | id | なし | このサーバーで使用するカレンダーを設定 |
| `/カレンダー認証` | なし | manage_guild | OAuth認証URLを発行（ephemeral） |
| `/カレンダー認証解除` | なし | manage_guild | OAuth認証を解除 |
| `/カレンダー認証状態` | なし | manage_guild | 認証方式・状態を表示 |

### 5.2 HTTPエンドポイント

#### `GET /health`
ヘルスチェック用エンドポイント。

- レスポンス: `200 OK`

#### `POST /weekly-notification`
週次通知のトリガーハンドラー。

- リクエスト: Pub/Subメッセージ形式 または cron からの直接呼び出し
- レスポンス: `204 No Content`（成功時）

#### `GET /oauth/callback`
Google OAuth 認証コールバック。

- クエリパラメータ: `code`, `state`, `error`
- 処理: state検証 → コード交換 → トークン保存
- レスポンス: 認証成功/エラーのHTMLページ

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

以下の優先順位でカレンダーにアクセスします：

1. **OAuth 2.0（推奨）**: `/カレンダー認証` でユーザーのGoogleアカウントを直接使用。サービスアカウントへのカレンダー共有設定が不要。
2. **サービスアカウント（フォールバック）**: `/カレンダー使用` で設定したサービスアカウント。カレンダーの共有設定が必要。
3. **デフォルト**: 環境変数で指定したサービスアカウント。

### 7.2 OAuth 2.0 認証フロー

```
1. ユーザーが Discord で /カレンダー認証 を実行
2. Bot が OAuth認証URL を ephemeral メッセージで送信
3. ユーザーがブラウザで Google認証 → カレンダーアクセスを許可
4. Google が Flask の /oauth/callback にリダイレクト
5. コールバックで state 検証 → コードをトークンに交換 → Firestore に保存
6. ブラウザに「認証成功」ページを表示
7. 以降、Bot はそのトークンでユーザーのカレンダーを操作
```

### 7.3 スコープ

```
https://www.googleapis.com/auth/calendar
```

### 7.4 タグと色の対応

色プリセットはサーバーごとに `/色追加` で登録できます。デフォルトのマッピング：

| タグ | 色ID | 色 |
|------|------|-----|
| 重要 | 11 | 赤 |
| チームミーティング | 9 | 青 |
| 個人 | 2 | 緑 |

## 8. セキュリティ

### 8.1 シークレット管理

1. **Secret Manager（推奨）**: GCP Secret Managerに保存
2. **環境変数（フォールバック）**: .envファイルから読み込み

### 8.2 保護対象

- `DISCORD_BOT_TOKEN`
- `GEMINI_API_KEY`
- `GOOGLE_APPLICATION_CREDENTIALS`（サービスアカウントJSON）
- `GOOGLE_OAUTH_CLIENT_ID`
- `GOOGLE_OAUTH_CLIENT_SECRET`
- OAuthトークン（Firestoreに保存、サーバーサイドアクセスのみ）

### 8.3 OAuth セキュリティ

- **CSRF対策**: stateパラメータをFirestoreに保存、コールバック時にワンタイム検証・削除
- **権限制御**: `/カレンダー認証` `/カレンダー認証解除` は `manage_guild` 権限必須
- **stateの有効期限**: Firestoreコンソールで `oauth_states` にTTLポリシー設定推奨（30分）

### 8.4 バックアップ

- **方式**: `firestore_backup.py` により Firestore の全データを JSON 化し GCS にアップロード
- **対象**: guilds（サブコレクション含む）、counters、settings、oauth_states
- **スケジュール**: cron で6時間ごとに自動実行
- **保持数**: 最新30件（古いものは自動削除）
- **リストア**: `firestore_backup.py --restore <BLOB_NAME>` で復元可能
- **ストレージ**: GCS（US リージョン、5GB まで無料枠）

## 9. 制約事項

### 9.1 技術的制約

- OCI Always Free VMの性能制限（E2.1.Micro: 1/8 OCPU, 1GB RAM）
- OAuth認証にはCloudflare Tunnel（またはHTTPS公開URL）が必要

### 9.2 API制限

- **Gemini API**: 無料枠は15 RPM（リクエスト/分）
- **Google Calendar API**: 1,000,000クエリ/日（十分な余裕あり）
- **Discord API**: レート制限あり（通常使用では問題なし）

## 10. 将来の拡張予定

- [ ] リマインダー機能
- [ ] iCal形式でのエクスポート
- [ ] 予定の重複チェック
- [ ] ボタンUIでの予定選択
