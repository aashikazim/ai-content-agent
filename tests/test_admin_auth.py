import json
import sqlite3
import stat

import pytest

from app.services.admin_auth_service import (
    AdminAuthService,
    AuthInvalidCredentials,
    AuthNotConfigured,
    AuthRateLimited,
)


PASSWORD = "A sufficiently long test password!"


@pytest.fixture
def clock():
    return [1_800_000_000]


@pytest.fixture
def auth(tmp_path, clock):
    service = AdminAuthService(
        db_path=tmp_path / "existing.db",
        credentials_path=tmp_path / "credentials" / "admin.json",
        clock=lambda: clock[0],
    )
    service.configure_admin("admin", PASSWORD)
    return service


def test_unconfigured_auth_refuses_login(tmp_path):
    service = AdminAuthService(tmp_path / "test.db", tmp_path / "admin.json")
    assert not service.configured
    with pytest.raises(AuthNotConfigured):
        service.login("admin", PASSWORD, "local")


def test_account_file_contains_only_hash_and_is_private(auth):
    document = json.loads(auth.credentials_path.read_text())
    assert document["username"] == "admin"
    assert PASSWORD not in auth.credentials_path.read_text()
    assert document["scrypt"]["hash"]
    assert stat.S_IMODE(auth.credentials_path.stat().st_mode) == 0o600
    with pytest.raises(ValueError, match="already configured"):
        auth.configure_admin("another-admin", PASSWORD)


def test_sessions_survive_new_service_and_store_token_digest(auth, clock):
    login = auth.login("admin", PASSWORD, "local")
    second_service = AdminAuthService(
        auth.db_path, auth.credentials_path, clock=lambda: clock[0]
    )
    session = second_service.get_session(login["token"])
    assert session == {key: login[key] for key in ("username", "csrf_token", "expires_at")}
    with auth.connect() as connection:
        row = connection.execute("SELECT * FROM admin_sessions").fetchone()
    assert row["token_hash"] != login["token"]
    assert login["token"] not in tuple(row)
    assert session["csrf_token"] != login["token"]


@pytest.mark.parametrize("username,password", [
    ("wrong", PASSWORD), ("admin", "Wrong password with enough length"),
    ("admin", "x" * 1025), ("admin", ""),
    ("admin", "invalid-unicode-\ud800"), ("bad-\ud800", PASSWORD),
])
def test_bad_credentials_have_generic_failure(auth, username, password):
    with pytest.raises(AuthInvalidCredentials, match="Invalid username or password"):
        auth.login(username, password, "local")


def test_login_failure_limit_persists_across_instances(auth, clock):
    for _ in range(auth.FAILURE_LIMIT):
        service = AdminAuthService(auth.db_path, auth.credentials_path, clock=lambda: clock[0])
        with pytest.raises(AuthInvalidCredentials):
            service.login("admin", "Incorrect test password", "local")
    with pytest.raises(AuthRateLimited) as failure:
        auth.login("admin", PASSWORD, "local")
    assert failure.value.retry_after == auth.LOCKOUT_SECONDS
    # Varying the username does not bypass the client's limit.
    with pytest.raises(AuthRateLimited):
        auth.login("other", PASSWORD, "local")
    clock[0] += auth.LOCKOUT_SECONDS
    assert auth.login("admin", PASSWORD, "local")["username"] == "admin"


def test_success_clears_prior_failed_attempts(auth):
    with pytest.raises(AuthInvalidCredentials):
        auth.login("admin", "Incorrect test password", "local")
    auth.login("admin", PASSWORD, "local")
    with auth.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM admin_login_attempts").fetchone()[0] == 0


def test_session_has_hard_expiry_and_cleanup(auth, clock):
    session = auth.login("admin", PASSWORD, "local")
    clock[0] = session["expires_at"] - 1
    assert auth.get_session(session["token"])
    clock[0] += 1
    assert auth.get_session(session["token"]) is None
    with auth.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM admin_sessions").fetchone()[0] == 0


