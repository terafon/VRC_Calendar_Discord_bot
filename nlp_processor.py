import google.generativeai as genai
import json
from typing import Dict, Any

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

class NLPProcessor:
    def __init__(self, api_key: str):
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(
            'gemini-1.5-flash',
            generation_config={
                "temperature": 0.1,
                "response_mime_type": "application/json"
            }
        )
    
    def parse_user_message(self, user_message: str) -> Dict[str, Any]:
        """ユーザーメッセージをパース"""
        prompt = f"{SYSTEM_PROMPT}\n\n入力: {user_message}"
        
        response = self.model.generate_content(prompt)
        try:
            result = json.loads(response.text)
        except json.JSONDecodeError:
            # フォールバック: JSON以外が含まれている場合（通常はないはずだが念のため）
            import re
            json_match = re.search(r'\{.*\}', response.text, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group(0))
            else:
                raise ValueError("Gemini APIからのレスポンスをパースできませんでした。")
        
        # バリデーション
        self._validate_result(result)
        
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
