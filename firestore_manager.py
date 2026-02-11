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
        x_url: Optional[str] = None,
        vrc_group_url: Optional[str] = None,
        official_url: Optional[str] = None,
        discord_channel_id: str = "",
        created_by: str = "",
        calendar_owner: str = "",
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
            "x_url": x_url,
            "vrc_group_url": vrc_group_url,
            "official_url": official_url,
            "google_calendar_events": None,
            "discord_channel_id": discord_channel_id,
            "created_by": created_by,
            "calendar_owner": calendar_owner,
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

    # ---- 色プリセット（カレンダー単位） ----

    def _color_presets_ref(self, guild_id: str, user_id: str):
        """oauth_tokens/{user_id}/color_presets への参照"""
        return (
            self._guild_ref(guild_id)
            .collection("oauth_tokens").document(user_id)
            .collection("color_presets")
        )

    def add_color_preset(self, guild_id: str, user_id: str, name: str, color_id: str, description: str = "",
                         recurrence_type: Optional[str] = None, is_auto_generated: bool = False):
        """色プリセットを追加（カレンダー単位）

        Args:
            user_id: カレンダーオーナーのユーザーID
            recurrence_type: "weekly" | "biweekly" | "monthly" | "nth_week" | "irregular" | None
            is_auto_generated: True = セットアップウィザードで自動生成
        """
        data = {
            "guild_id": guild_id,
            "name": name,
            "color_id": color_id,
            "description": description,
        }
        if recurrence_type is not None:
            data["recurrence_type"] = recurrence_type
        if is_auto_generated:
            data["is_auto_generated"] = True
        self._color_presets_ref(guild_id, user_id).document(name).set(data)

    def list_color_presets(self, guild_id: str, user_id: str) -> List[dict]:
        """色プリセット一覧（カレンダー単位）"""
        docs = (
            self._color_presets_ref(guild_id, user_id)
            .order_by("name")
            .get()
        )
        return [doc.to_dict() for doc in docs]

    def get_color_preset(self, guild_id: str, user_id: str, name: str) -> Optional[dict]:
        """色プリセットを取得（カレンダー単位）"""
        doc = self._color_presets_ref(guild_id, user_id).document(name).get()
        return doc.to_dict() if doc.exists else None

    def get_color_preset_by_recurrence(self, guild_id: str, user_id: str, recurrence_type: str) -> Optional[dict]:
        """繰り返しタイプに対応する色プリセットを取得（カレンダー単位）"""
        docs = (
            self._color_presets_ref(guild_id, user_id)
            .where(filter=firestore.FieldFilter("recurrence_type", "==", recurrence_type))
            .limit(1)
            .get()
        )
        for doc in docs:
            return doc.to_dict()
        return None

    def initialize_default_color_presets(self, guild_id: str, user_id: str, presets_data: list) -> bool:
        """色プリセットを一括登録する（セットアップウィザード用、カレンダー単位）

        Args:
            user_id: カレンダーオーナーのユーザーID
            presets_data: [{"name": "色名", "color_id": "9", "recurrence_type": "weekly", "description": "説明"}, ...]
        """
        batch = self.db.batch()
        for preset in presets_data:
            ref = self._color_presets_ref(guild_id, user_id).document(preset["name"])
            batch.set(ref, {
                "guild_id": guild_id,
                "name": preset["name"],
                "color_id": preset["color_id"],
                "recurrence_type": preset["recurrence_type"],
                "description": preset.get("description", ""),
                "is_auto_generated": True,
            })
        batch.commit()

        # セットアップ完了フラグを設定
        self.mark_color_setup_done(guild_id, user_id)
        return True

    def is_color_setup_done(self, guild_id: str, user_id: str) -> bool:
        """色セットアップが完了しているかどうかを確認（カレンダー単位）"""
        doc = self._guild_ref(guild_id).collection("oauth_tokens").document(user_id).get()
        if doc.exists:
            return doc.to_dict().get("color_setup_done", False)
        return False

    def mark_color_setup_done(self, guild_id: str, user_id: str):
        """色セットアップ完了フラグを設定（カレンダー単位）"""
        self._guild_ref(guild_id).collection("oauth_tokens").document(user_id).update({
            "color_setup_done": True,
        })

    def delete_color_preset(self, guild_id: str, user_id: str, name: str):
        """色プリセットを削除（カレンダー単位）"""
        self._color_presets_ref(guild_id, user_id).document(name).delete()

    def list_all_color_presets_by_calendar(self, guild_id: str) -> Dict[str, List[dict]]:
        """全カレンダーの色プリセットをdict形式で返す（NLPコンテキスト用）

        Returns:
            {user_id: [preset_dict, ...], ...}
        """
        all_tokens = self.get_all_oauth_tokens(guild_id)
        result: Dict[str, List[dict]] = {}
        for token in all_tokens:
            user_id = token.get("_doc_id") or token.get("authenticated_by", "")
            if not user_id:
                continue
            presets = self.list_color_presets(guild_id, user_id)
            display_name = token.get("display_name") or f"<@{user_id}>"
            result[display_name] = presets
        return result

    def migrate_guild_color_presets_to_calendars(self, guild_id: str):
        """旧guild単位プリセットを全カレンダーにコピー"""
        guild_doc = self._guild_ref(guild_id).get()
        if guild_doc.exists and guild_doc.to_dict().get("color_presets_migrated"):
            return  # 既にマイグレーション済み

        # 旧パスの色プリセットを取得
        old_docs = self._guild_ref(guild_id).collection("color_presets").get()
        old_presets = [doc.to_dict() for doc in old_docs]
        if not old_presets:
            return  # 旧プリセットなし

        # 全認証カレンダーにコピー
        all_tokens = self.get_all_oauth_tokens(guild_id)
        batch = self.db.batch()
        for token in all_tokens:
            user_id = token.get("_doc_id") or token.get("authenticated_by", "")
            if not user_id:
                continue
            for preset in old_presets:
                ref = self._color_presets_ref(guild_id, user_id).document(preset["name"])
                batch.set(ref, preset)
            # color_setup_done = True に設定
            token_ref = self._guild_ref(guild_id).collection("oauth_tokens").document(user_id)
            batch.update(token_ref, {"color_setup_done": True})
        batch.commit()

        # マイグレーション完了フラグ
        self._guild_ref(guild_id).set({"color_presets_migrated": True}, merge=True)
        print(f"Guild {guild_id}: migrated {len(old_presets)} color presets to {len(all_tokens)} calendars")

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

    # ---- OAuth トークン管理 ----

    def save_oauth_tokens(
        self,
        guild_id: str,
        access_token: str,
        refresh_token: str,
        token_expiry: str,
        calendar_id: str,
        authenticated_by: str,
        authenticated_at: str,
        display_name: str = "",
        description: str = "",
    ):
        """OAuth トークンを保存（ユーザーごとのドキュメント）"""
        doc_ref = self._guild_ref(guild_id).collection("oauth_tokens").document(authenticated_by)
        existing = doc_ref.get()

        data = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_expiry": token_expiry,
            "calendar_id": calendar_id,
            "authenticated_by": authenticated_by,
            "authenticated_at": authenticated_at,
        }

        if existing.exists:
            # 再認証: トークンのみ更新、表示名等は保持
            doc_ref.update(data)
        else:
            # 新規: display_name, description, is_default, color_setup_done を設定
            all_tokens = self.get_all_oauth_tokens(guild_id)
            data["display_name"] = display_name
            data["description"] = description
            data["is_default"] = len(all_tokens) == 0  # 最初のカレンダーならデフォルト
            data["color_setup_done"] = False  # 色初期設定は未完了
            doc_ref.set(data)

    def get_oauth_tokens(self, guild_id: str, user_id: str) -> Optional[dict]:
        """OAuth トークンを取得（ユーザーID指定）"""
        # 1. user_id ドキュメントを試行
        doc = self._guild_ref(guild_id).collection("oauth_tokens").document(user_id).get()
        if doc.exists:
            data = doc.to_dict()
            data["_doc_id"] = doc.id
            return data
        # 2. レガシー "google" ドキュメントをチェック
        legacy = self._guild_ref(guild_id).collection("oauth_tokens").document("google").get()
        if legacy.exists:
            data = legacy.to_dict()
            if data.get("authenticated_by") == user_id:
                # マイグレーション: display_name等を追加してコピー
                data.setdefault("display_name", "")
                data.setdefault("description", "")
                data.setdefault("is_default", True)
                self._guild_ref(guild_id).collection("oauth_tokens").document(user_id).set(data)
                legacy.reference.delete()
                data["_doc_id"] = user_id
                return data
        return None

    def get_all_oauth_tokens(self, guild_id: str) -> List[dict]:
        """サーバー内の全認証済みOAuthトークンを取得"""
        docs = self._guild_ref(guild_id).collection("oauth_tokens").get()
        results = []
        for doc in docs:
            data = doc.to_dict()
            data["_doc_id"] = doc.id
            results.append(data)
        return results

    def get_default_oauth_tokens(self, guild_id: str) -> Optional[dict]:
        """デフォルトカレンダーのOAuthトークンを取得"""
        docs = (self._guild_ref(guild_id).collection("oauth_tokens")
                .where(filter=firestore.FieldFilter("is_default", "==", True))
                .limit(1).get())
        for doc in docs:
            data = doc.to_dict()
            data["_doc_id"] = doc.id
            return data
        return None

    def get_oauth_tokens_by_display_name(self, guild_id: str, display_name: str) -> Optional[dict]:
        """表示名でカレンダーのOAuthトークンを検索"""
        docs = (self._guild_ref(guild_id).collection("oauth_tokens")
                .where(filter=firestore.FieldFilter("display_name", "==", display_name))
                .limit(1).get())
        for doc in docs:
            data = doc.to_dict()
            data["_doc_id"] = doc.id
            return data
        return None

    def update_oauth_settings(self, guild_id: str, user_id: str, **kwargs):
        """カレンダー設定を更新（display_name, description, calendar_id, is_default）"""
        doc_ref = self._guild_ref(guild_id).collection("oauth_tokens").document(user_id)
        updates = {k: v for k, v in kwargs.items() if v is not None}
        if updates.get("is_default"):
            # 他のデフォルトを解除
            all_docs = self._guild_ref(guild_id).collection("oauth_tokens").get()
            for d in all_docs:
                if d.id != user_id:
                    d.reference.update({"is_default": False})
        if updates:
            doc_ref.update(updates)

    def update_oauth_access_token(self, guild_id: str, user_id: str, access_token: str, token_expiry: str):
        """リフレッシュ後のアクセストークンを更新"""
        self._guild_ref(guild_id).collection("oauth_tokens").document(user_id).update({
            "access_token": access_token,
            "token_expiry": token_expiry,
        })

    def update_oauth_calendar_id(self, guild_id: str, user_id: str, calendar_id: str):
        """OAuth のカレンダーIDを更新"""
        self._guild_ref(guild_id).collection("oauth_tokens").document(user_id).update({
            "calendar_id": calendar_id,
        })

    def delete_oauth_tokens(self, guild_id: str, user_id: str):
        """OAuth トークンを削除（認証解除）"""
        self._guild_ref(guild_id).collection("oauth_tokens").document(user_id).delete()

    def save_oauth_state(self, state: str, guild_id: str, user_id: str):
        """CSRF state を保存"""
        self.db.collection("oauth_states").document(state).set({
            "guild_id": guild_id,
            "user_id": user_id,
            "created_at": datetime.utcnow().isoformat(),
        })

    def get_and_delete_oauth_state(self, state: str) -> Optional[dict]:
        """CSRF state をワンタイム検証（取得して削除）"""
        ref = self.db.collection("oauth_states").document(state)
        doc = ref.get()
        if not doc.exists:
            return None
        data = doc.to_dict()
        ref.delete()
        return data

    # ---- 通知設定 ----

    def get_notification_settings(self, guild_id: str) -> Optional[dict]:
        """通知設定を取得"""
        doc = (
            self._guild_ref(guild_id)
            .collection("notification_settings")
            .document("config")
            .get()
        )
        return doc.to_dict() if doc.exists else None

    def save_notification_settings(
        self,
        guild_id: str,
        enabled: bool,
        weekday: int,
        hour: int,
        minute: int,
        channel_id: str,
        calendar_owners: List[str],
        configured_by: str,
    ):
        """通知設定を保存"""
        data = {
            "enabled": enabled,
            "weekday": weekday,
            "hour": hour,
            "minute": minute,
            "channel_id": channel_id,
            "calendar_owners": calendar_owners,
            "configured_by": configured_by,
            "configured_at": datetime.utcnow().isoformat(),
        }
        (
            self._guild_ref(guild_id)
            .collection("notification_settings")
            .document("config")
            .set(data, merge=True)
        )

    def disable_notification(self, guild_id: str):
        """通知を無効化"""
        ref = (
            self._guild_ref(guild_id)
            .collection("notification_settings")
            .document("config")
        )
        doc = ref.get()
        if doc.exists:
            ref.update({"enabled": False})

    def update_notification_last_sent(self, guild_id: str, sent_at: str):
        """最終通知送信日時を更新"""
        (
            self._guild_ref(guild_id)
            .collection("notification_settings")
            .document("config")
            .update({"last_sent_at": sent_at})
        )

    def get_all_notification_settings(self) -> List[Dict]:
        """全サーバーの通知設定を取得（collection_groupクエリ）"""
        docs = (
            self.db.collection_group("notification_settings")
            .where(filter=firestore.FieldFilter("enabled", "==", True))
            .get()
        )
        results = []
        for doc in docs:
            data = doc.to_dict()
            # パスから guild_id を抽出: guilds/{guild_id}/notification_settings/config
            path_parts = doc.reference.path.split("/")
            if len(path_parts) >= 2:
                data["guild_id"] = path_parts[1]
            results.append(data)
        return results

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
