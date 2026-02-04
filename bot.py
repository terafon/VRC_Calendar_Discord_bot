import discord
from discord import app_commands
from discord.ext import commands
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

RECURRENCE_TYPES = {
    "weekly": "毎週",
    "biweekly": "隔週",
    "nth_week": "第n週",
    "irregular": "不定期"
}

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

    def get_calendar_manager_for_guild(self, guild_id: Optional[int]) -> Optional[GoogleCalendarManager]:
        if guild_id is None:
            return None

        guild_id_str = str(guild_id)
        oauth_tokens = self.db_manager.get_oauth_tokens(guild_id_str)
        if not oauth_tokens or not self.oauth_handler:
            return None

        try:
            def on_token_refresh(new_access_token: str, new_expiry: str):
                self.db_manager.update_oauth_access_token(guild_id_str, new_access_token, new_expiry)

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
            print(f"OAuth token error for guild {guild_id_str}: {e}")
            return None
    
    async def setup_hook(self):
        """起動時の初期化処理"""
        await self.tree.sync()
        print(f'{self.user} is ready!')
    
    async def on_ready(self):
        """Bot起動完了時"""
        print(f'Logged in as {self.user}')

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
            # 自然言語処理
            parsed = bot.nlp_processor.parse_user_message(メッセージ)
            
            # アクションに応じた処理
            if parsed['action'] == 'add':
                result = await confirm_and_handle_add_event(bot, interaction, parsed)
            elif parsed['action'] == 'edit':
                result = await confirm_and_handle_edit_event(bot, interaction, parsed)
            elif parsed['action'] == 'delete':
                result = await confirm_and_handle_delete_event(bot, interaction, parsed)
            elif parsed['action'] == 'search':
                result = await handle_search_event(bot, interaction, parsed)
            else:
                result = "アクションを認識できませんでした。"
            
            if result:
                await interaction.followup.send(result)
            
        except Exception as e:
            await interaction.followup.send(
                f"エラーが発生しました: {str(e)}",
                ephemeral=True
            )

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

    @bot.tree.command(name="色一覧", description="色プリセットとGoogleカレンダー色パレットを表示します")
    async def color_list_command(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        presets = bot.db_manager.list_color_presets(guild_id)
        cal_mgr = bot.get_calendar_manager_for_guild(interaction.guild_id)
        palette = cal_mgr.get_color_palette() if cal_mgr else {}
        embed = create_color_list_embed(presets, palette)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @bot.tree.command(name="色追加", description="色プリセットを追加/更新します")
    @app_commands.describe(名前="色名", color_id="GoogleカレンダーのcolorId", 説明="色の説明")
    async def color_add_command(interaction: discord.Interaction, 名前: str, color_id: str, 説明: str = ""):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        bot.db_manager.add_color_preset(guild_id, 名前, color_id, 説明)
        await update_legend_event(bot, interaction)
        await interaction.followup.send(f"✅ 色プリセット「{名前}」を設定しました。", ephemeral=True)

    @bot.tree.command(name="色削除", description="色プリセットを削除します")
    @app_commands.describe(名前="色名")
    async def color_delete_command(interaction: discord.Interaction, 名前: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        bot.db_manager.delete_color_preset(guild_id, 名前)
        await update_legend_event(bot, interaction)
        await interaction.followup.send(f"✅ 色プリセット「{名前}」を削除しました。", ephemeral=True)

    @bot.tree.command(name="タググループ一覧", description="タググループを表示します")
    async def tag_group_list_command(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        groups = bot.db_manager.list_tag_groups(guild_id)
        tags = bot.db_manager.list_tags(guild_id)
        embed = create_tag_group_list_embed(groups, tags)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @bot.tree.command(name="タググループ追加", description="タググループを追加します（最大3つ）")
    @app_commands.describe(名前="グループ名", 説明="グループの説明")
    async def tag_group_add_command(interaction: discord.Interaction, 名前: str, 説明: str = ""):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        bot.db_manager.add_tag_group(guild_id, 名前, 説明)
        await update_legend_event(bot, interaction)
        await interaction.followup.send(f"✅ タググループ「{名前}」を追加しました。", ephemeral=True)

    @bot.tree.command(name="タググループ削除", description="タググループを削除します")
    @app_commands.describe(id="グループID")
    async def tag_group_delete_command(interaction: discord.Interaction, id: int):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        bot.db_manager.delete_tag_group(guild_id, id)
        await update_legend_event(bot, interaction)
        await interaction.followup.send(f"✅ タググループID {id} を削除しました。", ephemeral=True)

    @bot.tree.command(name="タグ追加", description="タグを追加/更新します")
    @app_commands.describe(group_id="グループID", 名前="タグ名", 説明="タグの説明")
    async def tag_add_command(interaction: discord.Interaction, group_id: int, 名前: str, 説明: str = ""):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        bot.db_manager.add_tag(guild_id, group_id, 名前, 説明)
        await update_legend_event(bot, interaction)
        await interaction.followup.send(f"✅ タグ「{名前}」を追加しました。", ephemeral=True)

    @bot.tree.command(name="タグ削除", description="タグを削除します")
    @app_commands.describe(group_id="グループID", 名前="タグ名")
    async def tag_delete_command(interaction: discord.Interaction, group_id: int, 名前: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        bot.db_manager.delete_tag(guild_id, group_id, 名前)
        await update_legend_event(bot, interaction)
        await interaction.followup.send(f"✅ タグ「{名前}」を削除しました。", ephemeral=True)

    @bot.tree.command(name="凡例更新", description="色/タグの凡例イベントを更新します")
    async def legend_update_command(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await update_legend_event(bot, interaction)
        await interaction.followup.send("✅ 凡例イベントを更新しました。", ephemeral=True)

    @bot.tree.command(name="カレンダー設定", description="使用するカレンダーIDを設定します")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(calendar_id="GoogleカレンダーID（例: abc123@group.calendar.google.com）")
    async def calendar_set_command(interaction: discord.Interaction, calendar_id: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild_id)
        oauth_tokens = bot.db_manager.get_oauth_tokens(guild_id)
        if not oauth_tokens:
            await interaction.followup.send("❌ OAuth 認証がされていません。先に `/カレンダー認証` を実行してください。", ephemeral=True)
            return
        bot.db_manager.update_oauth_calendar_id(guild_id, calendar_id)
        await interaction.followup.send(f"✅ カレンダーIDを `{calendar_id}` に設定しました。", ephemeral=True)

    @bot.tree.command(name="カレンダー認証", description="Google OAuth認証でカレンダーを連携します")
    @app_commands.checks.has_permissions(manage_guild=True)
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
                "以下のリンクをクリックして Google アカウントでカレンダーへのアクセスを許可してください。\n\n"
                f"[認証ページを開く]({auth_url})\n\n"
                "認証が完了するとブラウザに「認証成功」と表示されます。"
            ),
            color=discord.Color.blue(),
        )
        embed.set_footer(text="このリンクは一度だけ使用できます")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @bot.tree.command(name="カレンダー認証解除", description="Google OAuth認証を解除します")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def calendar_oauth_revoke_command(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild_id)
        tokens = bot.db_manager.get_oauth_tokens(guild_id)
        if not tokens:
            await interaction.followup.send("ℹ️ OAuth 認証は設定されていません。", ephemeral=True)
            return

        bot.db_manager.delete_oauth_tokens(guild_id)
        await interaction.followup.send("✅ Google OAuth 認証を解除しました。", ephemeral=True)

    @bot.tree.command(name="カレンダー認証状態", description="カレンダーの認証状態を表示します")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def calendar_oauth_status_command(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild_id)
        oauth_tokens = bot.db_manager.get_oauth_tokens(guild_id)

        embed = discord.Embed(title="カレンダー認証状態", color=discord.Color.blue())

        if oauth_tokens:
            authenticated_by = oauth_tokens.get('authenticated_by', '不明')
            authenticated_at = oauth_tokens.get('authenticated_at', '不明')
            calendar_id = oauth_tokens.get('calendar_id', 'primary')
            embed.add_field(name="方式", value="OAuth 2.0（ユーザー認証）", inline=False)
            embed.add_field(name="認証者", value=f"<@{authenticated_by}>", inline=True)
            embed.add_field(name="認証日時", value=authenticated_at, inline=True)
            embed.add_field(name="カレンダーID", value=calendar_id, inline=False)
        else:
            embed.add_field(name="状態", value="未認証", inline=False)
            embed.add_field(name="説明", value="`/カレンダー認証` を実行して OAuth 認証を行ってください。", inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

async def handle_add_event(bot: CalendarBot, interaction: discord.Interaction, parsed: Dict[str, Any]) -> str:
    """予定追加処理"""
    guild_id = str(interaction.guild_id) if interaction.guild_id else ""

    # タグと色のバリデーション
    tags = parsed.get('tags', []) or []
    missing_tags = bot.db_manager.find_missing_tags(guild_id, tags)
    if missing_tags:
        return f"❌ 未登録のタグがあります: {', '.join(missing_tags)}"

    color_name = parsed.get('color_name')
    color_id = None
    if color_name:
        preset = bot.db_manager.get_color_preset(guild_id, color_name)
        if not preset:
            return f"❌ 色名「{color_name}」が登録されていません。"
        color_id = preset['color_id']

    urls = parsed.get('urls', []) or []

    # 説明欄にURLを追記
    description = parsed.get('description', '')
    if urls:
        url_lines = "\n".join(urls)
        description = f"{description}\n\nURLs:\n{url_lines}".strip()

    # データベースに保存
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
        description=description,
        color_name=color_name,
        urls=urls,
        discord_channel_id=str(interaction.channel_id),
        created_by=str(interaction.user.id)
    )
    
    cal_mgr = bot.get_calendar_manager_for_guild(interaction.guild_id)
    if not cal_mgr:
        return "❌ カレンダーが未認証です。`/カレンダー認証` を実行してください。"

    # 不定期以外の場合、Googleカレンダーに登録
    if parsed['recurrence'] != 'irregular':
        # 日付計算
        dates = RecurrenceCalculator.calculate_dates(
            recurrence=parsed['recurrence'],
            nth_weeks=parsed.get('nth_weeks'),
            weekday=parsed['weekday'],
            start_date=datetime.now(),
            months_ahead=3
        )
        
        # Googleカレンダーに登録
        google_events = cal_mgr.create_events(
            event_name=parsed['event_name'],
            dates=dates,
            time_str=parsed['time'],
            duration_minutes=parsed.get('duration_minutes', 60),
            description=description,
            tags=tags,
            color_id=color_id,
            extended_props={
                "tags": json.dumps(tags, ensure_ascii=False),
                "color_name": color_name or "",
                "urls": json.dumps(urls, ensure_ascii=False)
            }
        )
        
        # Googleイベント情報をDBに保存
        bot.db_manager.update_google_calendar_events(event_id, google_events)
        
        next_date = dates[0] if dates else None
        return (
            f"✅ 予定を登録しました！\n"
            f"📅 {parsed['event_name']}\n"
            f"🔄 {RECURRENCE_TYPES.get(parsed['recurrence'], parsed['recurrence'])}\n"
            f"⏰ {parsed.get('time', '時刻未設定')}\n"
            f"📌 次回: {next_date.strftime('%Y-%m-%d') if next_date else '未定'}"
        )
    else:
        return (
            f"✅ 不定期予定を登録しました！\n"
            f"📅 {parsed['event_name']}\n"
            f"個別の日時は `/予定 {parsed['event_name']} 1月25日14時` のように追加してください（※現在個別日時の追加はNLPで対応中）。"
        )

async def handle_edit_event(bot: CalendarBot, interaction: discord.Interaction, parsed: Dict[str, Any]) -> str:
    """予定編集処理"""
    guild_id = str(interaction.guild_id) if interaction.guild_id else ""
    events = bot.db_manager.search_events_by_name(parsed.get('event_name'), guild_id)

    if not events:
        return f"❌ 予定「{parsed.get('event_name')}」が見つかりませんでした。"

    if len(events) > 1:
        # TODO: 複数ある場合は選択UIを表示（MVPでは最初の一致を編集）
        pass

    event = events[0]

    # 更新内容を適用
    updates = {}
    if 'time' in parsed: updates['time'] = parsed['time']
    if 'event_type' in parsed: updates['event_type'] = parsed['event_type']
    if 'description' in parsed: updates['description'] = parsed['description']
    if 'tags' in parsed:
        tags = parsed.get('tags', []) or []
        missing_tags = bot.db_manager.find_missing_tags(guild_id, tags)
        if missing_tags:
            return f"❌ 未登録のタグがあります: {', '.join(missing_tags)}"
        updates['tags'] = tags
    if 'color_name' in parsed:
        color_name = parsed.get('color_name')
        if color_name:
            preset = bot.db_manager.get_color_preset(guild_id, color_name)
            if not preset:
                return f"❌ 色名「{color_name}」が登録されていません。"
        updates['color_name'] = color_name
    if 'urls' in parsed:
        updates['urls'] = parsed.get('urls', [])
    
    bot.db_manager.update_event(event['id'], updates)
    
    # Googleカレンダー更新（簡易版：時刻変更等の場合は再作成が望ましいが、ここではフィールド更新のみ）
    if event['google_calendar_events']:
        google_event_ids = [ge['event_id'] for ge in json.loads(event['google_calendar_events'])]
        
        google_updates = {}
        if 'event_name' in parsed: google_updates['summary'] = parsed['event_name']
        if 'description' in parsed:
            description = parsed['description']
            urls = updates.get('urls') if 'urls' in updates else None
            if urls:
                url_lines = "\n".join(urls)
                description = f"{description}\n\nURLs:\n{url_lines}".strip()
            google_updates['description'] = description
        if 'color_name' in updates:
            color_name = updates.get('color_name')
            color_id = None
            if color_name:
                preset = bot.db_manager.get_color_preset(guild_id, color_name)
                color_id = preset['color_id'] if preset else None
            if color_id:
                google_updates['colorId'] = color_id
        
        if google_updates:
            cal_mgr = bot.get_calendar_manager_for_guild(interaction.guild_id)
            if not cal_mgr:
                return "❌ カレンダーが未認証です。`/カレンダー認証` を実行してください。"
            bot_ext = {}
            if 'tags' in updates:
                bot_ext['tags'] = json.dumps(updates['tags'], ensure_ascii=False)
            if 'color_name' in updates:
                bot_ext['color_name'] = updates.get('color_name') or ""
            if 'urls' in updates:
                bot_ext['urls'] = json.dumps(updates['urls'], ensure_ascii=False)
            if bot_ext:
                google_updates['extendedProperties'] = {'private': bot_ext}
            cal_mgr.update_events(google_event_ids, google_updates)
        
    return f"✅ 予定「{event['event_name']}」を更新しました。"

async def handle_delete_event(bot: CalendarBot, interaction: discord.Interaction, parsed: Dict[str, Any]) -> str:
    """予定削除処理"""
    guild_id = str(interaction.guild_id) if interaction.guild_id else ""
    events = bot.db_manager.search_events_by_name(parsed.get('event_name'), guild_id)

    if not events:
        return f"❌ 予定「{parsed.get('event_name')}」が見つかりませんでした。"

    event = events[0]
    
    # Googleカレンダーから削除
    if event['google_calendar_events']:
        cal_mgr = bot.get_calendar_manager_for_guild(interaction.guild_id)
        if not cal_mgr:
            return "❌ カレンダーが未認証です。`/カレンダー認証` を実行してください。"
        google_event_ids = [ge['event_id'] for ge in json.loads(event['google_calendar_events'])]
        cal_mgr.delete_events(google_event_ids)
    
    # データベースから削除（論理削除）
    bot.db_manager.delete_event(event['id'])
    
    return f"✅ 予定「{event['event_name']}」を削除しました。"

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
    urls = parsed.get('urls', []) or []
    nth = parsed.get('nth_weeks')
    nth_str = f"第{','.join(str(n) for n in nth)}週" if nth else ""
    weekdays = ['月', '火', '水', '木', '金', '土', '日']
    weekday_val = parsed.get('weekday')
    weekday_str = weekdays[weekday_val] if isinstance(weekday_val, int) and 0 <= weekday_val <= 6 else "未設定"
    return (
        f"予定名: {parsed.get('event_name', '未設定')}\n"
        f"繰り返し: {RECURRENCE_TYPES.get(parsed.get('recurrence'), parsed.get('recurrence'))} {nth_str}\n"
        f"曜日: {weekday_str}\n"
        f"時刻: {parsed.get('time', '未設定')}\n"
        f"所要時間: {parsed.get('duration_minutes', 60)}分\n"
        f"色: {parsed.get('color_name', '未設定')}\n"
        f"タグ: {', '.join(tags) if tags else 'なし'}\n"
        f"URL: {', '.join(urls) if urls else 'なし'}\n"
        f"説明: {parsed.get('description', '')}"
    )

async def confirm_and_handle_add_event(bot: CalendarBot, interaction: discord.Interaction, parsed: Dict[str, Any]) -> Optional[str]:
    summary = build_event_summary(parsed)
    ok = await confirm_action(interaction, "予定追加の確認", summary)
    if not ok:
        return "キャンセルしました。"
    return await handle_add_event(bot, interaction, parsed)

async def confirm_and_handle_edit_event(bot: CalendarBot, interaction: discord.Interaction, parsed: Dict[str, Any]) -> Optional[str]:
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
        value="自然言語で予定の追加/編集/削除/検索を行います。必ず確認ダイアログが表示されます。",
        inline=False
    )
    embed.add_field(
        name="/今週の予定 /予定一覧",
        value="今週の予定や繰り返し予定の一覧を表示します。",
        inline=False
    )
    embed.add_field(
        name="色/タグ管理",
        value="`/色一覧` `/色追加` `/色削除` `/タググループ一覧` `/タググループ追加` `/タググループ削除` `/タグ追加` `/タグ削除`",
        inline=False
    )
    embed.add_field(
        name="凡例",
        value="`/凡例更新` で色とタグの凡例イベントを更新できます。",
        inline=False
    )
    embed.add_field(
        name="カレンダー",
        value=(
            "`/カレンダー認証` OAuth認証でユーザーのカレンダーに直接アクセス\n"
            "`/カレンダー認証解除` OAuth認証を解除\n"
            "`/カレンダー認証状態` 現在の認証方式を確認\n"
            "`/カレンダー設定` 使用するカレンダーIDを変更"
        ),
        inline=False
    )
    return embed

def create_color_list_embed(presets: List[Dict[str, Any]], palette: Dict[str, Any]) -> discord.Embed:
    embed = discord.Embed(title="🎨 色プリセット", color=discord.Color.blue())
    if presets:
        lines = [f"{p['name']} -> colorId {p['color_id']} ({p.get('description','')})" for p in presets]
        embed.add_field(name="登録済み", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="登録済み", value="なし", inline=False)

    event_colors = palette.get('event', {})
    if event_colors:
        sample = []
        for cid, info in sorted(event_colors.items(), key=lambda x: int(x[0])):
            sample.append(f"{cid}: {info.get('background')}")
        embed.add_field(name="GoogleカラーID", value="\n".join(sample[:20]), inline=False)
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

async def update_legend_event(bot: CalendarBot, interaction: discord.Interaction):
    guild_id = str(interaction.guild_id) if interaction.guild_id else ""
    groups = bot.db_manager.list_tag_groups(guild_id)
    tags = bot.db_manager.list_tags(guild_id)
    presets = bot.db_manager.list_color_presets(guild_id)

    lines = ["【色プリセット】"]
    if presets:
        for p in presets:
            lines.append(f"- {p['name']} (colorId {p['color_id']}): {p.get('description','')}")
    else:
        lines.append("- 登録なし")

    lines.append("\n【タググループ】")
    tags_by_group: Dict[int, List[Dict[str, Any]]] = {}
    for tag in tags:
        tags_by_group.setdefault(tag['group_id'], []).append(tag)
    for group in groups:
        lines.append(f"- {group['name']}: {group.get('description','')}")
        for tag in tags_by_group.get(group['id'], []):
            lines.append(f"  - {tag['name']}: {tag.get('description','')}")
    if not groups:
        lines.append("- 登録なし")

    description = "\n".join(lines)
    summary = "色/タグ 凡例"

    legend_key = f"legend_event_id:{interaction.guild_id}"
    legend_event_id = bot.db_manager.get_setting(legend_key, "")
    cal_mgr = bot.get_calendar_manager_for_guild(interaction.guild_id)
    if not cal_mgr:
        await interaction.followup.send("❌ カレンダーが未認証です。`/カレンダー認証` を実行してください。", ephemeral=True)
        return

    if legend_event_id:
        cal_mgr.update_event(legend_event_id, {
            "summary": summary,
            "description": description
        })
    else:
        start_date = datetime(2000, 1, 1)
        end_date = datetime(2100, 1, 1)
        event_body = {
            "summary": summary,
            "description": description,
            "start": {"date": start_date.strftime('%Y-%m-%d')},
            "end": {"date": end_date.strftime('%Y-%m-%d')}
        }
        event = cal_mgr.service.events().insert(
            calendarId=cal_mgr.calendar_id,
            body=event_body
        ).execute()
        bot.db_manager.update_setting(legend_key, event['id'])

