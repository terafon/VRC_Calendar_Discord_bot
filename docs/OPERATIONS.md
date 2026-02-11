# VRC Calendar Discord Bot - 運用ガイド

VRChatイベント管理Bot のデプロイ完了後の日常的なメンテナンス作業をまとめたドキュメントです。
デプロイがまだの場合は [デプロイガイド](DEPLOY.md) を参照してください。
認証情報の有効期限や更新手順については [認証情報・有効期限ガイド](CREDENTIALS.md) を参照してください。

## 目次

1. [変更時の対応一覧](#変更時の対応一覧)
2. [コード更新の反映](#コード更新の反映)
3. [サービス管理](#サービス管理)
4. [設定変更](#設定変更)
5. [バックアップ](#バックアップ)
6. [Firestoreデータ管理](#firestoreデータ管理)
7. [トラブルシューティング](#トラブルシューティング)

---

## 変更時の対応一覧

コードや設定を変更した場合、変更内容に応じて必要な作業が異なります。

| 変更内容 | 必要な作業 | 再起動 |
|----------|-----------|--------|
| Pythonコード（`.py`ファイル） | `git pull` → Botサービス再起動 | 必要 |
| `requirements.txt`（パッケージ追加） | `git pull` → `pip install -r requirements.txt` → Botサービス再起動 | 必要 |
| `requirements.txt`（パッケージ削除） | `git pull` → `pip uninstall -y パッケージ名` → Botサービス再起動 | 必要 |
| `.env`ファイル | `.env`を編集 → Botサービス再起動 | 必要 |
| `credentials.json` | ファイルを差し替え → Botサービス再起動 | 必要 |
| systemdサービスファイル | ファイルを編集 → `daemon-reload` → Botサービス再起動 | 必要 |
| Cloudflare Tunnel設定 | 設定ファイルを編集 → cloudflaredサービス再起動 | cloudflaredのみ |
| crontab（バックアップ・通知） | `crontab -e`で編集 → 即時反映 | 不要 |
| ドキュメントのみ（`docs/`） | 対応不要 | 不要 |

---

## コード更新の反映

### 基本的な流れ

```bash
# [OCI VM上で実行]
cd /home/ubuntu/VRC_Calendar_Discord_bot

# 1. 最新コードを取得
git pull

# 2. 仮想環境を有効化
source .venv/bin/activate

# 3. パッケージに変更がある場合のみ
pip install -r requirements.txt

# 4. Botサービスを再起動
sudo systemctl restart vrc-calendar-bot
```

### パッケージが削除された場合

`pip install -r requirements.txt` は新規パッケージの追加のみ行います。不要になったパッケージは自動では削除されません。

```bash
# [OCI VM上で実行]
source .venv/bin/activate

# 削除されたパッケージを手動でアンインストール
pip uninstall -y パッケージ名
```

> **確認方法**: `git diff HEAD~1 requirements.txt` で差分を確認し、削除された行があれば対象パッケージを `pip uninstall` してください。

---

## サービス管理

### Botサービス

```bash
# [OCI VM上で実行]
# 状態確認
sudo systemctl status vrc-calendar-bot

# 再起動
sudo systemctl restart vrc-calendar-bot

# 停止
sudo systemctl stop vrc-calendar-bot

# 起動
sudo systemctl start vrc-calendar-bot

# リアルタイムログ
sudo journalctl -u vrc-calendar-bot -f

# 最近のエラーのみ
sudo journalctl -u vrc-calendar-bot -p err --since "1 hour ago"
```

### Cloudflare Tunnel

```bash
# [OCI VM上で実行]
# 状態確認
sudo systemctl status cloudflared

# 再起動
sudo systemctl restart cloudflared

# ログ確認
sudo journalctl -u cloudflared -f
```

### systemdサービスファイルの変更

サービスファイル（`/etc/systemd/system/vrc-calendar-bot.service`）を編集した場合、再読み込みが必要です。

```bash
# [OCI VM上で実行]
# サービスファイルを編集
sudo nano /etc/systemd/system/vrc-calendar-bot.service

# systemdに変更を認識させる（必須）
sudo systemctl daemon-reload

# サービスを再起動
sudo systemctl restart vrc-calendar-bot
```

> **注意**: `daemon-reload` を忘れると、編集前のサービスファイルで再起動されます。

---

## 設定変更

### 環境変数（.env）

```bash
# [OCI VM上で実行]
cd /home/ubuntu/VRC_Calendar_Discord_bot
nano .env

# 変更後、再起動で反映
sudo systemctl restart vrc-calendar-bot
```

### Cloudflare Tunnel設定

```bash
# [OCI VM上で実行]
sudo nano /etc/cloudflared/config.yml

# 変更後、cloudflaredを再起動
sudo systemctl restart cloudflared
```

### crontab（バックアップ・週次通知）

```bash
# [OCI VM上で実行]
crontab -e
```

crontabの変更は保存と同時に反映されるため、サービスの再起動は不要です。

---

## バックアップ

### 手動バックアップ

```bash
# [OCI VM上で実行]
cd /home/ubuntu/VRC_Calendar_Discord_bot
source .venv/bin/activate
python firestore_backup.py
```

### バックアップ状態の確認

```bash
# [OCI VM上で実行]
# GCSバケット内のバックアップ一覧
source .venv/bin/activate
python -c "
from google.cloud import storage
import os
client = storage.Client()
bucket = client.bucket(os.getenv('GCS_BUCKET_NAME', ''))
blobs = list(bucket.list_blobs(prefix='firestore-backup/'))
for b in sorted(blobs, key=lambda x: x.name, reverse=True)[:5]:
    print(f'{b.name}  ({b.size} bytes, {b.updated})')
"
```

### バックアップからの復元

```bash
# [OCI VM上で実行]
cd /home/ubuntu/VRC_Calendar_Discord_bot
source .venv/bin/activate

# 最新のバックアップから復元
python firestore_backup.py --restore

# 復元後、Botを再起動
sudo systemctl restart vrc-calendar-bot
```

---

## Firestoreデータ管理

`scripts/` ディレクトリにFirestoreのデータ確認・削除用のユーティリティスクリプトがあります。

### 前提条件

- `.env` に `GCP_PROJECT_ID` が設定されていること
- `credentials.json`（サービスアカウントキー）が配置されていること
- 仮想環境が有効化されていること（`source .venv/bin/activate`）

### データ確認（firestore_inspect.py）

Firestoreに登録されているデータをターミナルから確認できます。

```bash
# [OCI VM上で実行]
cd /home/ubuntu/VRC_Calendar_Discord_bot
source .venv/bin/activate

# 全ギルドの概要（コレクション名と件数の一覧）
python scripts/firestore_inspect.py

# 特定ギルドの全データ詳細
python scripts/firestore_inspect.py --guild-id 123456789

# 特定ギルドのサブコレクションを指定して表示
python scripts/firestore_inspect.py --guild-id 123456789 --sub events          # 予定一覧
python scripts/firestore_inspect.py --guild-id 123456789 --sub color_presets   # 色プリセット
python scripts/firestore_inspect.py --guild-id 123456789 --sub tag_groups      # タググループ
python scripts/firestore_inspect.py --guild-id 123456789 --sub tags            # タグ
python scripts/firestore_inspect.py --guild-id 123456789 --sub oauth_tokens    # OAuth認証情報

# トップレベルコレクションを表示（凡例イベントIDなど）
python scripts/firestore_inspect.py --collection settings
python scripts/firestore_inspect.py --collection counters
```

#### Firestoreのコレクション構造

```
guilds/{guild_id}/
  ├── events/                          予定データ
  ├── tag_groups/                      タググループ
  ├── tags/                            タグ
  ├── oauth_tokens/{user_id}/          OAuth認証情報
  │   └── color_presets/               色プリセット
  ├── irregular_events/                不定期予定
  └── notification_settings/           通知設定
counters/                              ID自動採番カウンター
settings/                              凡例イベントID等のグローバル設定
oauth_states/                          OAuth認証一時状態
```

### データ削除（firestore_truncate.py）

テストデータのクリアなど、Firestoreのデータを一括削除できます。

```bash
# [OCI VM上で実行]
cd /home/ubuntu/VRC_Calendar_Discord_bot
source .venv/bin/activate

# ドライラン（削除せず対象を表示して確認）
python scripts/firestore_truncate.py --all --dry-run

# 全データ削除（guilds, counters, settings, oauth_states）
# ※ 確認プロンプトで "yes" を入力すると実行
python scripts/firestore_truncate.py --all

# 特定ギルドのデータのみ削除
python scripts/firestore_truncate.py --guild-id 123456789

# 特定ギルドのドライラン
python scripts/firestore_truncate.py --guild-id 123456789 --dry-run
```

> **注意**: `--all` で全データを削除すると、OAuth認証情報も消えるためユーザーの再認証が必要になります。本番環境では `--guild-id` での個別削除か、事前にバックアップ（`python firestore_backup.py`）を取ることを推奨します。

### GUIでの確認

[Firebaseコンソール](https://console.firebase.google.com/) → プロジェクト選択 → Firestore Database からもGUIでデータの確認・編集・削除が可能です。

---

## トラブルシューティング

### よくある問題と解決策

| 症状 | 原因 | 解決策 |
|------|------|--------|
| Botがオフラインのまま | トークンが無効 | Discord Developer Portalで新しいトークンを生成 |
| スラッシュコマンドが表示されない | コマンド未同期 | Botを再起動、または1時間待つ |
| カレンダー登録エラー | 権限不足 | `/カレンダー 認証状態` で認証状態を確認、必要に応じて `/カレンダー 認証` で再認証 |
| 「曜日を特定できませんでした」 | NLP解析失敗 | 「毎週水曜14時に〜」など明確に指定 |
| `audioop-lts`のインストールエラー | Python 3.13未満 | Python 3.13にアップグレードするか、`audioop-lts`を`requirements.txt`から削除（[DEPLOY.md 1.5参照](DEPLOY.md#15-必要なパッケージのインストール)） |
| バックアップが失敗する | GCS権限不足 | サービスアカウントに`roles/storage.objectAdmin`を付与、`GCS_BUCKET_NAME`が正しいか確認 |
| Firestoreへの接続エラー | IAM権限不足 | サービスアカウントに `roles/datastore.user` を付与（[DEPLOY.md 1.9参照](DEPLOY.md#19-サービスアカウントの作成gcp)） |
| OAuth認証で「redirect_uri_mismatch」 | リダイレクトURI不一致 | GCPコンソールの承認済みURIと`OAUTH_REDIRECT_URI`が完全一致しているか確認 |
| OAuth認証で「access_denied」 | 同意画面のテストユーザー未追加 | OAuth同意画面でテストユーザーにGoogleアカウントを追加 |
| OAuth認証後にカレンダー操作エラー | トークン期限切れ | `/カレンダー 認証` で再認証するか、Google側でアクセスを取消していないか確認 |
| `/予定` で「色初期設定を実行してください」と表示される | 色初期設定が未完了 | `/色 初期設定` を実行して繰り返しタイプごとのデフォルト色を設定してください |

### 旧バージョン（SQLite）からの移行

以前のSQLite版から移行する場合は `migrate_to_firestore.py` を使用してください。

```bash
# [OCI VM上で実行]
python migrate_to_firestore.py --db calendar.db --project YOUR_PROJECT_ID
```

### ヘルスチェック

ヘルスチェックスクリプトを使って、Bot の全体的な稼働状態を一括確認できます。

```bash
# [OCI VM上で実行]
cd /home/ubuntu/VRC_Calendar_Discord_bot
bash healthcheck.sh
```

スクリプトは以下の項目を自動チェックします:

| # | チェック内容 |
|---|------------|
| 1 | Bot サービス稼働状態（systemd） |
| 2 | Cloudflare Tunnel 稼働状態（systemd） |
| 3 | Flask ヘルスエンドポイント（ローカル） |
| 4 | Flask ヘルスエンドポイント（Tunnel 経由） |
| 5 | .env 必須変数の存在確認 |
| 6 | credentials.json の存在確認 |
| 7 | Firestore 接続テスト |
| 8 | crontab エントリの確認（バックアップ・通知） |
| 9 | ディスク使用量 |

各項目は `[OK]` / `[NG]` で表示され、`[NG]` の項目には対処法が併記されます。

#### 個別の手動チェック

```bash
# [OCI VM上で実行]
# ローカルでFlaskの動作確認
curl http://localhost:8080/health
# → "OK" と返れば正常

# Cloudflare Tunnel経由の確認
curl -L -s -o /dev/null -w '%{http_code}' https://bot.yourdomain.com/health
# → 200 が返れば Tunnel も正常
```
