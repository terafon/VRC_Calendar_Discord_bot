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
  "x_url": "https://x.com/... or null",
  "vrc_group_url": "https://vrc.group/... or null",
  "official_url": "https://example.com or null",
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

# タグ選択のルール
- tags配列に入れるタグは、登録済みタググループに属するタグのみを使用してください。
- 各タググループから任意の数のタグを選べます。tags配列にまとめてください。
- ユーザーが登録されていないタグ名を指定した場合は、そのままtags配列に含めてください。

# editアクション時の重要ルール
- 登録済みの予定一覧に各予定の現在の設定値が記載されています。
- action=edit の場合、ユーザーが明示的に変更を指示したフィールドのみを出力JSONに含めてください。
- 変更しないフィールドはキー自体を含めないでください（nullも設定しないでください）。
- event_name は対象予定の特定に必要なので必ず含めてください。
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
    "x_url": "収集済みのXアカウントURL or null",
    "vrc_group_url": "収集済みのVRCグループURL or null",
    "official_url": "収集済みの公式サイトURL or null",
    "calendar_name": "登録先カレンダーの表示名 or null"
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
    "x_url": "XアカウントURL or null",
    "vrc_group_url": "VRCグループURL or null",
    "official_url": "公式サイトURL or null",
    "calendar_name": "登録先カレンダーの表示名 or null"
  }},
  "search_query": {{
    "date_range": "today|this_week|next_week|this_month",
    "tags": ["タグ"],
    "event_name": "部分一致文字列"
  }}
}}

# 質問の仕方
- 基本的に一度に1つの質問をしてください（ただしURLは3種まとめて聞いてOK）
- 利用可能な選択肢がある場合は提示してください
- フレンドリーで親しみやすい日本語を使ってください
- 質問の順序: 開催頻度 → 曜日 → 時刻 → 所要時間 → タグ → 説明 → URL

# 所要時間の質問ルール
- 時刻の質問の後に、所要時間（duration_minutes）を質問してください。
- 「特になし」「デフォルトで」等の場合はデフォルトの60分を設定してください。
- action=add の場合、所要時間の質問が完了するまで status: complete にしないでください。

# 説明の質問ルール
- タグの質問の後に、予定の説明（備考）があるか質問してください。
- 説明は任意です。「なし」と回答された場合は description を空文字にしてください。
- action=add の場合、説明の質問が完了するまで status: complete にしないでください。

# URL質問のルール
- 説明の質問の後に、以下の3種URLをまとめて1回の質問で聞いてください:
  1. X(旧Twitter)アカウントURL
  2. VRCグループURL
  3. 公式サイトURL
- 3つのURLは全て任意です。「なし」と回答された場合はnullにしてください。
- action=add の場合、URLの質問が完了するまで status: complete にしないでください。

# カレンダー選択のルール
- カレンダーが1つしかない場合は質問不要で、calendar_name に null を設定してください。
- カレンダーが複数ある場合、URLの質問の後にどのカレンダーに登録するか質問してください。
- デフォルトカレンダーがある場合は「特に指定がなければ〇〇に登録します」と案内してください。

# 色の割当ルール
- 色は繰り返しタイプに基づいてシステムが自動で割り当てます。
- ユーザーに色を質問する必要はありません。
- ただし、ユーザーが明示的に色を指定した場合はその色名を color_name に設定してください。

# タグ選択のルール（非常に重要）
- タグは必ず「タググループ」に所属します。tags配列に入れるタグは、必ずいずれかのタググループに属するタグでなければなりません。
- **タグの選択方法**: 各タググループから任意の数のタグを選び、tags配列にまとめてください。1グループから複数選択も可能です。
  - 例: 【ジャンル】集会 / 試着会 / 交流会 と 【規模】小規模 / 大規模 がある場合 → tags: ["集会", "交流会", "小規模"] のようにグループをまたいで自由に選択
- **タグの提案方法**: ユーザーにタグを質問する際は、必ずタググループ名と所属タグの一覧を提示し、グループごとに選んでもらってください。個別のタグをバラバラに提示しないでください。
- **重要**: AIが独自に新しいタグ名を作成・提案しないでください。登録済みのタグのみを選択肢として表示してください。
- タグが登録されていないグループは質問から除外してください。
- ユーザーが登録されていないタグ名を指定した場合は、そのままtags配列に含めてください（システム側でどのグループに追加するか確認し、自動登録処理を行います）。

