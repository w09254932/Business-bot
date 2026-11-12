import unittest
from datetime import datetime, timezone, timedelta
from widget_calculator import _history_page, _parse_request, load_widget_html


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
        self.docs = [d for d in self.docs if d.get(key) == value]
        return self

    def order_by(self, key, direction=None):
        self.docs.sort(key=lambda d: d.get(key), reverse=(direction == "DESCENDING"))
        return self

    def offset(self, start):
        self.start = start
        return self

    def limit(self, count):
        self.count = count
        return self

    def stream(self):
        return [FakeDoc(d) for d in self.docs[self.start:self.start + self.count]]


class FakeCollection:
    def __init__(self, docs):
        self.docs = docs

    def where(self, key, operator, value):
        return FakeQuery([d for d in self.docs if d.get(key) == value])


class FakeDB:
    def __init__(self, docs):
        self.docs = docs

    def collection(self, name):
        return FakeCollection(self.docs)


class WidgetProtocolTests(unittest.TestCase):
    def test_parse_calculation(self):
        self.assertEqual(_parse_request("calc|individual|25000|18000"),
                         ("calc", "individual", 25000, 18000))
        self.assertEqual(_parse_request("calc|home|0|15000"),
                         ("calc", "home", 0, 15000))
        self.assertEqual(_parse_request("calc|team|5001|5000"),
                         ("calc", "team", 5001, 5000))

    def test_parse_history(self):
        self.assertEqual(_parse_request("history|all|2"), ("history", "all", 2))

    def test_reject_invalid_inputs(self):
        for value in (
            "calc|evil|10|20",
            "calc|individual|-1|20",
            "calc|individual|1.5|20",
            "history|all|-1",
            "x" * 65,
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    _parse_request(value)

    def test_html_meets_telegram_size_limits(self):
        html = load_widget_html()
        self.assertIn("W.request(data)", html)

    def test_history_page_returns_records_and_pagination(self):
        now = datetime.now(timezone.utc)
        docs = [
            {"user_id": 7, "mode": "individual", "my_number": i, "opp_number": 2*i,
             "my_result": 10, "result": "فوز ✅", "ts": now + timedelta(minutes=i)}
            for i in range(7)
        ]
        result = _history_page(FakeDB(docs), 7, "all", 0)
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["rows"]), 5)
        self.assertTrue(result["has_more"])
        self.assertEqual(result["rows"][0]["mine"], 6)
        page2 = _history_page(FakeDB(docs), 7, "all", 1)
        self.assertEqual(len(page2["rows"]), 2)
        self.assertFalse(page2["has_more"])


if __name__ == "__main__":
    unittest.main()
