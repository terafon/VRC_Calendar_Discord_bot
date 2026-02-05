# VRC Calendar Discord Bot - 仕様書

## 1. 概要

VRC Calendar Discord Botは、Discord上でVRChatイベント（集会、ワールド紹介、アバター試着会など）を自然言語で管理できるBotです。OAuth 2.0でユーザーのGoogleカレンダーと連携し、予定の登録・編集・削除・検索を行えます。情報が不足している場合はGemini AIとの対話で補完します。

## 2. 機能一覧

### 2.1 コア機能

| 機能 | 説明 |
|------|------|
| 予定追加 | 自然言語で予定を登録（Googleカレンダーに同期） |
| 対話型情報収集 | 不足情報をスレッド内でGeminiとの対話で補完 |
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
│  │              │ │ (マルチターン│ │                          │ │
│  │              │ │  会話対応)   │ │                          │ │
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
      ├── events/{event_id}                    # 予定マスター
      ├── irregular_events/{doc_id}            # 不定期予定の個別日時
      ├── color_presets/{name}                 # 色プリセット
      ├── tag_groups/{group_id}                # タググループ
      ├── tags/{tag_id}                        # タグ
      ├── guild_settings/config                # サーバー設定
      └── oauth_tokens/google                  # OAuthトークン
```

### 5.2 events ドキュメント（予定マスター）

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

### 5.3 oauth_tokens ドキュメント

パス: `guilds/{guild_id}/oauth_tokens/google`

| フィールド | 型 | 説明 |
|----------|------|------|
| access_token | string | Googleアクセストークン |
| refresh_token | string | リフレッシュトークン |
| token_expiry | string | トークン有効期限（ISO 8601） |
| calendar_id | string | 対象カレンダーID |
| authenticated_by | string | 認証したユーザーのDiscord User ID |
| authenticated_at | string | 認証日時（ISO 8601） |

## 6. API設計

### 6.1 Discord スラッシュコマンド

#### 予定管理

| コマンド | パラメータ | 説明 |
|---------|-----------|------|
| `/予定` | メッセージ（自然言語） | 予定の追加・編集・削除・検索（対話モード対応） |
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
| `/カレンダー認証` | なし | manage_guild | OAuth認証URLを発行（ephemeral） |
| `/カレンダー認証解除` | なし | manage_guild | OAuth認証を解除 |
| `/カレンダー認証状態` | なし | manage_guild | 認証方式・状態を表示 |
| `/カレンダー設定` | calendar_id | manage_guild | 使用するカレンダーIDを変更 |

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

Gemini APIを使用してユーザーメッセージを解析し、以下のJSON形式で返却：

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
    "urls": ["収集済みのURL"] or null
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
    "urls": ["https://..."]
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

### 7.3 曜日マッピング

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

OAuth 2.0 認証を使用します。各サーバーの管理者が `/カレンダー認証` を実行し、Google アカウントでカレンダーへのアクセスを許可する必要があります。

### 8.2 OAuth 2.0 認証フロー

```
1. ユーザーが Discord で /カレンダー認証 を実行
2. Bot が OAuth認証URL を ephemeral メッセージで送信
3. ユーザーがブラウザで Google認証 → カレンダーアクセスを許可
4. Google が Flask の /oauth/callback にリダイレクト
5. コールバックで state 検証 → コードをトークンに交換 → Firestore に保存
6. ブラウザに「認証成功」ページを表示
7. 以降、Bot はそのトークンでユーザーのカレンダーを操作
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
- **権限制御**: `/カレンダー認証` `/カレンダー認証解除` は `manage_guild` 権限必須
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

## 11. 将来の拡張予定

- [ ] リマインダー機能
- [ ] iCal形式でのエクスポート
- [ ] 予定の重複チェック
- [ ] ボタンUIでの予定選択
- [ ] 会話セッションのFirestore永続化（オプション）
