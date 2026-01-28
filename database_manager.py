import sqlite3
import json
from datetime import datetime, timedelta
from typing import List, Optional, Dict
from contextlib import contextmanager
import os

class DatabaseManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._initialize_db()
    
    @contextmanager
    def get_connection(self):
        """データベース接続のコンテキストマネージャー"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
    
    def _initialize_db(self):
        """データベース初期化"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # 予定マスターテーブル
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    event_name TEXT NOT NULL,
                    tags TEXT,
                    recurrence TEXT NOT NULL,
                    nth_weeks TEXT,
                    event_type TEXT,
                    time TEXT,
                    weekday INTEGER,
                    duration_minutes INTEGER DEFAULT 60,
                    description TEXT,
                    color_name TEXT,
                    urls TEXT,
                    google_calendar_events TEXT,
                    discord_channel_id TEXT,
                    created_by TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_active BOOLEAN DEFAULT 1
                )
            ''')
            
            # インデックス
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_events_guild_id ON events(guild_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_events_recurrence ON events(recurrence)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_events_weekday ON events(weekday)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_events_created_by ON events(created_by)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_events_is_active ON events(is_active)')
            
            # 不定期予定の個別日時テーブル
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS irregular_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id INTEGER NOT NULL,
                    event_date DATE NOT NULL,
                    event_time TEXT,
                    google_calendar_event_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
                )
            ''')
            
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_irregular_events_event_id ON irregular_events(event_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_irregular_events_date ON irregular_events(event_date)')
            
            # 設定情報テーブル
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # 色プリセット（サーバーごとに管理）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS color_presets (
                    guild_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    color_id TEXT NOT NULL,
                    description TEXT,
                    PRIMARY KEY (guild_id, name)
                )
            ''')

            # タググループ（サーバーごとに最大3グループ）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS tag_groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT,
                    UNIQUE(guild_id, name)
                )
            ''')

            # タグ
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT,
                    UNIQUE(group_id, name),
                    FOREIGN KEY (group_id) REFERENCES tag_groups(id) ON DELETE CASCADE
                )
            ''')

            # カレンダーアカウント（サーバーごとに管理）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS calendar_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    calendar_id TEXT NOT NULL,
                    credentials_path TEXT,
                    UNIQUE(guild_id, name)
                )
            ''')

            # サーバー設定（どのカレンダーを使うか）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id TEXT PRIMARY KEY,
                    calendar_account_id INTEGER,
                    FOREIGN KEY (calendar_account_id) REFERENCES calendar_accounts(id)
                )
            ''')
            
            # 初期設定
            cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('last_backup_at', datetime('now'))")
            cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('last_notification_at', datetime('now'))")
            cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('legend_event_id', '')")

            # 既存DBのマイグレーション（列追加）
            cursor.execute("PRAGMA table_info(events)")
            existing_columns = {row[1] for row in cursor.fetchall()}
            if "color_name" not in existing_columns:
                cursor.execute("ALTER TABLE events ADD COLUMN color_name TEXT")
            if "urls" not in existing_columns:
                cursor.execute("ALTER TABLE events ADD COLUMN urls TEXT")
            if "guild_id" not in existing_columns:
                cursor.execute("ALTER TABLE events ADD COLUMN guild_id TEXT DEFAULT ''")

            # color_presetsのマイグレーション
            cursor.execute("PRAGMA table_info(color_presets)")
            cp_columns = {row[1] for row in cursor.fetchall()}
            if "guild_id" not in cp_columns:
                # 古いテーブルをリネームして新しいテーブルを作成
                cursor.execute("ALTER TABLE color_presets RENAME TO color_presets_old")
                cursor.execute('''
                    CREATE TABLE color_presets (
                        guild_id TEXT NOT NULL,
                        name TEXT NOT NULL,
                        color_id TEXT NOT NULL,
                        description TEXT,
                        PRIMARY KEY (guild_id, name)
                    )
                ''')
                # 古いデータは移行しない（guild_idが不明のため）
                cursor.execute("DROP TABLE IF EXISTS color_presets_old")

            # tag_groupsのマイグレーション
            cursor.execute("PRAGMA table_info(tag_groups)")
            tg_columns = {row[1] for row in cursor.fetchall()}
            if "guild_id" not in tg_columns:
                cursor.execute("ALTER TABLE tag_groups RENAME TO tag_groups_old")
                cursor.execute('''
                    CREATE TABLE tag_groups (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        guild_id TEXT NOT NULL,
                        name TEXT NOT NULL,
                        description TEXT,
                        UNIQUE(guild_id, name)
                    )
                ''')
                cursor.execute("DROP TABLE IF EXISTS tag_groups_old")
                # tagsも古いgroup_idを参照しているのでクリア
                cursor.execute("DELETE FROM tags")

            # calendar_accountsのマイグレーション
            cursor.execute("PRAGMA table_info(calendar_accounts)")
            ca_columns = {row[1] for row in cursor.fetchall()}
            if "guild_id" not in ca_columns:
                cursor.execute("ALTER TABLE calendar_accounts RENAME TO calendar_accounts_old")
                cursor.execute('''
                    CREATE TABLE calendar_accounts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        guild_id TEXT NOT NULL,
                        name TEXT NOT NULL,
                        calendar_id TEXT NOT NULL,
                        credentials_path TEXT,
                        UNIQUE(guild_id, name)
                    )
                ''')
                cursor.execute("DROP TABLE IF EXISTS calendar_accounts_old")
                # guild_settingsもクリア
                cursor.execute("DELETE FROM guild_settings")

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
        created_by: str
    ) -> int:
        """予定を追加"""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute('''
                INSERT INTO events (
                    guild_id, event_name, tags, recurrence, nth_weeks,
                    event_type, time, weekday, duration_minutes,
                    description, color_name, urls, discord_channel_id, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                guild_id,
                event_name,
                json.dumps(tags, ensure_ascii=False),
                recurrence,
                json.dumps(nth_weeks) if nth_weeks else None,
                event_type,
                time,
                weekday,
                duration_minutes,
                description,
                color_name,
                json.dumps(urls, ensure_ascii=False) if urls else None,
                discord_channel_id,
                created_by
            ))
            
            return cursor.lastrowid
    
    def update_google_calendar_events(
        self,
        event_id: int,
        google_events: List[dict]
    ):
        """Googleカレンダーイベント情報を更新"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                UPDATE events
                SET google_calendar_events = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (
                json.dumps(google_events, ensure_ascii=False),
                event_id
            ))
    
    def get_this_week_events(self, guild_id: Optional[str] = None) -> List[dict]:
        """今週の予定を取得"""
        from recurrence_calculator import RecurrenceCalculator

        today = datetime.now().date()
        start_of_week = today - timedelta(days=today.weekday())
        end_of_week = start_of_week + timedelta(days=6)

        return self.search_events(
            guild_id=guild_id,
            start_date=datetime.combine(start_of_week, datetime.min.time()),
            end_date=datetime.combine(end_of_week, datetime.max.time())
        )

    def search_events(
        self,
        start_date: datetime,
        end_date: datetime,
        guild_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
        event_name: Optional[str] = None
    ) -> List[dict]:
        """予定を検索（guild_id指定時はそのサーバーのみ）"""
        from recurrence_calculator import RecurrenceCalculator

        with self.get_connection() as conn:
            cursor = conn.cursor()

            # アクティブな予定を取得
            if guild_id:
                cursor.execute('''
                    SELECT * FROM events
                    WHERE is_active = 1 AND guild_id = ?
                ''', (guild_id,))
            else:
                cursor.execute('''
                    SELECT * FROM events
                    WHERE is_active = 1
                ''')
            
            events = [dict(row) for row in cursor.fetchall()]
            
            # 各予定の該当日を計算
            result = []
            for event in events:
                if event['recurrence'] == 'irregular':
                    # 不定期予定は個別テーブルから取得
                    cursor.execute('''
                        SELECT * FROM irregular_events
                        WHERE event_id = ?
                        AND event_date BETWEEN ? AND ?
                    ''', (event['id'], start_date.date().isoformat(), end_date.date().isoformat()))
                    
                    for irr_event in cursor.fetchall():
                        result.append({
                            **event,
                            'date': irr_event['event_date'],
                            'time': irr_event['event_time']
                        })
                else:
                    # 繰り返し予定の日付を計算
                    dates = RecurrenceCalculator.calculate_dates(
                        recurrence=event['recurrence'],
                        nth_weeks=json.loads(event['nth_weeks']) if event['nth_weeks'] else None,
                        weekday=event['weekday'],
                        start_date=start_date,
                        months_ahead=0,
                        end_date_limit=end_date
                    )
                    
                    for date in dates:
                        if start_date.date() <= date.date() <= end_date.date():
                            result.append({
                                **event,
                                'date': date.strftime('%Y-%m-%d')
                            })
            
            # フィルタリング
            if tags:
                result = [
                    e for e in result
                    if any(tag in json.loads(e['tags']) for tag in tags)
                ]
            
            if event_name:
                result = [
                    e for e in result
                    if event_name.lower() in e['event_name'].lower()
                ]
            
            return sorted(result, key=lambda x: (x['date'], x['time'] or ""))
    
    def search_events_by_name(self, name: str, guild_id: Optional[str] = None) -> List[dict]:
        """予定名で検索（guild_id指定時はそのサーバーのみ）"""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            if guild_id:
                cursor.execute('''
                    SELECT * FROM events
                    WHERE event_name LIKE ? AND is_active = 1 AND guild_id = ?
                ''', (f'%{name}%', guild_id))
            else:
                cursor.execute('''
                    SELECT * FROM events
                    WHERE event_name LIKE ? AND is_active = 1
                ''', (f'%{name}%',))

            return [dict(row) for row in cursor.fetchall()]
    
    def update_event(self, event_id: int, updates: dict):
        """予定を更新"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            set_clauses = []
            values = []
            
            for key, value in updates.items():
                set_clauses.append(f"{key} = ?")
                if isinstance(value, (list, dict)):
                    values.append(json.dumps(value, ensure_ascii=False))
                else:
                    values.append(value)
            
            values.append(event_id)
            
            sql = f'''
                UPDATE events
                SET {', '.join(set_clauses)},
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            '''
            
            cursor.execute(sql, values)
    
    def delete_event(self, event_id: int):
        """予定を削除（論理削除）"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                UPDATE events
                SET is_active = 0,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (event_id,))
    
    def get_all_active_events(self, guild_id: Optional[str] = None) -> List[dict]:
        """全てのアクティブな予定を取得（guild_id指定時はそのサーバーのみ）"""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            if guild_id:
                cursor.execute('''
                    SELECT * FROM events
                    WHERE is_active = 1 AND guild_id = ?
                    ORDER BY created_at DESC
                ''', (guild_id,))
            else:
                cursor.execute('''
                    SELECT * FROM events
                    WHERE is_active = 1
                    ORDER BY created_at DESC
                ''')

            return [dict(row) for row in cursor.fetchall()]

    def update_setting(self, key: str, value: str):
        """設定情報を更新"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO settings (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            ''', (key, value))

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT value FROM settings WHERE key = ?', (key,))
            row = cursor.fetchone()
            return row['value'] if row else default

    # ---- 色プリセット ----
    def add_color_preset(self, guild_id: str, name: str, color_id: str, description: str = ""):
        """色プリセットを追加（サーバーごとに管理）"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO color_presets (guild_id, name, color_id, description)
                VALUES (?, ?, ?, ?)
            ''', (guild_id, name, color_id, description))

    def list_color_presets(self, guild_id: str) -> List[dict]:
        """色プリセット一覧（サーバーごと）"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM color_presets WHERE guild_id = ? ORDER BY name', (guild_id,))
            return [dict(row) for row in cursor.fetchall()]

    def delete_color_preset(self, guild_id: str, name: str):
        """色プリセットを削除（サーバーごと）"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM color_presets WHERE guild_id = ? AND name = ?', (guild_id, name))

    def get_color_preset(self, guild_id: str, name: str) -> Optional[dict]:
        """色プリセットを取得（サーバーごと）"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM color_presets WHERE guild_id = ? AND name = ?', (guild_id, name))
            row = cursor.fetchone()
            return dict(row) if row else None

    # ---- タググループ/タグ ----
    def list_tag_groups(self, guild_id: str) -> List[dict]:
        """タググループ一覧（サーバーごと）"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM tag_groups WHERE guild_id = ? ORDER BY id', (guild_id,))
            return [dict(row) for row in cursor.fetchall()]

    def add_tag_group(self, guild_id: str, name: str, description: str = "") -> int:
        """タググループを追加（サーバーごとに最大3つ）"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) AS cnt FROM tag_groups WHERE guild_id = ?', (guild_id,))
            if cursor.fetchone()['cnt'] >= 3:
                raise ValueError("タググループは最大3つまでです。")
            cursor.execute('''
                INSERT INTO tag_groups (guild_id, name, description)
                VALUES (?, ?, ?)
            ''', (guild_id, name, description))
            return cursor.lastrowid

    def update_tag_group(self, guild_id: str, group_id: int, name: Optional[str] = None, description: Optional[str] = None):
        """タググループを更新（サーバーごと）"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            updates = []
            values = []
            if name is not None:
                updates.append("name = ?")
                values.append(name)
            if description is not None:
                updates.append("description = ?")
                values.append(description)
            if not updates:
                return
            values.extend([group_id, guild_id])
            cursor.execute(f'''
                UPDATE tag_groups
                SET {', '.join(updates)}
                WHERE id = ? AND guild_id = ?
            ''', values)

    def delete_tag_group(self, guild_id: str, group_id: int):
        """タググループを削除（サーバーごと）"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM tag_groups WHERE id = ? AND guild_id = ?', (group_id, guild_id))

    def get_tag_group(self, guild_id: str, group_id: int) -> Optional[dict]:
        """タググループを取得（サーバーごと）"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM tag_groups WHERE id = ? AND guild_id = ?', (group_id, guild_id))
            row = cursor.fetchone()
            return dict(row) if row else None

    def add_tag(self, guild_id: str, group_id: int, name: str, description: str = ""):
        """タグを追加（サーバーのグループに紐づく）"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            # グループがこのサーバーのものか確認
            cursor.execute('SELECT 1 FROM tag_groups WHERE id = ? AND guild_id = ?', (group_id, guild_id))
            if not cursor.fetchone():
                raise ValueError("指定されたタググループは存在しません。")
            cursor.execute('''
                INSERT OR REPLACE INTO tags (group_id, name, description)
                VALUES (?, ?, ?)
            ''', (group_id, name, description))

    def delete_tag(self, guild_id: str, group_id: int, name: str):
        """タグを削除（サーバーのグループに紐づく）"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            # グループがこのサーバーのものか確認
            cursor.execute('SELECT 1 FROM tag_groups WHERE id = ? AND guild_id = ?', (group_id, guild_id))
            if not cursor.fetchone():
                return
            cursor.execute('DELETE FROM tags WHERE group_id = ? AND name = ?', (group_id, name))

    def list_tags(self, guild_id: str) -> List[dict]:
        """タグ一覧（サーバーごと）"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT t.id, t.group_id, g.name as group_name, t.name, t.description
                FROM tags t
                JOIN tag_groups g ON g.id = t.group_id
                WHERE g.guild_id = ?
                ORDER BY g.id, t.name
            ''', (guild_id,))
            return [dict(row) for row in cursor.fetchall()]

    def list_tags_by_group(self, guild_id: str) -> Dict[int, List[dict]]:
        """タグをグループごとに取得（サーバーごと）"""
        tags = self.list_tags(guild_id)
        grouped: Dict[int, List[dict]] = {}
        for tag in tags:
            grouped.setdefault(tag['group_id'], []).append(tag)
        return grouped

    def tag_exists(self, guild_id: str, name: str) -> bool:
        """タグが存在するか確認（サーバーごと）"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT 1 FROM tags t
                JOIN tag_groups g ON g.id = t.group_id
                WHERE g.guild_id = ? AND t.name = ?
                LIMIT 1
            ''', (guild_id, name))
            return cursor.fetchone() is not None

    def find_missing_tags(self, guild_id: str, tags: List[str]) -> List[str]:
        """存在しないタグを検索（サーバーごと）"""
        if not tags:
            return []
        with self.get_connection() as conn:
            cursor = conn.cursor()
            placeholders = ','.join(['?'] * len(tags))
            cursor.execute(f'''
                SELECT t.name FROM tags t
                JOIN tag_groups g ON g.id = t.group_id
                WHERE g.guild_id = ? AND t.name IN ({placeholders})
            ''', [guild_id] + tags)
            existing = {row['name'] for row in cursor.fetchall()}
            return [t for t in tags if t not in existing]

    # ---- カレンダーアカウント ----
    def add_calendar_account(self, guild_id: str, name: str, calendar_id: str, credentials_path: Optional[str] = None) -> int:
        """カレンダーアカウントを追加（サーバーごとに管理）"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO calendar_accounts (guild_id, name, calendar_id, credentials_path)
                VALUES (?, ?, ?, ?)
            ''', (guild_id, name, calendar_id, credentials_path))
            return cursor.lastrowid

    def list_calendar_accounts(self, guild_id: str) -> List[dict]:
        """カレンダーアカウント一覧（サーバーごと）"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM calendar_accounts WHERE guild_id = ? ORDER BY id', (guild_id,))
            return [dict(row) for row in cursor.fetchall()]

    def get_calendar_account(self, guild_id: str, account_id: int) -> Optional[dict]:
        """カレンダーアカウントを取得（サーバーごと）"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM calendar_accounts WHERE id = ? AND guild_id = ?', (account_id, guild_id))
            row = cursor.fetchone()
            return dict(row) if row else None

    def delete_calendar_account(self, guild_id: str, account_id: int):
        """カレンダーアカウントを削除（サーバーごと）"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM calendar_accounts WHERE id = ? AND guild_id = ?', (account_id, guild_id))

    def set_guild_calendar_account(self, guild_id: str, account_id: Optional[int]):
        """サーバーで使用するカレンダーアカウントを設定"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            # account_idがNoneでない場合、このサーバーのアカウントか確認
            if account_id is not None:
                cursor.execute('SELECT 1 FROM calendar_accounts WHERE id = ? AND guild_id = ?', (account_id, guild_id))
                if not cursor.fetchone():
                    raise ValueError("指定されたカレンダーアカウントは存在しません。")
            cursor.execute('''
                INSERT OR REPLACE INTO guild_settings (guild_id, calendar_account_id)
                VALUES (?, ?)
            ''', (guild_id, account_id))

    def get_guild_calendar_account(self, guild_id: str) -> Optional[dict]:
        """サーバーで使用中のカレンダーアカウントを取得"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT ca.*
                FROM guild_settings gs
                JOIN calendar_accounts ca ON ca.id = gs.calendar_account_id
                WHERE gs.guild_id = ? AND ca.guild_id = ?
            ''', (guild_id, guild_id))
            row = cursor.fetchone()
            return dict(row) if row else None
