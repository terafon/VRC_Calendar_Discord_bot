# VRC Calendar Discord Bot - 運用ガイド

デプロイ完了後の日常的なメンテナンス作業をまとめたドキュメントです。
デプロイがまだの場合は [デプロイガイド](DEPLOY.md) を参照してください。
認証情報の有効期限や更新手順については [認証情報・有効期限ガイド](CREDENTIALS.md) を参照してください。

## 目次

1. [変更時の対応一覧](#変更時の対応一覧)
2. [コード更新の反映](#コード更新の反映)
3. [サービス管理](#サービス管理)
4. [設定変更](#設定変更)
5. [バックアップ](#バックアップ)
6. [トラブルシューティング](#トラブルシューティング)

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

## トラブルシューティング

### よくある問題と解決策

| 症状 | 原因 | 解決策 |
|------|------|--------|
| Botがオフラインのまま | トークンが無効 | Discord Developer Portalで新しいトークンを生成 |
| スラッシュコマンドが表示されない | コマンド未同期 | Botを再起動、または1時間待つ |
| カレンダー登録エラー | 権限不足 | `/カレンダー認証状態` で認証状態を確認、必要に応じて `/カレンダー認証` で再認証 |
| 「曜日を特定できませんでした」 | NLP解析失敗 | 「毎週水曜14時に〜」など明確に指定 |
| `audioop-lts`のインストールエラー | Python 3.13未満 | Python 3.13にアップグレードするか、`audioop-lts`を`requirements.txt`から削除（[DEPLOY.md 1.5参照](DEPLOY.md#15-必要なパッケージのインストール)） |
| バックアップが失敗する | GCS権限不足 | サービスアカウントに`roles/storage.objectAdmin`を付与、`GCS_BUCKET_NAME`が正しいか確認 |
| Firestoreへの接続エラー | IAM権限不足 | サービスアカウントに `roles/datastore.user` を付与（[DEPLOY.md 1.9参照](DEPLOY.md#19-サービスアカウントの作成gcp)） |
| OAuth認証で「redirect_uri_mismatch」 | リダイレクトURI不一致 | GCPコンソールの承認済みURIと`OAUTH_REDIRECT_URI`が完全一致しているか確認 |
| OAuth認証で「access_denied」 | 同意画面のテストユーザー未追加 | OAuth同意画面でテストユーザーにGoogleアカウントを追加 |
| OAuth認証後にカレンダー操作エラー | トークン期限切れ | `/カレンダー認証` で再認証するか、Google側でアクセスを取消していないか確認 |

### 旧バージョン（SQLite）からの移行

以前のSQLite版から移行する場合は `migrate_to_firestore.py` を使用してください。

```bash
# [OCI VM上で実行]
python migrate_to_firestore.py --db calendar.db --project YOUR_PROJECT_ID
```

### ヘルスチェック

```bash
# [OCI VM上で実行]
# ローカルでFlaskの動作確認
curl http://localhost:8080/health
# → "OK" と返れば正常

# Cloudflare Tunnel経由の確認
curl -L -s -o /dev/null -w '%{http_code}' https://bot.yourdomain.com/health
# → 200 が返れば Tunnel も正常
```
