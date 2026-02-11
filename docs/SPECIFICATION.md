# VRC Calendar Discord Bot - 仕様書

## 1. 概要

VRC Calendar Discord Botは、Discord上でVRChatイベント（集会、ワールド紹介、アバター試着会など）を自然言語で管理できるBotです。OAuth 2.0でユーザーのGoogleカレンダーと連携し、予定の登録・編集・削除・検索を行えます。情報が不足している場合はGemini AIとの対話で補完します。

## 2. 機能一覧

### 2.1 コア機能

| 機能 | 説明 |
|------|------|
| 予定追加 | 自然言語で予定を登録（Googleカレンダーに同期） |
| 対話型情報収集 | 不足情報をスレッド内でGeminiとの対話で補完 |
| 色の自動割当 | 繰り返しタイプ（毎週/隔週/月1回/第n週/不定期）に応じて色を自動設定 |
| 色初期設定ウィザード | OAuth認証後に `/色 初期設定` で5カテゴリの色を一括設定 |
| タググループ別選択 | タグをグループごとに分類し、各グループから1つずつ選択 |
| 予定編集 | 既存予定の時刻・内容を変更（繰り返し変更時に色を自動再割当） |
| 予定削除 | 予定を削除（論理削除） |
| 予定検索 | 期間・タグ・名前で予定を検索 |
| 今週の予定 | 今週の予定一覧を表示 |
| 予定一覧 | 登録されている繰り返し予定のマスター一覧 |
| 週次通知 | 毎週月曜日の指定時刻に今週の予定を自動通知 |
| OAuth認証 | ユーザーのGoogleカレンダーにOAuth 2.0で直接アクセス |

### 2.2 繰り返しパターン

| パターン | 説明 | 例 |
|----------|------|-----|
| `weekly` | 毎週 | 毎週土曜日 |
| `biweekly` | 隔週 | 隔週金曜日 |
| `nth_week` | 第n週 | 第2・第4土曜日 |
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
│  │  │                 │  │                                 │ │  │
│  │  │ ConversationMgr │  │                                 │ │  │
│  │  │ (対話セッション)│  │                                 │ │  │
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
│  │              │ │ (2.0 Flash   │ │                          │ │
│  │              │ │ マルチターン)│ │                          │ │
│  └──────────────┘ └──────────────┘ └──────────────────────────┘ │
│  ┌──────────────┐ ┌──────────────┐                              │
│  │ Firestore    │ │ Secret Mgr   │                              │
│  └──────────────┘ └──────────────┘                              │
└─────────────────────────────────────────────────────────────────┘
```

## 4. 会話管理アーキテクチャ

### 4.1 対話型予定登録フロー

```
┌─────────────────────────────────────────────────────────────────┐
│ ユーザー: /予定 VRC集会を登録して                                │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ NLPProcessor.create_chat_session()                              │
│  - サーバーのタグ・色プリセット・既存予定名を含むコンテキストで │
│    Geminiチャットセッションを初期化                              │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ NLPProcessor.send_message(chat_session, "VRC集会を登録して")    │
│  - Geminiがメッセージを解析                                      │
│  - status: "needs_info" / "complete" を返す                     │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
              ┌──────────────┴──────────────┐
              ▼                             ▼
┌──────────────────────┐      ┌──────────────────────────────────┐
│ status: "complete"   │      │ status: "needs_info"             │
│                      │      │                                  │
│ 即座に確認ダイアログ │      │ 1. スレッドを作成                │
│ を表示して実行       │      │ 2. ConversationManager に登録    │
└──────────────────────┘      │ 3. 質問をスレッドに投稿          │
                              └────────────────┬─────────────────┘
                                               ▼
                              ┌──────────────────────────────────┐
                              │ on_message イベントハンドラ       │
                              │  - スレッド内のメッセージを監視   │
                              │  - セッションオーナーのみ応答     │
                              │  - Geminiに送信して次の質問を取得 │
                              └────────────────┬─────────────────┘
                                               ▼
                              ┌──────────────────────────────────┐
                              │ status: "complete" になったら     │
                              │  - 確認ダイアログを表示           │
                              │  - 実行後スレッドをアーカイブ     │
                              └──────────────────────────────────┘
