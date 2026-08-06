"""Tests for the at-rest credential encryption (poller/web crypto.py, identical copies)."""
import importlib


def _fresh_crypto(monkeypatch, tmp_path, passphrase=None):
    """Reload crypto with a clean key cache + a tmp key location."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    if passphrase is None:
        monkeypatch.delenv("MATE_SECRET_KEY", raising=False)
    else:
        monkeypatch.setenv("MATE_SECRET_KEY", passphrase)
    import crypto
    importlib.reload(crypto)
    return crypto


def test_roundtrip(monkeypatch, tmp_path):
    c = _fresh_crypto(monkeypatch, tmp_path)
    tok = c.encrypt("S3cr3t p@ss with spaces")
    assert tok.startswith("enc:v1:")
    assert c.is_encrypted(tok)
    assert c.decrypt(tok) == "S3cr3t p@ss with spaces"


def test_empty_and_plaintext_passthrough(monkeypatch, tmp_path):
    c = _fresh_crypto(monkeypatch, tmp_path)
    assert c.encrypt("") == ""
    assert c.decrypt("") == ""
    assert c.decrypt("legacy_plaintext") == "legacy_plaintext"   # no marker -> unchanged
    assert not c.is_encrypted("legacy_plaintext")


def test_idempotent_encrypt(monkeypatch, tmp_path):
    c = _fresh_crypto(monkeypatch, tmp_path)
    once = c.encrypt("x")
    assert c.encrypt(once) == once   # already encrypted -> no double-wrap


def test_key_file_created_0600(monkeypatch, tmp_path):
    c = _fresh_crypto(monkeypatch, tmp_path)
    c.encrypt("trigger key creation")
    keyfile = tmp_path / "secret.key"
    assert keyfile.exists()
    assert (keyfile.stat().st_mode & 0o777) == 0o600


def test_env_passphrase_override_no_keyfile(monkeypatch, tmp_path):
    c = _fresh_crypto(monkeypatch, tmp_path, passphrase="my-passphrase")
    tok = c.encrypt("hello")
    assert c.decrypt(tok) == "hello"
    assert not (tmp_path / "secret.key").exists()   # env override -> no key file written


def test_wrong_key_cannot_decrypt(monkeypatch, tmp_path):
    c1 = _fresh_crypto(monkeypatch, tmp_path, passphrase="key-A")
    tok = c1.encrypt("topsecret")
    c2 = _fresh_crypto(monkeypatch, tmp_path, passphrase="key-B")
    # Wrong key -> nothing comes out. This assertion used to require the opposite — that decrypt
    # handed the raw ciphertext back — and that is what sent `enc:v1:…` to Leapmotor as @Ng-EY's
    # password until his account locked (#227). The secret is unreadable, and "unreadable" is "".
    assert c2.decrypt(tok) == ""
    assert c2.can_decrypt(tok) is False
    # That the stored ciphertext survives — so the right key still opens it — is asserted in
    # test_lost_secret_key_fails_loudly.py, which owns that whole scenario.
