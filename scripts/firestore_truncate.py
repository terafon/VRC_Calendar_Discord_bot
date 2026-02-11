#!/usr/bin/env python3
"""Firestoreの全データを削除（TRUNCATE）するスクリプト

使い方:
    # 特定ギルドのデータのみ削除
    python scripts/firestore_truncate.py --guild-id 123456789

    # 全データ削除（guilds, counters, settings, oauth_states）
    python scripts/firestore_truncate.py --all

    # ドライラン（削除せず対象を表示）
    python scripts/firestore_truncate.py --all --dry-run
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from google.cloud import firestore


def delete_collection(db, col_ref, batch_size=100, dry_run=False):
    """コレクション内の全ドキュメントを再帰的に削除"""
    deleted = 0
    docs = col_ref.limit(batch_size).get()
    doc_list = list(docs)

    while doc_list:
        for doc in doc_list:
            # サブコレクションを先に削除
            for subcol in doc.reference.collections():
                deleted += delete_collection(db, subcol, batch_size, dry_run)

            if dry_run:
                print(f"  [DRY-RUN] 削除対象: {doc.reference.path}")
            else:
                doc.reference.delete()
            deleted += 1

        docs = col_ref.limit(batch_size).get()
        doc_list = list(docs)

    return deleted


def truncate_guild(db, guild_id, dry_run=False):
    """特定ギルドのデータを削除"""
    print(f"\n--- ギルド {guild_id} のデータを削除 ---")
    guild_ref = db.collection("guilds").document(guild_id)

    subcollections = ["events", "tag_groups", "tags", "oauth_tokens",
                      "irregular_events", "notification_settings", "color_presets"]
    total = 0
    for sub_name in subcollections:
        sub_ref = guild_ref.collection(sub_name)
        count = delete_collection(db, sub_ref, dry_run=dry_run)
        if count > 0:
            print(f"  {sub_name}: {count}件削除")
        total += count

    # ギルドドキュメント自体
    guild_doc = guild_ref.get()
    if guild_doc.exists:
        if dry_run:
            print(f"  [DRY-RUN] 削除対象: guilds/{guild_id}")
        else:
            guild_ref.delete()
        total += 1

    print(f"  合計: {total}件")
    return total


def truncate_all(db, dry_run=False):
    """全トップレベルコレクションを削除"""
    top_collections = ["guilds", "counters", "settings", "oauth_states"]
    grand_total = 0

    for col_name in top_collections:
        print(f"\n--- コレクション: {col_name} ---")
        col_ref = db.collection(col_name)
        count = delete_collection(db, col_ref, dry_run=dry_run)
        print(f"  {count}件削除")
        grand_total += count

    print(f"\n=== 全体: {grand_total}件削除 ===")
    return grand_total


def main():
    parser = argparse.ArgumentParser(description="Firestoreデータ削除")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--guild-id", help="削除対象のギルドID")
    group.add_argument("--all", action="store_true", help="全データを削除")
    parser.add_argument("--dry-run", action="store_true", help="削除せず対象を表示")
    args = parser.parse_args()

    project_id = os.getenv("GCP_PROJECT_ID")
    if not project_id:
        print("ERROR: GCP_PROJECT_ID が設定されていません (.env を確認してください)")
        sys.exit(1)

    db = firestore.Client(project=project_id)

    if args.dry_run:
        print("*** ドライランモード（削除は行いません） ***")

    if args.all:
        if not args.dry_run:
            confirm = input("⚠️ 全データを削除します。よろしいですか？ (yes/no): ")
            if confirm != "yes":
                print("キャンセルしました。")
                return
        truncate_all(db, dry_run=args.dry_run)
    else:
        if not args.dry_run:
            confirm = input(f"⚠️ ギルド {args.guild_id} のデータを削除します。よろしいですか？ (yes/no): ")
            if confirm != "yes":
                print("キャンセルしました。")
                return
        truncate_guild(db, args.guild_id, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