```

### 4.2 ConversationSession クラス

```python
class ConversationSession:
    guild_id: str           # DiscordサーバーID
    channel_id: int         # 元のチャンネルID
    thread_id: int          # 会話スレッドID
    user_id: int            # セッションオーナーのユーザーID
    chat_session: Any       # Geminiのチャットセッションオブジェクト
    action: str             # add | edit | delete | search
    partial_data: Dict      # 収集途中のイベントデータ
    server_context: Dict    # サーバーのタグ・色情報
    created_at: float       # 作成タイムスタンプ
    last_activity: float    # 最終アクティビティ
    timeout: int            # タイムアウト秒数（デフォルト: 300）
```

### 4.3 ConversationManager クラス

```python
class ConversationManager:
    _sessions: Dict[int, ConversationSession]  # thread_id をキーに管理

    def create_session(...) -> ConversationSession
    def get_session(thread_id) -> Optional[ConversationSession]
    def remove_session(thread_id)
    def cleanup_expired() -> List[int]  # タイムアウトしたthread_idリスト
```

## 5. データベース設計（Firestore）

> **重要**: すべてのデータはDiscordサーバー（guild）ごとに分離されています。
> あるサーバーのユーザーは他のサーバーのデータを閲覧・操作できません。

### 5.1 Firestoreコレクション構造

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
      │   └── { pending_color_setup, default_colors_initialized, ... }
      ├── events/{event_id}                    # 予定マスター
      ├── irregular_events/{doc_id}            # 不定期予定の個別日時
      ├── color_presets/{name}                 # 色プリセット（recurrence_type対応）
      ├── tag_groups/{group_id}                # タググループ
      ├── tags/{tag_id}                        # タグ
      ├── guild_settings/config                # サーバー設定
      ├── oauth_tokens/{user_id}               # OAuthトークン（ユーザーごと）
      └── notification_settings/config          # 通知設定
```

### 5.2 guilds ドキュメント（サーバー設定）

パス: `guilds/{guild_id}`

| フィールド | 型 | 説明 |
|----------|------|------|
| pending_color_setup | boolean | 色初期設定が未完了の場合 true |
| default_colors_initialized | boolean | 色初期設定が完了済みの場合 true |

### 5.3 color_presets ドキュメント

パス: `guilds/{guild_id}/color_presets/{name}`

| フィールド | 型 | 説明 |
|----------|------|------|
| name | string | 色プリセット名 |
| color_id | string | Google Calendar の colorId（1-11） |
| description | string | 説明 |
| recurrence_type | string | 関連付けられた繰り返しタイプ（weekly/biweekly/monthly/nth_week/irregular）|
| is_auto_generated | boolean | セットアップウィザードで自動生成された場合 true |

#### 色カテゴリと繰り返しタイプの対応

| カテゴリ | recurrence_type | 判定条件 |
|---------|----------------|---------|
| 毎週 | weekly | `recurrence == "weekly"` |
| 隔週 | biweekly | `recurrence == "biweekly"` |
| 月1回 | monthly | `recurrence == "nth_week" and len(nth_weeks) == 1` |
| 第n週 | nth_week | `recurrence == "nth_week" and len(nth_weeks) >= 2` |
| 不定期 | irregular | `recurrence == "irregular"` |

### 5.4 events ドキュメント（予定マスター）

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
| x_url | string | X(旧Twitter)アカウントURL |
| vrc_group_url | string | VRCグループURL |
| official_url | string | 公式サイトURL |
| google_calendar_events | string (JSON) | Googleカレンダーイベント情報 |
| discord_channel_id | string | Discord通知先チャンネル |
| created_by | string | 作成者のDiscord User ID |
| calendar_owner | string | Googleカレンダー登録先ユーザーのDiscord User ID |
| created_at | string | 作成日時（ISO 8601） |
| updated_at | string | 更新日時（ISO 8601） |
| is_active | boolean | 有効フラグ（論理削除用） |

### 5.3 oauth_tokens ドキュメント

パス: `guilds/{guild_id}/oauth_tokens/{user_id}`（ユーザーごとに1ドキュメント）

| フィールド | 型 | 説明 |
|----------|------|------|
| access_token | string | Googleアクセストークン |
| refresh_token | string | リフレッシュトークン |
| token_expiry | string | トークン有効期限（ISO 8601） |
| calendar_id | string | 対象カレンダーID |
| authenticated_by | string | 認証したユーザーのDiscord User ID |
| authenticated_at | string | 認証日時（ISO 8601） |
| display_name | string | カレンダーの表示名（例: "メインカレンダー"） |
| description | string | 用途説明（例: "VRCイベント用"） |
| is_default | boolean | デフォルトカレンダーか（最初の認証時にtrue） |

### 5.5 notification_settings ドキュメント

