import time
from typing import Dict, Optional, Any


class ConversationSession:
    """1つのスレッド内の会話セッションを管理する"""

    def __init__(
        self,
        guild_id: str,
        channel_id: int,
        thread_id: int,
        user_id: int,
        chat_session: Any,
        action: Optional[str] = None,
        timeout: int = 300,
    ):
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.thread_id = thread_id
        self.user_id = user_id
        self.chat_session = chat_session
        self.action = action
        self.partial_data: Dict[str, Any] = {}
        self.server_context: Dict[str, Any] = {}
        self.created_at = time.time()
        self.last_activity = time.time()
        self.timeout = timeout

    def is_expired(self) -> bool:
        return (time.time() - self.last_activity) > self.timeout

    def touch(self):
        self.last_activity = time.time()


class ConversationManager:
    """会話セッションをスレッドIDで管理する"""

    def __init__(self):
        self._sessions: Dict[int, ConversationSession] = {}

    def create_session(
        self,
        guild_id: str,
        channel_id: int,
        thread_id: int,
        user_id: int,
        chat_session: Any,
        action: Optional[str] = None,
        server_context: Optional[Dict[str, Any]] = None,
        timeout: int = 300,
    ) -> ConversationSession:
        session = ConversationSession(
            guild_id=guild_id,
            channel_id=channel_id,
            thread_id=thread_id,
            user_id=user_id,
            chat_session=chat_session,
            action=action,
            timeout=timeout,
        )
        if server_context:
            session.server_context = server_context
        self._sessions[thread_id] = session
        return session

    def get_session(self, thread_id: int) -> Optional[ConversationSession]:
        session = self._sessions.get(thread_id)
        if session and session.is_expired():
            self.remove_session(thread_id)
            return None
        return session

    def remove_session(self, thread_id: int):
        self._sessions.pop(thread_id, None)

    def cleanup_expired(self) -> list:
        """タイムアウトしたセッションを削除し、削除対象のthread_idリストを返す"""
        expired = [
            tid for tid, session in self._sessions.items()
            if session.is_expired()
        ]
        for tid in expired:
            self._sessions.pop(tid, None)
        return expired

    @property
    def active_count(self) -> int:
        return len(self._sessions)