def test_logout_revokes_session(auth):
    session = auth.login("admin", PASSWORD, "local")
    auth.logout(session["token"])
    auth.logout(session["token"])
    assert auth.get_session(session["token"]) is None


def test_local_reset_replaces_only_admin_and_revokes_sessions(auth):
    session = auth.login("admin", PASSWORD, "local")
    auth.configure_admin("new-admin", "A completely different strong password!", reset=True)
    assert auth.get_session(session["token"]) is None
    with pytest.raises(AuthInvalidCredentials):
        auth.login("admin", PASSWORD, "local")
    assert auth.login("new-admin", "A completely different strong password!", "local")


def test_old_credential_id_invalidates_a_racing_session(auth):
    session = auth.login("admin", PASSWORD, "local")
    document = json.loads(auth.credentials_path.read_text())
    document["credential_id"] = "a" * 64
    auth.credentials_path.write_text(json.dumps(document))
    assert auth.get_session(session["token"]) is None


def test_existing_database_content_is_preserved(tmp_path):
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE jobs (job_id TEXT PRIMARY KEY, status TEXT)")
        connection.execute("INSERT INTO jobs VALUES ('legacy-job', 'approved')")
        connection.execute("CREATE TABLE publications (publication_id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO publications VALUES ('legacy-publication')")
    auth = AdminAuthService(db_path, tmp_path / "admin.json")
    auth.initialize()
    with auth.connect() as connection:
        assert connection.execute("SELECT status FROM jobs").fetchone()[0] == "approved"
        assert connection.execute("SELECT publication_id FROM publications").fetchone()[0] == "legacy-publication"


@pytest.mark.parametrize("username,password", [
    ("a", PASSWORD), ("admin\n", PASSWORD), ("admin", "too-short"),
    ("admin", " " * 20), ("admin", "x" * 1025),
    ("admin", "invalid-unicode-\ud800"),
])
def test_invalid_setup_values_are_rejected(tmp_path, username, password):
    auth = AdminAuthService(tmp_path / "test.db", tmp_path / "admin.json")
    with pytest.raises(ValueError):
        auth.configure_admin(username, password)
    assert not auth.credentials_path.exists()


@pytest.mark.parametrize("document", [
    "not json", "[]", "{}", '{"version":1,"username":"admin","scrypt":null}',
])
def test_malformed_credentials_fail_closed(tmp_path, document):
    credential_path = tmp_path / "admin.json"
    credential_path.write_text(document)
    auth = AdminAuthService(tmp_path / "test.db", credential_path)
    assert not auth.configured
    assert auth.get_session("a" * 43) is None
    with pytest.raises(AuthNotConfigured):
        auth.login("admin", PASSWORD, "local")


def test_credential_hash_parameters_are_not_trusted(auth):
    document = json.loads(auth.credentials_path.read_text())
    document["scrypt"]["n"] = 2 ** 30
    auth.credentials_path.write_text(json.dumps(document))
    assert not auth.configured


def test_invalid_session_tokens_fail_closed(auth):
    for token in (None, "", "invalid", "x" * 4096, "ü" * 43):
        assert auth.get_session(token) is None


def test_cli_uses_hidden_password_and_explicit_reset(tmp_path, monkeypatch, capsys):
    from scripts import setup_admin

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "cli.db"))
    monkeypatch.setenv("ADMIN_CREDENTIALS_FILE", str(tmp_path / "admin.json"))
    monkeypatch.setattr("sys.argv", ["setup_admin.py", "--username", "admin"])
    passwords = iter([PASSWORD, PASSWORD])
    monkeypatch.setattr(setup_admin.getpass, "getpass", lambda prompt: next(passwords))
    setup_admin.main()
    assert PASSWORD not in capsys.readouterr().out
    with pytest.raises(SystemExit) as error:
        setup_admin.main()
    assert error.value.code == 2