パス: `guilds/{guild_id}/notification_settings/config`

| フィールド | 型 | 説明 |
|----------|------|------|
| enabled | boolean | 通知有効/無効 |
| weekday | number | 通知曜日（0=月〜6=日） |
| hour | number | 時刻（JST, 0-23） |
| minute | number | 分（0-59） |
| channel_id | string | 通知先チャンネルID |
| calendar_owners | array | 対象カレンダーオーナー（空=全カレンダー） |
| last_sent_at | string | 最終送信日時（重複防止） |
| configured_by | string | 設定者のDiscord User ID |
| configured_at | string | 設定日時（ISO 8601） |

## 6. API設計

### 6.1 Discord スラッシュコマンド

#### 予定管理

| コマンド | パラメータ | 説明 |
|---------|-----------|------|
| `/予定` | メッセージ（自然言語） | 予定の追加・編集・削除・検索（対話モード対応） |
| `/今週の予定` | なし | 今週の予定一覧をEmbed形式で表示 |
| `/予定一覧` | なし | 登録されている繰り返し予定のマスター一覧 |
| `/ヘルプ` | なし | Botの使い方とコマンド説明を表示 |

#### 色管理（`/色` グループ）

| コマンド | パラメータ | 説明 |
|---------|-----------|------|
| `/色 初期設定` | なし | 繰り返しタイプごとのデフォルト色を一括設定（manage_guild権限必要）|
| `/色 一覧` | なし | 色プリセットをカラーバー付きEmbedで表示 |
| `/色 追加` | 名前, color_id, 説明 | 色プリセットを追加/更新 |
| `/色 削除` | 名前 | 色プリセットを削除 |

#### タグ管理（`/タグ` グループ）

| コマンド | パラメータ | 説明 |
|---------|-----------|------|
| `/タグ 一覧` | なし | タググループとタグの一覧 |
| `/タグ グループ追加` | 名前, 説明 | タググループを追加（最大3） |
| `/タグ グループ削除` | id | タググループを削除 |
| `/タグ 追加` | group_id, 名前, 説明 | タグを追加 |
| `/タグ 削除` | group_id, 名前 | タグを削除 |

#### カレンダー管理（`/カレンダー` グループ）

| コマンド | パラメータ | 必要権限 | 説明 |
|---------|-----------|---------|------|
| `/カレンダー 認証` | なし | manage_guild | OAuth認証URLを発行（ephemeral） |
| `/カレンダー 認証解除` | なし | manage_guild | 自分のOAuth認証を解除 |
| `/カレンダー 認証状態` | なし | manage_guild | 自分の認証方式・状態を表示 |
| `/カレンダー 設定` | 表示名, カレンダーid, 説明, デフォルト | manage_guild | 自分のカレンダー設定を変更 |
| `/カレンダー 一覧` | なし | manage_guild | サーバー内の認証済みカレンダー一覧を表示 |

#### 通知管理（`/通知` グループ）

| コマンド | パラメータ | 必要権限 | 説明 |
|---------|-----------|---------|------|
| `/通知 設定` | 曜日, 時刻, チャンネル, 分(任意) | manage_guild | 週次通知のスケジュールを設定 |
| `/通知 停止` | なし | manage_guild | 週次通知を停止 |
| `/通知 状態` | なし | manage_guild | 通知設定の状態を表示 |

### 6.2 HTTPエンドポイント

#### `GET /health`
ヘルスチェック用エンドポイント。

- レスポンス: `{"status": "ok", "discord_bot": true/false}`

#### `POST /weekly-notification`
週次通知のトリガーハンドラー。

- リクエスト: Pub/Subメッセージ形式 または cron からの直接呼び出し
- レスポンス: `204 No Content`（成功時）

#### `GET /oauth/callback`
Google OAuth 認証コールバック。

- クエリパラメータ: `code`, `state`, `error`
- 処理: state検証 → コード交換 → トークン保存
- レスポンス: 認証成功/エラーのHTMLページ

## 7. 自然言語処理

### 7.1 NLPスキーマ（マルチターン会話対応）

Gemini 2.0 Flash APIを使用してユーザーメッセージを解析し、以下のJSON形式で返却：

#### 情報が不足している場合

