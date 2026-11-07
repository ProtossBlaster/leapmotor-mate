"""A new password ends the sessions opened with the old one.

The session cookie was a Fernet token over a constant: any token Mate ever issued stayed valid for its
30 days whatever happened to the password. An owner who changed the password because someone else had
it left that someone logged in. The token now carries a fingerprint of the password it was issued
under, and stops opening the UI when the password changes.
"""
import auth
import db_reader
import crypto


def _clean(monkeypatch, tmp_path):
    import db as D
    path = str(tmp_path / "auth.db")
    D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setattr(crypto, "KEY_PATH", str(tmp_path / "secret.key"), raising=False)
    monkeypatch.setattr(auth, "_fernet", None)
    for var in ("MATE_AUTH_PASSWORD", "SUPERVISOR_TOKEN", "HASSIO_TOKEN"):
        monkeypatch.delenv(var, raising=False)


def test_a_session_survives_while_the_password_stays(monkeypatch, tmp_path):
    _clean(monkeypatch, tmp_path)
    auth.set_password("correct horse")
    assert auth.valid(auth.make_token()) is True


def test_a_new_password_ends_the_old_sessions(monkeypatch, tmp_path):
    _clean(monkeypatch, tmp_path)
    auth.set_password("correct horse")
    old = auth.make_token()
    auth.set_password("battery staple")
    assert auth.valid(old) is False
    assert auth.valid(auth.make_token()) is True


def test_a_new_password_in_the_environment_ends_them_too(monkeypatch, tmp_path):
    _clean(monkeypatch, tmp_path)
    monkeypatch.setenv("MATE_AUTH_PASSWORD", "correct horse")
    old = auth.make_token()
    monkeypatch.setenv("MATE_AUTH_PASSWORD", "battery staple")
    assert auth.valid(old) is False
