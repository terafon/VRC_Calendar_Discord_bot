import os
import asyncio
import threading
from flask import Flask, request
import base64
import json
from dotenv import load_dotenv
from datetime import datetime
from typing import Optional

from bot import CalendarBot, setup_commands, create_weekly_embed
from nlp_processor import NLPProcessor
from calendar_manager import GoogleCalendarManager
from database_manager import DatabaseManager
from storage_backup import StorageBackup
from google.cloud import secretmanager

# 環境変数の読み込み
load_dotenv()

def get_secret(secret_id: str, default: Optional[str] = None) -> Optional[str]:
    """Secret Managerからシークレットを取得"""
    project_id = os.getenv('GCP_PROJECT_ID')
    if not project_id:
        return os.getenv(secret_id, default)

    try:
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode('UTF-8')
    except Exception as e:
        print(f"Secret Manager error for {secret_id}: {e}")
        return os.getenv(secret_id, default)

app = Flask(__name__)

# 各種インスタンスの初期化
db_manager = DatabaseManager('calendar.db')
# 各種APIキーをSecret Managerまたは環境変数から取得
gemini_api_key = get_secret('GEMINI_API_KEY')
discord_bot_token = get_secret('DISCORD_BOT_TOKEN')
nlp_processor = NLPProcessor(gemini_api_key)
calendar_manager = GoogleCalendarManager(
    credentials_path=os.getenv('GOOGLE_APPLICATION_CREDENTIALS', 'credentials.json'),
    calendar_id=os.getenv('GOOGLE_CALENDAR_ID', 'primary')
)
backup_manager = StorageBackup(
    bucket_name=os.getenv('GCS_BUCKET_NAME'),
    db_path='calendar.db'
)

# Discord Bot
bot = CalendarBot(
    nlp_processor,
    calendar_manager,
    db_manager,
    default_credentials_path=os.getenv('GOOGLE_APPLICATION_CREDENTIALS', 'credentials.json'),
    default_calendar_id=os.getenv('GOOGLE_CALENDAR_ID', 'primary')
)
setup_commands(bot)

# Bot用の非同期イベントループ（スレッド間で共有）
bot_loop: Optional[asyncio.AbstractEventLoop] = None
bot_ready = threading.Event()

@app.route('/health', methods=['GET'])
def health_check():
    return 'OK', 200

@app.route('/weekly-notification', methods=['POST'])
def weekly_notification_handler():
    """週次通知のPub/Subハンドラー"""
    envelope = request.get_json()
    
    if not envelope:
        return 'Bad Request: no Pub/Sub message received', 400
    
    # Pub/Subメッセージの検証
    if not isinstance(envelope, dict) or 'message' not in envelope:
        return 'Bad Request: invalid Pub/Sub message format', 400
    
    # メッセージデータをデコード（必要に応じて）
    pubsub_message = envelope['message']
    if isinstance(pubsub_message, dict) and 'data' in pubsub_message:
        try:
            message_data = base64.b64decode(pubsub_message['data']).decode('utf-8')
            print(f"Received Pub/Sub message: {message_data}")
        except Exception as e:
            print(f"Error decoding Pub/Sub message: {e}")
    
    # Botの準備完了を待機
    if not bot_ready.wait(timeout=30):
        return 'Bot not ready', 503

    # 非同期で通知を送信
    if bot_loop:
        asyncio.run_coroutine_threadsafe(send_weekly_notifications(), bot_loop)
    return '', 204

async def send_weekly_notifications():
    """週次通知を全チャンネルに送信"""
    # Botが準備できるまで待機
    await bot.wait_until_ready()
    
    # 今週の予定を取得
    events = bot.db_manager.get_this_week_events()
    
    # チャンネルごとにグループ化
    channels = set()
    for event in events:
        if event.get('discord_channel_id'):
            channels.add(event['discord_channel_id'])
    
    # 各チャンネルに通知
    for channel_id in channels:
        try:
            channel = await bot.fetch_channel(int(channel_id))
            if not channel:
                continue
            
            # そのチャンネルの予定のみフィルタ
            channel_events = [
                e for e in events
                if e.get('discord_channel_id') == channel_id
            ]
            
            embed = create_weekly_embed(channel_events)
            await channel.send(content="🔔 **今週の予定通知**", embed=embed)
            
        except Exception as e:
            print(f'Failed to send notification to channel {channel_id}: {e}')
    
    # 最終通知時刻を更新
    bot.db_manager.update_setting('last_notification_at', datetime.now().isoformat())

def run_discord_bot():
    """Discord Botを別スレッドで実行"""
    global bot_loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bot_loop = loop

    async def runner():
        async with bot:
            # on_readyが呼ばれたらbot_readyをセット
            @bot.event
            async def on_ready():
                print(f'Logged in as {bot.user}')
                bot_ready.set()

            await bot.start(discord_bot_token)

    try:
        loop.run_until_complete(runner())
    except Exception as e:
        print(f"Discord Bot error: {e}")
    finally:
        loop.close()

if __name__ == '__main__':
    # 起動時にGCSからDBを復元
    backup_manager.restore_from_cloud()
    
    # 定期バックアップをバックグラウンドで開始
    backup_manager.start_background_backup(interval_hours=6)
    
    # Discord Botをスレッドで開始
    bot_thread = threading.Thread(target=run_discord_bot, daemon=True)
    bot_thread.start()
    
    # Flaskサーバーをメインスレッドで実行（Cloud Run用）
    port = int(os.getenv('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