{server_context}

# 重要なルール
- 必ずJSONのみを返してください。JSON以外のテキストは含めないでください。
- search アクションの場合は event_data は不要です。search_query を含めてください。
- delete アクションの場合は event_name が分かれば status: complete にしてください。
- edit アクションの場合は event_name だけでは status: complete にしないでください。ユーザーが変更内容（どのフィールドをどう変更するか）を明示するまで status: needs_info で質問してください。ただし、ユーザーが最初のメッセージで変更内容も一緒に指定している場合は status: complete にしてください。
- duration_minutes のデフォルトは 60 です。ユーザーに質問した上で、特に指定がなければ 60 を設定してください。

# editアクション時の重要ルール
- 登録済みの予定一覧に各予定の現在の設定値が記載されています。editアクション時はこれを参照してください。
- action=edit の場合、ユーザーが明示的に変更を指示したフィールドのみを event_data に含めてください。
- 変更しないフィールドは event_data にキー自体を含めないでください（nullも設定しないでください）。
- event_name は対象予定の特定に必要なので必ず event_data に含めてください。
- 例: 「VRC集会の時刻を22時に変更」→ event_data には event_name と time のみを含める。recurrence, weekday, tags 等の変更しないフィールドは含めない。

# 非常に重要: action=add の status 判定ルール
action=add の場合、以下の全ステップの質問が完了するまで **必ず** status: needs_info を返してください。
途中で complete にしないでください。

## 質問ステップ（すべて完了するまで needs_info）:
1. event_name（予定名）— 必須
2. recurrence（開催頻度）— 必須
3. weekday（曜日）— 必須（recurrence が irregular の場合は不要）
4. time（開始時刻）— 必須
5. duration_minutes（所要時間）— デフォルト60分だが必ず質問する
6. tags（タグ）— 任意だが必ず質問する。「なし」の場合は空配列
7. description（説明）— 任意だが必ず質問する。「なし」の場合は空文字
8. URL 3種（x_url, vrc_group_url, official_url）— 任意だが必ず質問する。「なし」の場合はnull
9. calendar_name（カレンダー選択）— 複数カレンダーがある場合のみ質問

**全ステップの質問が完了して初めて** status: complete にしてください。

