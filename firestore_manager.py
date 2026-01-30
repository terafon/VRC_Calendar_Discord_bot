import json
from datetime import datetime, timedelta
from typing import List, Optional, Dict

from google.cloud import firestore


class FirestoreManager:
    def __init__(self, project_id: str = None):
        self.db = firestore.Client(project=project_id)

    # ---- helpers ----

    def _guild_ref(self, guild_id: str):
        """guilds/{guild_id} への参照"""
        return self.db.collection("guilds").document(guild_id)

    def _next_id(self, counter_name: str) -> int:
        """トランザクションベースのID自動採番"""
        counter_ref = self.db.collection("counters").document(counter_name)

        @firestore.transactional
        def _increment(transaction):
            snapshot = counter_ref.get(transaction=transaction)
            if snapshot.exists:
                current = snapshot.get("current")
            else:
                current = 0
            new_val = current + 1
            transaction.set(counter_ref, {"current": new_val})
            return new_val

        return _increment(self.db.transaction())

    def _find_event_ref(self, event_id: int):
        """collection_group('events') で event_id からドキュメント参照を検索"""
        docs = (
            self.db.collection_group("events")
            .where(filter=firestore.FieldFilter("id", "==", event_id))
            .where(filter=firestore.FieldFilter("is_active", "==", True))
            .limit(1)
            .get()
        )
        for doc in docs:
            return doc.reference
        return None

    # ---- イベント管理 ----

    def add_event(
        self,
        guild_id: str,
        event_name: str,
        tags: List[str],
        recurrence: str,
        nth_weeks: Optional[List[int]],
        event_type: Optional[str],
        time: Optional[str],
        weekday: int,
        duration_minutes: int,
        description: str,
        color_name: Optional[str],
        urls: Optional[List[str]],
        discord_channel_id: str,
        created_by: str,
    ) -> int:
        """予定を追加"""
        event_id = self._next_id("events")
        now = datetime.utcnow().isoformat()

        data = {
            "id": event_id,
            "guild_id": guild_id,
            "event_name": event_name,
            "tags": json.dumps(tags, ensure_ascii=False),
            "recurrence": recurrence,
            "nth_weeks": json.dumps(nth_weeks) if nth_weeks else None,
            "event_type": event_type,
            "time": time,
            "weekday": weekday,
            "duration_minutes": duration_minutes,
            "description": description,
            "color_name": color_name,
            "urls": json.dumps(urls, ensure_ascii=False) if urls else None,
            "google_calendar_events": None,
            "discord_channel_id": discord_channel_id,
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
            "is_active": True,
        }

        self._guild_ref(guild_id).collection("events").document(str(event_id)).set(data)
        return event_id

    def update_google_calendar_events(self, event_id: int, google_events: List[dict]):
        """Google カレンダーイベント情報を更新"""
        ref = self._find_event_ref(event_id)
        if ref:
            ref.update({
                "google_calendar_events": json.dumps(google_events, ensure_ascii=False),
                "updated_at": datetime.utcnow().isoformat(),
            })

    def get_this_week_events(self, guild_id: Optional[str] = None) -> List[dict]:
        """今週の予定を取得"""
        today = datetime.now().date()
        start_of_week = today - timedelta(days=today.weekday())
        end_of_week = start_of_week + timedelta(days=6)

        return self.search_events(
            guild_id=guild_id,
            start_date=datetime.combine(start_of_week, datetime.min.time()),
            end_date=datetime.combine(end_of_week, datetime.max.time()),
        )

    def search_events(
        self,
        start_date: datetime,
        end_date: datetime,
        guild_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
        event_name: Optional[str] = None,
    ) -> List[dict]:
        """予定を検索"""
        from recurrence_calculator import RecurrenceCalculator

        events = self._get_active_events(guild_id)

        result = []
        for event in events:
            if event["recurrence"] == "irregular":
                # 不定期予定は個別サブコレクションから取得
                irr_ref = (
                    self._guild_ref(event["guild_id"])
                    .collection("irregular_events")
                )
                irr_docs = irr_ref.where(
                    filter=firestore.FieldFilter("event_id", "==", event["id"])
                ).get()

                for irr_doc in irr_docs:
                    irr = irr_doc.to_dict()
                    irr_date = irr["event_date"]
                    if start_date.date().isoformat() <= irr_date <= end_date.date().isoformat():
                        result.append({
                            **event,
                            "date": irr["event_date"],
                            "time": irr["event_time"],
                        })
            else:
                dates = RecurrenceCalculator.calculate_dates(
                    recurrence=event["recurrence"],
                    nth_weeks=json.loads(event["nth_weeks"]) if event["nth_weeks"] else None,
                    weekday=event["weekday"],
                    start_date=start_date,
                    months_ahead=0,
                    end_date_limit=end_date,
                )
                for date in dates:
                    if start_date.date() <= date.date() <= end_date.date():
                        result.append({**event, "date": date.strftime("%Y-%m-%d")})

        # フィルタリング
        if tags:
            result = [
                e for e in result
                if any(tag in json.loads(e["tags"]) for tag in tags)
            ]
        if event_name:
            result = [
                e for e in result
                if event_name.lower() in e["event_name"].lower()
            ]

        return sorted(result, key=lambda x: (x["date"], x["time"] or ""))

    def search_events_by_name(self, name: str, guild_id: Optional[str] = None) -> List[dict]:
        """予定名で検索（LIKE相当 — 全件取得後Pythonでフィルタ）"""
        events = self._get_active_events(guild_id)
        if not name:
            return events
        lower_name = name.lower()
        return [e for e in events if lower_name in e["event_name"].lower()]

    def update_event(self, event_id: int, updates: dict):
        """予定を更新"""
        ref = self._find_event_ref(event_id)
        if not ref:
            return

        fs_updates = {}
        for key, value in updates.items():
            if isinstance(value, (list, dict)):
                fs_updates[key] = json.dumps(value, ensure_ascii=False)
            else:
                fs_updates[key] = value
        fs_updates["updated_at"] = datetime.utcnow().isoformat()
        ref.update(fs_updates)

    def delete_event(self, event_id: int):
        """予定を削除（論理削除）"""
        ref = self._find_event_ref(event_id)
        if ref:
            ref.update({
                "is_active": False,
                "updated_at": datetime.utcnow().isoformat(),
            })

    def get_all_active_events(self, guild_id: Optional[str] = None) -> List[dict]:
        """全てのアクティブな予定を取得"""
        events = self._get_active_events(guild_id)
        return sorted(events, key=lambda x: x.get("created_at", ""), reverse=True)

    # ---- 設定 ----

    def update_setting(self, key: str, value: str):
        """設定情報を更新"""
        self.db.collection("settings").document(key).set({
            "value": value,
            "updated_at": datetime.utcnow().isoformat(),
        })

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """設定情報を取得"""
        doc = self.db.collection("settings").document(key).get()
        if doc.exists:
            return doc.to_dict().get("value", default)
        return default

    # ---- 色プリセット ----

    def add_color_preset(self, guild_id: str, name: str, color_id: str, description: str = ""):
        """色プリセットを追加"""
        self._guild_ref(guild_id).collection("color_presets").document(name).set({
            "guild_id": guild_id,
            "name": name,
            "color_id": color_id,
            "description": description,
        })

    def list_color_presets(self, guild_id: str) -> List[dict]:
        """色プリセット一覧"""
        docs = (
            self._guild_ref(guild_id)
            .collection("color_presets")
            .order_by("name")
            .get()
        )
        return [doc.to_dict() for doc in docs]

    def get_color_preset(self, guild_id: str, name: str) -> Optional[dict]:
        """色プリセットを取得"""
        doc = self._guild_ref(guild_id).collection("color_presets").document(name).get()
        return doc.to_dict() if doc.exists else None

    def delete_color_preset(self, guild_id: str, name: str):
        """色プリセットを削除"""
        self._guild_ref(guild_id).collection("color_presets").document(name).delete()

    # ---- タググループ / タグ ----

    def list_tag_groups(self, guild_id: str) -> List[dict]:
        """タググループ一覧"""
        docs = (
            self._guild_ref(guild_id)
            .collection("tag_groups")
            .order_by("id")
            .get()
        )
        return [doc.to_dict() for doc in docs]

    def add_tag_group(self, guild_id: str, name: str, description: str = "") -> int:
        """タググループを追加（最大3つ）"""
        existing = self.list_tag_groups(guild_id)
        if len(existing) >= 3:
            raise ValueError("タググループは最大3つまでです。")

        group_id = self._next_id("tag_groups")
        data = {
            "id": group_id,
            "guild_id": guild_id,
            "name": name,
            "description": description,
        }
        self._guild_ref(guild_id).collection("tag_groups").document(str(group_id)).set(data)
        return group_id

    def update_tag_group(
        self, guild_id: str, group_id: int,
        name: Optional[str] = None, description: Optional[str] = None,
    ):
        """タググループを更新"""
        updates = {}
        if name is not None:
            updates["name"] = name
        if description is not None:
            updates["description"] = description
        if not updates:
            return

        ref = self._guild_ref(guild_id).collection("tag_groups").document(str(group_id))
        doc = ref.get()
        if doc.exists:
            ref.update(updates)

    def delete_tag_group(self, guild_id: str, group_id: int):
        """タググループを削除（タグもカスケード削除）"""
        guild_ref = self._guild_ref(guild_id)

        # 関連タグを取得して削除
        tags_docs = (
            guild_ref.collection("tags")
            .where(filter=firestore.FieldFilter("group_id", "==", group_id))
            .get()
        )
        batch = self.db.batch()
        for tag_doc in tags_docs:
            batch.delete(tag_doc.reference)

        # タググループ自体を削除
        group_ref = guild_ref.collection("tag_groups").document(str(group_id))
        batch.delete(group_ref)
        batch.commit()

    def get_tag_group(self, guild_id: str, group_id: int) -> Optional[dict]:
        """タググループを取得"""
        doc = (
            self._guild_ref(guild_id)
            .collection("tag_groups")
            .document(str(group_id))
            .get()
        )
        return doc.to_dict() if doc.exists else None

    def add_tag(self, guild_id: str, group_id: int, name: str, description: str = ""):
        """タグを追加"""
        # グループがこのサーバーのものか確認
        group = self.get_tag_group(guild_id, group_id)
        if not group:
            raise ValueError("指定されたタググループは存在しません。")

        tag_id = self._next_id("tags")
        data = {
            "id": tag_id,
            "group_id": group_id,
            "group_name": group["name"],
            "name": name,
            "description": description,
        }
        self._guild_ref(guild_id).collection("tags").document(str(tag_id)).set(data)

    def delete_tag(self, guild_id: str, group_id: int, name: str):
        """タグを削除"""
        group = self.get_tag_group(guild_id, group_id)
        if not group:
            return

        docs = (
            self._guild_ref(guild_id)
            .collection("tags")
            .where(filter=firestore.FieldFilter("group_id", "==", group_id))
            .where(filter=firestore.FieldFilter("name", "==", name))
            .get()
        )
        for doc in docs:
            doc.reference.delete()

    def list_tags(self, guild_id: str) -> List[dict]:
        """タグ一覧"""
        docs = self._guild_ref(guild_id).collection("tags").get()
        tags = [doc.to_dict() for doc in docs]
        return sorted(tags, key=lambda t: (t.get("group_id", 0), t.get("name", "")))

    def list_tags_by_group(self, guild_id: str) -> Dict[int, List[dict]]:
        """タグをグループごとに取得"""
        tags = self.list_tags(guild_id)
        grouped: Dict[int, List[dict]] = {}
        for tag in tags:
            grouped.setdefault(tag["group_id"], []).append(tag)
        return grouped

    def tag_exists(self, guild_id: str, name: str) -> bool:
        """タグが存在するか確認"""
        docs = (
            self._guild_ref(guild_id)
            .collection("tags")
            .where(filter=firestore.FieldFilter("name", "==", name))
            .limit(1)
            .get()
        )
        return len(list(docs)) > 0

    def find_missing_tags(self, guild_id: str, tags: List[str]) -> List[str]:
        """存在しないタグを検索"""
        if not tags:
            return []
        all_tags = self.list_tags(guild_id)
        existing_names = {t["name"] for t in all_tags}
        return [t for t in tags if t not in existing_names]

    # ---- カレンダーアカウント ----

    def add_calendar_account(
        self, guild_id: str, name: str, calendar_id: str,
        credentials_path: Optional[str] = None,
    ) -> int:
        """カレンダーアカウントを追加"""
        account_id = self._next_id("calendar_accounts")
        data = {
            "id": account_id,
            "guild_id": guild_id,
            "name": name,
            "calendar_id": calendar_id,
            "credentials_path": credentials_path,
        }
        self._guild_ref(guild_id).collection("calendar_accounts").document(str(account_id)).set(data)
        return account_id

    def list_calendar_accounts(self, guild_id: str) -> List[dict]:
        """カレンダーアカウント一覧"""
        docs = (
            self._guild_ref(guild_id)
            .collection("calendar_accounts")
            .order_by("id")
            .get()
        )
        return [doc.to_dict() for doc in docs]

    def get_calendar_account(self, guild_id: str, account_id: int) -> Optional[dict]:
        """カレンダーアカウントを取得"""
        doc = (
            self._guild_ref(guild_id)
            .collection("calendar_accounts")
            .document(str(account_id))
            .get()
        )
        return doc.to_dict() if doc.exists else None

    def delete_calendar_account(self, guild_id: str, account_id: int):
        """カレンダーアカウントを削除"""
        self._guild_ref(guild_id).collection("calendar_accounts").document(str(account_id)).delete()

    def set_guild_calendar_account(self, guild_id: str, account_id: Optional[int]):
        """サーバーで使用するカレンダーアカウントを設定"""
        if account_id is not None:
            account = self.get_calendar_account(guild_id, account_id)
            if not account:
                raise ValueError("指定されたカレンダーアカウントは存在しません。")

        self._guild_ref(guild_id).collection("guild_settings").document("config").set({
            "guild_id": guild_id,
            "calendar_account_id": account_id,
        })

    def get_guild_calendar_account(self, guild_id: str) -> Optional[dict]:
        """サーバーで使用中のカレンダーアカウントを取得"""
        config_doc = (
            self._guild_ref(guild_id)
            .collection("guild_settings")
            .document("config")
            .get()
        )
        if not config_doc.exists:
            return None

        config = config_doc.to_dict()
        account_id = config.get("calendar_account_id")
        if account_id is None:
            return None

        return self.get_calendar_account(guild_id, account_id)

    # ---- private helpers ----

    def _get_active_events(self, guild_id: Optional[str] = None) -> List[dict]:
        """アクティブなイベントを取得"""
        if guild_id:
            docs = (
                self._guild_ref(guild_id)
                .collection("events")
                .where(filter=firestore.FieldFilter("is_active", "==", True))
                .get()
            )
        else:
            docs = (
                self.db.collection_group("events")
                .where(filter=firestore.FieldFilter("is_active", "==", True))
                .get()
            )
        return [doc.to_dict() for doc in docs]
