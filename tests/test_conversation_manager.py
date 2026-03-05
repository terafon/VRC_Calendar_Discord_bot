"""conversation_manager.py のユニットテスト"""
import time
import unittest
from conversation_manager import ConversationManager, ConversationSession


class TestConversationSession(unittest.TestCase):
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


class TestConversationManager(unittest.TestCase):
    def test_create_and_get_session(self):
        mgr = ConversationManager()
        session = mgr.create_session("g1", 1, 100, 200, None)
        retrieved = mgr.get_session(100)
        self.assertIs(session, retrieved)

    def test_get_session_expired_returns_none(self):
        mgr = ConversationManager()
        mgr.create_session("g1", 1, 100, 200, None, timeout=0)
        time.sleep(0.01)
        self.assertIsNone(mgr.get_session(100))

    def test_remove_session(self):
        mgr = ConversationManager()
        mgr.create_session("g1", 1, 100, 200, None)
        mgr.remove_session(100)
        self.assertIsNone(mgr.get_session(100))

    def test_cleanup_expired(self):
        mgr = ConversationManager()
        # _sessionsに直接追加してクリーンアップのトリガーを避ける
        s1 = ConversationSession("g1", 1, 100, 200, None, timeout=0)
        s2 = ConversationSession("g2", 2, 101, 201, None, timeout=300)
        mgr._sessions[100] = s1
        mgr._sessions[101] = s2
        time.sleep(0.01)
        expired = mgr.cleanup_expired()
        self.assertIn(100, expired)
        self.assertNotIn(101, expired)

    def test_active_count_excludes_expired(self):
        mgr = ConversationManager()
        mgr.create_session("g1", 1, 100, 200, None, timeout=0)
        mgr.create_session("g2", 2, 101, 201, None, timeout=300)
        time.sleep(0.01)
        self.assertEqual(mgr.active_count, 1)

    def test_create_session_triggers_cleanup(self):
        mgr = ConversationManager()
        mgr.create_session("g1", 1, 100, 200, None, timeout=0)
        time.sleep(0.01)
        # 新しいセッション作成時にクリーンアップされる
        mgr.create_session("g2", 2, 101, 201, None, timeout=300)
        self.assertIsNone(mgr.get_session(100))

    def test_overwrite_session(self):
        """同じthread_idで新セッション作成すると上書き"""
        mgr = ConversationManager()
        s1 = mgr.create_session("g1", 1, 100, 200, None)
        s2 = mgr.create_session("g2", 2, 100, 300, None)
        retrieved = mgr.get_session(100)
        self.assertIs(retrieved, s2)
        self.assertEqual(retrieved.user_id, 300)


if __name__ == "__main__":
    unittest.main()
