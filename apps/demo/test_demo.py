import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "mcp-server"))
sys.path.insert(0, _HERE)
os.environ.pop("GEMINI_API_KEY", None)

from fastapi.testclient import TestClient
import app as demo_app

client = TestClient(demo_app.app)


def login(email, pw):
    r = client.post("/login", json={"email": email, "password": pw})
    return r


def hdr(email, pw):
    return {"Authorization": "Bearer " + login(email, pw).json()["token"]}


class AuthTests(unittest.TestCase):
    def test_login_ok_and_caps(self):
        d = login("codelab_user_1@lab.com", "user_1").json()
        self.assertEqual(d["role"], "user")
        self.assertEqual(d["capabilities"], ["query"])

    def test_login_bad_password(self):
        self.assertEqual(login("codelab_user_1@lab.com", "wrong").status_code, 401)

    def test_login_admin_caps(self):
        d = login("codelab_admin_1@lab.com", "admin_1").json()
        self.assertEqual(sorted(d["capabilities"]),
                         ["author", "manage_users", "promote", "query", "run_eval"])

    def test_unauthenticated_ask(self):
        self.assertEqual(client.post("/ask", json={"question": "x"}).status_code, 401)


class GatingTests(unittest.TestCase):
    def test_user_cannot_author(self):
        self.assertEqual(client.post("/author", headers=hdr("codelab_user_1@lab.com", "user_1")).status_code, 403)

    def test_developer_author_eval_but_not_promote(self):
        h = hdr("codelab_developer_1@lab.com", "developer_1")
        self.assertEqual(client.post("/author", headers=h).status_code, 200)
        self.assertEqual(client.post("/run_eval", headers=h).status_code, 200)
        self.assertEqual(client.post("/promote", headers=h).status_code, 403)
        self.assertEqual(client.get("/users", headers=h).status_code, 403)

    def test_admin_all(self):
        h = hdr("codelab_admin_1@lab.com", "admin_1")
        self.assertEqual(client.post("/promote", headers=h).status_code, 200)
        self.assertEqual(client.get("/users", headers=h).status_code, 200)

    def test_contributor_author_only(self):
        h = hdr("codelab_contributor_1@lab.com", "contributor_1")
        self.assertEqual(client.post("/author", headers=h).status_code, 200)
        self.assertEqual(client.post("/run_eval", headers=h).status_code, 403)


class AskTests(unittest.TestCase):
    def test_user_revisit_computes_real_answer(self):
        h = hdr("codelab_user_1@lab.com", "user_1")
        r = client.post("/ask", headers=h, json={
            "question": "What is the mean revisit period over Tempe Arizona for the VIIRS instrument onboard NOAA 20?"})
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertIn("hours", d["answer"].lower())
        self.assertEqual(d["steps"][0]["tool"], "revisit_over_point")
        self.assertIn("9.", d["answer"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
