"""Which Leapmotor account is this instance using? — beta #13, @ebagnoli.

He runs several Mate instances against several Leapmotor accounts and asked whether Mate could
show which account a given instance is logged in with. It could not: the Settings "Vehicle" card
named the model and the VIN — both properties of the CAR — and never the login, so two instances
watching the same car looked identical from the inside.

The value has two homes and they are not interchangeable: the setup wizard writes it to
`settings`, while a dev or add-on install passes it in `LEAPMOTOR_USER` and the setting stays
empty. `get_account_user()` is the one definition of that precedence — the command client reads
the same function for its credentials, so the account the page names is the account Mate logs in
with, not a second guess at it.
"""
import db_reader as R


def _fresh(tmp_path, monkeypatch):
    """A DB with a settings table and nothing in it, and no ambient environment."""
    monkeypatch.setattr(R, "DB_PATH", str(tmp_path / "s.db"), raising=False)
    monkeypatch.delenv("LEAPMOTOR_USER", raising=False)
    db = R._conn_rw()
    db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    db.commit()
    return db


def test_the_wizards_account_is_the_one_reported(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    R.set_setting("leapmotor_user", "enrico@example.com")
    assert R.get_account_user() == "enrico@example.com"


def test_env_only_install_still_names_its_account(tmp_path, monkeypatch):
    """Add-on/compose: nothing in settings, the login lives in the environment. Without the
    fallback the card would go blank on exactly the installs that have several instances."""
    _fresh(tmp_path, monkeypatch)
    monkeypatch.setenv("LEAPMOTOR_USER", "beta@example.com")
    assert R.get_account_user() == "beta@example.com"


def test_the_stored_account_wins_over_a_stale_environment(tmp_path, monkeypatch):
    """A container that once had the env var must not keep naming the OLD account after the
    user logged in with another one — that is the exact confusion this row exists to end."""
    _fresh(tmp_path, monkeypatch)
    monkeypatch.setenv("LEAPMOTOR_USER", "old@example.com")
    R.set_setting("leapmotor_user", "new@example.com")
    assert R.get_account_user() == "new@example.com"


def test_no_account_yet_is_empty_not_a_crash(tmp_path, monkeypatch):
    """Fresh install, wizard not finished: the caller hides the row rather than showing 'None'."""
    _fresh(tmp_path, monkeypatch)
    assert R.get_account_user() == ""


def test_the_settings_card_actually_renders_it(tmp_path, monkeypatch):
    """The value is useless if the card never prints it. The suite has no HTTP client, so this
    checks the template itself: the row must live INSIDE the vehicle card (next to the VIN), be
    guarded so a fresh install shows nothing, and reuse the existing translated label rather
    than introducing an eighth-language-shaped hole."""
    import pathlib
    tpl = (pathlib.Path(__file__).resolve().parent.parent
           / "web" / "templates" / "settings.html").read_text(encoding="utf-8")
    vin_at = tpl.index("t('vin')")
    row_at = tpl.index("account_user")
    assert 0 < row_at - vin_at < 800, "the account row is not beside the VIN in the vehicle card"
    assert "{% if account_user %}" in tpl, "an install with no account would print an empty row"
    assert "t('setup_email')" in tpl, "must reuse the label that already exists in all 7 locales"
    assert "{{ account_user }}" in tpl, "the row prints a label but never the account itself"
