import discord
from discord import app_commands
from discord.ext import commands, tasks
import json
import calendar
import secrets
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple

from nlp_processor import NLPProcessor
from calendar_manager import GoogleCalendarManager
from firestore_manager import FirestoreManager
from recurrence_calculator import RecurrenceCalculator
from oauth_handler import OAuthHandler
from conversation_manager import ConversationManager

RECURRENCE_TYPES = {
    "weekly": "毎週",
    "biweekly": "隔週",
    "nth_week": "第n週",
    "irregular": "不定期"
}

COLOR_CATEGORIES = [
    {"key": "weekly", "label": "毎週", "description": "毎週開催のイベント"},
    {"key": "biweekly", "label": "隔週", "description": "隔週開催のイベント"},
    {"key": "monthly", "label": "月1回", "description": "月に1回開催のイベント"},
    {"key": "nth_week", "label": "第n週", "description": "月に複数回（第2,4週など）開催のイベント"},
    {"key": "irregular", "label": "不定期", "description": "不定期開催のイベント"},
]

# Google Calendar colorId → 色名マッピング
GOOGLE_CALENDAR_COLORS = {
    "1": {"name": "ラベンダー", "hex": "#7986CB"},
    "2": {"name": "セージ", "hex": "#33B679"},
    "3": {"name": "ブドウ", "hex": "#8E24AA"},
    "4": {"name": "フラミンゴ", "hex": "#E67C73"},
    "5": {"name": "バナナ", "hex": "#F6BF26"},
    "6": {"name": "ミカン", "hex": "#F4511E"},
    "7": {"name": "ピーコック", "hex": "#039BE5"},
    "8": {"name": "グラファイト", "hex": "#616161"},
    "9": {"name": "ブルーベリー", "hex": "#3F51B5"},
    "10": {"name": "バジル", "hex": "#0B8043"},
    "11": {"name": "トマト", "hex": "#D50000"},
}

# colorId → 絵文字マッピング（SelectMenuやパレット表示用）
COLOR_EMOJI = {
    "1": "🪻", "2": "🌿", "3": "🍇", "4": "🌸",
    "5": "🍌", "6": "🍊", "7": "🦚", "8": "✏️",
    "9": "🫐", "10": "🌿", "11": "🍅",
}


def _create_color_palette_embeds() -> list:
    """Google Calendar色パレットのEmbed一覧を作成（各色のカラーバーで実際の色を表示）
    グラファイト（凡例専用）は除外。"""
    embeds = []
    for cid, info in USER_SELECTABLE_COLORS.items():
        hex_int = int(info['hex'].lstrip('#'), 16)
        emoji = COLOR_EMOJI.get(cid, "")
        embed = discord.Embed(
            description=f"{emoji} **{cid}** {info['name']}",
            color=discord.Color(hex_int),
        )
        embeds.append(embed)
    return embeds


# ユーザーが選択可能な色（グラファイト=凡例専用を除外）
USER_SELECTABLE_COLORS = {
    cid: info for cid, info in GOOGLE_CALENDAR_COLORS.items() if cid != "8"
}
LEGEND_COLOR_ID = "8"  # グラファイト = 凡例イベント専用

CANCEL_KEYWORDS = {"キャンセル", "やめる", "やめ", "中止", "取り消し", "cancel", "quit", "exit"}


class CalendarBot(commands.Bot):
    def __init__(
        self,
        nlp_processor: NLPProcessor,
        db_manager: FirestoreManager,
        oauth_handler: Optional[OAuthHandler] = None,
    ):
        intents = discord.Intents.default()
        intents.message_content = True

        super().__init__(
            command_prefix='!',
            intents=intents
        )

        self.nlp_processor = nlp_processor
        self.db_manager = db_manager
        self.oauth_handler = oauth_handler
        self.conversation_manager = ConversationManager()

    def get_calendar_manager_for_user(self, guild_id: Optional[int], user_id: str) -> Optional[GoogleCalendarManager]:
        """ユーザーのOAuthトークンでカレンダーマネージャを取得"""
        if guild_id is None:
            return None

        guild_id_str = str(guild_id)
        oauth_tokens = self.db_manager.get_oauth_tokens(guild_id_str, user_id)
        if not oauth_tokens or not self.oauth_handler:
            return None

        try:
            def on_token_refresh(new_access_token: str, new_expiry: str):
                self.db_manager.update_oauth_access_token(guild_id_str, user_id, new_access_token, new_expiry)

            return GoogleCalendarManager(
                access_token=oauth_tokens['access_token'],
                refresh_token=oauth_tokens['refresh_token'],
                token_expiry=oauth_tokens.get('token_expiry'),
                client_id=self.oauth_handler.client_id,
                client_secret=self.oauth_handler.client_secret,
                calendar_id=oauth_tokens.get('calendar_id', 'primary'),
                on_token_refresh=on_token_refresh,
            )
        except Exception as e:
            print(f"OAuth token error for guild {guild_id_str}, user {user_id}: {e}")
            return None

    def _get_server_context(self, guild_id: str) -> Dict[str, Any]:
        """サーバーのタグ・色・既存予定名・カレンダーの情報を取得する"""
        tag_groups = self.db_manager.list_tag_groups(guild_id)
        tags = self.db_manager.list_tags(guild_id)
        color_presets_by_calendar = self.db_manager.list_all_color_presets_by_calendar(guild_id)
        active_events = self.db_manager.get_all_active_events(guild_id)
        event_names = [e['event_name'] for e in active_events]

        all_tokens = self.db_manager.get_all_oauth_tokens(guild_id)
        calendars = [{
            "display_name": t.get("display_name") or f"<@{t.get('authenticated_by', '?')}>",
            "description": t.get("description", ""),
            "is_default": t.get("is_default", False),
        } for t in all_tokens]

        return {
            "tag_groups": tag_groups,
            "tags": tags,
            "color_presets_by_calendar": color_presets_by_calendar,
            "event_names": event_names,
            "calendars": calendars,
        }

    async def setup_hook(self):
        """起動時の初期化処理"""
        await self.tree.sync()
        print(f'{self.user} is ready!')

    async def on_ready(self):
        """Bot起動完了時"""
        print(f'Logged in as {self.user}')
        if not self.cleanup_sessions.is_running():
            self.cleanup_sessions.start()

        # 既存サーバーの色プリセットマイグレーション（guild単位→カレンダー単位）
        for guild in self.guilds:
            guild_id = str(guild.id)
            try:
                self.db_manager.migrate_guild_color_presets_to_calendars(guild_id)
            except Exception as e:
                print(f"Migration error for guild {guild_id}: {e}")

        # 定期通知タスクループ開始
        if not self.check_scheduled_notifications.is_running():
            self.check_scheduled_notifications.start()

        # Google Calendarイベント整合性チェック開始
        if not self.sync_calendar_events.is_running():
            self.sync_calendar_events.start()

    @tasks.loop(minutes=1)
    async def cleanup_sessions(self):
        """期限切れの会話セッションを定期的にクリーンアップ"""
        expired_thread_ids = self.conversation_manager.cleanup_expired()
        for thread_id in expired_thread_ids:
            try:
                thread = await self.fetch_channel(thread_id)
                if thread and isinstance(thread, discord.Thread):
                    await thread.send("⏰ タイムアウトしました。セッションを終了します。新しく `/予定` コマンドを実行してください。")
                    await thread.edit(archived=True)
            except Exception as e:
                print(f"Failed to archive expired thread {thread_id}: {e}")

    @tasks.loop(minutes=1)
    async def check_scheduled_notifications(self):
        """サーバーごとの定期通知をチェック・送信"""
        from datetime import timezone, timedelta as td
        import traceback

        jst = timezone(td(hours=9))
        now_jst = datetime.now(jst)
        current_weekday = now_jst.weekday()
        current_hour = now_jst.hour
        current_minute = now_jst.minute
        today_str = now_jst.strftime("%Y-%m-%d")

        try:
            all_settings = self.db_manager.get_all_notification_settings()
        except Exception as e:
            print(f"Error fetching notification settings: {e}")
            return

        for settings in all_settings:
            try:
                if (settings.get("weekday") != current_weekday or
                        settings.get("hour") != current_hour or
                        settings.get("minute") != current_minute):
                    continue

                # 重複送信防止
                last_sent = settings.get("last_sent_at", "")
                if last_sent.startswith(today_str):
                    continue

                guild_id = settings.get("guild_id")
                if not guild_id:
                    continue

                await self._send_scheduled_notification(guild_id, settings)
            except Exception as e:
                print(f"Error processing notification for guild {settings.get('guild_id')}: {e}")
                traceback.print_exc()

    @check_scheduled_notifications.before_loop
    async def before_check_scheduled_notifications(self):
        await self.wait_until_ready()

    @tasks.loop(minutes=30)
    async def sync_calendar_events(self):
        """30分ごとにGoogle Calendarイベントの整合性をチェックし、不正な変更を復元する"""
        import traceback

        for guild in self.guilds:
            guild_id = str(guild.id)
            try:
                all_tokens = self.db_manager.get_all_oauth_tokens(guild_id)
                if not all_tokens:
                    continue

                active_events = self.db_manager.get_all_active_events(guild_id)

                # イベントを calendar_owner でグループ化
                events_by_owner: Dict[str, List[Dict[str, Any]]] = {}
                for event in active_events:
                    owner = event.get('calendar_owner', '')
                    if owner:
                        events_by_owner.setdefault(owner, []).append(event)

                for cal_owner, events in events_by_owner.items():
                    try:
                        cal_mgr = self.get_calendar_manager_for_user(int(guild_id), cal_owner)
                        if not cal_mgr:
                            continue

                        for event in events:
                            try:
                                await self._sync_single_event(guild_id, event, cal_mgr, cal_owner)
                            except Exception as e:
                                print(f"[sync] Error syncing event {event.get('id')} in guild {guild_id}: {e}")

                    except Exception as e:
                        print(f"[sync] Error processing calendar owner {cal_owner} in guild {guild_id}: {e}")
                        traceback.print_exc()

                # 凡例イベントも同期
                try:
                    await _update_legend_event_by_guild(self, guild_id)
                except Exception as e:
                    print(f"[sync] Error syncing legend events for guild {guild_id}: {e}")

            except Exception as e:
                print(f"[sync] Error processing guild {guild_id}: {e}")
                traceback.print_exc()

    async def _sync_single_event(
        self, guild_id: str, event: Dict[str, Any],
        cal_mgr: 'GoogleCalendarManager', cal_owner: str
    ):
        """単一イベントのGoogle Calendar整合性チェック・復元"""
        google_cal_events_json = event.get('google_calendar_events')
        if not google_cal_events_json:
            # 不定期イベント等、Google Calendarイベントなし → スキップ
            return

        google_cal_data = json.loads(google_cal_events_json)
        if not google_cal_data:
            return

        for ge in google_cal_data:
            google_event_id = ge.get('event_id')
            if not google_event_id:
                continue

            gcal_event = cal_mgr.get_event(google_event_id)

            if gcal_event is None:
                # イベントが削除されている → 再作成
                print(f"[sync] Event {event['id']} ({event['event_name']}) deleted from Google Calendar, recreating...")
                new_event_id = _recreate_calendar_event(self, guild_id, event, cal_mgr, cal_owner)
                if new_event_id:
                    print(f"[sync] Recreated event {event['id']} as {new_event_id}")
                return  # 再作成したので残りのgoogle_event_idのチェックは不要

            # イベントが存在する → summary/description/colorId を比較
            expected = _rebuild_expected_event(self, guild_id, event, cal_owner)

            needs_update = False
            update_fields = {}

            if gcal_event.get('summary', '') != expected.get('summary', ''):
                update_fields['summary'] = expected['summary']
                needs_update = True

            if gcal_event.get('description', '') != expected.get('description', ''):
                update_fields['description'] = expected['description']
                needs_update = True

            expected_color = expected.get('colorId')
            actual_color = gcal_event.get('colorId')
            if expected_color and expected_color != actual_color:
                update_fields['colorId'] = expected_color
                needs_update = True

            if needs_update:
                print(f"[sync] Event {event['id']} ({event['event_name']}) modified on Google Calendar, restoring: {list(update_fields.keys())}")
                try:
                    cal_mgr.update_event(google_event_id, update_fields)
                except Exception as e:
                    print(f"[sync] Failed to restore event {google_event_id}: {e}")

    @sync_calendar_events.before_loop
    async def before_sync_calendar_events(self):
        await self.wait_until_ready()

    async def _send_scheduled_notification(self, guild_id: str, settings: dict):
        """スケジュール通知を送信"""
        channel_id = settings.get("channel_id")
        if not channel_id:
            return

        try:
            channel = await self.fetch_channel(int(channel_id))
        except Exception:
            print(f"Cannot fetch channel {channel_id} for guild {guild_id}")
            return

        events = self.db_manager.get_this_week_events(guild_id)

        # calendar_owners フィルタ
        calendar_owners = settings.get("calendar_owners", [])
        if calendar_owners:
            events = [e for e in events if e.get("calendar_owner") in calendar_owners]

        embed = create_weekly_embed(events)
        try:
            await channel.send(content="🔔 **今週の予定通知**", embed=embed)
            # 最終送信時刻を更新
            from datetime import timezone, timedelta as td
            jst = timezone(td(hours=9))
            now_str = datetime.now(jst).isoformat()
            self.db_manager.update_notification_last_sent(guild_id, now_str)
        except Exception as e:
            print(f"Failed to send scheduled notification to {channel_id}: {e}")


# コマンド定義