```json
{
  "status": "needs_info",
  "action": "add|edit|delete|search",
  "question": "ユーザーへの質問テキスト",
  "event_data": {
    "event_name": "収集済みの予定名 or null",
    "tags": ["収集済みのタグ"] or null,
    "recurrence": "収集済みの繰り返しパターン or null",
    "nth_weeks": [2, 4] or null,
    "time": "収集済みの時刻 or null",
    "weekday": 5 or null,
    "duration_minutes": 60 or null,
    "description": "収集済みの説明 or null",
    "color_name": "収集済みの色名 or null",
    "x_url": "収集済みのXアカウントURL or null",
    "vrc_group_url": "収集済みのVRCグループURL or null",
    "official_url": "収集済みの公式サイトURL or null",
    "calendar_name": "登録先カレンダーの表示名 or null"
  }
}
```

#### すべての必須情報が揃った場合

```json
{
  "status": "complete",
  "action": "add|edit|delete|search",
  "event_data": {
    "event_name": "予定名",
    "tags": ["タグ1"],
    "recurrence": "weekly|biweekly|nth_week|irregular",
    "nth_weeks": [2, 4],
    "time": "21:00",
    "weekday": 5,
    "duration_minutes": 60,
    "description": "説明",
    "color_name": "色名",
    "x_url": "XアカウントURL or null",
    "vrc_group_url": "VRCグループURL or null",
    "official_url": "公式サイトURL or null",
    "calendar_name": "登録先カレンダーの表示名 or null"
  },
  "search_query": {
    "date_range": "today|this_week|next_week|this_month",
    "tags": ["タグ"],
    "event_name": "部分一致文字列"
  }
}
```

### 7.2 action=add の必須フィールド

| フィールド | 必須条件 |
|-----------|---------|
| event_name | 常に必須 |
| recurrence | 常に必須 |
| weekday | recurrence が irregular 以外の場合は必須 |
| time | 常に必須 |

### 7.3 色の自動割当ルール

NLPプロセッサーはユーザーに色を質問しません。色は繰り返しタイプに基づいてシステムが自動で割り当てます。ユーザーが明示的に色を指定した場合のみ、その色名が `color_name` に設定されます。

質問の順序: 開催頻度 → 曜日 → 時刻 → タグ（任意） → URL（任意: X / VRCグループ / 公式サイト） → カレンダー選択（複数カレンダーがある場合のみ）

### 7.4 タグのグループ別選択ルール

タグはグループごとに分類されています。各グループから最も適切なタグを1つ選択し、`tags` 配列にまとめます。タグが未登録のグループは無視されます。

NLPは登録済みタグのみを選択肢として提示します。ユーザーが未登録のタグ名を入力した場合、NLPはそのままtags配列に含め、システム側で自動登録処理を行います。

### 7.5 未登録タグの自動作成

予定追加・編集時に未登録のタグが含まれている場合、確認ダイアログ表示前に以下のフローで自動作成を行います。

```
未登録タグを検出
    │
    ▼
確認ダイアログ: 「以下のタグは未登録です。自動作成しますか？」
    │
    ├── 「作成して続行」 → タグを自動作成
    │     ├── タググループが0個 → デフォルト「一般」グループを作成して追加
    │     ├── タググループが1個 → そのグループに追加
    │     └── タググループが2個以上 → タグごとにグループ選択を表示
    │
    └── 「タグなしで続行」 → 未登録タグを除外して続行
```

### 7.5 曜日マッピング

| 曜日 | 値 |
|------|-----|
| 月曜 | 0 |
| 火曜 | 1 |
| 水曜 | 2 |
| 木曜 | 3 |
| 金曜 | 4 |
| 土曜 | 5 |
| 日曜 | 6 |

## 8. Googleカレンダー連携

### 8.1 認証方式

OAuth 2.0 認証を使用します。各ユーザーが `/カレンダー 認証` を実行し、自分のGoogle アカウントでカレンダーへのアクセスを許可します。1つのサーバーで複数ユーザーがそれぞれのカレンダーを認証できます。

### 8.2 OAuth 2.0 認証フロー

```
1. ユーザーが Discord で /カレンダー 認証 を実行
2. Bot が OAuth認証URL を ephemeral メッセージで送信
3. ユーザーがブラウザで Google認証 → カレンダーアクセスを許可
4. Google が Flask の /oauth/callback にリダイレクト
5. コールバックで state 検証 → コードをトークンに交換 → Firestore に保存
6. 色セットアップ未完了フラグ（pending_color_setup）を設定
7. ブラウザに「認証成功」ページを表示（/色 初期設定 の実行を案内）
8. ユーザーが Discord で /色 初期設定 を実行し、繰り返しタイプごとの色を設定
9. 以降、Bot はそのトークンでユーザーのカレンダーを操作し、色を自動割当
```

