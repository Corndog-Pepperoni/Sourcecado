"""Hosted demo: Supabase sign-in gate, invite list, origins, launcher, owner.

The tokens are real ES256 JWTs signed with a P-256 key made in the test. The
key lookup seam hands the server the matching public key, so no test calls the
network.
"""

from __future__ import annotations

import time
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from coworker import db as db_module
from coworker.hosted_auth import HostedAuth
from coworker.provider import FakeProvider
from coworker.run import main
from coworker.server import TOKEN_HEADER, create_app

SUPABASE = "https://demo-project.supabase.co"
ISSUER = f"{SUPABASE}/auth/v1"
INVITED = "Director@Example.com"
CLUB_TOKEN = "c" * 64
VERCEL = "https://sourcecado-demo.vercel.app"

KEY = ec.generate_private_key(ec.SECP256R1())
OTHER_KEY = ec.generate_private_key(ec.SECP256R1())


def token(
    *,
    email: str = "director@example.com",
    key=KEY,
    aud: str = "authenticated",
    iss: str = ISSUER,
    expires_in: int = 3600,
) -> str:
    now = int(time.time())
    claims = {
        "sub": str(uuid4()),
        "email": email,
        "aud": aud,
        "iss": iss,
        "iat": now - 10,
        "exp": now + expires_in,
        "role": "authenticated",
    }
    return jwt.encode(claims, key, algorithm="ES256", headers={"kid": "test"})


def hosted_auth() -> HostedAuth:
    return HostedAuth(
        SUPABASE, [INVITED, "other@example.com"], key_lookup=lambda _: KEY.public_key()
    )


@pytest.fixture
def hosted(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", f"{VERCEL}, https://second.example.org/")
    app = create_app(
        token=CLUB_TOKEN,
        state=tmp_path,
        provider=FakeProvider(deltas=("hi",)),
        hosted_auth=hosted_auth(),
    )
    return TestClient(app)


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
    app = create_app(
        token=CLUB_TOKEN, state=tmp_path, provider=FakeProvider(deltas=("hi",))
    )
    return TestClient(app)


def get(client: TestClient, value: str | None, origin: str | None = None):
    headers = {}
    if value is not None:
        headers[TOKEN_HEADER] = value
    if origin is not None:
        headers["origin"] = origin
    return client.get("/v1/hello", headers=headers)


# --- HTTP gate --------------------------------------------------------------


def test_no_token_is_401(hosted):
    assert get(hosted, None).status_code == 401


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(token(key=OTHER_KEY), id="wrong-signature"),
        pytest.param(token(expires_in=-60), id="expired"),
        pytest.param(token(aud="anon"), id="wrong-audience"),
        pytest.param(token(iss="https://evil.supabase.co/auth/v1"), id="wrong-issuer"),
        pytest.param("not-a-jwt", id="garbage"),
    ],
)
def test_invalid_token_is_401(hosted, bad):
    response = get(hosted, bad)
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}


def test_valid_token_for_an_email_not_listed_is_403_not_invited(hosted):
    response = get(hosted, token(email="stranger@example.com"))
    assert response.status_code == 403
    assert response.json() == {"error": "not_invited"}


def test_valid_listed_token_reaches_a_normal_route(hosted):
    # The list holds "Director@Example.com"; the comparison ignores case.
    response = get(hosted, token(email="director@example.com"))
    assert response.status_code == 200
    assert response.json()["message"] == "backend alive"


def test_the_club_token_is_refused_in_hosted_mode(hosted):
    assert get(hosted, CLUB_TOKEN).status_code == 401


def test_healthz_answers_with_no_token_and_any_origin(hosted, local):
    for client in (hosted, local):
        response = client.get("/healthz", headers={"origin": "https://evil.example"})
        assert response.status_code == 200
        assert response.json() == {"ok": True}


def test_an_allowed_origin_is_accepted_and_another_is_refused(hosted):
    good = token()
    assert get(hosted, good, origin=VERCEL).status_code == 200
    assert get(hosted, good, origin="https://second.example.org").status_code == 200
    refused = get(hosted, good, origin="https://evil.example")
    assert refused.status_code == 403
    assert refused.json() == {"error": "origin_not_allowed"}


def test_cors_lets_the_vercel_origin_send_the_token_header(hosted):
    preflight = hosted.options(
        "/v1/hello",
        headers={
            "origin": VERCEL,
            "access-control-request-method": "GET",
            "access-control-request-headers": TOKEN_HEADER,
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == VERCEL
    assert TOKEN_HEADER.lower() in preflight.headers["access-control-allow-headers"].lower()


def test_the_browser_can_read_not_invited(hosted):
    # CORS wraps the gate, so the 403 carries the CORS header too.
    response = get(hosted, token(email="stranger@example.com"), origin=VERCEL)
    assert response.status_code == 403
    assert response.headers["access-control-allow-origin"] == VERCEL


# --- WebSocket gate ---------------------------------------------------------


def ws_refused(client: TestClient, *, subprotocols=None, url="/ws/chat", origin=None):
    headers = {"origin": origin} if origin else {}
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(url, subprotocols=subprotocols, headers=headers):
            pass
    return exc.value


def test_ws_without_token_is_refused(hosted):
    assert ws_refused(hosted, subprotocols=["club"]).code == 1008


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(token(key=OTHER_KEY), id="wrong-signature"),
        pytest.param(token(expires_in=-60), id="expired"),
        pytest.param(token(aud="anon"), id="wrong-audience"),
        pytest.param(token(iss="https://evil.supabase.co/auth/v1"), id="wrong-issuer"),
    ],
)
def test_ws_invalid_token_is_refused(hosted, bad):
    assert ws_refused(hosted, subprotocols=["club", bad]).code == 1008


