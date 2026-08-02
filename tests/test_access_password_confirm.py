"""The access password is typed twice, and the two must match (#214 @rop12770).

He set a password in Settings → Access, gets in from his PC, and his phone says it's wrong. With a
single box a typo is hashed in silence — and the machine you set it on keeps working, because the
browser saved what you actually typed. The mistake only shows up on the NEXT device, by which time
there is no way left to know which key you pressed.

There is a way out and it already existed: the change-password form doesn't ask for the old one, so
anyone with a live session can set a new one. That is the answer for someone already locked out;
this is the answer for everyone after them.
"""
import pytest

pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")

import auth
import db as D
import db_reader
import main


class _Form(dict):
    pass


def _post(monkeypatch, tmp_path, **fields):
    """Drive the endpoint directly with a given form, on a throwaway database."""
    import asyncio

    D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    db_reader._lang_memo[0] = None

    class _Req:
        async def form(self):
            return _Form(fields)

    return asyncio.new_event_loop().run_until_complete(main.set_ui_password(_Req()))


def test_matching_pair_is_accepted(tmp_path, monkeypatch):
    r = _post(monkeypatch, tmp_path, password="correct-horse", password2="correct-horse")
    assert r.status_code == 204
    assert auth.check_password("correct-horse")


def test_a_typo_in_the_second_box_stores_nothing(tmp_path, monkeypatch):
    r = _post(monkeypatch, tmp_path, password="correct-horse", password2="correct-hoarse")
    assert r.status_code == 422
    # and — the part that matters — NEITHER of them is now the password
    assert not auth.check_password("correct-horse")
    assert not auth.check_password("correct-hoarse")


def test_the_confirmation_cannot_simply_be_omitted(tmp_path, monkeypatch):
    # A form (or a script) that sends only `password` must not slip past the cross-check: a
    # password nobody typed twice is exactly the one that locks its owner out.
    r = _post(monkeypatch, tmp_path, password="correct-horse")
    assert r.status_code == 422
    assert not auth.check_password("correct-horse")


def test_too_short_is_still_refused_even_when_both_boxes_agree(tmp_path, monkeypatch):
    r = _post(monkeypatch, tmp_path, password="short", password2="short")
    assert r.status_code == 422
    assert not auth.check_password("short")
