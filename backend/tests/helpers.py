import io
import uuid

import fitz
from fastapi.testclient import TestClient

TEST_PASSWORD = "correct horse battery staple"
CSRF_HEADER = "X-CSRF-Token"


def fetch_csrf(client: TestClient) -> str:
    token = client.get("/auth/csrf").json()["csrf_token"]
    client.headers[CSRF_HEADER] = token
    return token


def unique_email(prefix: str = "user") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"


def signup(client: TestClient, email: str | None = None, password: str = TEST_PASSWORD) -> dict:
    """Register through the real API; leaves the client signed in with its CSRF header set."""
    fetch_csrf(client)
    resp = client.post("/auth/register", json={"email": email or unique_email(), "password": password})
    assert resp.status_code == 201, resp.text
    client.headers[CSRF_HEADER] = resp.json()["csrf_token"]
    return resp.json()["user"]


def login(client: TestClient, email: str, password: str = TEST_PASSWORD):
    fetch_csrf(client)
    resp = client.post("/auth/login", json={"email": email, "password": password})
    if resp.status_code == 200:
        client.headers[CSRF_HEADER] = resp.json()["csrf_token"]
    return resp


def start_demo(client: TestClient):
    fetch_csrf(client)
    resp = client.post("/auth/demo")
    if resp.status_code == 201:
        client.headers[CSRF_HEADER] = resp.json()["csrf_token"]
    return resp


def make_pdf(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(fitz.Rect(50, 50, 550, 800), text)
    return doc.tobytes()


def upload_pdf(client: TestClient, text: str, filename: str = "doc.pdf") -> dict:
    resp = client.post("/documents/upload", files={"file": (filename, io.BytesIO(make_pdf(text)), "application/pdf")})
    assert resp.status_code == 200, resp.text
    return resp.json()["document"]


def set_cookie_headers(resp) -> dict[str, str]:
    """Map cookie name -> its raw Set-Cookie header, to assert on flags."""
    headers = {}
    for raw in resp.headers.get_list("set-cookie"):
        headers[raw.split("=", 1)[0]] = raw
    return headers
