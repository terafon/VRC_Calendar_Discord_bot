# VRC Calendar Discord Bot - 認証情報・有効期限ガイド

本Botで使用する各サービスの認証情報やリソースについて、有効期限・更新方法・注意事項をまとめたドキュメントです。

## 目次

1. [有効期限一覧](#有効期限一覧)
2. [Google OAuth トークン（最重要）](#google-oauth-トークン最重要)
3. [GCP サービスアカウントキー](#gcp-サービスアカウントキー)
4. [Discord Bot トークン](#discord-bot-トークン)
5. [Gemini API キー](#gemini-api-キー)
6. [Cloudflare Tunnel](#cloudflare-tunnel)
7. [Cloudflare SSL 証明書](#cloudflare-ssl-証明書)
8. [ドメイン名](#ドメイン名)
9. [OCI Always Free VM](#oci-always-free-vm)
10. [GCP 無料枠リソース](#gcp-無料枠リソース)
11. [定期メンテナンスチェックリスト](#定期メンテナンスチェックリスト)

---

## 有効期限一覧

| 認証情報 / リソース | 有効期限 | 自動更新 | 対応の緊急度 |
|---------------------|---------|----------|-------------|
| OAuth アクセストークン | **1時間** | Bot が自動リフレッシュ | 自動（対応不要） |
| OAuth リフレッシュトークン（テストモード） | **7日** | なし | **非常に高い** |
| OAuth リフレッシュトークン（本番モード） | **6ヶ月未使用で失効** | なし | 中 |
| GCP サービスアカウントキー | **無期限**（デフォルト） | なし | 低（定期ローテーション推奨） |
| Discord Bot トークン | **無期限** | なし | 低（漏洩時のみ） |
| Gemini API キー | **無期限** | なし | 低（定期ローテーション推奨） |
| Cloudflare Tunnel cert.pem | **10年以上** | なし | 非常に低い |
| Cloudflare Tunnel 認証ファイル | **無期限**（取消まで有効） | なし | 非常に低い |
| Cloudflare Universal SSL | **90日** | **自動更新** | 自動（対応不要） |
| ドメイン名 | **登録サービスに依存** | サービスによる | サービスによる |
| OCI VM インスタンス | **無期限**（条件あり） | なし | **高い（アイドル回収あり）** |
| GCP Firestore | **無期限**（無料枠内） | なし | 低 |
| GCP Cloud Storage | **無期限**（無料枠内） | なし | 低 |

---

## Google OAuth トークン（最重要）

本Botで **最も注意が必要** な認証情報です。

### アクセストークン（有効期限: 1時間）

- Google OAuth のアクセストークンは発行から **1時間** で期限切れになります
- Bot は期限切れ検知時にリフレッシュトークンを使って **自動的に再取得** します
- **対応不要** — Bot が自動で処理します

### リフレッシュトークン — テストモードの場合（有効期限: 7日）

OAuth 同意画面が **テストモード** の場合、リフレッシュトークンは **発行から7日で失効** します。

**症状**: 7日後にカレンダー操作が突然エラーになる

**対応方法**:
```
1. Discord で /カレンダー認証 を再度実行
2. ブラウザで Google 認証を完了
3. 新しいトークンが自動保存される
```

> **重要**: テストモードでは7日ごとに再認証が必要です。運用が安定したら本番モードへの移行を検討してください。本番モードへの移行手順は [DEPLOY.md の OAuth セクション](DEPLOY.md#oauth-20-ユーザー認証の設定) を参照してください。

### リフレッシュトークン — 本番モードの場合

本番モードでは、リフレッシュトークンは以下の場合に失効します:

| 失効条件 | 詳細 |
|---------|------|
| **6ヶ月間未使用** | 6ヶ月連続でトークンが使用されなかった場合に自動失効。本Botは定期的にカレンダーアクセスするため、通常は発生しません |
| **ユーザーがアクセスを取消** | Google アカウントの設定画面（[myaccount.google.com/permissions](https://myaccount.google.com/permissions)）から Bot のアクセスを取り消した場合 |
| **パスワードリセット** | Google アカウントのパスワードを変更した場合（Gmail スコープ使用時） |
| **100トークン上限** | 同一ユーザーが同一アプリに対して100個以上のリフレッシュトークンを発行した場合、最も古いトークンが無効化 |

**対応方法**: いずれの場合も `/カレンダー認証` で再認証すれば復旧します。

### テストモードから本番モードへの切替時の注意

本番モードに切り替えた後は、**新しい OAuth 認証情報（クライアント ID / シークレット）を再作成** することを推奨します。古い認証情報のまま使い続けると、テストモード時に発行されたリフレッシュトークンが7日制限のまま残る場合があります。

切替後の手順:
```
1. GCP コンソール → APIとサービス → 認証情報
2. 新しい OAuth クライアント ID を作成（既存のものと同じリダイレクト URI を設定）
3. .env の GOOGLE_OAUTH_CLIENT_ID と GOOGLE_OAUTH_CLIENT_SECRET を更新
4. Bot を再起動
5. 各サーバーで /カレンダー認証 を再実行
```

---

## GCP サービスアカウントキー

### 有効期限

デフォルトでは **無期限** です。削除するまで有効です。

### 推奨ローテーション周期

Google Cloud の推奨（CIS ベンチマーク準拠）: **90日ごと**

ただし、本Botのような個人プロジェクトでは、キーが外部に漏洩しない限り即座のリスクは低いです。

### ローテーション手順

```bash
# [ローカルマシンで実行]

# 1. 新しいキーを作成
gcloud iam service-accounts keys create new-credentials.json \
  --iam-account=calendar-bot-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com

# 2. OCI VM に新しいキーを転送
scp new-credentials.json ubuntu@VM_IP:/home/ubuntu/VRC_Calendar_Discord_bot/credentials.json

# 3. Bot を再起動
ssh ubuntu@VM_IP "sudo systemctl restart vrc-calendar-bot"

# 4. 動作確認後、古いキーを削除
gcloud iam service-accounts keys list \
  --iam-account=calendar-bot-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com

gcloud iam service-accounts keys delete OLD_KEY_ID \
  --iam-account=calendar-bot-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com
```

### キーが漏洩した場合

即座に以下を実行してください:

```bash
# 1. 漏洩したキーを無効化
gcloud iam service-accounts keys delete LEAKED_KEY_ID \
  --iam-account=calendar-bot-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com

# 2. 新しいキーを発行して配置（上記ローテーション手順を実行）
```

---

## Discord Bot トークン

### 有効期限

**無期限** です。手動でリセットするか、以下の場合に自動無効化されます。

### 自動リセットされる条件

| 条件 | 詳細 |
|------|------|
| **トークンの漏洩検知** | GitHub 等の公開リポジトリにトークンが掲載された場合、Discord が自動でリセットします |
| **短時間の大量接続** | 短時間に1000回以上の接続が検知された場合（バグによる再接続ループ等） |
| **手動リセット** | Discord Developer Portal から手動でリセットした場合 |

### トークンの再取得手順

```
1. Discord Developer Portal (https://discord.com/developers/applications) にアクセス
2. アプリケーションを選択 → 「Bot」タブ
3. 「Reset Token」をクリック
4. 新しいトークンをコピー
5. OCI VM 上の .env ファイルを更新
6. Bot を再起動: sudo systemctl restart vrc-calendar-bot
```

### 漏洩防止

- `.env` ファイルは `.gitignore` に含まれていることを確認
- Secret Manager を使用している場合は、そちらのシークレットも更新

---

## Gemini API キー

### 有効期限

**無期限** です。手動で削除するまで有効です。

### 注意事項

- API キー自体は失効しませんが、**Gemini モデルのバージョンが非推奨になる** ことがあります
- モデルの非推奨化は Google から事前に告知されます
- 非推奨化された場合は、Bot のコード内でモデル名を更新する必要があります

### キーのローテーション手順

```
1. Google AI Studio (https://aistudio.google.com/app/apikey) にアクセス
2. 新しい API キーを作成
3. OCI VM 上の .env の GEMINI_API_KEY を更新
4. Bot を再起動: sudo systemctl restart vrc-calendar-bot
5. 動作確認後、古いキーを Google AI Studio から削除
```

---

## Cloudflare Tunnel

### cert.pem（アカウント証明書）

- **有効期限**: 10年以上
- Tunnel の作成・削除・管理に使用する証明書です
- 保管場所: `/etc/cloudflared/cert.pem`
- **対応不要** — 通常の運用で期限切れになることはありません

### Tunnel 認証ファイル（UUID.json）

- **有効期限**: 無期限（取消まで有効）
- 特定の Tunnel を実行するための認証ファイルです
- 保管場所: `/etc/cloudflared/<TUNNEL-UUID>.json`
- **対応不要** — Tunnel を削除しない限り有効です

### Tunnel の再作成が必要になった場合

```bash
# [OCI VM上で実行]

# 1. 既存の Tunnel を削除
cloudflared tunnel delete VRC_CALENDAR_BOT

# 2. 新しい Tunnel を作成
cloudflared tunnel create VRC_CALENDAR_BOT

# 3. 新しい認証ファイルを /etc/cloudflared/ にコピー
sudo cp ~/.cloudflared/<NEW-TUNNEL-UUID>.json /etc/cloudflared/

# 4. /etc/cloudflared/config.yml の credentials-file パスを更新
sudo nano /etc/cloudflared/config.yml

# 5. DNS ルーティングを再設定
cloudflared tunnel route dns VRC_CALENDAR_BOT bot.yourdomain.com

# 6. cloudflared を再起動
sudo systemctl restart cloudflared
```

---

## Cloudflare SSL 証明書

### Universal SSL（エッジ証明書）

- **有効期限**: 90日
- **自動更新**: はい — Cloudflare が自動で更新します
- **対応不要** — Cloudflare のネームサーバーを使用している限り、自動更新されます

### 自動更新の前提条件

- ドメインが Cloudflare のネームサーバーを使用していること
- ドメインが Cloudflare 上でアクティブであること

### 自動更新が失敗した場合の症状

- `https://bot.yourdomain.com/health` にアクセスすると SSL エラーが発生
- `ERR_SSL_PROTOCOL_ERROR` や `ERR_CERT_DATE_INVALID` が表示される

**対応方法**:
```
1. Cloudflare ダッシュボード → SSL/TLS → Edge Certificates を確認
2. Universal SSL が「Active」であることを確認
3. 無効になっていた場合は、Disable → Enable で再発行
```

---

## ドメイン名

### 無料ドメインの場合

無料ドメインの有効期限や更新ポリシーは提供サービスによって異なります。

| サービス | 更新要件 | 注意点 |
|---------|---------|--------|
| DigitalPlat (FreeDomain) | 無料で更新可能 | 利用可能な TLD が限定的 |
| EU.org | 無期限（サブドメイン） | 実質的にサブドメイン（例: `yourname.eu.org`） |
| OpenHost Domain Registry | 要確認 | 2023年開始の比較的新しいサービス |

> **重要**: 無料ドメインサービスは予告なくサービス終了する可能性があります（Freenom の例）。ドメインが失効すると **OAuth のリダイレクト URI が機能しなくなり、新規認証ができなくなります**。既存のトークンは引き続き使えますが、再認証が必要になった時点で問題が発生します。

### ドメインが失効した場合の影響

| 機能 | 影響 |
|------|------|
| OAuth 新規認証（`/カレンダー認証`） | **不可** — リダイレクト URI が機能しない |
| 既存 OAuth トークンでのカレンダー操作 | トークンが有効な間は動作する |
| 週次通知（cron 経由） | 影響なし（ローカル実行のため） |
| 週次通知（HTTP 経由） | **不可** — Tunnel 経由の URL が無効 |
| Discord Bot の基本機能 | 影響なし |

### ドメインが失効した場合の対応

```
1. 新しいドメインを取得
2. Cloudflare に新しいドメインを追加
3. Tunnel の DNS ルーティングを新ドメインに更新:
   cloudflared tunnel route dns VRC_CALENDAR_BOT bot.newdomain.com
4. GCP コンソール → APIとサービス → 認証情報 → OAuth クライアント
   → 承認済みリダイレクト URI を https://bot.newdomain.com/oauth/callback に変更
5. .env の OAUTH_REDIRECT_URI を更新
6. Bot を再起動
7. 各サーバーで /カレンダー認証 を再実行
```

---

## OCI Always Free VM

### アイドルインスタンスの回収ポリシー

Oracle は **Always Free アカウント（無料アカウント）** のアイドルインスタンスを回収する場合があります。

#### アイドルと判定される条件

7日間にわたって以下の **すべて** を満たした場合:

| メトリック | 閾値 |
|-----------|------|
| CPU 使用率（95パーセンタイル） | **20% 未満** |

> **注意**: この閾値は過去に10%→15%→20%と段階的に引き上げられています。Oracle の公式ドキュメントで最新の値を確認してください。

#### 回収の流れ

```
1. Oracle から「アイドルインスタンスが検出されました」というメール通知
2. 通知から1週間後にインスタンスが停止される
3. 停止されたインスタンスは手動で再起動可能（削除はされない）
```

#### 回収を防ぐ方法

**方法1: Pay As You Go（PAYG）にアップグレード（推奨）**

PAYG にアップグレードしても Always Free リソースの範囲内であれば課金されません。アイドルインスタンスの回収対象から完全に除外されます。

```
1. OCI コンソール → 管理 → アカウント詳細
2. 「Pay As You Go にアップグレード」を選択
3. 支払い方法を登録（Always Free 枠内なら課金されない）
```

**方法2: 十分な負荷を維持する**

本 Bot は Discord WebSocket 接続を常時維持し、Flask サーバーも稼働しているため、通常はアイドルと判定されにくいですが、小さいインスタンス（1/8 OCPU）では CPU 使用率が閾値を下回る可能性があります。

#### インスタンスが停止された場合の対応

```bash
# OCI コンソールから再起動
# または OCI CLI で再起動:
oci compute instance action --instance-id <INSTANCE_ID> --action START
```

再起動後、Bot サービスは systemd により自動的に起動します（`Restart=always` 設定済みの場合）。

---

## GCP 無料枠リソース

### Firestore

- **有効期限**: なし
- **無料枠**: 1 GiB ストレージ、50,000 読み取り/日、20,000 書き込み/日
- 本 Bot の使用量では枠を超えることはまずありません
- **対応不要**

### Cloud Storage（GCS）

- **有効期限**: なし
- **無料枠**: 5 GB（US リージョン）
- バックアップスクリプトは最新30件を保持し、古いものを自動削除します
- **対応不要**（バックアップが30件を大幅に超えないよう自動管理されています）

### Secret Manager

- **有効期限**: なし（シークレット自体は無期限）
- **無料枠**: 6 つのシークレットバージョン
- シークレットの値を更新すると新しいバージョンが作成されます
- 古いバージョンは手動で無効化・削除できます

### Google Calendar API

- **有効期限**: なし
- **無料枠**: 1,000,000 リクエスト/日
- **対応不要**

---

## 定期メンテナンスチェックリスト

### 月次（毎月1回）

- [ ] Bot が正常に動作しているか確認（`/ヘルプ` コマンドを実行）
- [ ] ヘルスチェック確認: `curl https://bot.yourdomain.com/health`
- [ ] バックアップが正常に実行されているか確認（[運用ガイド](OPERATIONS.md#バックアップ) 参照）
- [ ] OCI VM の CPU 使用率が回収閾値（20%）を下回り続けていないか確認

### 四半期（3ヶ月ごと）

- [ ] GCP サービスアカウントキーのローテーション（推奨）
- [ ] Gemini API キーのローテーション（推奨）
- [ ] OCI / GCP / Cloudflare からのメール通知を見落としていないか確認

### 年次

- [ ] ドメインの更新期限を確認（有料ドメインの場合）
- [ ] GCP の無料枠条件が変更されていないか確認
- [ ] OCI の Always Free 条件が変更されていないか確認
- [ ] 使用している Gemini モデルが非推奨になっていないか確認
- [ ] Discord API / discord.py のバージョンアップが必要か確認

### テストモードで OAuth を使用している場合（7日ごと）

- [ ] `/カレンダー認証状態` で認証が有効か確認
- [ ] 失効していた場合は `/カレンダー認証` で再認証