例: ユーザーが「VRC集会を登録」とだけ入力した場合:
→ event_name のみ判明。開催頻度から順に質問を開始する
"""


def _build_server_context(server_context: Optional[Dict[str, Any]] = None) -> str:
    """サーバーのタグ・色情報からコンテキスト文字列を構築する"""
    if not server_context:
        return ""

    lines = []

    tag_groups = server_context.get("tag_groups", [])
    tags = server_context.get("tags", [])
    # タグ名→グループ名マッピング（予定詳細のタグ表示で使用）
    tag_to_group: Dict[str, str] = {}
    if tag_groups or tags:
        lines.append("# このサーバーで利用可能なタグ（各タググループから複数選択可能）")
        tags_by_group: Dict[int, list] = {}
        for tag in tags:
            tags_by_group.setdefault(tag.get("group_id", 0), []).append(tag)
        for group in tag_groups:
            group_tags = tags_by_group.get(group["id"], [])
            tag_names = [t["name"] for t in group_tags]
            for t_name in tag_names:
                tag_to_group[t_name] = group["name"]
            desc = f" - {group['description']}" if group.get('description') else ""
            if tag_names:
                lines.append(f"【タググループ: {group['name']}{desc}】選択肢: {' / '.join(tag_names)}")
        if not tag_groups and tags:
            tag_names = [t["name"] for t in tags]
            lines.append(f"利用可能: {' / '.join(tag_names)}")

    color_presets_by_calendar = server_context.get("color_presets_by_calendar", {})
    if color_presets_by_calendar:
        lines.append("\n# 色プリセット（カレンダーごと。繰り返しタイプで自動割当。明示的指定しない限り設定不要）")
        for cal_name, presets in color_presets_by_calendar.items():
            if presets:
                lines.append(f"## {cal_name}")
                for preset in presets:
                    rt = preset.get('recurrence_type')
                    rt_label = f" [→ {rt}]" if rt else ""
                    desc = f"({preset['description']})" if preset.get("description") else ""
                    lines.append(f"- {preset['name']}{rt_label} {desc}")

    calendars = server_context.get("calendars", [])
    if calendars:
        lines.append("\n# 利用可能なカレンダー")
        for cal in calendars:
            default_mark = "（デフォルト）" if cal.get("is_default") else ""
            desc = f" - {cal['description']}" if cal.get("description") else ""
            lines.append(f"- {cal['display_name']}{default_mark}{desc}")

    events = server_context.get("events", [])
    if events:
        weekdays = ['月', '火', '水', '木', '金', '土', '日']
        recurrence_labels = {
            "weekly": "毎週", "biweekly": "隔週",
            "nth_week": "第n週", "irregular": "不定期",
        }
        lines.append("\n# 登録済みの予定（編集・削除時の参照用。各予定の現在の設定値）")
        for ev in events:
            name = ev.get("event_name", "?")
            rec = ev.get("recurrence", "")
            rec_label = recurrence_labels.get(rec, rec)
            wd = ev.get("weekday")
            wd_str = weekdays[wd] if isinstance(wd, int) and 0 <= wd <= 6 else ""
            time_str = ev.get("time", "")
            dur = ev.get("duration_minutes", 60)
            nth = ev.get("nth_weeks")
            nth_str = f" 第{','.join(str(n) for n in nth)}週" if nth else ""
            tags = ev.get("tags", [])
            if tags and tag_to_group:
                tags_with_group = []
                for t in tags:
                    group_name = tag_to_group.get(t)
                    if group_name:
                        tags_with_group.append(f"{group_name}:{t}")
                    else:
                        tags_with_group.append(t)
                tags_str = ", ".join(tags_with_group)
            else:
                tags_str = ", ".join(tags) if tags else ""
            desc = ev.get("description", "")
            color = ev.get("color_name", "")
            x_url = ev.get("x_url", "")
            vrc_url = ev.get("vrc_group_url", "")
            official = ev.get("official_url", "")

            detail_parts = []
            if rec_label:
                detail_parts.append(f"繰り返し:{rec_label}{nth_str}")
            if wd_str:
                detail_parts.append(f"曜日:{wd_str}")
            if time_str:
                detail_parts.append(f"時刻:{time_str}")
            if dur and dur != 60:
                detail_parts.append(f"所要時間:{dur}分")
            if tags_str:
                detail_parts.append(f"タグ:[{tags_str}]")
            if color:
                detail_parts.append(f"色:{color}")
            if desc:
                detail_parts.append(f"説明:{desc}")
            if x_url:
                detail_parts.append(f"X:{x_url}")
            if vrc_url:
                detail_parts.append(f"VRC:{vrc_url}")
            if official:
                detail_parts.append(f"公式:{official}")

            detail = " | ".join(detail_parts)
            lines.append(f"- {name}（{detail}）")

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
        # gemini-2.0-flash が最新の推奨モデル
        self.model = genai.GenerativeModel(
            'gemini-2.0-flash',
            generation_config={"temperature": 0.1}
        )
        self.conversation_model = genai.GenerativeModel(
            'gemini-2.0-flash',
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

        # 必須フィールドの検証と強制修正
        result = self._ensure_required_fields(result)
        return result

    def _ensure_required_fields(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """action=addの場合、必須フィールドが揃っているか検証し、不足があればneeds_infoに強制変更"""
        action = result.get("action")
        status = result.get("status", "complete")

        if action != "add" or status == "needs_info":
            return result

        event_data = result.get("event_data", {})
        missing_fields = []

        # 必須フィールドをチェック
        if not event_data.get("event_name"):
            missing_fields.append("予定名")
        if not event_data.get("recurrence"):
            missing_fields.append("開催頻度（毎週/隔週/第n週/不定期）")
        if not event_data.get("time"):
            missing_fields.append("開始時刻")

        recurrence = event_data.get("recurrence")
        if recurrence and recurrence != "irregular" and event_data.get("weekday") is None:
            missing_fields.append("曜日")

        if missing_fields:
            # 不足フィールドがある場合、needs_infoに強制変更
            question = f"以下の情報を教えてください:\n• {missing_fields[0]}"
            return {
                "status": "needs_info",
                "action": action,
                "question": question,
                "event_data": event_data,
            }

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
