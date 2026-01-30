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
from firestore_manager import FirestoreManager
from oauth_handler import OAuthHandler
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
db_manager = FirestoreManager(project_id=os.getenv('GCP_PROJECT_ID'))
# 各種APIキーをSecret Managerまたは環境変数から取得
gemini_api_key = get_secret('GEMINI_API_KEY')
discord_bot_token = get_secret('DISCORD_BOT_TOKEN')
nlp_processor = NLPProcessor(gemini_api_key)
calendar_manager = GoogleCalendarManager(
    credentials_path=os.getenv('GOOGLE_APPLICATION_CREDENTIALS', 'credentials.json'),
    calendar_id=os.getenv('GOOGLE_CALENDAR_ID', 'primary')
)

# OAuth Handler（環境変数未設定時は None）
oauth_client_id = get_secret('GOOGLE_OAUTH_CLIENT_ID')
oauth_client_secret = get_secret('GOOGLE_OAUTH_CLIENT_SECRET')
oauth_redirect_uri = os.getenv('OAUTH_REDIRECT_URI')

oauth_handler = None
if oauth_client_id and oauth_client_secret and oauth_redirect_uri:
    oauth_handler = OAuthHandler(oauth_client_id, oauth_client_secret, oauth_redirect_uri)
    print("OAuth handler initialized")
else:
    print("OAuth handler not configured (GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, OAUTH_REDIRECT_URI required)")

# Discord Bot
bot = CalendarBot(
    nlp_processor,
    calendar_manager,
    db_manager,
    default_credentials_path=os.getenv('GOOGLE_APPLICATION_CREDENTIALS', 'credentials.json'),
    default_calendar_id=os.getenv('GOOGLE_CALENDAR_ID', 'primary'),
    oauth_handler=oauth_handler,
)
setup_commands(bot)

# Bot用の非同期イベントループ（スレッド間で共有）
bot_loop: Optional[asyncio.AbstractEventLoop] = None
bot_ready = threading.Event()

@app.route('/health', methods=['GET'])
def health_check():
    return 'OK', 200

@app.route('/oauth/callback', methods=['GET'])
def oauth_callback():
    """Google OAuth コールバックエンドポイント"""
    if not oauth_handler:
        return _oauth_error_html("OAuth が設定されていません。"), 500

    error = request.args.get('error')
    if error:
        return _oauth_error_html(f"認証が拒否されました: {error}"), 400

    code = request.args.get('code')
    state = request.args.get('state')
    if not code or not state:
        return _oauth_error_html("不正なリクエストです。"), 400

    # state 検証（ワンタイム）
    state_data = db_manager.get_and_delete_oauth_state(state)
    if not state_data:
        return _oauth_error_html("認証セッションが無効または期限切れです。再度 /カレンダー認証 を実行してください。"), 400

    guild_id = state_data['guild_id']
    user_id = state_data['user_id']

    try:
        tokens = oauth_handler.exchange_code(code)
    except Exception as e:
        print(f"OAuth token exchange error: {e}")
        return _oauth_error_html("トークンの取得に失敗しました。再度お試しください。"), 500

    # Firestore に保存（calendar_id は primary をデフォルト、後でコマンドで変更可能）
    now = datetime.utcnow().isoformat()
    db_manager.save_oauth_tokens(
        guild_id=guild_id,
        access_token=tokens['access_token'],
        refresh_token=tokens['refresh_token'],
        token_expiry=tokens.get('token_expiry', ''),
        calendar_id='primary',
        authenticated_by=user_id,
        authenticated_at=now,
    )

    return _oauth_success_html(), 200


def _oauth_success_html() -> str:
    return """<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><title>認証成功</title>
<style>body{font-family:sans-serif;display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0;background:#2c2f33;color:#fff}
.card{background:#36393f;padding:2rem 3rem;border-radius:12px;text-align:center;box-shadow:0 4px 20px rgba(0,0,0,.3)}
h1{color:#43b581;margin-bottom:.5rem}p{color:#b9bbbe}</style></head>
<body><div class="card"><h1>認証成功</h1><p>Google カレンダーとの連携が完了しました。<br>このページを閉じて Discord に戻ってください。</p></div></body></html>"""


def _oauth_error_html(message: str) -> str:
    import html
    safe_msg = html.escape(message)
    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><title>認証エラー</title>
<style>body{{font-family:sans-serif;display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0;background:#2c2f33;color:#fff}}
.card{{background:#36393f;padding:2rem 3rem;border-radius:12px;text-align:center;box-shadow:0 4px 20px rgba(0,0,0,.3)}}
h1{{color:#f04747;margin-bottom:.5rem}}p{{color:#b9bbbe}}</style></head>
<body><div class="card"><h1>認証エラー</h1><p>{safe_msg}</p></div></body></html>"""

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
    """週次通知を全サーバー・チャンネルに送信"""
    # Botが準備できるまで待機
    await bot.wait_until_ready()

    # 各サーバー（ギルド）ごとに処理
    for guild in bot.guilds:
        guild_id = str(guild.id)

        # このサーバーの今週の予定を取得
        events = bot.db_manager.get_this_week_events(guild_id)

        if not events:
            continue

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
    # Discord Botをスレッドで開始
    bot_thread = threading.Thread(target=run_discord_bot, daemon=True)
    bot_thread.start()
    
    # Flaskサーバーをメインスレッドで実行（Cloud Run用）
    port = int(os.getenv('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
