"""conversation_manager.py のユニットテスト"""
import asyncio
import time
import unittest
from conversation_manager import ConversationManager, ConversationSession


class TestConversationSession(unittest.TestCase):
    """ConversationSession は同期クラスなので同期テストで検証"""
    def test_is_expired_false(self):
        session = ConversationSession("g1", 1, 100, 200, None, timeout=300)
        self.assertFalse(session.is_expired())

    def test_is_expired_true(self):
        session = ConversationSession("g1", 1, 100, 200, None, timeout=0)
        time.sleep(0.01)
        self.assertTrue(session.is_expired())

    def test_touch_resets_expiry(self):
        session = ConversationSession("g1", 1, 100, 200, None, timeout=1)
        time.sleep(0.5)
        session.touch()
        self.assertFalse(session.is_expired())


class TestConversationManager(unittest.IsolatedAsyncioTestCase):
    async def test_create_and_get_session(self):
        mgr = ConversationManager()
        session = await mgr.create_session("g1", 1, 100, 200, None)
        retrieved = await mgr.get_session(100)
        self.assertIs(session, retrieved)

    async def test_get_session_expired_returns_none(self):
        mgr = ConversationManager()
        await mgr.create_session("g1", 1, 100, 200, None, timeout=0)
        await asyncio.sleep(0.01)
        self.assertIsNone(await mgr.get_session(100))

    async def test_remove_session(self):
        mgr = ConversationManager()
        await mgr.create_session("g1", 1, 100, 200, None)
        await mgr.remove_session(100)
        self.assertIsNone(await mgr.get_session(100))

    async def test_cleanup_expired(self):
        mgr = ConversationManager()
        # _sessionsに直接追加してクリーンアップのトリガーを避ける
        s1 = ConversationSession("g1", 1, 100, 200, None, timeout=0)
        s2 = ConversationSession("g2", 2, 101, 201, None, timeout=300)
        mgr._sessions[100] = s1
        mgr._sessions[101] = s2
        await asyncio.sleep(0.01)
        expired = await mgr.cleanup_expired()
        self.assertIn(100, expired)
        self.assertNotIn(101, expired)

    async def test_active_count_excludes_expired(self):
        mgr = ConversationManager()
        await mgr.create_session("g1", 1, 100, 200, None, timeout=0)
        await mgr.create_session("g2", 2, 101, 201, None, timeout=300)
        await asyncio.sleep(0.01)
        self.assertEqual(mgr.active_count, 1)

    async def test_create_session_triggers_cleanup(self):
        mgr = ConversationManager()
        await mgr.create_session("g1", 1, 100, 200, None, timeout=0)
        await asyncio.sleep(0.01)
        # 新しいセッション作成時にクリーンアップされる
        await mgr.create_session("g2", 2, 101, 201, None, timeout=300)
        self.assertIsNone(await mgr.get_session(100))

    async def test_overwrite_session(self):
        """同じthread_idで新セッション作成すると上書き"""
        mgr = ConversationManager()
        s1 = await mgr.create_session("g1", 1, 100, 200, None)
        s2 = await mgr.create_session("g2", 2, 100, 300, None)
        retrieved = await mgr.get_session(100)
        self.assertIs(retrieved, s2)
        self.assertEqual(retrieved.user_id, 300)


if __name__ == "__main__":
    unittest.main()
