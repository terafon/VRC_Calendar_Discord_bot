"""
SQLite -> Firestore 1回限りの移行スクリプト

使い方:
    python migrate_to_firestore.py [--db calendar.db] [--project GCP_PROJECT_ID]
"""

import argparse
import json
import os
import sqlite3
from datetime import datetime

from google.cloud import firestore


def get_connection(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def migrate(db_path: str, project_id: str):
    conn = get_connection(db_path)
    cursor = conn.cursor()
    db = firestore.Client(project=project_id)

    counters: dict[str, int] = {}

    # ---- 1. settings ----
    cursor.execute("SELECT key, value, updated_at FROM settings")
    rows = cursor.fetchall()
    for row in rows:
        db.collection("settings").document(row["key"]).set({
            "value": row["value"],
            "updated_at": row["updated_at"] or datetime.utcnow().isoformat(),
        })
    print(f"settings: {len(rows)} 件移行完了")

    # ---- 2. events ----
    cursor.execute("SELECT * FROM events")
    events = [dict(r) for r in cursor.fetchall()]
    max_event_id = 0
    for ev in events:
        guild_id = ev["guild_id"] or ""
        event_id = ev["id"]
        max_event_id = max(max_event_id, event_id)

        # is_active を bool に変換
        ev["is_active"] = bool(ev["is_active"])

        doc_data = {k: v for k, v in ev.items()}
        db.collection("guilds").document(guild_id).collection("events").document(str(event_id)).set(doc_data)
    counters["events"] = max_event_id
    print(f"events: {len(events)} 件移行完了")

    # ---- 3. irregular_events ----
    cursor.execute("SELECT * FROM irregular_events")
    irr_rows = [dict(r) for r in cursor.fetchall()]
    for irr in irr_rows:
        event_id = irr["event_id"]
        # 親イベントの guild_id を取得
        parent = next((e for e in events if e["id"] == event_id), None)
        guild_id = parent["guild_id"] if parent else ""
        doc_ref = (
            db.collection("guilds")
            .document(guild_id)
            .collection("irregular_events")
            .document()
        )
        doc_ref.set(irr)
    print(f"irregular_events: {len(irr_rows)} 件移行完了")

    # ---- 4. color_presets ----
    cursor.execute("SELECT * FROM color_presets")
    cp_rows = [dict(r) for r in cursor.fetchall()]
    for cp in cp_rows:
        guild_id = cp["guild_id"]
        name = cp["name"]
        db.collection("guilds").document(guild_id).collection("color_presets").document(name).set(cp)
    print(f"color_presets: {len(cp_rows)} 件移行完了")

    # ---- 5. tag_groups ----
    cursor.execute("SELECT * FROM tag_groups")
    tg_rows = [dict(r) for r in cursor.fetchall()]
    max_tg_id = 0
    for tg in tg_rows:
        guild_id = tg["guild_id"]
        group_id = tg["id"]
        max_tg_id = max(max_tg_id, group_id)
        db.collection("guilds").document(guild_id).collection("tag_groups").document(str(group_id)).set(tg)
    counters["tag_groups"] = max_tg_id
    print(f"tag_groups: {len(tg_rows)} 件移行完了")

    # ---- 6. tags ----
    cursor.execute("""
        SELECT t.id, t.group_id, g.name as group_name, t.name, t.description, g.guild_id
        FROM tags t
        JOIN tag_groups g ON g.id = t.group_id
    """)
    tag_rows = [dict(r) for r in cursor.fetchall()]
    max_tag_id = 0
    for tag in tag_rows:
        guild_id = tag["guild_id"]
        tag_id = tag["id"]
        max_tag_id = max(max_tag_id, tag_id)
        db.collection("guilds").document(guild_id).collection("tags").document(str(tag_id)).set({
            "id": tag_id,
            "group_id": tag["group_id"],
            "group_name": tag["group_name"],
            "name": tag["name"],
            "description": tag["description"],
        })
    counters["tags"] = max_tag_id
    print(f"tags: {len(tag_rows)} 件移行完了")

    # ---- 7. calendar_accounts ----
    cursor.execute("SELECT * FROM calendar_accounts")
    ca_rows = [dict(r) for r in cursor.fetchall()]
    max_ca_id = 0
    for ca in ca_rows:
        guild_id = ca["guild_id"]
        ca_id = ca["id"]
        max_ca_id = max(max_ca_id, ca_id)
        db.collection("guilds").document(guild_id).collection("calendar_accounts").document(str(ca_id)).set(ca)
    counters["calendar_accounts"] = max_ca_id
    print(f"calendar_accounts: {len(ca_rows)} 件移行完了")

    # ---- 8. guild_settings ----
    cursor.execute("SELECT * FROM guild_settings")
    gs_rows = [dict(r) for r in cursor.fetchall()]
    for gs in gs_rows:
        guild_id = gs["guild_id"]
        db.collection("guilds").document(guild_id).collection("guild_settings").document("config").set(gs)
    print(f"guild_settings: {len(gs_rows)} 件移行完了")

    # ---- 9. counters ----
    for name, val in counters.items():
        db.collection("counters").document(name).set({"current": val})
    print(f"counters: {len(counters)} 件設定完了")

    conn.close()
    print("\n移行完了!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SQLite -> Firestore 移行")
    parser.add_argument("--db", default="calendar.db", help="SQLite DB パス")
    parser.add_argument("--project", default=os.getenv("GCP_PROJECT_ID"), help="GCP プロジェクトID")
    args = parser.parse_args()

    if not args.project:
        print("ERROR: --project または GCP_PROJECT_ID 環境変数を設定してください")
        exit(1)

    migrate(args.db, args.project)
