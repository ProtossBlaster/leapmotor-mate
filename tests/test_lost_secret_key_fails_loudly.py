"""A secret that cannot be decrypted must fail LOUDLY and CLEANLY (#227, @Ng-EY).

He restarted Docker clean, so `/data` was empty and Mate generated a fresh `secret.key`. Then he
restored his old database — whose secrets were encrypted with the key that was gone. From his log:

    04:31:51 [INFO]    crypto: Generated new secret key at /data/secret.key
    04:45:13 [WARNING] crypto: A stored secret could not be decrypted (wrong/lost key?)   ×101
    04:45:14 [WARNING] command_client: Leapmotor login failed: Incorrect account or password
    04:45:35 [WARNING] command_client: Password error limit has reached maximum, try again in 2 min

Two separate defects, both visible in those four lines:

1. `decrypt` returned the RAW CIPHERTEXT on failure, so `enc:v1:gAAAA…` was handed to the cloud as
   his password. It retried until Leapmotor locked the account. Returning the ciphertext exists to
   let legacy PLAINTEXT secrets through — but that case returns earlier (`not is_encrypted`), so in
   the failure branch the value is always ciphertext and passing it on can only do harm.

2. The poller has `_check_decryption`, which says exactly what happened and names the file. The web
   has nothing — and the web is where he was looking. He got 101 identical generic warnings and no
   instruction.

⚠️ The stored value is NEVER erased: put the right key back and everything decrypts again.
"""
import logging
import pathlib

import crypto
import pytest


@pytest.fixture
def keyed(tmp_path, monkeypatch):
    """Encrypt with one key, then read with another — his exact situation."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))

    def use(passphrase):
        monkeypatch.setenv("MATE_SECRET_KEY", passphrase)
        crypto._fernet = None            # the module caches it
    return use


# ── 1. the ciphertext must not travel ─────────────────────────────────────────
def test_a_secret_from_a_lost_key_reads_as_nothing(keyed):
    keyed("the-old-key")
    token = crypto.encrypt("hunter2")
    assert crypto.is_encrypted(token)

    keyed("a-brand-new-key")
    out = crypto.decrypt(token)
    assert out == "", f"the ciphertext leaked out as a value: {out[:24]!r}"
    assert not crypto.is_encrypted(out)


def test_the_ciphertext_never_reaches_a_login(keyed):
    """The consequence, stated as its own test because it is the one that cost him his account:
    whatever comes back must not be usable as a password."""
    keyed("the-old-key")
    token = crypto.encrypt("hunter2")
    keyed("a-brand-new-key")
    assert "enc:v1:" not in crypto.decrypt(token)
    assert "gAAAA" not in crypto.decrypt(token)


def test_the_stored_value_is_untouched(keyed):
    """Reading is not repairing. Put the right key back and the secret is still there — a decrypt
    that erased it would turn a recoverable mistake into a permanent one."""
    keyed("the-old-key")
    token = crypto.encrypt("hunter2")
    keyed("a-brand-new-key")
    crypto.decrypt(token)
    keyed("the-old-key")
    assert crypto.decrypt(token) == "hunter2"


def test_the_normal_paths_are_unchanged(keyed):
    keyed("some-key")
    assert crypto.decrypt(crypto.encrypt("hunter2")) == "hunter2"
    assert crypto.decrypt("") == ""
    assert crypto.decrypt("plain-legacy-value") == "plain-legacy-value"   # pre-encryption installs
    assert crypto.decrypt(None) is None


def test_it_still_says_so_in_the_log(keyed, caplog):
    keyed("the-old-key")
    token = crypto.encrypt("hunter2")
    keyed("a-brand-new-key")
    with caplog.at_level(logging.WARNING):
        crypto.decrypt(token)
    assert any("could not be decrypted" in r.message for r in caplog.records)


# ── 2. asking "can this be read" must not depend on what decrypt returns ──────
def test_can_decrypt_answers_the_question_directly(keyed):
    """`_check_decryption` used to detect failure by testing whether decrypt gave the ciphertext
    back. With the fix above it gives back "", so that test would silently stop firing — the
    warning would vanish exactly where it matters."""
    keyed("the-old-key")
    token = crypto.encrypt("hunter2")
    assert crypto.can_decrypt(token) is True
    keyed("a-brand-new-key")
    assert crypto.can_decrypt(token) is False


def test_plain_and_empty_values_count_as_readable(keyed):
    """They are not encrypted, so there is nothing that could fail — a legacy install must not be
    reported as having a lost key."""
    keyed("some-key")
    assert crypto.can_decrypt("plain-legacy-value") is True
    assert crypto.can_decrypt("") is True
    assert crypto.can_decrypt(None) is True


# ── 3. both copies, and both processes ───────────────────────────────────────
def test_the_two_crypto_copies_stay_identical():
    """`crypto.py` is duplicated in poller/ and web/ (separate import roots). A fix applied to one
    is a fix the other process does not have."""
    root = pathlib.Path(__file__).resolve().parent.parent
    assert (root / "poller" / "crypto.py").read_text() == (root / "web" / "crypto.py").read_text()


def test_the_poller_still_warns_through_the_new_helper():
    root = pathlib.Path(__file__).resolve().parent.parent
    body = (root / "poller" / "db.py").read_text().split("def _check_decryption", 1)[1] \
        .split("\n    def ", 1)[0]
    assert "can_decrypt" in body, "the poller's check still infers failure from decrypt's return"
    assert "secret.key" in body, "the message stopped naming the file the user has to restore"


def test_the_web_checks_it_too():
    """The whole point of #227: the poller knew, the web did not, and the web is the screen."""
    root = pathlib.Path(__file__).resolve().parent.parent
    src = (root / "web" / "db_reader.py").read_text()
    assert "def check_decryption" in src
    assert "secret.key" in src


def test_the_web_runs_that_check_at_startup():
    root = pathlib.Path(__file__).resolve().parent.parent
    src = (root / "web" / "main.py").read_text()
    assert "check_decryption()" in src, "the web check exists but nothing calls it"