### 8.3 スコープ

```
https://www.googleapis.com/auth/calendar
```

## 9. セキュリティ

### 9.1 シークレット管理

1. **Secret Manager（推奨）**: GCP Secret Managerに保存
2. **環境変数（フォールバック）**: .envファイルから読み込み

### 9.2 保護対象

- `DISCORD_BOT_TOKEN`
- `GEMINI_API_KEY`
- `GOOGLE_APPLICATION_CREDENTIALS`（サービスアカウントJSON）
- `GOOGLE_OAUTH_CLIENT_ID`
- `GOOGLE_OAUTH_CLIENT_SECRET`
- OAuthトークン（Firestoreに保存、サーバーサイドアクセスのみ）

### 9.3 OAuth セキュリティ

- **CSRF対策**: stateパラメータをFirestoreに保存、コールバック時にワンタイム検証・削除
- **権限制御**: `/カレンダー 認証` `/カレンダー 認証解除` は `manage_guild` 権限必須
- **stateの有効期限**: Firestoreコンソールで `oauth_states` にTTLポリシー設定推奨（30分）

### 9.4 会話セッションのセキュリティ

- **セッションオーナー制限**: スレッド内のメッセージはセッションを開始したユーザーのみが処理される
- **タイムアウト**: 5分間操作がないとセッションが自動削除される
- **メモリ上管理**: 会話セッションはメモリ上に保持（Firestoreには保存しない）
  - Bot再起動でセッションは消失するが、進行中の予定登録は稀なので許容範囲

### 9.5 バックアップ

- **方式**: `firestore_backup.py` により Firestore の全データを JSON 化し GCS にアップロード
- **対象**: guilds（サブコレクション含む）、counters、settings、oauth_states
- **スケジュール**: cron で6時間ごとに自動実行
- **保持数**: 最新30件（古いものは自動削除）
- **リストア**: `firestore_backup.py --restore <BLOB_NAME>` で復元可能
- **ストレージ**: GCS（US リージョン、5GB まで無料枠）

## 10. 制約事項

### 10.1 技術的制約

- OCI Always Free VMの性能制限（E2.1.Micro: 1/8 OCPU, 1GB RAM）
- OAuth認証にはCloudflare Tunnel（またはHTTPS公開URL）が必要
- 会話セッションはBot再起動で消失

### 10.2 API制限

- **Gemini API**: 無料枠は15 RPM（リクエスト/分）
- **Google Calendar API**: 1,000,000クエリ/日（十分な余裕あり）
- **Discord API**: レート制限あり（通常使用では問題なし）

### 10.3 会話セッション制限

- **タイムアウト**: 5分間操作がないと自動終了
- **同時セッション**: 1ユーザー1スレッドの想定（複数スレッドでの同時操作は非推奨）

## 11. 色自動割当アーキテクチャ

### 11.1 色自動割当フロー

```
予定追加（/予定 コマンド or スレッド内対話）
    │
    ▼
recurrence + nth_weeks から色カテゴリを決定
（_resolve_color_category）
    │
    ▼
色カテゴリに対応する色プリセットをFirestoreから取得
（_auto_assign_color → get_color_preset_by_recurrence）
    │
    ├── プリセットあり → 色を自動割当（確認画面に「（自動割当）」表示）
    │
    └── プリセットなし → 新色追加ダイアログを表示
         ├── 追加を選択 → colorId選択 → プリセット登録 → 確認フローへ
         └── スキップ → 色なしで確認フローへ
```

### 11.2 色セットアップウィザード（/色 初期設定）

OAuth認証後に実行する初期設定コマンドです。5つの色カテゴリ（毎週/隔週/月1回/第n週/不定期）それぞれにGoogle CalendarのcolorId（1-11）を割り当てます。

```
/色 初期設定 実行
    │
    ▼
SelectMenu で各カテゴリの色を選択（5カテゴリ分）
    │
    ▼
色プリセットを一括登録（recurrence_type + is_auto_generated=True）
    │
    ▼
凡例イベントを更新
```

### 11.3 予定編集時の色再割当

予定の繰り返しパターン（recurrence）が変更された場合、新しい繰り返しタイプに対応する色が自動的に再割当されます。ユーザーが明示的に色を変更指定している場合は、その色が優先されます。

## 12. 将来の拡張予定

- [ ] リマインダー機能
- [ ] iCal形式でのエクスポート
- [ ] 予定の重複チェック
- [ ] ボタンUIでの予定選択
- [ ] 会話セッションのFirestore永続化（オプション）
