from google.oauth2.credentials import Credentials as OAuthCredentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Callable

SCOPES = ['https://www.googleapis.com/auth/calendar']

class GoogleCalendarManager:
    def __init__(
        self,
        access_token: str,
        refresh_token: str,
        token_expiry: Optional[str],
        client_id: str,
        client_secret: str,
        calendar_id: str,
        on_token_refresh: Optional[Callable] = None,
    ):
        """OAuth トークンから GoogleCalendarManager を構築する"""
        expiry = None
        if token_expiry:
            try:
                expiry = datetime.fromisoformat(token_expiry)
            except (ValueError, TypeError):
                pass

        creds = OAuthCredentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=SCOPES,
            expiry=expiry,
        )

        if creds.expired and creds.refresh_token:
            creds.refresh(Request())

        self.credentials = creds
        self.service = build('calendar', 'v3', credentials=creds)
        self.calendar_id = calendar_id
        self._on_token_refresh = on_token_refresh

    def create_events(
        self,
        event_name: str,
        dates: List[datetime],
        time_str: str,
        duration_minutes: int,
        description: str = "",
        tags: List[str] = None,
        color_id: Optional[str] = None,
        extended_props: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        複数のイベントを一括作成
        """
        created_events = []
        
        for date in dates:
            try:
                event_id = self.create_event(
                    event_name, date, time_str, 
                    duration_minutes, description, tags,
                    color_id=color_id,
                    extended_props=extended_props
                )
                
                created_events.append({
                    "event_id": event_id,
                    "date": date.strftime("%Y-%m-%d"),
                    "time": time_str,
                    "created_at": datetime.utcnow().isoformat() + "Z"
                })
            except Exception as e:
                print(f"Failed to create event on {date}: {e}")
        
        return created_events
    
    def create_event(
        self,
        summary: str,
        date: datetime,
        time_str: str,
        duration_minutes: int,
        description: str = "",
        tags: List[str] = None,
        color_id: Optional[str] = None,
        extended_props: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        単一イベントを作成
        """
        # 開始時刻
        if time_str:
            hour, minute = map(int, time_str.split(':'))
            start_datetime = date.replace(hour=hour, minute=minute, second=0, microsecond=0)
            end_datetime = start_datetime + timedelta(minutes=duration_minutes)
            
            start = {'dateTime': start_datetime.isoformat(), 'timeZone': 'Asia/Tokyo'}
            end = {'dateTime': end_datetime.isoformat(), 'timeZone': 'Asia/Tokyo'}
        else:
            # 終日イベント（時刻指定なしの場合）
            start = {'date': date.strftime('%Y-%m-%d')}
            end = {'date': (date + timedelta(days=1)).strftime('%Y-%m-%d')}
        
        # イベント本文
        event_body = {
            'summary': summary,
            'description': description,
            'start': start,
            'end': end
        }
        
        # タグをカラーIDに変換
        if color_id:
            event_body['colorId'] = color_id

        if extended_props:
            event_body['extendedProperties'] = {
                'private': extended_props
            }
        
        event = self.service.events().insert(
            calendarId=self.calendar_id,
            body=event_body
        ).execute()
        
        return event['id']

    def create_recurring_event(
        self,
        summary: str,
        start_datetime: datetime,
        end_datetime: datetime,
        rrule: str,
        description: str = "",
        color_id: Optional[str] = None,
        extended_props: Optional[Dict[str, Any]] = None
    ) -> str:
        event_body = {
            'summary': summary,
            'description': description,
            'start': {'dateTime': start_datetime.isoformat(), 'timeZone': 'Asia/Tokyo'},
            'end': {'dateTime': end_datetime.isoformat(), 'timeZone': 'Asia/Tokyo'},
            'recurrence': [rrule]
        }
        if color_id:
            event_body['colorId'] = color_id
        if extended_props:
            event_body['extendedProperties'] = {'private': extended_props}
        event = self.service.events().insert(
            calendarId=self.calendar_id,
            body=event_body
        ).execute()
        return event['id']
    
    def update_events(
        self,
        event_ids: List[str],
        updated_fields: Dict[str, Any]
    ):
        """
        複数イベントを一括更新
        """
        for event_id in event_ids:
            try:
                self.update_event(event_id, updated_fields)
            except Exception as e:
                print(f"Failed to update event {event_id}: {e}")
    
    def update_event(self, event_id: str, updated_fields: Dict[str, Any]):
        """単一イベントを更新"""
        event = self.service.events().get(
            calendarId=self.calendar_id,
            eventId=event_id
        ).execute()
        
        # フィールドを更新
        event.update(updated_fields)
        
        self.service.events().update(
            calendarId=self.calendar_id,
            eventId=event_id,
            body=event
        ).execute()
    
    def delete_events(self, event_ids: List[str]):
        """複数イベントを一括削除"""
        for event_id in event_ids:
            try:
                self.service.events().delete(
                    calendarId=self.calendar_id,
                    eventId=event_id
                ).execute()
            except Exception as e:
                print(f"Failed to delete event {event_id}: {e}")
    
    def search_events(
        self,
        start_date: datetime,
        end_date: datetime,
        query: str = None
    ) -> List[Dict[str, Any]]:
        """
        期間内のイベントを検索
        """
        events_result = self.service.events().list(
            calendarId=self.calendar_id,
            timeMin=start_date.isoformat() + 'Z',
            timeMax=end_date.isoformat() + 'Z',
            q=query,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        return events_result.get('items', [])
    
    def _get_color_for_tags(self, tags: List[str]) -> Optional[str]:
        """タグに応じた色IDを返す"""
        color_map = {
            "重要": "11",  # 赤
            "チームミーティング": "9",  # 青
            "個人": "2",  # 緑
        }
        
        for tag in tags:
            if tag in color_map:
                return color_map[tag]
        
        return None

    def get_color_palette(self) -> Dict[str, Any]:
        """Googleカレンダーの色パレットを取得"""
        return self.service.colors().get().execute()