def test_ws_not_invited_closes_with_4403(hosted):
    # Accepted, then closed at once, so a browser can read the code and reason.
    with hosted.websocket_connect(
        "/ws/chat", subprotocols=["club", token(email="stranger@example.com")]
    ) as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 4403
    assert exc.value.reason == "not_invited"


def test_ws_club_token_is_refused_in_hosted_mode(hosted):
    assert ws_refused(hosted, subprotocols=["club", CLUB_TOKEN]).code == 1008


def test_ws_query_token_is_refused_in_hosted_mode(hosted):
    assert ws_refused(hosted, subprotocols=["club"], url=f"/ws/chat?token={token()}").code == 1008


def test_ws_origin_not_listed_is_refused(hosted):
    refused = ws_refused(
        hosted, subprotocols=["club", token()], origin="https://evil.example"
    )
    assert refused.code == 1008


def test_ws_valid_listed_token_from_vercel_chats(hosted):
    with hosted.websocket_connect(
        "/ws/chat", subprotocols=["club", token()], headers={"origin": VERCEL}
    ) as ws:
        ws.send_json({"type": "chat", "text": "hi"})
        while True:
            event = ws.receive_json()
            if event["type"] in ("turn_end", "error"):
                break
        assert event["type"] == "turn_end", event


# --- Local mode is unchanged ------------------------------------------------


def test_local_mode_still_takes_the_club_token(local):
    assert get(local, CLUB_TOKEN).status_code == 200
    assert get(local, token()).status_code == 401
    with local.websocket_connect("/ws/chat", subprotocols=["club", CLUB_TOKEN]):
        pass
    with local.websocket_connect(f"/ws/chat?token={CLUB_TOKEN}"):
        pass


def test_local_mode_refuses_a_vercel_origin(local):
    assert get(local, CLUB_TOKEN, origin=VERCEL).status_code == 403


def test_hosted_mode_comes_from_supabase_url(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    assert HostedAuth.from_env() is None
    monkeypatch.setenv("SUPABASE_URL", SUPABASE + "/")
    monkeypatch.setenv("ALLOWED_EMAILS", " A@x.org ,b@x.org,")
    auth = HostedAuth.from_env()
    assert auth is not None
    assert auth.supabase_url == SUPABASE
    assert auth.allowed_emails == {"a@x.org", "b@x.org"}


# --- Launcher ---------------------------------------------------------------

HOSTED_ENV = {
    "SUPABASE_URL": SUPABASE,
    "ALLOWED_EMAILS": INVITED,
    "WORKSPACE_USER_ID": str(uuid4()),
}


@pytest.mark.parametrize("missing", sorted(HOSTED_ENV))
def test_public_host_is_refused_when_a_hosted_setting_is_missing(
    monkeypatch, capsys, missing
):
    for name, value in HOSTED_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv(missing)
    with pytest.raises(SystemExit) as exc:
        main(["--host", "0.0.0.0"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "loopback" in err and missing in err
    for present in set(HOSTED_ENV) - {missing}:
        assert present not in err


def test_public_host_passes_the_guard_when_all_settings_are_set(monkeypatch, tmp_path):
    from coworker import run

    for name, value in HOSTED_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("CLUB_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(run, "_load_dotenv", lambda path: None)
    reached = {}

    def stop(**kwargs):
        reached.update(kwargs)
        raise SystemExit(0)

    monkeypatch.setattr(run, "create_app", stop)
    monkeypatch.setenv("PUBLIC_URL", "https://api.example.org")
    with pytest.raises(SystemExit) as exc:
        main(["--host", "0.0.0.0", "--port", "8080"])
    assert exc.value.code == 0
    assert reached["public_url"] == "https://api.example.org"
    assert reached["token"] != ""
    # Hosted mode writes no token file.
    assert list(tmp_path.glob("backend-*.token")) == []


def test_launcher_names_a_workspace_user_that_is_not_in_auth_users(
    monkeypatch, tmp_path, capsys, pg_database
):
    from coworker import run
    from coworker.db import Database

    database = Database(pg_database, max_size=2)
    stray = uuid4()
    db_module.set_resolver(lambda base_dir: (database, stray))
    for name, value in HOSTED_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("WORKSPACE_USER_ID", str(stray))
    monkeypatch.setenv("CLUB_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(run, "_load_dotenv", lambda path: None)
    try:
        with pytest.raises(SystemExit) as exc:
            main(["--host", "0.0.0.0", "--port", "8080", "--mock-provider"])
    finally:
        database.close()
    assert exc.value.code == 2
    assert "does not match a Supabase user" in capsys.readouterr().err


# --- Owner of the data ------------------------------------------------------


def test_resolve_returns_workspace_user_id_when_set(monkeypatch):
    db_module.set_resolver(None)
    monkeypatch.setattr(db_module, "_default_database", lambda: "the-database")
    owner = uuid4()
    monkeypatch.setenv("WORKSPACE_USER_ID", str(owner))
    assert db_module.resolve("/any/dir") == ("the-database", owner)
    monkeypatch.delenv("WORKSPACE_USER_ID")
    assert db_module.resolve("/any/dir") == ("the-database", db_module.DEV_USER_ID)
    assert db_module.DEV_USER_ID == UUID("00000000-0000-4000-8000-000000000001")