def setup_commands(bot: CalendarBot):
    @bot.tree.command(name="予定", description="予定を自然言語で管理します")
    @app_commands.describe(
        メッセージ="予定の追加・編集・削除・検索を自然言語で指定してください"
    )
    async def schedule_command(
        interaction: discord.Interaction,
        メッセージ: str
    ):
        """メインの予定管理コマンド"""
        await interaction.response.defer(thinking=True)

        try:
            guild_id = str(interaction.guild_id) if interaction.guild_id else ""

            server_context = bot._get_server_context(guild_id)

            # マルチターン会話セッションでメッセージを送信
            chat_session = bot.nlp_processor.create_chat_session(server_context)
            result = bot.nlp_processor.send_message(chat_session, メッセージ)

            status = result.get("status", "complete")
            action = result.get("action")

            if status == "needs_info":
                # スレッドを作成して対話モードに入る
                thread_name = f"予定管理: {メッセージ[:20]}"
                # チャンネルに直接スレッドを作成
                thread = await interaction.channel.create_thread(
                    name=thread_name,
                    type=discord.ChannelType.public_thread,
                )
                await interaction.followup.send(
                    f"💬 情報が不足しているため、対話モードで情報を収集します。\nスレッド {thread.mention} をご確認ください。"
                )

                # セッションを登録
                session = bot.conversation_manager.create_session(
                    guild_id=guild_id,
                    channel_id=interaction.channel_id,
                    thread_id=thread.id,
                    user_id=interaction.user.id,
                    chat_session=chat_session,
                    action=action,
                    server_context=server_context,
                )
                if result.get("event_data"):
                    session.partial_data = result["event_data"]

                # 質問をスレッドに投稿
                question = result.get("question", "追加の情報を教えてください。")
                await thread.send(f"{interaction.user.mention}\n{question}\n\n💡 「キャンセル」と入力するとセッションを終了できます。")

            elif status == "complete":
                # event_dataがある場合はそこからパースデータを構築
                event_data = result.get("event_data", {})
                if event_data and action in ("add", "edit", "delete"):
                    parsed = _event_data_to_parsed(event_data, action)
                    # 色自動割当（addまたはeditでcolor_name未指定の場合）
                    if action in ("add", "edit") and not parsed.get("color_name"):
                        # デフォルトカレンダーのオーナーを使用
                        token_info = _resolve_calendar_owner(bot, guild_id, parsed.get('calendar_name'))
                        default_owner = (token_info.get('_doc_id') or token_info.get('authenticated_by')) if token_info else None
                        if default_owner:
                            auto_color = _auto_assign_color(
                                bot.db_manager, guild_id, default_owner,
                                parsed.get("recurrence"), parsed.get("nth_weeks"),
                            )
                            if auto_color:
                                parsed["color_name"] = auto_color["name"]
                                parsed["_auto_color"] = True
                elif action == "search":
                    parsed = {
                        "action": "search",
                        "search_query": result.get("search_query", {}),
                    }
                else:
                    # フォールバック: 旧方式でパース
                    parsed = bot.nlp_processor.parse_user_message(メッセージ)

                # アクションに応じた処理
                response = await _dispatch_action(bot, interaction, parsed)
                if response:
                    await interaction.followup.send(response)
            else:
                # status不明の場合はフォールバック
                parsed = bot.nlp_processor.parse_user_message(メッセージ)
                response = await _dispatch_action(bot, interaction, parsed)
                if response:
                    await interaction.followup.send(response)

        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "Resource exhausted" in error_msg.lower():
                await interaction.followup.send(
                    "⚠️ APIの利用制限に達しました。1分ほど待ってから再度お試しください。",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    f"エラーが発生しました: {error_msg}",
                    ephemeral=True
                )

    @bot.event
    async def on_message(message: discord.Message):
        """スレッド内のメッセージを処理"""
        # Bot自身のメッセージは無視
        if message.author.bot:
            return

        # スレッド内のメッセージかチェック
        if not isinstance(message.channel, discord.Thread):
            return

        thread = message.channel
        session = bot.conversation_manager.get_session(thread.id)

        if not session:
            return

        # セッションオーナーのメッセージのみ処理
        if message.author.id != session.user_id:
            return

        session.touch()

        # キャンセルチェック
        if message.content.strip() in CANCEL_KEYWORDS:
            bot.conversation_manager.remove_session(thread.id)
            await thread.send("❌ セッションをキャンセルしました。")
            await thread.edit(archived=True)
            return

        try:
            async with thread.typing():
                result = bot.nlp_processor.send_message(session.chat_session, message.content)

            status = result.get("status", "needs_info")
            action = result.get("action", session.action)
            session.action = action

            if result.get("event_data"):
                session.partial_data.update(
                    {k: v for k, v in result["event_data"].items() if v is not None}
                )

            if status == "complete":
                # 情報収集完了 → 確認フロー
                if action in ("add", "edit", "delete"):
                    parsed = _event_data_to_parsed(session.partial_data, action)
                    # 色自動割当はカレンダー選択後に行うため、ここでは行わない
                elif action == "search":
                    parsed = {
                        "action": "search",
                        "search_query": result.get("search_query", {}),
                    }
                else:
                    await thread.send("アクションを認識できませんでした。")
                    return

                # スレッド内で確認フロー
                try:
                    response, should_end_session = await _dispatch_action_in_thread(bot, thread, message.author, parsed, session.guild_id)
                except Exception as e:
                    await thread.send(f"❌ エラーが発生しました: {e}")
                    response = None
                    should_end_session = True

                if response:
                    await thread.send(response)

                if should_end_session:
                    # セッション終了 → アーカイブ
                    bot.conversation_manager.remove_session(thread.id)
                    try:
                        await thread.edit(archived=True)
                    except Exception:
                        pass
                # else: 修正モード → セッション継続（何もしない、次のメッセージを待つ）

            elif status == "needs_info":
                # 次の質問を投稿
                question = result.get("question", "追加の情報を教えてください。")
                await thread.send(question)

        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "Resource exhausted" in error_msg.lower():
                await thread.send("⚠️ APIの利用制限に達しました。1分ほど待ってから再度お試しください。")
            else:
                await thread.send(f"エラーが発生しました: {error_msg}\nもう一度入力してください。")

    @bot.tree.command(name="今週の予定", description="今週の予定一覧を表示します")
    async def this_week_command(interaction: discord.Interaction):
        """今週の予定表示"""
        await interaction.response.defer()

        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        events = bot.db_manager.get_this_week_events(guild_id)
        embed = create_weekly_embed(events)

        await interaction.followup.send(embed=embed)

    @bot.tree.command(name="予定一覧", description="登録されている繰り返し予定の一覧を表示")
    async def list_command(interaction: discord.Interaction):
        """繰り返し予定マスター一覧"""
        await interaction.response.defer()

        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        events = bot.db_manager.get_all_active_events(guild_id)
        embed = create_event_list_embed(events)

        await interaction.followup.send(embed=embed)

    @bot.tree.command(name="ヘルプ", description="Botの使い方とコマンド説明を表示します")
    async def help_command(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        embed = create_help_embed()
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---- 色管理グループ ----
    color_group = app_commands.Group(name="色", description="色プリセットの管理")

    @color_group.command(name="初期設定", description="繰り返しタイプごとのデフォルト色を設定します")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def color_setup_command(interaction: discord.Interaction):
        """色セットアップウィザード"""
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        user_id = str(interaction.user.id)

        # 実行ユーザーのoauth_tokenが存在するか確認
        oauth_tokens = bot.db_manager.get_oauth_tokens(guild_id, user_id)
        if not oauth_tokens:
            await interaction.followup.send(
                "❌ あなたのカレンダーが認証されていません。先に `/カレンダー 認証` を実行してください。",
                ephemeral=True,
            )
            return

        # Google Calendar色パレットをEmbed一覧で表示（グラファイト除外、10色）
        palette_embeds = _create_color_palette_embeds()

        # 色パレット表示（10色なので1メッセージに収まる）
        await interaction.followup.send(
            content="🎨 **Google Calendar 色パレット**",
            embeds=palette_embeds,
            ephemeral=True,
        )

        # ウィザード本体
        wizard_embed = discord.Embed(
            title="🎨 色初期設定ウィザード",
            description=(
                "繰り返しタイプごとにGoogleカレンダーの色を設定します。\n"
                "上の色パレットを参考に、各カテゴリに対して色を選択してください。"
            ),
            color=discord.Color.blue(),
        )
        view = ColorSetupView(interaction.user.id, guild_id, bot, target_user_id=user_id)
        await interaction.followup.send(
            embeds=[wizard_embed],
            view=view,
            ephemeral=True,
        )

    @color_group.command(name="一覧", description="色プリセットの一覧を表示します")
    async def color_list_command(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        user_id = str(interaction.user.id)

        # 実行ユーザーのoauth_token確認
        oauth_tokens = bot.db_manager.get_oauth_tokens(guild_id, user_id)
        if not oauth_tokens:
            await interaction.followup.send(
                "❌ あなたのカレンダーが認証されていません。先に `/カレンダー 認証` を実行してください。",
                ephemeral=True,
            )
            return

        presets = bot.db_manager.list_color_presets(guild_id, user_id)

        if not presets:
            embed = discord.Embed(
                title="🎨 色プリセット",
                description="色プリセットが登録されていません。\n`/色 初期設定` で繰り返しタイプごとのデフォルト色を設定してください。",
                color=discord.Color.blue(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        cat_labels = {c["key"]: c["label"] for c in COLOR_CATEGORIES}
        embeds = []
        for p in presets:
            color_info = GOOGLE_CALENDAR_COLORS.get(p['color_id'], {})
            hex_int = int(color_info.get('hex', '#808080').lstrip('#'), 16)
            emoji = COLOR_EMOJI.get(p['color_id'], "")
            rt = p.get('recurrence_type')
            rt_label = f" [→ {cat_labels.get(rt, rt)}]" if rt else ""

            embed = discord.Embed(
                description=f"{emoji} **{p['name']}** (colorId {p['color_id']}: {color_info.get('name', '?')}){rt_label}",
                color=discord.Color(hex_int),
            )
            embeds.append(embed)

        # 10 embed/message の制限を考慮して分割送信
        for i in range(0, len(embeds), 10):
            chunk = embeds[i:i+10]
            if i == 0:
                await interaction.followup.send(
                    content="🎨 **登録済み色プリセット**",
                    embeds=chunk,
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(embeds=chunk, ephemeral=True)

    @color_group.command(name="追加", description="色プリセットを追加/更新します")
    @app_commands.describe(名前="色名", 説明="色の説明")
    async def color_add_command(interaction: discord.Interaction, 名前: str, 説明: str = ""):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        user_id = str(interaction.user.id)

        # 実行ユーザーのoauth_token確認
        oauth_tokens = bot.db_manager.get_oauth_tokens(guild_id, user_id)
        if not oauth_tokens:
            await interaction.followup.send(
                "❌ あなたのカレンダーが認証されていません。先に `/カレンダー 認証` を実行してください。",
                ephemeral=True,
            )
            return

        # 色パレット表示 + SelectMenu で色を選択
        palette_embeds = _create_color_palette_embeds()
        view = ColorSelectForEventView(author_id=interaction.user.id)
        await interaction.followup.send(
            content="🎨 **色を選択してください**",
            embeds=palette_embeds,
            view=view,
            ephemeral=True,
        )

        timed_out = await view.wait()
        if timed_out or view.selected_color_id is None:
            await interaction.followup.send("⏰ タイムアウトしました。もう一度やり直してください。", ephemeral=True)
            return

        color_id = view.selected_color_id

        # 変更前のプリセットを取得（colorId変更検出用）
        old_preset = bot.db_manager.get_color_preset(guild_id, user_id, 名前)
        bot.db_manager.add_color_preset(guild_id, user_id, 名前, color_id, 説明)
        await _update_legend_event_for_user(bot, guild_id, user_id)

        # colorId が変更された場合、該当色の全予定を更新
        msg = f"✅ 色プリセット「{名前}」を設定しました。"
        if old_preset and old_preset.get('color_id') != color_id:
            affected = bot.db_manager.get_events_by_color_name(guild_id, 名前)
            # calendar_ownerがこのユーザーの予定のみ対象
            affected = [e for e in affected if (e.get('calendar_owner') or e.get('created_by', '')) == user_id]
            if affected:
                cnt = await _batch_update_google_calendar_events(
                    bot, guild_id, affected, {'colorId': color_id}
                )
                msg += f"\n📝 既存予定 {cnt} 件のカレンダー色を更新しました。"
        await interaction.followup.send(msg, ephemeral=True)

    @color_group.command(name="削除", description="色プリセットを削除します")
    @app_commands.describe(名前="色名")
    async def color_delete_command(interaction: discord.Interaction, 名前: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        user_id = str(interaction.user.id)

        # 実行ユーザーのoauth_token確認
        oauth_tokens = bot.db_manager.get_oauth_tokens(guild_id, user_id)
        if not oauth_tokens:
            await interaction.followup.send(
                "❌ あなたのカレンダーが認証されていません。先に `/カレンダー 認証` を実行してください。",
                ephemeral=True,
            )
            return

        bot.db_manager.delete_color_preset(guild_id, user_id, 名前)
        await _update_legend_event_for_user(bot, guild_id, user_id)
        await interaction.followup.send(f"✅ 色プリセット「{名前}」を削除しました。", ephemeral=True)

    bot.tree.add_command(color_group)

    # ---- タグ管理グループ ----
    tag_group = app_commands.Group(name="タグ", description="タグの管理")

    async def tag_group_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[int]]:
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        groups = bot.db_manager.list_tag_groups(guild_id)
        choices = []
        for g in groups:
            name = g["name"]
            if current.lower() in name.lower() or current == "":
                choices.append(app_commands.Choice(name=name, value=g["id"]))
        return choices[:25]

    @tag_group.command(name="一覧", description="タググループとタグを表示します")
    async def tag_group_list_command(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        groups = bot.db_manager.list_tag_groups(guild_id)
        tags = bot.db_manager.list_tags(guild_id)
        embed = create_tag_group_list_embed(groups, tags)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @tag_group.command(name="グループ追加", description="タググループを追加します（最大3つ）")
    @app_commands.describe(名前="グループ名", 説明="グループの説明")
    async def tag_group_add_command(interaction: discord.Interaction, 名前: str, 説明: str = ""):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        bot.db_manager.add_tag_group(guild_id, 名前, 説明)
        await update_legend_event(bot, interaction)
        await interaction.followup.send(f"✅ タググループ「{名前}」を追加しました。", ephemeral=True)

    @tag_group.command(name="グループ名変更", description="タググループの名前を変更します")
    @app_commands.describe(id="タググループ", 新しい名前="新しいグループ名")
    @app_commands.autocomplete(id=tag_group_autocomplete)
    async def tag_group_rename_command(interaction: discord.Interaction, id: int, 新しい名前: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""

        # グループ存在確認
        group = bot.db_manager.get_tag_group(guild_id, id)
        if not group:
            await interaction.followup.send(f"❌ タググループID {id} は存在しません。", ephemeral=True)
            return

        old_name = group['name']

        # グループ名更新
        bot.db_manager.update_tag_group(guild_id, id, name=新しい名前)
        # 子タグの group_name 更新
        bot.db_manager.update_tags_group_name(guild_id, id, 新しい名前)
        # 凡例イベント更新
        await update_legend_event(bot, interaction)

        # このグループのタグを含む予定の Google Calendar 説明欄を再構築
        tag_groups = bot.db_manager.list_tag_groups(guild_id)
        tags_list = bot.db_manager.list_tags(guild_id)
        tags_in_group = [t['name'] for t in tags_list if t.get('group_id') == id]

        updated_count = 0
        if tags_in_group:
            all_events = bot.db_manager.get_all_active_events(guild_id)
            for event in all_events:
                event_tags = json.loads(event.get('tags') or '[]')
                if not any(t in tags_in_group for t in event_tags):
                    continue
                if not event.get('google_calendar_events'):
                    continue
                cal_owner = event.get('calendar_owner') or event.get('created_by', '')
                cal_mgr = bot.get_calendar_manager_for_user(int(guild_id), cal_owner) if cal_owner else None
                if not cal_mgr:
                    continue
                new_desc = _build_event_description(
                    raw_description=event.get('description', ''),
                    tags=event_tags if event_tags else None,
                    tag_groups=[{'name': g['name'], 'tags': [t for t in tags_list if t.get('group_id') == g['id']]} for g in tag_groups],
                    x_url=event.get('x_url'),
                    vrc_group_url=event.get('vrc_group_url'),
                    official_url=event.get('official_url'),
                )
                google_cal_data = json.loads(event['google_calendar_events'])
                ids = [ge['event_id'] for ge in google_cal_data]
                try:
                    cal_mgr.update_events(ids, {'description': new_desc})
                except Exception:
                    pass
                updated_count += 1

        msg = f"✅ タググループ「{old_name}」を「{新しい名前}」に変更しました。"
        if updated_count:
            msg += f"\n📝 {updated_count} 件の予定の説明欄を更新しました。"
        await interaction.followup.send(msg, ephemeral=True)

    @tag_group.command(name="グループ削除", description="タググループを削除します")
    @app_commands.describe(id="タググループ")
    @app_commands.autocomplete(id=tag_group_autocomplete)
    async def tag_group_delete_command(interaction: discord.Interaction, id: int):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""

        # 削除前にグループ内のタグ名一覧を取得
        tags_in_group = [
            t['name'] for t in bot.db_manager.list_tags(guild_id)
            if t.get('group_id') == id
        ]

        bot.db_manager.delete_tag_group(guild_id, id)
        await update_legend_event(bot, interaction)

        # 影響する予定から全タグを除去
        if tags_in_group:
            all_events = bot.db_manager.get_all_active_events(guild_id)
            tag_groups = bot.db_manager.list_tag_groups(guild_id)
            tags_list = bot.db_manager.list_tags(guild_id)
            updated_count = 0
            for event in all_events:
                old_tags = json.loads(event.get('tags') or '[]')
                new_tags = [t for t in old_tags if t not in tags_in_group]
                if old_tags != new_tags:
                    bot.db_manager.update_event(event['id'], {'tags': new_tags})
                    # Google Calendar 説明欄を再構築
                    if event.get('google_calendar_events'):
                        cal_owner = event.get('calendar_owner') or event.get('created_by', '')
                        cal_mgr = bot.get_calendar_manager_for_user(int(guild_id), cal_owner) if cal_owner else None
                        if cal_mgr:
                            new_desc = _build_event_description(
                                raw_description=event.get('description', ''),
                                tags=new_tags if new_tags else None,
                                tag_groups=[{'name': g['name'], 'tags': [t for t in tags_list if t.get('group_id') == g['id']]} for g in tag_groups],
                                x_url=event.get('x_url'),
                                vrc_group_url=event.get('vrc_group_url'),
                                official_url=event.get('official_url'),
                            )
                            google_cal_data = json.loads(event['google_calendar_events'])
                            ids = [ge['event_id'] for ge in google_cal_data]
                            try:
                                cal_mgr.update_events(ids, {
                                    'description': new_desc,
                                    'extendedProperties': {'private': {'tags': json.dumps(new_tags, ensure_ascii=False)}},
                                })
                            except Exception:
                                pass
                    updated_count += 1
            msg = f"✅ タググループID {id} を削除しました。"
            if updated_count:
                msg += f"\n📝 {updated_count} 件の予定からタグを除去しました。"
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.followup.send(f"✅ タググループID {id} を削除しました。", ephemeral=True)

    @tag_group.command(name="追加", description="タグを追加/更新します")
    @app_commands.describe(group_id="タググループ", 名前="タグ名", 説明="タグの説明")
    @app_commands.autocomplete(group_id=tag_group_autocomplete)
    async def tag_add_command(interaction: discord.Interaction, group_id: int, 名前: str, 説明: str = ""):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        bot.db_manager.add_tag(guild_id, group_id, 名前, 説明)
        await update_legend_event(bot, interaction)
        await interaction.followup.send(f"✅ タグ「{名前}」を追加しました。", ephemeral=True)

    @tag_group.command(name="削除", description="タグを削除します")
    @app_commands.describe(group_id="タググループ", 名前="タグ名")
    @app_commands.autocomplete(group_id=tag_group_autocomplete)
    async def tag_delete_command(interaction: discord.Interaction, group_id: int, 名前: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""

        # 削除前に影響する予定を取得
        affected = bot.db_manager.get_events_by_tag(guild_id, 名前)

        bot.db_manager.delete_tag(guild_id, group_id, 名前)
        await update_legend_event(bot, interaction)

        # 影響する予定からタグを除去
        tag_groups = bot.db_manager.list_tag_groups(guild_id)
        tags_list = bot.db_manager.list_tags(guild_id)
        updated_count = 0
        for event in affected:
            old_tags = json.loads(event.get('tags') or '[]')
            new_tags = [t for t in old_tags if t != 名前]
            bot.db_manager.update_event(event['id'], {'tags': new_tags})

            # Google Calendar 説明欄を再構築
            if event.get('google_calendar_events'):
                cal_owner = event.get('calendar_owner') or event.get('created_by', '')
                cal_mgr = bot.get_calendar_manager_for_user(int(guild_id), cal_owner) if cal_owner else None
                if cal_mgr:
                    new_desc = _build_event_description(
                        raw_description=event.get('description', ''),
                        tags=new_tags if new_tags else None,
                        tag_groups=[{'name': g['name'], 'tags': [t for t in tags_list if t.get('group_id') == g['id']]} for g in tag_groups],
                        x_url=event.get('x_url'),
                        vrc_group_url=event.get('vrc_group_url'),
                        official_url=event.get('official_url'),
                    )
                    google_cal_data = json.loads(event['google_calendar_events'])
                    ids = [ge['event_id'] for ge in google_cal_data]
                    try:
                        cal_mgr.update_events(ids, {
                            'description': new_desc,
                            'extendedProperties': {'private': {'tags': json.dumps(new_tags, ensure_ascii=False)}},
                        })
                    except Exception:
                        pass
            updated_count += 1

        msg = f"✅ タグ「{名前}」を削除しました。"
        if updated_count:
            msg += f"\n📝 {updated_count} 件の予定からタグを除去しました。"
        await interaction.followup.send(msg, ephemeral=True)

    bot.tree.add_command(tag_group)

    # ---- カレンダー管理グループ ----
    calendar_group = app_commands.Group(
        name="カレンダー", description="カレンダーの管理",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @calendar_group.command(name="認証", description="Google OAuth認証でカレンダーを連携します")
    async def calendar_oauth_command(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not bot.oauth_handler:
            await interaction.followup.send("❌ OAuth が設定されていません。管理者に連絡してください。", ephemeral=True)
            return

        state = secrets.token_urlsafe(32)
        guild_id = str(interaction.guild_id)
        user_id = str(interaction.user.id)

        bot.db_manager.save_oauth_state(state, guild_id, user_id)
        auth_url = bot.oauth_handler.generate_auth_url(state)

        embed = discord.Embed(
            title="Google カレンダー認証",
            description=(
                "**認証の前に**: 認証に使用するGoogleアカウントのメールアドレスをBot管理者に伝えてください。"
                "OAuth同意画面のテストユーザーに登録されていないと認証できません。\n\n"
                "以下のリンクをクリックして Google アカウントでカレンダーへのアクセスを許可してください。\n\n"
                f"[認証ページを開く]({auth_url})\n\n"
                "認証が完了するとブラウザに「認証成功」と表示されます。"
            ),
            color=discord.Color.blue(),
        )
        embed.set_footer(text="このリンクは一度だけ使用できます")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @calendar_group.command(name="認証解除", description="自分のGoogle OAuth認証を解除します")
    async def calendar_oauth_revoke_command(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild_id)
        user_id = str(interaction.user.id)
        tokens = bot.db_manager.get_oauth_tokens(guild_id, user_id)
        if not tokens:
            await interaction.followup.send("ℹ️ あなたの OAuth 認証は設定されていません。", ephemeral=True)
            return

        bot.db_manager.delete_oauth_tokens(guild_id, user_id)
        await interaction.followup.send("✅ あなたの Google OAuth 認証を解除しました。", ephemeral=True)

    @calendar_group.command(name="認証状態", description="自分のカレンダー認証状態を表示します")
    async def calendar_oauth_status_command(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild_id)
        user_id = str(interaction.user.id)
        oauth_tokens = bot.db_manager.get_oauth_tokens(guild_id, user_id)

        embed = discord.Embed(title="カレンダー認証状態", color=discord.Color.blue())

        if oauth_tokens:
            authenticated_at = oauth_tokens.get('authenticated_at', '不明')
            calendar_id = oauth_tokens.get('calendar_id', 'primary')
            display_name = oauth_tokens.get('display_name', '未設定')
            is_default = "⭐ はい" if oauth_tokens.get('is_default') else "いいえ"
            embed.add_field(name="方式", value="OAuth 2.0（ユーザー認証）", inline=False)
            embed.add_field(name="表示名", value=display_name or "未設定", inline=True)
            embed.add_field(name="認証日時", value=authenticated_at, inline=True)
            embed.add_field(name="カレンダーID", value=calendar_id, inline=False)
            embed.add_field(name="デフォルト", value=is_default, inline=True)
            if oauth_tokens.get('description'):
                embed.add_field(name="説明", value=oauth_tokens['description'], inline=True)
        else:
            embed.add_field(name="状態", value="未認証", inline=False)
            embed.add_field(name="説明", value="`/カレンダー 認証` を実行して OAuth 認証を行ってください。", inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    @calendar_group.command(name="設定", description="自分のカレンダー設定を変更します")
    @app_commands.describe(
        表示名="カレンダーの表示名",
        カレンダーid="GoogleカレンダーID",
        説明="カレンダーの用途説明",
        デフォルト="このカレンダーをデフォルトにする"
    )
    async def calendar_set_command(interaction: discord.Interaction,
                                   表示名: Optional[str] = None,
                                   カレンダーid: Optional[str] = None,
                                   説明: Optional[str] = None,
                                   デフォルト: Optional[bool] = None):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild_id)
        user_id = str(interaction.user.id)
        oauth_tokens = bot.db_manager.get_oauth_tokens(guild_id, user_id)
        if not oauth_tokens:
            await interaction.followup.send("❌ OAuth 認証がされていません。先に `/カレンダー 認証` を実行してください。", ephemeral=True)
            return

        if 表示名 is None and カレンダーid is None and 説明 is None and デフォルト is None:
            await interaction.followup.send("❌ 変更する項目を少なくとも1つ指定してください。", ephemeral=True)
            return

        bot.db_manager.update_oauth_settings(
            guild_id, user_id,
            display_name=表示名, calendar_id=カレンダーid,
            description=説明, is_default=デフォルト
        )

        changes = []
        if 表示名 is not None:
            changes.append(f"表示名: `{表示名}`")
        if カレンダーid is not None:
            changes.append(f"カレンダーID: `{カレンダーid}`")
        if 説明 is not None:
            changes.append(f"説明: `{説明}`")
        if デフォルト is not None:
            changes.append(f"デフォルト: {'はい' if デフォルト else 'いいえ'}")

        await interaction.followup.send(
            f"✅ カレンダー設定を更新しました。\n" + "\n".join(f"• {c}" for c in changes),
            ephemeral=True
        )

    @calendar_group.command(name="一覧", description="サーバー内の認証済みカレンダー一覧を表示します")
    async def calendar_list_command(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild_id)
        all_tokens = bot.db_manager.get_all_oauth_tokens(guild_id)

        if not all_tokens:
            embed = discord.Embed(
                title="📅 認証済みカレンダー一覧",
                description="認証済みのカレンダーがありません。\n`/カレンダー 認証` でカレンダーを連携してください。",
                color=discord.Color.blue(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        embed = discord.Embed(
            title="📅 認証済みカレンダー一覧",
            color=discord.Color.blue(),
        )
        for token in all_tokens:
            user_id = token.get("_doc_id") or token.get("authenticated_by")
            display_name = token.get("display_name") or "未設定"
            is_default = "⭐ " if token.get("is_default") else ""
            desc = token.get("description", "")
            desc_line = f"\n説明: {desc}" if desc else ""
            embed.add_field(
                name=f"{is_default}{display_name}",
                value=f"認証者: <@{user_id}>{desc_line}",
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    bot.tree.add_command(calendar_group)

    # ---- 通知管理グループ ----
    notification_group = app_commands.Group(
        name="通知", description="週次通知の管理",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    WEEKDAY_CHOICES = [
        app_commands.Choice(name="月曜日", value=0),
        app_commands.Choice(name="火曜日", value=1),
        app_commands.Choice(name="水曜日", value=2),
        app_commands.Choice(name="木曜日", value=3),
        app_commands.Choice(name="金曜日", value=4),
        app_commands.Choice(name="土曜日", value=5),
        app_commands.Choice(name="日曜日", value=6),
    ]

    @notification_group.command(name="設定", description="週次通知のスケジュールを設定します")
    @app_commands.describe(
        曜日="通知する曜日",
        時刻="通知する時刻（0-23、JST）",
        チャンネル="通知を送信するチャンネル",
        分="通知する分（0-59、デフォルト: 0）",
    )
    @app_commands.choices(曜日=WEEKDAY_CHOICES)
    async def notification_setup_command(
        interaction: discord.Interaction,
        曜日: app_commands.Choice[int],
        時刻: int,
        チャンネル: discord.TextChannel,
        分: int = 0,
    ):
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild_id:
            await interaction.followup.send("サーバー内で使用してください。", ephemeral=True)
            return

        if 時刻 < 0 or 時刻 > 23:
            await interaction.followup.send("時刻は0〜23の範囲で指定してください。", ephemeral=True)
            return

        if 分 < 0 or 分 > 59:
            await interaction.followup.send("分は0〜59の範囲で指定してください。", ephemeral=True)
            return

        guild_id = str(interaction.guild_id)
        user_id = str(interaction.user.id)

        # 複数カレンダーがあるかチェック
        all_tokens = bot.db_manager.get_all_oauth_tokens(guild_id)
        if len(all_tokens) > 1:
            # カレンダー選択UIを表示
            view = NotificationCalendarSelectView(
                bot, guild_id, user_id, all_tokens,
                曜日.value, 時刻, 分, str(チャンネル.id)
            )
            await interaction.followup.send(
                "通知対象のカレンダーを選択してください（複数選択可）:",
                view=view, ephemeral=True
            )
        else:
            # カレンダーが1つ以下 → 全カレンダーで設定
            bot.db_manager.save_notification_settings(
                guild_id=guild_id,
                enabled=True,
                weekday=曜日.value,
                hour=時刻,
                minute=分,
                channel_id=str(チャンネル.id),
                calendar_owners=[],
                configured_by=user_id,
            )
            weekday_names = ["月", "火", "水", "木", "金", "土", "日"]
            await interaction.followup.send(
                f"✅ 週次通知を設定しました！\n"
                f"📅 毎週{weekday_names[曜日.value]}曜日 {時刻:02d}:{分:02d}（JST）\n"
                f"📢 通知先: <#{チャンネル.id}>",
                ephemeral=True
            )

    @notification_group.command(name="停止", description="週次通知を停止します")
    async def notification_stop_command(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild_id:
            await interaction.followup.send("サーバー内で使用してください。", ephemeral=True)
            return

        guild_id = str(interaction.guild_id)
        bot.db_manager.disable_notification(guild_id)
        await interaction.followup.send("✅ 週次通知を停止しました。", ephemeral=True)

    @notification_group.command(name="状態", description="週次通知の設定状態を表示します")
    async def notification_status_command(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild_id:
            await interaction.followup.send("サーバー内で使用してください。", ephemeral=True)
            return

        guild_id = str(interaction.guild_id)
        settings = bot.db_manager.get_notification_settings(guild_id)

        if not settings:
            await interaction.followup.send("通知は設定されていません。`/通知 設定` で設定してください。", ephemeral=True)
            return

        weekday_names = ["月", "火", "水", "木", "金", "土", "日"]
        status_emoji = "✅" if settings.get("enabled") else "⏸️"
        status_text = "有効" if settings.get("enabled") else "停止中"

        embed = discord.Embed(
            title="🔔 週次通知設定",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="状態",
            value=f"{status_emoji} {status_text}",
            inline=True
        )
        embed.add_field(
            name="スケジュール",
            value=f"毎週{weekday_names[settings.get('weekday', 0)]}曜日 {settings.get('hour', 0):02d}:{settings.get('minute', 0):02d}（JST）",
            inline=True
        )
        embed.add_field(
            name="通知先",
            value=f"<#{settings.get('channel_id', '')}>",
            inline=True
        )

        calendar_owners = settings.get("calendar_owners", [])
        if calendar_owners:
            owner_mentions = [f"<@{uid}>" for uid in calendar_owners]
            embed.add_field(
                name="対象カレンダー",
                value=", ".join(owner_mentions),
                inline=False
            )
        else:
            embed.add_field(
                name="対象カレンダー",
                value="全カレンダー",
                inline=False
            )

        if settings.get("last_sent_at"):
            embed.add_field(
                name="最終送信",
                value=settings["last_sent_at"],
                inline=True
            )

        configured_by = settings.get("configured_by", "")
        if configured_by:
            member = interaction.guild.get_member(int(configured_by))
            configured_name = member.display_name if member else f"不明なユーザー ({configured_by})"
            embed.set_footer(text=f"設定者: {configured_name}")

        await interaction.followup.send(embed=embed, ephemeral=True)

    @notification_group.command(name="テスト", description="通知をテスト送信します")
    async def notification_test_command(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild_id:
            await interaction.followup.send("サーバー内で使用してください。", ephemeral=True)
            return

        guild_id = str(interaction.guild_id)
        settings = bot.db_manager.get_notification_settings(guild_id)

        if not settings or not settings.get("enabled"):
            await interaction.followup.send("❌ 通知が設定されていないか、停止中です。`/通知 設定` で設定してください。", ephemeral=True)
            return

        try:
            await bot._send_scheduled_notification(guild_id, settings)
            await interaction.followup.send("✅ テスト通知を送信しました。通知先チャンネルを確認してください。", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 通知送信に失敗しました: {e}", ephemeral=True)

    bot.tree.add_command(notification_group)


# ---- ヘルパー関数 ----

def _resolve_color_category(recurrence: Optional[str], nth_weeks: Optional[List[int]]) -> Optional[str]:
    """recurrence + nth_weeks から色カテゴリキーを返す"""
    if recurrence == "weekly":
        return "weekly"
    if recurrence == "biweekly":
        return "biweekly"
    if recurrence == "nth_week":
        if nth_weeks and len(nth_weeks) == 1:
            return "monthly"
        return "nth_week"
    if recurrence == "irregular":
        return "irregular"
    return None


def _auto_assign_color(db_manager: FirestoreManager, guild_id: str, user_id: str, recurrence: Optional[str], nth_weeks: Optional[List[int]]) -> Optional[Dict[str, str]]:
    """色カテゴリに基づいて色プリセットを自動割当（カレンダー単位）。
    Returns: {"name": "色名", "color_id": "9"} or None"""
    category = _resolve_color_category(recurrence, nth_weeks)
    if not category:
        return None
    return db_manager.get_color_preset_by_recurrence(guild_id, user_id, category)


async def _batch_update_google_calendar_events(
    bot: CalendarBot,
    guild_id: str,
    events: List[Dict],
    google_updates: Dict[str, Any],
) -> int:
    """複数予定のGoogle Calendarイベントを一括更新。更新成功件数を返す。"""
    updated = 0
    for event in events:
        if not event.get('google_calendar_events'):
            continue
        cal_owner = event.get('calendar_owner') or event.get('created_by', '')
        if not cal_owner:
            continue
        cal_mgr = bot.get_calendar_manager_for_user(int(guild_id), cal_owner)
        if not cal_mgr:
            continue
        google_cal_data = json.loads(event['google_calendar_events'])
        google_event_ids = [ge['event_id'] for ge in google_cal_data]
        try:
            cal_mgr.update_events(google_event_ids, google_updates)
            updated += 1
        except Exception:
            pass
    return updated


def _next_weekday_datetime(
    weekday: int,
    time_str: str,
    recurrence: str = "weekly",
    nth_weeks: Optional[List[int]] = None,
) -> datetime:
    """次の該当曜日の日時を返す

    nth_week の場合は直近で該当する第n週の曜日を返す。
    weekly/biweekly の場合は次の該当曜日を返す（今日が該当曜日なら今日）。
    """
    hour, minute = map(int, time_str.split(':'))

    if recurrence == "nth_week" and nth_weeks:
        now = datetime.now()
        # 今月と来月で直近の該当日を探す
        for month_offset in range(3):
            year = now.year
            month = now.month + month_offset
            if month > 12:
                year += (month - 1) // 12
                month = (month - 1) % 12 + 1
            for nth in sorted(nth_weeks):
                candidate = RecurrenceCalculator._get_nth_weekday(year, month, nth, weekday)
                if candidate and candidate.date() >= now.date():
                    return candidate.replace(hour=hour, minute=minute, second=0, microsecond=0)
        # フォールバック（通常到達しない）
        return _next_weekday_datetime(weekday, time_str)

    now = datetime.now()
    days_ahead = (weekday - now.weekday()) % 7
    target = now + timedelta(days=days_ahead)
    return target.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _build_event_description(
    raw_description: str = "",
    tags: Optional[List[str]] = None,
    tag_groups: Optional[List[Dict[str, Any]]] = None,
    x_url: Optional[str] = None,
    vrc_group_url: Optional[str] = None,
    official_url: Optional[str] = None,
) -> str:
    """Google Calendar 予定の説明欄を統一フォーマットで構築"""
    sections = []
    if raw_description:
        sections.append(raw_description)

    # タグセクション
    if tags:
        tag_lines = ["─── タグ ───"]
        if tag_groups:
            tags_by_group: Dict[str, List[str]] = {}
            for tg in tag_groups:
                matched = [t for t in tags if t in [tag['name'] for tag in tg.get('tags', [])]]
                if matched:
                    tags_by_group[tg['name']] = matched
            for group_name, group_tags in tags_by_group.items():
                tag_lines.append(f"[{group_name}] {', '.join(group_tags)}")
            # グループに属さないタグ
            grouped: set = set()
            for gt in tags_by_group.values():
                grouped.update(gt)
            ungrouped = [t for t in tags if t not in grouped]
            if ungrouped:
                tag_lines.append(f"{', '.join(ungrouped)}")
        else:
            tag_lines.append(", ".join(tags))
        sections.append("\n".join(tag_lines))

    # URLセクション
    url_lines = []
    if x_url:
        url_lines.append(f"X: {x_url}")
    if vrc_group_url:
        url_lines.append(f"VRCグループ: {vrc_group_url}")
    if official_url:
        url_lines.append(f"公式サイト: {official_url}")
    if url_lines:
        sections.append("─── リンク ───\n" + "\n".join(url_lines))

    return "\n\n".join(sections)


def _event_data_to_parsed(event_data: Dict[str, Any], action: str) -> Dict[str, Any]:
    """会話で収集したevent_dataを既存のparsedフォーマットに変換する"""
    parsed = {"action": action}
    field_mapping = {
        "event_name": "event_name",
        "tags": "tags",
        "recurrence": "recurrence",
        "nth_weeks": "nth_weeks",
        "time": "time",
        "weekday": "weekday",
        "duration_minutes": "duration_minutes",
        "description": "description",
        "color_name": "color_name",
        "x_url": "x_url",
        "vrc_group_url": "vrc_group_url",
        "official_url": "official_url",
        "calendar_name": "calendar_name",
    }
    for src, dst in field_mapping.items():
        val = event_data.get(src)
        if val is not None:
            parsed[dst] = val

    # duration_minutes のデフォルト
    if action == "add" and "duration_minutes" not in parsed:
        parsed["duration_minutes"] = 60

    return parsed


async def _dispatch_action(
    bot: CalendarBot,
    interaction: discord.Interaction,
    parsed: Dict[str, Any],
) -> Optional[str]:
    """アクションに応じた処理を実行する（interactionベース）"""
    action = parsed.get("action")
    if action == "add":
        return await confirm_and_handle_add_event(bot, interaction, parsed)
    elif action == "edit":
        return await confirm_and_handle_edit_event(bot, interaction, parsed)
    elif action == "delete":
        return await confirm_and_handle_delete_event(bot, interaction, parsed)
    elif action == "search":
        return await handle_search_event(bot, interaction, parsed)
    else:
        return "アクションを認識できませんでした。"


async def _dispatch_action_in_thread(
    bot: CalendarBot,
    thread: discord.Thread,
    author: discord.Member,
    parsed: Dict[str, Any],
    guild_id: str,
) -> Tuple[Optional[str], bool]:
    """スレッド内でアクションを実行する

    Returns:
        Tuple[Optional[str], bool]: (メッセージ, セッション終了フラグ)
            - セッション終了フラグがTrueの場合、セッションを終了してスレッドをアーカイブ
            - Falseの場合、セッションを継続（修正モード）
    """
    action = parsed.get("action")
    if action == "add":
        return await _confirm_and_handle_in_thread(bot, thread, author, parsed, guild_id, "add")
    elif action == "edit":
        return await _confirm_and_handle_in_thread(bot, thread, author, parsed, guild_id, "edit")
    elif action == "delete":
        return await _confirm_and_handle_in_thread(bot, thread, author, parsed, guild_id, "delete")
    elif action == "search":
        result = await _handle_search_in_thread(bot, thread, parsed, guild_id)
        return (result, True)  # 検索は常にセッション終了
    else:
        return ("アクションを認識できませんでした。", True)


async def _confirm_and_handle_in_thread(
    bot: CalendarBot,
    thread: discord.Thread,
    author: discord.Member,
    parsed: Dict[str, Any],
    guild_id: str,
    action: str,
) -> Tuple[Optional[str], bool]:
    """スレッド内での確認→実行フロー

    Returns:
        Tuple[Optional[str], bool]: (メッセージ, セッション終了フラグ)
    """
    # 1. カレンダー選択（複数ある場合のみUI表示、addのみ）
    calendar_owner = None
    if action == "add":
        all_tokens = bot.db_manager.get_all_oauth_tokens(guild_id)
        if len(all_tokens) > 1 and not parsed.get('calendar_name'):
            cal_view = CalendarSelectView(author.id, all_tokens)
            await thread.send("📅 どのカレンダーに登録しますか？", view=cal_view)
            await cal_view.wait()
            if cal_view.selected_calendar_owner:
                parsed['calendar_name'] = cal_view.selected_display_name
                parsed['_calendar_owner'] = cal_view.selected_calendar_owner
                calendar_owner = cal_view.selected_calendar_owner
        elif len(all_tokens) == 1:
            calendar_owner = all_tokens[0].get('_doc_id') or all_tokens[0].get('authenticated_by')
            parsed['_calendar_owner'] = calendar_owner
        elif not all_tokens:
            # カレンダー未認証
            pass
        if not calendar_owner:
            token_info = _resolve_calendar_owner(bot, guild_id, parsed.get('calendar_name'))
            if token_info:
                calendar_owner = token_info.get('_doc_id') or token_info.get('authenticated_by')

    # 2. 色セットアップ完了チェック（addのみ）— 未設定時はウィザードを表示
    if action == "add" and calendar_owner:
        if not bot.db_manager.is_color_setup_done(guild_id, calendar_owner):
            await thread.send(
                "🎨 このカレンダーの色初期設定がまだ完了していません。\n"
                "各予定種類に対するデフォルト色を設定しましょう！"
            )
            setup_view = ColorSetupView(author.id, guild_id, bot, target_user_id=calendar_owner)
            msg = await thread.send(
                f"**{COLOR_CATEGORIES[0]['label']}**（{COLOR_CATEGORIES[0]['description']}）の色を選択してください。",
                view=setup_view,
            )
            await setup_view.wait()
            # セットアップ完了後、色凡例を更新
            await _update_color_legend_for_user(bot, guild_id, calendar_owner)

    # 3. 色チェック（色自動割当）— calendar_owner を使用
    if action == "add" and not parsed.get("color_name") and calendar_owner:
        auto_color = _auto_assign_color(
            bot.db_manager, guild_id, calendar_owner,
            parsed.get("recurrence"), parsed.get("nth_weeks"),
        )
        if auto_color:
            parsed["color_name"] = auto_color["name"]
            parsed["_auto_color"] = True
        else:
            # 色プリセットがない場合、新色追加ダイアログ
            recurrence = parsed.get("recurrence")
            nth_weeks = parsed.get("nth_weeks")
            category = _resolve_color_category(recurrence, nth_weeks)
            if category:
                cat_labels = {c["key"]: c["label"] for c in COLOR_CATEGORIES}
                category_label = cat_labels.get(category, category)
                new_color_view = NewColorLegendView(author.id, category_label)
                await thread.send(
                    f"🎨 「{category_label}」に対応する色プリセットがありません。\n新しく色を追加しますか？",
                    view=new_color_view,
                )
                await new_color_view.wait()

                if new_color_view.value == "add":
                    color_select_view = ColorSelectForEventView(author.id)
                    await thread.send("📎 色を選択してください:", view=color_select_view)
                    await color_select_view.wait()

                    if color_select_view.selected_color_id:
                        # プリセットを登録して色を自動割当
                        bot.db_manager.add_color_preset(
                            guild_id, calendar_owner, category_label, color_select_view.selected_color_id,
                            description=f"{category_label}のイベント",
                            recurrence_type=category, is_auto_generated=True,
                        )
                        parsed["color_name"] = category_label
                        parsed["_auto_color"] = True
                        color_info = GOOGLE_CALENDAR_COLORS.get(color_select_view.selected_color_id, {})
                        await thread.send(
                            f"✅ 色プリセット「{category_label}」（{color_info.get('name', '?')} / colorId {color_select_view.selected_color_id}）を登録しました。"
                        )
                        # 色凡例を更新
                        await _update_color_legend_for_user(bot, guild_id, calendar_owner)

    # 4. 未登録タグの確認・自動作成（add/edit でタグがある場合）
    if action in ("add", "edit"):
        tags = parsed.get('tags', []) or []
        if tags:
            resolved_tags = await _resolve_missing_tags(
                bot, guild_id, tags, author.id, thread.send
            )
            parsed['tags'] = resolved_tags
            # タグが変更された場合、タグ凡例を更新
            if calendar_owner:
                await _update_tag_legend_for_user(bot, guild_id, calendar_owner)

    if action == "add":
        summary = build_event_summary(parsed)
        title = "予定追加の確認"
    elif action == "edit":
        events = bot.db_manager.search_events_by_name(parsed.get('event_name'), guild_id)
        if not events:
            return (f"❌ 予定「{parsed.get('event_name')}」が見つかりませんでした。", True)
        event = events[0]
        summary = (
            f"対象: {event['event_name']} (ID {event['id']})\n"
            f"{build_event_summary(parsed)}"
        )
        title = "予定編集の確認"
    elif action == "delete":
        events = bot.db_manager.search_events_by_name(parsed.get('event_name'), guild_id)
        if not events:
            return (f"❌ 予定「{parsed.get('event_name')}」が見つかりませんでした。", True)
        event = events[0]
        summary = (
            f"対象: {event['event_name']} (ID {event['id']})\n"
            f"繰り返し: {RECURRENCE_TYPES.get(event['recurrence'], event['recurrence'])}"
        )
        title = "予定削除の確認"
    else:
        return ("不正なアクションです。", True)

    # 確認Embed + ボタン
    embed = discord.Embed(
        title=title,
        description=summary,
        color=discord.Color.orange()
    )
    view = ThreadConfirmView(author.id)
    await thread.send(embed=embed, view=view)
    await view.wait()

    if view.value == ThreadConfirmView.CANCELLED or view.value is None:
        # キャンセルまたはタイムアウト → セッション終了
        return (None, True)

    if view.value == ThreadConfirmView.EDIT:
        # 修正モード → セッション継続
        return (None, False)

    # 確定 → 実行してセッション終了
    if action == "add":
        result = await _handle_add_event_direct(bot, guild_id, thread.parent_id, author.id, parsed)
        return (result, True)
    elif action == "edit":
        result = await _handle_edit_event_direct(bot, guild_id, parsed)
        return (result, True)
    elif action == "delete":
        result = await _handle_delete_event_direct(bot, guild_id, parsed)
        return (result, True)
    return (None, True)


async def _handle_search_in_thread(
    bot: CalendarBot,
    thread: discord.Thread,
    parsed: Dict[str, Any],
    guild_id: str,
) -> Optional[str]:
    """スレッド内で検索を実行"""
    query = parsed.get('search_query', {})
    date_range = query.get('date_range', 'this_week')
    start_date, end_date = get_date_range(date_range)

    events = bot.db_manager.search_events(
        start_date=start_date,
        end_date=end_date,
        guild_id=guild_id,
        tags=query.get('tags'),
        event_name=query.get('event_name')
    )

    if not events:
        return "📭 該当する予定が見つかりませんでした。"

    embed = create_search_result_embed(events, start_date, end_date)
    await thread.send(embed=embed)
    return None


# ---- スレッド内用の確認ビュー ----

class ThreadConfirmView(discord.ui.View):
    """スレッド内の確認ビュー（確定/修正/キャンセル）"""
    CONFIRMED = "confirmed"
    EDIT = "edit"
    CANCELLED = "cancelled"

    def __init__(self, author_id: int):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.value: Optional[str] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    @discord.ui.button(label="確定", style=discord.ButtonStyle.green)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = self.CONFIRMED
        await interaction.response.send_message("✅ 確定しました。処理を実行します。")
        self.stop()

    @discord.ui.button(label="修正", style=discord.ButtonStyle.blurple)
    async def edit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = self.EDIT
        await interaction.response.send_message("📝 修正モードに入ります。変更したい内容を入力してください。\n例: 「時刻を22時に変更」「タグを追加して」")
        self.stop()

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.red)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = self.CANCELLED
        await interaction.response.send_message("❌ キャンセルしました。セッションを終了します。")
        self.stop()


# ---- 色セットアップウィザード ----

class ColorSetupView(discord.ui.View):
    """カテゴリごとにcolorIdを選択するウィザード"""

    def __init__(self, author_id: int, guild_id: str, bot: CalendarBot, target_user_id: str = ""):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.guild_id = guild_id
        self.bot = bot
        self.target_user_id = target_user_id or str(author_id)
        self.selections: Dict[str, Dict[str, str]] = {}  # key -> {"color_id": "9", "name": "色名"}
        self.current_index = 0
        self._add_select_for_current()

    def _add_select_for_current(self):
        """現在のカテゴリ用のSelectMenuを追加"""
        self.clear_items()
        if self.current_index >= len(COLOR_CATEGORIES):
            return

        category = COLOR_CATEGORIES[self.current_index]
        options = [
            discord.SelectOption(
                label=f"{cid}: {info['name']}",
                value=cid,
                description=info['hex'],
                emoji=COLOR_EMOJI.get(cid),
            )
            for cid, info in USER_SELECTABLE_COLORS.items()
        ]

        select = discord.ui.Select(
            placeholder=f"{category['label']}（{category['description']}）の色を選択",
            options=options,
            custom_id=f"color_setup_{category['key']}",
        )
        select.callback = self._on_select
        self.add_item(select)

        # スキップボタン
        skip_btn = discord.ui.Button(label="全てスキップ", style=discord.ButtonStyle.grey, custom_id="skip_all")
        skip_btn.callback = self._on_skip_all
        self.add_item(skip_btn)

    async def _on_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            return

        category = COLOR_CATEGORIES[self.current_index]
        selected_color_id = interaction.data["values"][0]
        color_info = GOOGLE_CALENDAR_COLORS[selected_color_id]

        # カテゴリのラベルを色名として使用
        self.selections[category["key"]] = {
            "color_id": selected_color_id,
            "name": category["label"],
            "description": category["description"],
        }

        self.current_index += 1

        if self.current_index >= len(COLOR_CATEGORIES):
            # 全カテゴリ選択完了 → 一括登録
            await self._finalize(interaction)
        else:
            # 次のカテゴリ
            self._add_select_for_current()
            next_cat = COLOR_CATEGORIES[self.current_index]
            await interaction.response.edit_message(
                content=f"✅ 「{category['label']}」→ {color_info['name']}（colorId {selected_color_id}）に設定しました。\n\n次は **{next_cat['label']}** の色を選択してください。",
                view=self,
            )

    async def _on_skip_all(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            return
        # セットアップ完了フラグだけ設定（カレンダー単位）
        self.bot.db_manager.mark_color_setup_done(self.guild_id, self.target_user_id)
        await interaction.response.edit_message(
            content="⏭️ 色初期設定をスキップしました。後から `/色 初期設定` で設定できます。",
            view=None,
        )
        self.stop()

    async def _finalize(self, interaction: discord.Interaction):
        """選択完了後、色プリセットを一括登録（カレンダー単位）"""
        presets_data = []
        for key, data in self.selections.items():
            presets_data.append({
                "name": data["name"],
                "color_id": data["color_id"],
                "recurrence_type": key,
                "description": data["description"],
            })

        self.bot.db_manager.initialize_default_color_presets(self.guild_id, self.target_user_id, presets_data)

        # サマリー構築 → 先にインタラクション応答（3秒タイムアウト回避）
        summary_lines = []
        for key, data in self.selections.items():
            color_info = GOOGLE_CALENDAR_COLORS.get(data["color_id"], {})
            summary_lines.append(f"• {data['name']}: {color_info.get('name', '?')}（colorId {data['color_id']}）")

        await interaction.response.edit_message(
            content="✅ 色初期設定が完了しました！\n\n" + "\n".join(summary_lines) + "\n\n⏳ 凡例・既存予定を更新中...",
            view=None,
        )
        self.stop()

        # 重い処理はインタラクション応答後に実行
        # 対象カレンダーの凡例イベントを更新
        await _update_legend_event_for_user(self.bot, self.guild_id, self.target_user_id)

        # 色プリセットに基づいて既存イベントのGoogle Calendar色を同期
        color_update_count = 0
        for key, data in self.selections.items():
            color_name = data["name"]
            new_color_id = data["color_id"]
            affected = self.bot.db_manager.get_events_by_color_name(self.guild_id, color_name)
            affected = [e for e in affected if (e.get('calendar_owner') or e.get('created_by', '')) == self.target_user_id]
            if affected:
                cnt = await _batch_update_google_calendar_events(
                    self.bot, self.guild_id, affected, {'colorId': new_color_id}
                )
                color_update_count += cnt

        # 既存予定で色未割当のものに自動割当
        all_events = self.bot.db_manager.get_all_active_events(self.guild_id)
        owner_events = [
            e for e in all_events
            if (e.get('calendar_owner') or e.get('created_by', '')) == self.target_user_id
            and not e.get('color_name')
        ]
        auto_count = 0
        for event in owner_events:
            recurrence = event.get('recurrence')
            nth_weeks_raw = event.get('nth_weeks')
            nth_weeks = json.loads(nth_weeks_raw) if nth_weeks_raw else None
            auto_color = _auto_assign_color(
                self.bot.db_manager, self.guild_id, self.target_user_id, recurrence, nth_weeks
            )
            if auto_color:
                self.bot.db_manager.update_event(event['id'], {'color_name': auto_color['name']})
                if event.get('google_calendar_events'):
                    cal_mgr = self.bot.get_calendar_manager_for_user(int(self.guild_id), self.target_user_id)
                    if cal_mgr:
                        google_cal_data = json.loads(event['google_calendar_events'])
                        ids = [ge['event_id'] for ge in google_cal_data]
                        try:
                            cal_mgr.update_events(ids, {'colorId': auto_color['color_id']})
                        except Exception:
                            pass
                auto_count += 1

        # 処理完了後にメッセージを最終更新
        final_content = "✅ 色初期設定が完了しました！\n\n" + "\n".join(summary_lines)
        if color_update_count:
            final_content += f"\n\n🔄 既存予定 {color_update_count} 件のカレンダー色を更新しました。"
        if auto_count:
            final_content += f"\n\n📝 既存予定 {auto_count} 件に色を自動割当しました。"
        try:
            await interaction.edit_original_response(content=final_content)
        except Exception:
            pass

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id


class NewColorLegendView(discord.ui.View):
    """新色プリセット追加確認（追加 / スキップ）"""

    def __init__(self, author_id: int, category_label: str):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.category_label = category_label
        self.value: Optional[str] = None  # "add" or "skip"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    @discord.ui.button(label="色を追加", style=discord.ButtonStyle.green)
    async def add_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = "add"
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="スキップ", style=discord.ButtonStyle.grey)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = "skip"
        await interaction.response.defer()
        self.stop()


class ColorSelectForEventView(discord.ui.View):
    """Google Calendar colorId 選択（SelectMenu 1-11）- イベント追加時用"""

    def __init__(self, author_id: int):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.selected_color_id: Optional[str] = None

        options = [
            discord.SelectOption(
                label=f"{cid}: {info['name']}",
                value=cid,
                description=info['hex'],
                emoji=COLOR_EMOJI.get(cid),
            )
            for cid, info in USER_SELECTABLE_COLORS.items()
        ]
        select = discord.ui.Select(
            placeholder="色を選択してください",
            options=options,
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            return
        self.selected_color_id = interaction.data["values"][0]
        await interaction.response.defer()
        self.stop()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id


def _resolve_calendar_owner(bot: CalendarBot, guild_id: str, calendar_name: str = None) -> Optional[dict]:
    """calendar_nameからOAuthトークン情報を解決する"""
    if calendar_name:
        tokens = bot.db_manager.get_oauth_tokens_by_display_name(guild_id, calendar_name)
        if tokens:
            return tokens
    # calendar_name未指定 or 見つからない → デフォルト
    default = bot.db_manager.get_default_oauth_tokens(guild_id)
    if default:
        return default
    # デフォルトなし → 最初の1つ
    all_tokens = bot.db_manager.get_all_oauth_tokens(guild_id)
    return all_tokens[0] if all_tokens else None


class CalendarSelectView(discord.ui.View):
    """確認画面用のカレンダー選択ドロップダウン"""

    def __init__(self, author_id: int, calendars: List[dict], default_name: str = ""):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.selected_calendar_owner: Optional[str] = None
        self.selected_display_name: Optional[str] = None

        options = []
        for cal in calendars:
            doc_id = cal.get("_doc_id") or cal.get("authenticated_by", "")
            display = cal.get("display_name") or f"<@{doc_id}>"
            desc = cal.get("description", "") or ""
            is_default = cal.get("is_default", False)
            label = f"{display}{'（デフォルト）' if is_default else ''}"
            options.append(discord.SelectOption(
                label=label[:100],
                value=doc_id,
                description=desc[:100] if desc else None,
                default=(display == default_name) if default_name else is_default,
            ))

        select = discord.ui.Select(
            placeholder="登録先カレンダーを選択してください",
            options=options,
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            return
        self.selected_calendar_owner = interaction.data["values"][0]
        # 表示名を復元
        for opt in self.children[0].options:
            if opt.value == self.selected_calendar_owner:
                self.selected_display_name = opt.label.replace("（デフォルト）", "").strip()
                break
        await interaction.response.defer()
        self.stop()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id


class NotificationCalendarSelectView(discord.ui.View):
    """通知対象カレンダー選択UI"""
    def __init__(self, bot, guild_id: str, user_id: str,
                 all_tokens: list, weekday: int, hour: int, minute: int, channel_id: str):
        super().__init__(timeout=120)
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.weekday = weekday
        self.hour = hour
        self.minute = minute
        self.channel_id = channel_id

        options = [
            discord.SelectOption(label="全カレンダー", value="__all__", description="すべてのカレンダーの予定を通知")
        ]
        for token in all_tokens:
            uid = token.get("_doc_id") or token.get("authenticated_by", "")
            display_name = token.get("display_name") or f"カレンダー（{uid[:8]}...）"
            is_default = "⭐ " if token.get("is_default") else ""
            options.append(
                discord.SelectOption(
                    label=f"{is_default}{display_name}",
                    value=uid,
                    description=token.get("description", "")[:100] if token.get("description") else None,
                )
            )

        select = discord.ui.Select(
            placeholder="通知対象カレンダーを選択...",
            options=options,
            min_values=1,
            max_values=len(options),
        )
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        selected = interaction.data["values"]
        calendar_owners = [] if "__all__" in selected else selected

        self.bot.db_manager.save_notification_settings(
            guild_id=self.guild_id,
            enabled=True,
            weekday=self.weekday,
            hour=self.hour,
            minute=self.minute,
            channel_id=self.channel_id,
            calendar_owners=calendar_owners,
            configured_by=self.user_id,
        )

        weekday_names = ["月", "火", "水", "木", "金", "土", "日"]
        cal_text = "全カレンダー" if not calendar_owners else f"{len(calendar_owners)}個のカレンダー"
        await interaction.response.edit_message(
            content=(
                f"✅ 週次通知を設定しました！\n"
                f"📅 毎週{weekday_names[self.weekday]}曜日 {self.hour:02d}:{self.minute:02d}（JST）\n"
                f"📢 通知先: <#{self.channel_id}>\n"
                f"📋 対象: {cal_text}"
            ),
            view=None,
        )
        self.stop()


class MissingTagConfirmView(discord.ui.View):
    """未登録タグの自動作成確認"""

    def __init__(self, author_id: int, missing_tags: List[str]):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.missing_tags = missing_tags
        self.value: Optional[str] = None  # "create" or "skip"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    @discord.ui.button(label="作成して続行", style=discord.ButtonStyle.green)
    async def create_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = "create"
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="タグなしで続行", style=discord.ButtonStyle.grey)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = "skip"
        await interaction.response.defer()
        self.stop()


class TagGroupSelectView(discord.ui.View):
    """タグのグループ割当選択"""

    def __init__(self, author_id: int, groups: List[Dict[str, Any]], tag_name: str):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.tag_name = tag_name
        self.selected_group_id: Optional[int] = None

        options = [
            discord.SelectOption(
                label=group['name'],
                value=str(group['id']),
                description=(group.get('description', '') or '')[:50],
            )
            for group in groups
        ]
        select = discord.ui.Select(
            placeholder=f"「{tag_name}」の追加先グループを選択",
            options=options,
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            return
        self.selected_group_id = int(interaction.data["values"][0])
        await interaction.response.defer()
        self.stop()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id


# ---- 未登録タグ自動作成ヘルパー ----

async def _resolve_missing_tags(
    bot: CalendarBot,
    guild_id: str,
    tags: List[str],
    author_id: int,
    send_func,
) -> List[str]:
    """未登録タグを検出し、ユーザー確認後に自動作成する。

    Args:
        send_func: メッセージ送信用callable（thread.send または interaction.followup.send ラッパー）
    Returns:
        解決済みタグリスト（未登録タグを除外またはDB登録済み）
    """
    if not tags:
        return tags

    missing_tags = bot.db_manager.find_missing_tags(guild_id, tags)
    if not missing_tags:
        return tags

    # 確認ダイアログ
    view = MissingTagConfirmView(author_id, missing_tags)
    await send_func(
        f"🏷️ 以下のタグは未登録です:\n"
        f"• {'、'.join(missing_tags)}\n\n"
        f"自動作成しますか？",
        view=view,
    )
    await view.wait()

    if view.value != "create":
        # タグなしで続行: 未登録タグを除外
        return [t for t in tags if t not in missing_tags]

    # グループを取得して割当
    groups = bot.db_manager.list_tag_groups(guild_id)

    if not groups:
        # デフォルトグループを作成
        group_id = bot.db_manager.add_tag_group(guild_id, "一般", "自動作成されたタググループ")
        for tag_name in missing_tags:
            bot.db_manager.add_tag(guild_id, group_id, tag_name)
        await send_func(f"✅ タググループ「一般」を作成し、タグ {'、'.join(missing_tags)} を追加しました。")
    elif len(groups) == 1:
        group = groups[0]
        for tag_name in missing_tags:
            bot.db_manager.add_tag(guild_id, group['id'], tag_name)
        await send_func(f"✅ タグ {'、'.join(missing_tags)} をグループ「{group['name']}」に追加しました。")
    else:
        # 複数グループ — タグごとにグループを選択
        for tag_name in missing_tags:
            select_view = TagGroupSelectView(author_id, groups, tag_name)
            await send_func(
                f"🏷️ タグ「{tag_name}」をどのグループに追加しますか？",
                view=select_view,
            )
            await select_view.wait()
            if select_view.selected_group_id:
                bot.db_manager.add_tag(guild_id, select_view.selected_group_id, tag_name)
                group_name = next(
                    (g['name'] for g in groups if g['id'] == select_view.selected_group_id), "?"
                )
                await send_func(f"✅ タグ「{tag_name}」をグループ「{group_name}」に追加しました。")
            else:
                # タイムアウト — このタグをスキップ
                tags = [t for t in tags if t != tag_name]

    return tags


# ---- ダイレクト実行関数（interaction不要版） ----

async def _handle_add_event_direct(
    bot: CalendarBot,
    guild_id: str,
    channel_id: int,
    user_id: int,
    parsed: Dict[str, Any],
) -> str:
    """interactionなしで予定を追加する（スレッド内用）"""
    # タグと色のバリデーション
    tags = parsed.get('tags', []) or []
    missing_tags = bot.db_manager.find_missing_tags(guild_id, tags)
    if missing_tags:
        return f"❌ 未登録のタグがあります: {', '.join(missing_tags)}"

    # カレンダーオーナー解決
    calendar_owner = parsed.get('_calendar_owner')
    if not calendar_owner:
        token_info = _resolve_calendar_owner(bot, guild_id, parsed.get('calendar_name'))
        calendar_owner = token_info.get('_doc_id') or token_info.get('authenticated_by') if token_info else None

    color_name = parsed.get('color_name')
    color_id = None
    if color_name and calendar_owner:
        preset = bot.db_manager.get_color_preset(guild_id, calendar_owner, color_name)
        if not preset:
            return f"❌ 色名「{color_name}」が登録されていません。"
        color_id = preset['color_id']

    x_url = parsed.get('x_url') or None
    vrc_group_url = parsed.get('vrc_group_url') or None
    official_url = parsed.get('official_url') or None

    raw_description = parsed.get('description', '')
    cal_description = _build_event_description(
        raw_description=raw_description,
        tags=tags,
        x_url=x_url, vrc_group_url=vrc_group_url, official_url=official_url,
    )

    event_id = bot.db_manager.add_event(
        guild_id=guild_id,
        event_name=parsed['event_name'],
        tags=tags,
        recurrence=parsed['recurrence'],
        nth_weeks=parsed.get('nth_weeks'),
        event_type=parsed.get('event_type'),
        time=parsed.get('time'),
        weekday=parsed.get('weekday'),
        duration_minutes=parsed.get('duration_minutes', 60),
        description=raw_description,
        color_name=color_name,
        x_url=x_url,
        vrc_group_url=vrc_group_url,
        official_url=official_url,
        discord_channel_id=str(channel_id),
        created_by=str(user_id),
        calendar_owner=calendar_owner or str(user_id),
    )

    if not calendar_owner:
        return "❌ カレンダーが未認証です。`/カレンダー 認証` を実行してください。"

    cal_mgr = bot.get_calendar_manager_for_user(int(guild_id), calendar_owner)
    if not cal_mgr:
        return "❌ カレンダーが未認証です。`/カレンダー 認証` を実行してください。"

    if parsed['recurrence'] != 'irregular':
        nth_weeks = parsed.get('nth_weeks') or []
        rrule = RecurrenceCalculator.to_rrule(
            recurrence=parsed['recurrence'],
            nth_weeks=nth_weeks,
            weekday=parsed['weekday'],
        )
        start_dt = _next_weekday_datetime(
            parsed['weekday'], parsed['time'],
            recurrence=parsed['recurrence'], nth_weeks=nth_weeks,
        )
        end_dt = start_dt + timedelta(minutes=parsed.get('duration_minutes', 60))

        google_event_id = cal_mgr.create_recurring_event(
            summary=parsed['event_name'],
            start_datetime=start_dt,
            end_datetime=end_dt,
            rrule=rrule,
            description=cal_description,
            color_id=color_id,
            extended_props={
                "tags": json.dumps(tags, ensure_ascii=False),
                "color_name": color_name or "",
                "x_url": x_url or "",
                "vrc_group_url": vrc_group_url or "",
                "official_url": official_url or "",
            },
        )

        bot.db_manager.update_google_calendar_events(
            event_id,
            [{"event_id": google_event_id, "rrule": rrule}]
        )

        return (
            f"✅ 予定を登録しました！\n"
            f"📅 {parsed['event_name']}\n"
            f"🔄 {RECURRENCE_TYPES.get(parsed['recurrence'], parsed['recurrence'])}\n"
            f"⏰ {parsed.get('time', '時刻未設定')}\n"
            f"📌 次回: {start_dt.strftime('%Y-%m-%d')}"
        )
    else:
        return (
            f"✅ 不定期予定を登録しました！\n"
            f"📅 {parsed['event_name']}\n"
            f"個別の日時は `/予定 {parsed['event_name']} 1月25日14時` のように追加してください。"
        )


def _sync_google_calendar_edit(
    bot: CalendarBot,
    guild_id: str,
    event: Dict[str, Any],
    parsed: Dict[str, Any],
    updates: Dict[str, Any],
    cal_owner: str,
) -> Optional[str]:
    """Google Calendar側のイベントを編集内容に応じて同期する。エラー時はメッセージを返す。"""
    if not event.get('google_calendar_events'):
        return None

    google_cal_data = json.loads(event['google_calendar_events'])
    google_event_ids = [ge['event_id'] for ge in google_cal_data]

    structural_change = any(k in parsed for k in ('recurrence', 'time', 'weekday', 'nth_weeks', 'duration_minutes'))

    cal_mgr = bot.get_calendar_manager_for_user(int(guild_id), cal_owner) if cal_owner else None
    if not cal_mgr:
        return f"❌ この予定が登録されたカレンダー（<@{cal_owner}>）の認証が無効です。再認証してもらってください。"

    new_recurrence = parsed.get('recurrence', event.get('recurrence'))

    if structural_change and new_recurrence != 'irregular':
        # 旧イベントを削除
        for ge in google_cal_data:
            try:
                cal_mgr.service.events().delete(
                    calendarId=cal_mgr.calendar_id, eventId=ge['event_id']
                ).execute()
            except Exception:
                pass

        # 新しいRRULEで再作成
        new_nth_weeks = parsed.get('nth_weeks') or (
            json.loads(event['nth_weeks']) if event.get('nth_weeks') else []
        )
        new_weekday = parsed.get('weekday', event.get('weekday'))
        new_time = parsed.get('time', event.get('time'))
        new_duration = parsed.get('duration_minutes', event.get('duration_minutes', 60))
        new_event_name = parsed.get('event_name', event['event_name'])

        color_name = updates.get('color_name', event.get('color_name'))
        color_id = None
        if color_name and cal_owner:
            preset = bot.db_manager.get_color_preset(guild_id, cal_owner, color_name)
            color_id = preset['color_id'] if preset else None

        edit_tags = updates.get('tags') if 'tags' in updates else (
            json.loads(event['tags']) if event.get('tags') else []
        )
        raw_desc = parsed.get('description') if 'description' in parsed else event.get('description', '')
        cal_description = _build_event_description(
            raw_description=raw_desc,
            tags=edit_tags if edit_tags else None,
            x_url=updates.get('x_url', event.get('x_url')),
            vrc_group_url=updates.get('vrc_group_url', event.get('vrc_group_url')),
            official_url=updates.get('official_url', event.get('official_url')),
        )

        rrule = RecurrenceCalculator.to_rrule(new_recurrence, new_nth_weeks, new_weekday)
        start_dt = _next_weekday_datetime(
            new_weekday, new_time,
            recurrence=new_recurrence, nth_weeks=new_nth_weeks,
        )
        end_dt = start_dt + timedelta(minutes=new_duration)

        google_event_id = cal_mgr.create_recurring_event(
            summary=new_event_name,
            start_datetime=start_dt,
            end_datetime=end_dt,
            rrule=rrule,
            description=cal_description,
            color_id=color_id,
            extended_props={
                "tags": json.dumps(edit_tags or [], ensure_ascii=False),
                "color_name": color_name or "",
                "x_url": updates.get('x_url', event.get('x_url')) or "",
                "vrc_group_url": updates.get('vrc_group_url', event.get('vrc_group_url')) or "",
                "official_url": updates.get('official_url', event.get('official_url')) or "",
            },
        )
        bot.db_manager.update_google_calendar_events(
            event['id'],
            [{"event_id": google_event_id, "rrule": rrule}]
        )
    else:
        # 属性のみの変更（summary, description, colorId等）
        google_updates = {}
        if 'event_name' in parsed: google_updates['summary'] = parsed['event_name']
        if 'description' in parsed or any(k in updates for k in ('x_url', 'vrc_group_url', 'official_url', 'tags')):
            raw_desc = parsed.get('description') if 'description' in parsed else event.get('description', '')
            edit_tags = updates.get('tags') if 'tags' in updates else (
                json.loads(event['tags']) if event.get('tags') else []
            )
            google_updates['description'] = _build_event_description(
                raw_description=raw_desc,
                tags=edit_tags if edit_tags else None,
                x_url=updates.get('x_url', event.get('x_url')),
                vrc_group_url=updates.get('vrc_group_url', event.get('vrc_group_url')),
                official_url=updates.get('official_url', event.get('official_url')),
            )
        if 'color_name' in updates:
            color_name = updates.get('color_name')
            color_id = None
            if color_name and cal_owner:
                preset = bot.db_manager.get_color_preset(guild_id, cal_owner, color_name)
                color_id = preset['color_id'] if preset else None
            if color_id:
                google_updates['colorId'] = color_id

        if google_updates:
            bot_ext = {}
            if 'tags' in updates:
                bot_ext['tags'] = json.dumps(updates['tags'], ensure_ascii=False)
            if 'color_name' in updates:
                bot_ext['color_name'] = updates.get('color_name') or ""
            if 'x_url' in updates:
                bot_ext['x_url'] = updates.get('x_url') or ""
            if 'vrc_group_url' in updates:
                bot_ext['vrc_group_url'] = updates.get('vrc_group_url') or ""
            if 'official_url' in updates:
                bot_ext['official_url'] = updates.get('official_url') or ""
            if bot_ext:
                google_updates['extendedProperties'] = {'private': bot_ext}
            cal_mgr.update_events(google_event_ids, google_updates)

    return None


async def _handle_edit_event_direct(
    bot: CalendarBot,
    guild_id: str,
    parsed: Dict[str, Any],
) -> str:
    """interactionなしで予定を編集する（スレッド内用）"""
    events = bot.db_manager.search_events_by_name(parsed.get('event_name'), guild_id)
    if not events:
        return f"❌ 予定「{parsed.get('event_name')}」が見つかりませんでした。"

    event = events[0]

    updates = {}
    if 'event_name' in parsed: updates['event_name'] = parsed['event_name']
    if 'time' in parsed: updates['time'] = parsed['time']
    if 'weekday' in parsed: updates['weekday'] = parsed['weekday']
    if 'recurrence' in parsed: updates['recurrence'] = parsed['recurrence']
    if 'nth_weeks' in parsed: updates['nth_weeks'] = parsed['nth_weeks']
    if 'duration_minutes' in parsed: updates['duration_minutes'] = parsed['duration_minutes']
    if 'event_type' in parsed: updates['event_type'] = parsed['event_type']
    if 'description' in parsed: updates['description'] = parsed['description']
    if 'tags' in parsed:
        tags = parsed.get('tags', []) or []
        missing_tags = bot.db_manager.find_missing_tags(guild_id, tags)
        if missing_tags:
            return f"❌ 未登録のタグがあります: {', '.join(missing_tags)}"
        updates['tags'] = tags
    # イベントのカレンダーオーナーを特定
    cal_owner = event.get('calendar_owner') or event.get('created_by', '')

    if 'color_name' in parsed:
        color_name = parsed.get('color_name')
        if color_name and cal_owner:
            preset = bot.db_manager.get_color_preset(guild_id, cal_owner, color_name)
            if not preset:
                return f"❌ 色名「{color_name}」が登録されていません。"
        updates['color_name'] = color_name

    # recurrence変更時の色自動再割当
    if 'recurrence' in parsed and 'color_name' not in parsed and cal_owner:
        new_recurrence = parsed.get('recurrence')
        new_nth_weeks = parsed.get('nth_weeks') or (
            json.loads(event['nth_weeks']) if event.get('nth_weeks') else None
        )
        auto_color = _auto_assign_color(bot.db_manager, guild_id, cal_owner, new_recurrence, new_nth_weeks)
        if auto_color:
            updates['color_name'] = auto_color['name']

    if 'x_url' in parsed:
        updates['x_url'] = parsed.get('x_url') or None
    if 'vrc_group_url' in parsed:
        updates['vrc_group_url'] = parsed.get('vrc_group_url') or None
    if 'official_url' in parsed:
        updates['official_url'] = parsed.get('official_url') or None

    bot.db_manager.update_event(event['id'], updates)

    error = _sync_google_calendar_edit(bot, guild_id, event, parsed, updates, cal_owner)
    if error:
        return error

    return f"✅ 予定「{event['event_name']}」を更新しました。"


async def _handle_delete_event_direct(
    bot: CalendarBot,
    guild_id: str,
    parsed: Dict[str, Any],
) -> str:
    """interactionなしで予定を削除する（スレッド内用）"""
    events = bot.db_manager.search_events_by_name(parsed.get('event_name'), guild_id)
    if not events:
        return f"❌ 予定「{parsed.get('event_name')}」が見つかりませんでした。"

    event = events[0]

    if event['google_calendar_events']:
        cal_owner = event.get('calendar_owner') or event.get('created_by', '')
        cal_mgr = bot.get_calendar_manager_for_user(int(guild_id), cal_owner) if cal_owner else None
        if not cal_mgr:
            return f"❌ この予定が登録されたカレンダー（<@{cal_owner}>）の認証が無効です。再認証してもらってください。"
        google_event_ids = [ge['event_id'] for ge in json.loads(event['google_calendar_events'])]
        cal_mgr.delete_events(google_event_ids)

    bot.db_manager.delete_event(event['id'])

    return f"✅ 予定「{event['event_name']}」を削除しました。"


# ---- 既存の interaction ベースのハンドラ（/予定 で complete の場合に使用） ----

async def handle_add_event(bot: CalendarBot, interaction: discord.Interaction, parsed: Dict[str, Any]) -> str:
    """予定追加処理（interactionベース → 共通ロジックに委譲）"""
    guild_id = str(interaction.guild_id) if interaction.guild_id else ""
    return await _handle_add_event_direct(
        bot, guild_id, interaction.channel_id, interaction.user.id, parsed
    )

async def handle_edit_event(bot: CalendarBot, interaction: discord.Interaction, parsed: Dict[str, Any]) -> str:
    """予定編集処理（interactionベース → 共通ロジックに委譲）"""
    guild_id = str(interaction.guild_id) if interaction.guild_id else ""
    return await _handle_edit_event_direct(bot, guild_id, parsed)

async def handle_delete_event(bot: CalendarBot, interaction: discord.Interaction, parsed: Dict[str, Any]) -> str:
    """予定削除処理（interactionベース → 共通ロジックに委譲）"""
    guild_id = str(interaction.guild_id) if interaction.guild_id else ""
    return await _handle_delete_event_direct(bot, guild_id, parsed)

async def handle_search_event(bot: CalendarBot, interaction: discord.Interaction, parsed: Dict[str, Any]) -> Optional[str]:
    """予定検索処理"""
    guild_id = str(interaction.guild_id) if interaction.guild_id else ""
    query = parsed.get('search_query', {})

    # 日付範囲の計算
    date_range = query.get('date_range', 'this_week')
    start_date, end_date = get_date_range(date_range)

    # データベースから検索
    events = bot.db_manager.search_events(
        start_date=start_date,
        end_date=end_date,
        guild_id=guild_id,
        tags=query.get('tags'),
        event_name=query.get('event_name')
    )

    if not events:
        return "📭 該当する予定が見つかりませんでした。"

    # Embedで整形
    embed = create_search_result_embed(events, start_date, end_date)
    await interaction.followup.send(embed=embed)

    return None

class ConfirmView(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.value: Optional[bool] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    @discord.ui.button(label="確定", style=discord.ButtonStyle.green)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        await interaction.response.send_message("✅ 確定しました。処理を実行します。", ephemeral=True)
        self.stop()

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.red)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        await interaction.response.send_message("キャンセルしました。", ephemeral=True)
        self.stop()

async def confirm_action(interaction: discord.Interaction, title: str, description: str) -> bool:
    embed = discord.Embed(
        title=title,
        description=description,
        color=discord.Color.orange()
    )
    view = ConfirmView(interaction.user.id)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    await view.wait()
    return view.value is True

def build_event_summary(parsed: Dict[str, Any]) -> str:
    tags = parsed.get('tags', []) or []
    nth = parsed.get('nth_weeks')
    nth_str = f"第{','.join(str(n) for n in nth)}週" if nth else ""
    weekdays = ['月', '火', '水', '木', '金', '土', '日']
    weekday_val = parsed.get('weekday')
    weekday_str = weekdays[weekday_val] if isinstance(weekday_val, int) and 0 <= weekday_val <= 6 else "未設定"
    color_name = parsed.get('color_name', '未設定')
    if parsed.get('_auto_color') and color_name and color_name != '未設定':
        color_display = f"{color_name}（自動割当）"
    else:
        color_display = color_name
    calendar_name = parsed.get('calendar_name') or 'デフォルト'
    return (
        f"予定名: {parsed.get('event_name', '未設定')}\n"
        f"繰り返し: {RECURRENCE_TYPES.get(parsed.get('recurrence'), parsed.get('recurrence'))} {nth_str}\n"
        f"曜日: {weekday_str}\n"
        f"時刻: {parsed.get('time', '未設定')}\n"
        f"所要時間: {parsed.get('duration_minutes', 60)}分\n"
        f"色: {color_display}\n"
        f"カレンダー: {calendar_name}\n"
        f"タグ: {', '.join(tags) if tags else 'なし'}\n"
        f"X URL: {parsed.get('x_url') or 'なし'}\n"
        f"VRCグループURL: {parsed.get('vrc_group_url') or 'なし'}\n"
        f"公式サイトURL: {parsed.get('official_url') or 'なし'}\n"
        f"説明: {parsed.get('description', '')}"
    )

async def confirm_and_handle_add_event(bot: CalendarBot, interaction: discord.Interaction, parsed: Dict[str, Any]) -> Optional[str]:
    guild_id = str(interaction.guild_id) if interaction.guild_id else ""

    # 未登録タグの確認・自動作成
    tags = parsed.get('tags', []) or []
    if tags:
        async def _send_ephemeral(content, **kwargs):
            return await interaction.followup.send(content, ephemeral=True, **kwargs)
        resolved_tags = await _resolve_missing_tags(
            bot, guild_id, tags, interaction.user.id, _send_ephemeral
        )
        parsed['tags'] = resolved_tags

    # カレンダー選択（複数ある場合のみ）
    all_tokens = bot.db_manager.get_all_oauth_tokens(guild_id)
    if len(all_tokens) > 1 and not parsed.get('calendar_name'):
        cal_view = CalendarSelectView(interaction.user.id, all_tokens)
        await interaction.followup.send("📅 どのカレンダーに登録しますか？", view=cal_view, ephemeral=True)
        await cal_view.wait()
        if cal_view.selected_calendar_owner:
            parsed['calendar_name'] = cal_view.selected_display_name
            parsed['_calendar_owner'] = cal_view.selected_calendar_owner
    elif len(all_tokens) == 1:
        parsed['_calendar_owner'] = all_tokens[0].get('_doc_id') or all_tokens[0].get('authenticated_by')

    summary = build_event_summary(parsed)
    ok = await confirm_action(interaction, "予定追加の確認", summary)
    if not ok:
        return "キャンセルしました。"
    return await handle_add_event(bot, interaction, parsed)

async def confirm_and_handle_edit_event(bot: CalendarBot, interaction: discord.Interaction, parsed: Dict[str, Any]) -> Optional[str]:
    guild_id = str(interaction.guild_id) if interaction.guild_id else ""

    # 未登録タグの確認・自動作成（タグが変更される場合のみ）
    if 'tags' in parsed:
        tags = parsed.get('tags', []) or []
        if tags:
            async def _send_ephemeral(content, **kwargs):
                return await interaction.followup.send(content, ephemeral=True, **kwargs)
            resolved_tags = await _resolve_missing_tags(
                bot, guild_id, tags, interaction.user.id, _send_ephemeral
            )
            parsed['tags'] = resolved_tags

    events = bot.db_manager.search_events_by_name(parsed.get('event_name'), guild_id)
    if not events:
        return f"❌ 予定「{parsed.get('event_name')}」が見つかりませんでした。"
    event = events[0]
    if len(events) > 1:
        note = "同名が複数あるため、先頭の予定を対象にします。"
    else:
        note = ""
    summary = (
        f"対象: {event['event_name']} (ID {event['id']})\n"
        f"{build_event_summary(parsed)}\n"
        f"{note}"
    )
    ok = await confirm_action(interaction, "予定編集の確認", summary)
    if not ok:
        return "キャンセルしました。"
    return await handle_edit_event(bot, interaction, parsed)

async def confirm_and_handle_delete_event(bot: CalendarBot, interaction: discord.Interaction, parsed: Dict[str, Any]) -> Optional[str]:
    guild_id = str(interaction.guild_id) if interaction.guild_id else ""
    events = bot.db_manager.search_events_by_name(parsed.get('event_name'), guild_id)
    if not events:
        return f"❌ 予定「{parsed.get('event_name')}」が見つかりませんでした。"
    event = events[0]
    if len(events) > 1:
        note = "同名が複数あるため、先頭の予定を対象にします。"
    else:
        note = ""
    summary = (
        f"対象: {event['event_name']} (ID {event['id']})\n"
        f"繰り返し: {RECURRENCE_TYPES.get(event['recurrence'], event['recurrence'])}\n"
        f"{note}"
    )
    ok = await confirm_action(interaction, "予定削除の確認", summary)
    if not ok:
        return "キャンセルしました。"
    return await handle_delete_event(bot, interaction, parsed)

def get_date_range(range_str: str) -> Tuple[datetime, datetime]:
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if range_str == 'today':
        return today, today.replace(hour=23, minute=59, second=59)
    elif range_str == 'this_week':
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6, hours=23, minutes=59)
    elif range_str == 'next_week':
        start = today - timedelta(days=today.weekday()) + timedelta(weeks=1)
        return start, start + timedelta(days=6, hours=23, minutes=59)
    elif range_str == 'this_month':
        start = today.replace(day=1)
        _, last_day = calendar.monthrange(start.year, start.month)
        return start, start.replace(day=last_day, hour=23, minute=59)
    else:
        # デフォルトは今週
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6, hours=23, minutes=59)

def create_weekly_embed(events: List[Dict[str, Any]]) -> discord.Embed:
    embed = discord.Embed(
        title="📅 今週の予定",
        color=discord.Color.blue(),
        timestamp=datetime.now()
    )

    if not events:
        embed.description = "今週の予定はありません。"
        return embed

    events_by_day = {}
    for event in events:
        day = event['date']
        if day not in events_by_day:
            events_by_day[day] = []
        events_by_day[day].append(event)

    for day, day_events in sorted(events_by_day.items()):
        day_str = datetime.strptime(day, '%Y-%m-%d').strftime('%m/%d (%a)')

        event_lines = []
        for evt in day_events:
            time_str = evt['time'] if evt['time'] else '時刻未定'
            tags = json.loads(evt['tags']) if isinstance(evt['tags'], str) else evt['tags']
            tags_str = f" [{', '.join(tags)}]" if tags else ""
            event_lines.append(f"⏰ {time_str} - {evt['event_name']}{tags_str}")

        embed.add_field(
            name=day_str,
            value='\n'.join(event_lines),
            inline=False
        )

    embed.set_footer(text="予定の追加・管理は /予定 コマンドから")
    return embed

def create_event_list_embed(events: List[Dict[str, Any]]) -> discord.Embed:
    embed = discord.Embed(
        title="📋 登録されている繰り返し予定",
        color=discord.Color.green()
    )

    if not events:
        embed.description = "登録されている予定がありません。"
        return embed

    for event in events:
        recurrence_str = RECURRENCE_TYPES.get(event['recurrence'], event['recurrence'])

        if event['recurrence'] == 'nth_week':
            nth_weeks = json.loads(event['nth_weeks']) if isinstance(event['nth_weeks'], str) else event['nth_weeks']
            nth_str = '・'.join([f"第{n}" for n in nth_weeks])
            recurrence_str = f"{nth_str}週"

        weekdays = ['月', '火', '水', '木', '金', '土', '日']
        weekday_str = weekdays[event['weekday']] if event['weekday'] is not None else ""
        time_str = event['time'] if event['time'] else '時刻未定'

        tags = json.loads(event['tags']) if isinstance(event['tags'], str) else event['tags']
        tags_str = f"\n🏷️ {', '.join(tags)}" if tags else ""

        embed.add_field(
            name=f"{event['event_name']}",
            value=(
                f"🔄 {recurrence_str}{weekday_str}曜日\n"
                f"⏰ {time_str}"
                f"{tags_str}"
            ),
            inline=True
        )

    return embed

def create_search_result_embed(events: List[Dict[str, Any]], start_date: datetime, end_date: datetime) -> discord.Embed:
    embed = discord.Embed(
        title="🔍 検索結果",
        description=f"{start_date.strftime('%Y/%m/%d')} - {end_date.strftime('%Y/%m/%d')}",
        color=discord.Color.purple()
    )

    for event in events[:10]:
        date_str = datetime.strptime(event['date'], '%Y-%m-%d').strftime('%m/%d (%a)')
        time_str = event['time'] if event['time'] else '時刻未定'

        embed.add_field(
            name=f"{date_str} {time_str}",
            value=f"{event['event_name']}",
            inline=False
        )

    if len(events) > 10:
        embed.set_footer(text=f"他 {len(events) - 10} 件の予定があります")

    return embed

def create_help_embed() -> discord.Embed:
    embed = discord.Embed(
        title="📘 VRC Calendar Bot ヘルプ",
        color=discord.Color.teal()
    )
    embed.add_field(
        name="/予定",
        value=(
            "自然言語で予定の追加/編集/削除/検索を行います。\n"
            "情報が不足している場合はスレッドで対話的に情報を収集します。"
        ),
        inline=False
    )
    embed.add_field(
        name="/今週の予定 /予定一覧",
        value="今週の予定や繰り返し予定の一覧を表示します。",
        inline=False
    )
    embed.add_field(
        name="/色",
        value="`/色 初期設定` `/色 一覧` `/色 追加` `/色 削除`",
        inline=False
    )
    embed.add_field(
        name="/タグ",
        value="`/タグ 一覧` `/タグ グループ追加` `/タグ グループ削除` `/タグ 追加` `/タグ 削除`",
        inline=False
    )
    embed.add_field(
        name="/カレンダー",
        value=(
            "`認証` - Googleカレンダーと連携\n"
            "`設定` - 表示名・説明・デフォルトを変更\n"
            "`一覧` - 認証済みカレンダー一覧\n"
            "`認証解除` `/認証状態`\n"
            "※ サーバー管理権限が必要"
        ), inline=False
    )
    embed.add_field(
        name="🚀 初回セットアップ",
        value=(
            "1. `/カレンダー 認証` でGoogleカレンダーを連携\n"
            "2. `/色 初期設定` でデフォルト色を設定\n"
            "3. `/予定 毎週土曜21時にVRC集会` で登録！"
        ), inline=False
    )
    embed.add_field(
        name="📚 ドキュメント",
        value=(
            "[使い方ガイド](https://github.com/terafon/VRC_Calendar_Discord_bot/blob/main/docs/USAGE.md)\n"
            "[仕様書](https://github.com/terafon/VRC_Calendar_Discord_bot/blob/main/docs/SPECIFICATION.md)"
        ), inline=False
    )
    embed.add_field(
        name="/通知",
        value=(
            "`/通知 設定` - 週次通知のスケジュールを設定\n"
            "`/通知 停止` `/通知 状態`\n"
            "※ サーバー管理権限が必要"
        ), inline=False
    )
    return embed

def create_tag_group_list_embed(groups: List[Dict[str, Any]], tags: List[Dict[str, Any]]) -> discord.Embed:
    embed = discord.Embed(title="🏷️ タググループ", color=discord.Color.green())
    if not groups:
        embed.description = "タググループがありません。"
        return embed
    tags_by_group: Dict[int, List[Dict[str, Any]]] = {}
    for tag in tags:
        tags_by_group.setdefault(tag['group_id'], []).append(tag)
    for group in groups:
        group_tags = tags_by_group.get(group['id'], [])
        tag_lines = [t['name'] for t in group_tags] if group_tags else ["(タグなし)"]
        embed.add_field(
            name=f"{group['id']}: {group['name']}",
            value="\n".join(tag_lines),
            inline=False
        )
    return embed

def _upsert_legend_event(cal_mgr, db_manager, legend_key: str, legend_event_id: str, summary: str, description: str):
    """凡例イベントの作成/更新共通処理。既存イベントが見つからない場合は新規作成する。"""
    legend_start = "2026-01-01"
    legend_end = "2030-12-31"
    event_body = {
        "summary": summary,
        "description": description,
        "colorId": LEGEND_COLOR_ID,
        "start": {"date": legend_start},
        "end": {"date": legend_end},
    }

    # 既存イベントの更新を試行
    if legend_event_id:
        try:
            cal_mgr.update_event(legend_event_id, event_body)
            return
        except Exception as e:
            # イベントが削除済み等で更新失敗 → 新規作成にフォールバック
            print(f"Legend event update failed, will recreate ({legend_key}): {e}")

    # 新規作成
    try:
        event = cal_mgr.service.events().insert(
            calendarId=cal_mgr.calendar_id, body=event_body
        ).execute()
        db_manager.update_setting(legend_key, event['id'])
    except Exception as e:
        print(f"Legend event create failed ({legend_key}): {e}")


async def _update_color_legend_for_user(bot: CalendarBot, guild_id: str, user_id: str):
    """色凡例イベントを更新（カレンダー単位）"""
    presets = bot.db_manager.list_color_presets(guild_id, user_id)

    cat_labels = {c["key"]: c["label"] for c in COLOR_CATEGORIES}
    lines = ["═══ 色プリセット一覧 ═══", ""]
    if presets:
        for p in presets:
            cid = p['color_id']
            emoji = COLOR_EMOJI.get(cid, "")
            color_name = GOOGLE_CALENDAR_COLORS.get(cid, {}).get('name', '?')
            rt = p.get('recurrence_type')
            rt_label = f" → {cat_labels.get(rt, rt)}" if rt else ""
            desc = f"({p['description']})" if p.get('description') else ""
            lines.append(f"{emoji} {color_name} (colorId {cid}){rt_label} {desc}")
    else:
        lines.append("登録なし")

    description = "\n".join(lines)
    summary = "🎨 色プリセット凡例"

    cal_mgr = bot.get_calendar_manager_for_user(int(guild_id), user_id)
    if not cal_mgr:
        return

    legend_key = f"legend_color_event_id:{guild_id}:{user_id}"
    legend_event_id = bot.db_manager.get_setting(legend_key, "")

    _upsert_legend_event(cal_mgr, bot.db_manager, legend_key, legend_event_id, summary, description)


async def _update_tag_legend_for_user(bot: CalendarBot, guild_id: str, user_id: str):
    """タグ凡例イベントを更新（カレンダー単位）"""
    groups = bot.db_manager.list_tag_groups(guild_id)
    tags = bot.db_manager.list_tags(guild_id)

    lines = ["═══ タググループ一覧 ═══", ""]
    tags_by_group: Dict[int, List[Dict[str, Any]]] = {}
    for tag in tags:
        tags_by_group.setdefault(tag['group_id'], []).append(tag)
    for group in groups:
        lines.append(f"【{group['name']}】{group.get('description','')}")
        for t in tags_by_group.get(group['id'], []):
            lines.append(f"  ・{t['name']}: {t.get('description','')}")
    if not groups:
        lines.append("登録なし")

    description = "\n".join(lines)
    summary = "🏷️ タグ凡例"

    cal_mgr = bot.get_calendar_manager_for_user(int(guild_id), user_id)
    if not cal_mgr:
        return

    legend_key = f"legend_tag_event_id:{guild_id}:{user_id}"
    legend_event_id = bot.db_manager.get_setting(legend_key, "")

    _upsert_legend_event(cal_mgr, bot.db_manager, legend_key, legend_event_id, summary, description)


async def _update_legend_event_for_user(bot: CalendarBot, guild_id: str, user_id: str):
    """後方互換: 色・タグ両方の凡例を更新"""
    await _update_color_legend_for_user(bot, guild_id, user_id)
    await _update_tag_legend_for_user(bot, guild_id, user_id)


async def _update_legend_event_by_guild(bot: CalendarBot, guild_id: str):
    """guild_idベースで凡例イベントを全認証カレンダーに更新"""
    all_tokens = bot.db_manager.get_all_oauth_tokens(guild_id)
    for token_data in all_tokens:
        user_id = token_data.get("_doc_id") or token_data.get("authenticated_by")
        if user_id == "google":
            user_id = token_data.get("authenticated_by", "")
        if not user_id:
            continue
        await _update_color_legend_for_user(bot, guild_id, user_id)
        await _update_tag_legend_for_user(bot, guild_id, user_id)

    # 旧凡例イベントのマイグレーション（旧キーが存在する場合は削除して新キーに移行）
    for token_data in all_tokens:
        user_id = token_data.get("_doc_id") or token_data.get("authenticated_by")
        if user_id == "google":
            user_id = token_data.get("authenticated_by", "")
        if not user_id:
            continue
        old_key = f"legend_event_id:{guild_id}:{user_id}"
        old_event_id = bot.db_manager.get_setting(old_key, "")
        if old_event_id:
            cal_mgr = bot.get_calendar_manager_for_user(int(guild_id), user_id)
            if cal_mgr:
                try:
                    cal_mgr.service.events().delete(
                        calendarId=cal_mgr.calendar_id, eventId=old_event_id
                    ).execute()
                except Exception:
                    pass
            bot.db_manager.update_setting(old_key, "")


async def update_legend_event(bot: CalendarBot, interaction: discord.Interaction):
    guild_id = str(interaction.guild_id) if interaction.guild_id else ""
    all_tokens = bot.db_manager.get_all_oauth_tokens(guild_id)
    if not all_tokens:
        await interaction.followup.send("❌ カレンダーが未認証です。`/カレンダー 認証` を実行してください。", ephemeral=True)
        return
    await _update_legend_event_by_guild(bot, guild_id)


def _rebuild_expected_event(
    bot: CalendarBot, guild_id: str, event: Dict[str, Any], cal_owner: str
) -> Dict[str, Any]:
    """Firestoreイベントデータから「Google Calendarイベントのあるべき姿」を構築する"""
    tags = json.loads(event.get('tags') or '[]')
    tag_groups = bot.db_manager.list_tag_groups(guild_id)

    description = _build_event_description(
        raw_description=event.get('description', ''),
        tags=tags if tags else None,
        tag_groups=tag_groups if tags and tag_groups else None,
        x_url=event.get('x_url'),
        vrc_group_url=event.get('vrc_group_url'),
        official_url=event.get('official_url'),
    )

    result: Dict[str, Any] = {
        'summary': event.get('event_name', ''),
        'description': description,
    }

    color_name = event.get('color_name')
    if color_name and cal_owner:
        preset = bot.db_manager.get_color_preset(guild_id, cal_owner, color_name)
        if preset:
            result['colorId'] = preset['color_id']

    return result


def _recreate_calendar_event(
    bot: CalendarBot, guild_id: str, event: Dict[str, Any],
    cal_mgr: 'GoogleCalendarManager', cal_owner: str
) -> Optional[str]:
    """削除されたイベントをGoogle Calendarに再作成し、新しいイベントIDをFirestoreに保存する"""
    recurrence = event.get('recurrence', '')
    if recurrence == 'irregular':
        # 不定期イベントは Google Calendar イベントなしのためスキップ
        return None

    weekday = event.get('weekday')
    time_str = event.get('time')
    if weekday is None or not time_str:
        return None

    nth_weeks = json.loads(event['nth_weeks']) if event.get('nth_weeks') else []

    expected = _rebuild_expected_event(bot, guild_id, event, cal_owner)

    color_name = event.get('color_name')
    color_id = None
    if color_name and cal_owner:
        preset = bot.db_manager.get_color_preset(guild_id, cal_owner, color_name)
        if preset:
            color_id = preset['color_id']

    tags = json.loads(event.get('tags') or '[]')

    try:
        rrule = RecurrenceCalculator.to_rrule(
            recurrence=recurrence,
            nth_weeks=nth_weeks,
            weekday=weekday,
        )
        start_dt = _next_weekday_datetime(
            weekday, time_str,
            recurrence=recurrence, nth_weeks=nth_weeks,
        )
        duration_minutes = event.get('duration_minutes', 60)
        end_dt = start_dt + timedelta(minutes=duration_minutes)

        google_event_id = cal_mgr.create_recurring_event(
            summary=expected['summary'],
            start_datetime=start_dt,
            end_datetime=end_dt,
            rrule=rrule,
            description=expected['description'],
            color_id=color_id,
            extended_props={
                "tags": json.dumps(tags, ensure_ascii=False),
                "color_name": color_name or "",
                "x_url": event.get('x_url') or "",
                "vrc_group_url": event.get('vrc_group_url') or "",
                "official_url": event.get('official_url') or "",
            },
        )

        bot.db_manager.update_google_calendar_events(
            event['id'],
            [{"event_id": google_event_id, "rrule": rrule}]
        )

        return google_event_id
    except Exception as e:
        print(f"[sync] Failed to recreate event {event.get('id')}: {e}")
        return None
