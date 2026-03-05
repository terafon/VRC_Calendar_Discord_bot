"""nlp_processor.py の _parse_json_response ユニットテスト"""
import sys
import unittest
from unittest.mock import MagicMock

# google.generativeai がローカルにない場合はモック
if "google.generativeai" not in sys.modules:
    sys.modules["google.generativeai"] = MagicMock()

from nlp_processor import _parse_json_response


class TestParseJsonResponse(unittest.TestCase):
    def test_plain_json(self):
        result = _parse_json_response('{"action": "add", "event_name": "test"}')
        self.assertEqual(result["action"], "add")
        self.assertEqual(result["event_name"], "test")

    def test_markdown_code_block(self):
        text = '```json\n{"action": "search", "query": "test"}\n```'
        result = _parse_json_response(text)
        self.assertEqual(result["action"], "search")

    def test_markdown_code_block_without_lang(self):
        text = '```\n{"status": "complete"}\n```'
        result = _parse_json_response(text)
        self.assertEqual(result["status"], "complete")

    def test_json_embedded_in_text(self):
        text = 'Here is the result: {"action": "add", "event_name": "mtg"} end of response'
        result = _parse_json_response(text)
        self.assertEqual(result["action"], "add")

    def test_nested_json(self):
        text = '{"status": "complete", "event_data": {"name": "test", "tags": ["a", "b"]}}'
        result = _parse_json_response(text)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["event_data"]["name"], "test")

    def test_multiple_json_objects_picks_first(self):
        """テキストに複数のJSONがある場合、最初のものを取得"""
        text = 'first: {"a": 1} second: {"b": 2}'
        result = _parse_json_response(text)
        self.assertEqual(result["a"], 1)

    def test_invalid_json_raises(self):
        with self.assertRaises(ValueError):
            _parse_json_response("no json here at all")

    def test_empty_string_raises(self):
        with self.assertRaises((ValueError, Exception)):
            _parse_json_response("")

    def test_deeply_nested_json(self):
        text = '{"a": {"b": {"c": {"d": 1}}}}'
        result = _parse_json_response(text)
        self.assertEqual(result["a"]["b"]["c"]["d"], 1)


if __name__ == "__main__":
    unittest.main()
