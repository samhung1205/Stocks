"""Stage J regression: bad-input payloads on stateless routes must map to 400,
never leak an unhandled KeyError/ValueError through as a 500.

Both routes covered here (`/journal/position-size`, `/finance/simulate`) are
pure computation over the request payload -- no DB fixture needed.
"""
import unittest

from fastapi.testclient import TestClient

from app.main import app


class PositionSizeErrorHandlingTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_missing_field_is_400_not_500(self):
        r = self.client.post("/api/journal/position-size", json={"entry": 100, "stop": 90})
        self.assertEqual(r.status_code, 400)

    def test_non_numeric_field_is_400_not_500(self):
        r = self.client.post("/api/journal/position-size",
                             json={"entry": "x", "stop": 90, "max_loss": 1000})
        self.assertEqual(r.status_code, 400)

    def test_valid_payload_still_succeeds(self):
        r = self.client.post("/api/journal/position-size",
                             json={"entry": 100, "stop": 90, "max_loss": 1000})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["max_shares"], 100)


class FinanceSimulateErrorHandlingTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_non_numeric_age_is_400_not_500(self):
        r = self.client.post("/api/finance/simulate", json={"age": "abc"})
        self.assertEqual(r.status_code, 400)

    def test_valid_payload_still_succeeds(self):
        r = self.client.post("/api/finance/simulate", json={"age": 30, "retire_age": 65})
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
