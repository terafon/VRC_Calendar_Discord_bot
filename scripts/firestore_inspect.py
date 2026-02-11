#!/usr/bin/env python3
"""Firestoreの内容を確認するスクリプト

使い方:
    # 全ギルドの概要を表示
    python scripts/firestore_inspect.py

    # 特定ギルドの詳細を表示
    python scripts/firestore_inspect.py --guild-id 123456789

    # 特定コレクションの内容を表示（例: settings, counters）
    python scripts/firestore_inspect.py --collection settings

    # 特定ギルドの特定サブコレクションを表示
    python scripts/firestore_inspect.py --guild-id 123456789 --sub events
    python scripts/firestore_inspect.py --guild-id 123456789 --sub color_presets
    python scripts/firestore_inspect.py --guild-id 123456789 --sub tag_groups
    python scripts/firestore_inspect.py --guild-id 123456789 --sub tags
    python scripts/firestore_inspect.py --guild-id 123456789 --sub oauth_tokens
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from google.cloud import firestore


def print_doc(doc, indent=0):
    """ドキュメントを整形表示"""
    prefix = "  " * indent
    data = doc.to_dict()
    print(f"{prefix}📄 {doc.id}")
    if data:
        for key, value in data.items():
            val_str = str(value)
            if len(val_str) > 120:
                val_str = val_str[:120] + "..."
            print(f"{prefix}   {key}: {val_str}")


def show_overview(db):
    """全ギルドの概要を表示"""
    print("=== Firestore 概要 ===\n")

    # guilds
    guilds = list(db.collection("guilds").get())
    print(f"📁 guilds: {len(guilds)}件")
    for guild_doc in guilds:
        guild_id = guild_doc.id
        data = guild_doc.to_dict() or {}
        print(f"  🏠 {guild_id}")

        subcollections = ["events", "tag_groups", "tags", "oauth_tokens",
                          "irregular_events", "notification_settings"]
        for sub in subcollections:
            docs = list(db.collection("guilds").document(guild_id).collection(sub).get())
            if docs:
                print(f"    📂 {sub}: {len(docs)}件")

        # oauth_tokens内のcolor_presetsも確認
        token_docs = list(db.collection("guilds").document(guild_id).collection("oauth_tokens").get())
        for token_doc in token_docs:
            presets = list(
                db.collection("guilds").document(guild_id)
                .collection("oauth_tokens").document(token_doc.id)
                .collection("color_presets").get()
            )
            if presets:
                print(f"    📂 oauth_tokens/{token_doc.id}/color_presets: {len(presets)}件")

    # counters
    counters = list(db.collection("counters").get())
    if counters:
        print(f"\n📁 counters: {len(counters)}件")
        for doc in counters:
            data = doc.to_dict()
            print(f"  {doc.id}: {data}")

    # settings
    settings = list(db.collection("settings").get())
    if settings:
        print(f"\n📁 settings: {len(settings)}件")
        for doc in settings:
            data = doc.to_dict()
            val = str(data.get("value", ""))
            if len(val) > 80:
                val = val[:80] + "..."
            print(f"  {doc.id}: {val}")

    # oauth_states
    states = list(db.collection("oauth_states").get())
    if states:
        print(f"\n📁 oauth_states: {len(states)}件")


def show_guild_detail(db, guild_id):
    """特定ギルドの詳細を表示"""
    print(f"=== ギルド {guild_id} の詳細 ===\n")

    guild_ref = db.collection("guilds").document(guild_id)
    guild_doc = guild_ref.get()
    if guild_doc.exists:
        print("📄 ギルドドキュメント:")
        print_doc(guild_doc, indent=1)

    subcollections = ["events", "tag_groups", "tags", "oauth_tokens",
                      "irregular_events", "notification_settings"]
    for sub in subcollections:
        docs = list(guild_ref.collection(sub).get())
        if docs:
            print(f"\n📂 {sub} ({len(docs)}件):")
            for doc in docs:
                print_doc(doc, indent=1)

                # oauth_tokens のサブコレクション (color_presets)
                if sub == "oauth_tokens":
                    presets = list(
                        guild_ref.collection("oauth_tokens").document(doc.id)
                        .collection("color_presets").get()
                    )
                    if presets:
                        print(f"      📂 color_presets ({len(presets)}件):")
                        for p in presets:
                            print_doc(p, indent=3)


def show_guild_sub(db, guild_id, sub_name):
    """特定ギルドの特定サブコレクションを表示"""
    print(f"=== ギルド {guild_id} / {sub_name} ===\n")

    guild_ref = db.collection("guilds").document(guild_id)

    if sub_name == "color_presets":
        # color_presetsはoauth_tokens配下にあるため特別処理
        token_docs = list(guild_ref.collection("oauth_tokens").get())
        for token_doc in token_docs:
            presets = list(
                guild_ref.collection("oauth_tokens").document(token_doc.id)
                .collection("color_presets").get()
            )
            if presets:
                print(f"📂 oauth_tokens/{token_doc.id}/color_presets ({len(presets)}件):")
                for doc in presets:
                    print_doc(doc, indent=1)
    else:
        docs = list(guild_ref.collection(sub_name).get())
        if docs:
            print(f"📂 {sub_name} ({len(docs)}件):")
            for doc in docs:
                print_doc(doc, indent=1)
        else:
            print(f"(データなし)")


def show_collection(db, col_name):
    """トップレベルコレクションの内容を表示"""
    print(f"=== コレクション: {col_name} ===\n")
    docs = list(db.collection(col_name).get())
    if docs:
        for doc in docs:
            print_doc(doc)
        print(f"\n合計: {len(docs)}件")
    else:
        print("(データなし)")


def main():
    parser = argparse.ArgumentParser(description="Firestoreデータ確認")
    parser.add_argument("--guild-id", help="表示対象のギルドID")
    parser.add_argument("--sub", help="表示するサブコレクション名 (events, tags, tag_groups, oauth_tokens, color_presets等)")
    parser.add_argument("--collection", help="トップレベルコレクション名 (settings, counters, oauth_states)")
    args = parser.parse_args()

    project_id = os.getenv("GCP_PROJECT_ID")
    if not project_id:
        print("ERROR: GCP_PROJECT_ID が設定されていません (.env を確認してください)")
        sys.exit(1)

    db = firestore.Client(project=project_id)

    if args.collection:
        show_collection(db, args.collection)
    elif args.guild_id and args.sub:
        show_guild_sub(db, args.guild_id, args.sub)
    elif args.guild_id:
        show_guild_detail(db, args.guild_id)
    else:
        show_overview(db)


if __name__ == "__main__":
    main()
