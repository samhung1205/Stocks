"""Deployment access boundary: every page/API is private except readiness."""
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.access import PrivateAccessMiddleware


def client(**options):
    app = FastAPI()
    app.add_middleware(PrivateAccessMiddleware, **options)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.api_route("/private", methods=["GET", "POST"])
    def private():
        return {"private": True}

    return TestClient(app)


class PrivateAccessTests(unittest.TestCase):
    def test_local_default_stays_open(self):
        self.assertEqual(client().get("/private").status_code, 200)

    def test_missing_or_partial_credentials_fail_closed(self):
        for options in ({"required": True}, {"username": "test"}, {"password": "test"}):
            with self.subTest(options=options):
                c = client(**options)
                self.assertEqual(c.get("/private").status_code, 503)
                self.assertEqual(c.get("/api/health").json(), {"status": "ok"})
                self.assertEqual(c.post("/api/health").status_code, 503)

    def test_authentication_protects_routes_and_docs(self):
        c = client(required=True, username="test", password="example:password")
        for path in ("/private", "/docs", "/openapi.json", "/", "/api/health/"):
            response = c.get(path)
            self.assertEqual(response.status_code, 401)
            self.assertIn("Basic", response.headers["www-authenticate"])
            self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(c.get("/private", auth=("test", "wrong")).status_code, 401)
        response = c.get("/private", auth=("test", "example:password"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_malformed_authorization_does_not_raise(self):
        c = client(required=True, username="test", password="example")
        for value in ("Basic !!!", "Basic /w==", "Basic YWJj", "Bearer x", "Basic", "Basic " + "a" * 5000):
            self.assertEqual(c.get("/private", headers={"Authorization": value}).status_code, 401)

    def test_cross_site_write_rejected_even_with_valid_auth(self):
        c = client(required=True, username="test", password="example")
        auth = ("test", "example")
        for headers in ({"Origin": "https://evil.example"}, {"Origin": "null"}, {"Origin": "http://["}, {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(c.post("/private", auth=auth, headers=headers).status_code, 403)
        self.assertEqual(c.post("/private", auth=auth, headers={"Origin": "http://testserver"}).status_code, 200)
        self.assertEqual(c.post("/private", auth=auth).status_code, 200)


if __name__ == "__main__":
    unittest.main()
