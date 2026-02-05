import google.generativeai as genai
import json
import re
from typing import Dict, Any, Optional

# 既存の単発パース用プロンプト（後方互換）
SYSTEM_PROMPT = """
あなたはDiscord Calendar Botのアシスタントです。
ユーザーのメッセージから予定情報を抽出し、JSON形式で返してください。

# アクション種類
- add: 予定を新規追加
- edit: 既存予定を編集
- delete: 予定を削除
- search: 予定を検索

# 出力JSONスキーマ
{
  "action": "add|edit|delete|search",
  "event_name": "予定名",
  "tags": ["タグ1", "タグ2"],
  "recurrence": "weekly|biweekly|nth_week|irregular",
  "nth_weeks": [2, 4],
  "event_type": "種類",
  "time": "14:00",
  "weekday": 2,
  "duration_minutes": 60,
  "description": "説明",
  "color_name": "色名",
  "urls": ["https://example.com", "https://twitter.com/..."],
  "search_query": {
    "date_range": "today|this_week|next_week|this_month",
    "tags": ["タグ"],
    "event_name": "部分一致文字列"
  }
}

# 曜日マッピング
月曜: 0, 火曜: 1, 水曜: 2, 木曜: 3, 金曜: 4, 土曜: 5, 日曜: 6

# 例
入力: "第2・第4水曜日の14時に定例MTGを追加、タグはチームミーティング"
出力: {
  "action": "add",
  "event_name": "定例MTG",
  "tags": ["チームミーティング"],
  "recurrence": "nth_week",
  "nth_weeks": [2, 4],
  "weekday": 2,
  "time": "14:00",
  "duration_minutes": 60,
  "color_name": "青"
}

入力: "来週のチームミーティングを教えて"
出力: {
  "action": "search",
  "search_query": {
    "date_range": "next_week",
    "tags": ["チームミーティング"]
  }
}

必ずJSONのみを返し、余計な説明は含めないでください。
"""

# マルチターン会話用のシステムプロンプトテンプレート
CONVERSATION_SYSTEM_PROMPT = """あなたはVRChatイベント管理Discord Botのアシスタントです。
VRChat上のイベント（集会、ワールド紹介、アバター試着会など）をGoogleカレンダーに登録・管理する手伝いをします。

ユーザーとの対話を通じて予定情報を収集し、必要な情報が揃ったらJSON形式で返してください。

# アクション種類
- add: 予定を新規追加
- edit: 既存予定を編集
- delete: 予定を削除
- search: 予定を検索

# action=add の必須フィールド
- event_name: 予定名
- recurrence: 繰り返しパターン（weekly, biweekly, nth_week, irregular）
- weekday: 曜日（recurrence が irregular 以外の場合は必須）
- time: 開始時刻（HH:MM形式）

# 曜日マッピング
月曜: 0, 火曜: 1, 水曜: 2, 木曜: 3, 金曜: 4, 土曜: 5, 日曜: 6

# 出力JSONスキーマ
必ず以下の形式のJSONのみを返してください。余計な説明は含めないでください。

## 情報が不足している場合:
{{
  "status": "needs_info",
  "action": "add|edit|delete|search",
  "question": "ユーザーへの質問テキスト（フレンドリーな日本語で。利用可能な選択肢がある場合は箇条書きで表示）",
  "event_data": {{
    "event_name": "収集済みの予定名 or null",
    "tags": ["収集済みのタグ"] or null,
    "recurrence": "収集済みの繰り返しパターン or null",
    "nth_weeks": [2, 4] or null,
    "time": "収集済みの時刻 or null",
    "weekday": 5 or null,
    "duration_minutes": 60 or null,
    "description": "収集済みの説明 or null",
    "color_name": "収集済みの色名 or null",
    "urls": ["収集済みのURL"] or null
  }}
}}

## すべての必須情報が揃った場合:
{{
  "status": "complete",
  "action": "add|edit|delete|search",
  "event_data": {{
    "event_name": "予定名",
    "tags": ["タグ1"],
    "recurrence": "weekly|biweekly|nth_week|irregular",
    "nth_weeks": [2, 4],
    "time": "21:00",
    "weekday": 5,
    "duration_minutes": 60,
    "description": "説明",
    "color_name": "色名",
    "urls": ["https://..."]
  }},
  "search_query": {{
    "date_range": "today|this_week|next_week|this_month",
    "tags": ["タグ"],
    "event_name": "部分一致文字列"
  }}
}}

# 質問の仕方
- 一度に1つの質問をしてください
- 利用可能な選択肢がある場合は提示してください
- フレンドリーで親しみやすい日本語を使ってください
- 質問の順序: 開催頻度 → 曜日 → 時刻 → タグ（任意） → 色（任意）

{server_context}

# 重要なルール
- 必ずJSONのみを返してください。JSON以外のテキストは含めないでください。
- search アクションの場合は event_data は不要です。search_query を含めてください。
- delete/edit アクションの場合は event_name が分かれば status: complete にしてください。
- duration_minutes のデフォルトは 60 です。ユーザーが指定しなければ 60 を設定してください。
"""


