"""Firestore データを JSON にエクスポートして GCS にバックアップするスクリプト。

Usage:
    python firestore_backup.py                  # バックアップを実行
    python firestore_backup.py --restore FILE   # GCS 上の JSON からリストア

環境変数:
    GCP_PROJECT_ID                   - GCP プロジェクト ID
    GCS_BUCKET_NAME                  - バックアップ先の GCS バケット名
    GOOGLE_APPLICATION_CREDENTIALS   - サービスアカウント JSON パス
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime

from dotenv import load_dotenv
from google.cloud import firestore, storage

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# バックアップ対象のトップレベルコレクション
TOP_LEVEL_COLLECTIONS = ["guilds", "counters", "settings", "oauth_states"]

# guilds/{guild_id} 配下のサブコレクション
GUILD_SUBCOLLECTIONS = [
    "events",
    "irregular_events",
    "color_presets",
    "tag_groups",
    "tags",
    "calendar_accounts",
    "guild_settings",
    "oauth_tokens",
]


def _serialize_doc(doc) -> dict:
    """Firestore ドキュメントを JSON 化可能な dict に変換する。"""
    data = doc.to_dict()
    if data is None:
        return {}
    result = {}
    for k, v in data.items():
        if hasattr(v, "isoformat"):
            result[k] = v.isoformat()
        else:
            result[k] = v
    return result


def backup(project_id: str, bucket_name: str) -> str:
    """Firestore の全データを JSON にして GCS にアップロードする。

    Returns:
        アップロードした GCS オブジェクト名
    """
    db = firestore.Client(project=project_id)
    export_data: dict = {}

    # --- トップレベルコレクション ---
    for col_name in TOP_LEVEL_COLLECTIONS:
        if col_name == "guilds":
            continue  # guilds はサブコレクション込みで別処理
        docs = db.collection(col_name).get()
        export_data[col_name] = {doc.id: _serialize_doc(doc) for doc in docs}
        logger.info("  %s: %d docs", col_name, len(export_data[col_name]))

    # --- guilds + サブコレクション ---
    guilds_data: dict = {}
    guild_docs = db.collection("guilds").get()
    for guild_doc in guild_docs:
        gid = guild_doc.id
        guild_entry: dict = {"_doc": _serialize_doc(guild_doc)}

        for sub_name in GUILD_SUBCOLLECTIONS:
            sub_docs = db.collection("guilds").document(gid).collection(sub_name).get()
            guild_entry[sub_name] = {d.id: _serialize_doc(d) for d in sub_docs}

        guilds_data[gid] = guild_entry
        logger.info("  guilds/%s: %d subcollections", gid, len(GUILD_SUBCOLLECTIONS))

    export_data["guilds"] = guilds_data

    # --- JSON 化 ---
    json_bytes = json.dumps(export_data, ensure_ascii=False, indent=2).encode("utf-8")
    logger.info("Total export size: %.1f KB", len(json_bytes) / 1024)

    # --- GCS にアップロード ---
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    blob_name = f"firestore_backup/{timestamp}.json"

    client = storage.Client(project=project_id)
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_string(json_bytes, content_type="application/json")
    logger.info("Uploaded to gs://%s/%s", bucket_name, blob_name)

    # --- 古いバックアップの削除（最新30件を保持） ---
    blobs = sorted(
        client.list_blobs(bucket, prefix="firestore_backup/"),
        key=lambda b: b.name,
    )
    if len(blobs) > 30:
        for old_blob in blobs[: len(blobs) - 30]:
            old_blob.delete()
            logger.info("Deleted old backup: %s", old_blob.name)

    return blob_name


def restore(project_id: str, bucket_name: str, blob_name: str):
    """GCS 上の JSON バックアップから Firestore にリストアする。

    Warning:
        既存のドキュメントを上書きします。
    """
    client = storage.Client(project=project_id)
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)

    json_bytes = blob.download_as_bytes()
    data = json.loads(json_bytes.decode("utf-8"))
    logger.info("Downloaded %s (%.1f KB)", blob_name, len(json_bytes) / 1024)

    db = firestore.Client(project=project_id)

    # --- トップレベルコレクション ---
    for col_name in TOP_LEVEL_COLLECTIONS:
        if col_name == "guilds":
            continue
        col_data = data.get(col_name, {})
        for doc_id, doc_data in col_data.items():
            db.collection(col_name).document(doc_id).set(doc_data)
        logger.info("  Restored %s: %d docs", col_name, len(col_data))

    # --- guilds + サブコレクション ---
    guilds_data = data.get("guilds", {})
    for gid, guild_entry in guilds_data.items():
        # ギルドドキュメント本体
        guild_doc_data = guild_entry.get("_doc", {})
        if guild_doc_data:
            db.collection("guilds").document(gid).set(guild_doc_data)

        # サブコレクション
        for sub_name in GUILD_SUBCOLLECTIONS:
            sub_data = guild_entry.get(sub_name, {})
            for doc_id, doc_data in sub_data.items():
                db.collection("guilds").document(gid).collection(sub_name).document(doc_id).set(doc_data)
            if sub_data:
                logger.info("  Restored guilds/%s/%s: %d docs", gid, sub_name, len(sub_data))

    logger.info("Restore completed.")


def main():
    parser = argparse.ArgumentParser(description="Firestore backup/restore to GCS")
    parser.add_argument("--restore", metavar="BLOB_NAME", help="Restore from GCS blob (e.g. firestore_backup/20240101_120000.json)")
    args = parser.parse_args()

    project_id = os.environ.get("GCP_PROJECT_ID")
    bucket_name = os.environ.get("GCS_BUCKET_NAME")

    if not project_id:
        logger.error("GCP_PROJECT_ID is not set")
        sys.exit(1)
    if not bucket_name:
        logger.error("GCS_BUCKET_NAME is not set")
        sys.exit(1)

    if args.restore:
        logger.info("Restoring from gs://%s/%s ...", bucket_name, args.restore)
        restore(project_id, bucket_name, args.restore)
    else:
        logger.info("Starting Firestore backup ...")
        blob_name = backup(project_id, bucket_name)
        logger.info("Backup completed: %s", blob_name)


if __name__ == "__main__":
    main()
