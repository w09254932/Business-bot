import unittest
from datetime import datetime, timedelta, timezone

from widget_calculator import (
    encode_widget_answer,
    get_history_page,
    load_widget_html,
    parse_widget_request,
)


class FakeDoc:
    def __init__(self, data):
        self.data = data

    def to_dict(self):
        return self.data


class FakeQuery:
    def __init__(self, docs):
        self.docs = list(docs)
        self.start = 0
        self.count = 100

    def where(self, key, operator, value):
        self.docs = [row for row in self.docs if row.get(key) == value]
        return self

    def order_by(self, key, direction=None):
        self.docs.sort(key=lambda row: row.get(key), reverse=(direction == "DESCENDING"))
        return self

    def offset(self, start):
        self.start = start
        return self

    def limit(self, count):
        self.count = count
        return self

    def stream(self):
        return [FakeDoc(row) for row in self.docs[self.start:self.start + self.count]]


class FakeCollection:
    def __init__(self, docs):
        self.docs = docs

    def where(self, key, operator, value):
        return FakeQuery([row for row in self.docs if row.get(key) == value])


class FakeDB:
    def __init__(self, docs):
        self.docs = docs

    def collection(self, name):
        return FakeCollection(self.docs)


class WidgetTests(unittest.TestCase):
    def test_parse_calculation(self):
        self.assertEqual(parse_widget_request("calc|individual|25000|18000"),
                         ("calc", "individual", 25000, 18000))
        self.assertEqual(parse_widget_request("calc|home|0|15000"),
                         ("calc", "home", 0, 15000))
        self.assertEqual(parse_widget_request("calc|team|5001|5000"),
                         ("calc", "team", 5001, 5000))

    def test_parse_history(self):
        self.assertEqual(parse_widget_request("history|all|2"), ("history", "all", 2))

    def test_invalid_requests_are_rejected(self):
        invalid = [
            "calc|evil|10|20", "calc|individual|-1|20", "calc|individual|1.5|20",
            "history|all|-1", "x" * 65, "calc|individual||20", "calc|team|1|2|3",
        ]
        for request in invalid:
            with self.subTest(request=request):
                with self.assertRaises(ValueError):
                    parse_widget_request(request)

    def test_widget_size_is_within_telegram_limits(self):
        html = load_widget_html()
        self.assertIn("TelegramWidget", html)

    def test_answer_data_is_within_1024_bytes(self):
        data = encode_widget_answer({"ok": True, "kind": "result", "message": "تم"})
        self.assertLessEqual(len(data.encode("utf-8")), 1024)

    def test_history_page_and_pagination(self):
        now = datetime.now(timezone.utc)
        docs = [
            {"user_id": 7, "mode": "individual", "my_number": i,
             "opp_number": i * 2, "my_result": 10, "result": "فوز ✅",
             "ts": now + timedelta(minutes=i)}
            for i in range(7)
        ]
        first = get_history_page(FakeDB(docs), 7, "all", 0)
        self.assertTrue(first["ok"])
        self.assertEqual(len(first["rows"]), 5)
        self.assertTrue(first["has_more"])
        self.assertEqual(first["rows"][0]["mine"], 6)
        second = get_history_page(FakeDB(docs), 7, "all", 1)
        self.assertEqual(len(second["rows"]), 2)
        self.assertFalse(second["has_more"])

    def test_history_is_scoped_to_user(self):
        now = datetime.now(timezone.utc)
        db = FakeDB([
            {"user_id": 1, "mode": "team", "my_number": 10, "opp_number": 8,
             "my_result": 30, "result": "فوز ✅", "ts": now},
            {"user_id": 2, "mode": "team", "my_number": 90, "opp_number": 80,
             "my_result": 30, "result": "فوز ✅", "ts": now},
        ])
        result = get_history_page(db, 1, "all", 0)
        self.assertEqual([row["mine"] for row in result["rows"]], [10])


if __name__ == "__main__":
    unittest.main()