def _build_server_context(server_context: Optional[Dict[str, Any]] = None) -> str:
    """サーバーのタグ・色情報からコンテキスト文字列を構築する"""
    if not server_context:
        return ""

    lines = []

    tag_groups = server_context.get("tag_groups", [])
    tags = server_context.get("tags", [])
    if tag_groups or tags:
        lines.append("# このサーバーで利用可能なタグ")
        tags_by_group: Dict[int, list] = {}
        for tag in tags:
            tags_by_group.setdefault(tag.get("group_id", 0), []).append(tag)
        for group in tag_groups:
            group_tags = tags_by_group.get(group["id"], [])
            tag_names = [t["name"] for t in group_tags]
            if tag_names:
                lines.append(f"【{group['name']}】{' / '.join(tag_names)}")
        if not tag_groups and tags:
            tag_names = [t["name"] for t in tags]
            lines.append(f"利用可能: {' / '.join(tag_names)}")

    color_presets = server_context.get("color_presets", [])
    if color_presets:
        lines.append("\n# このサーバーで利用可能な色プリセット")
        for preset in color_presets:
            desc = f"({preset['description']})" if preset.get("description") else ""
            lines.append(f"- {preset['name']} {desc}")

    event_names = server_context.get("event_names", [])
    if event_names:
        lines.append("\n# 登録済みの予定名（編集・削除時の参照用）")
        for name in event_names:
            lines.append(f"- {name}")

    return "\n".join(lines)


def _parse_json_response(text: str) -> Dict[str, Any]:
    """Geminiのレスポンスからjsonをパースする"""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(0))
        raise ValueError("Gemini APIからのレスポンスをパースできませんでした。")


class NLPProcessor:
    def __init__(self, api_key: str):
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(
            'gemini-1.5-flash',
            generation_config={"temperature": 0.1}
        )
        self.conversation_model = genai.GenerativeModel(
            'gemini-1.5-flash',
            generation_config={"temperature": 0.3}
        )

    def parse_user_message(self, user_message: str) -> Dict[str, Any]:
        """ユーザーメッセージをパース（後方互換）"""
        prompt = f"{SYSTEM_PROMPT}\n\n入力: {user_message}"

        response = self.model.generate_content(prompt)
        result = _parse_json_response(response.text)

        # バリデーション
        self._validate_result(result)

        return result

    def create_chat_session(self, server_context: Optional[Dict[str, Any]] = None):
        """マルチターン会話用のチャットセッションを作成する"""
        context_str = _build_server_context(server_context)
        system_prompt = CONVERSATION_SYSTEM_PROMPT.format(server_context=context_str)

        chat = self.conversation_model.start_chat(
            history=[
                {"role": "user", "parts": [system_prompt]},
                {"role": "model", "parts": ['{"status": "ready"}']},
            ]
        )
        return chat

    def send_message(self, chat_session, user_message: str) -> Dict[str, Any]:
        """チャットセッションにメッセージを送信し、構造化レスポンスを返す"""
        response = chat_session.send_message(user_message)
        result = _parse_json_response(response.text)
        return result

    def _validate_result(self, result: Dict[str, Any]):
        """結果の検証"""
        if 'action' not in result:
            raise ValueError("actionが指定されていません")

        if result['action'] == 'add':
            if 'event_name' not in result:
                raise ValueError("予定名が必要です")
            if 'recurrence' not in result:
                raise ValueError("繰り返しパターンが必要です")
            if result['recurrence'] != 'irregular' and 'weekday' not in result:
                # 繰り返し予定の場合は曜日が必須（NLPが失敗した場合）
                raise ValueError("曜日を特定できませんでした。")
