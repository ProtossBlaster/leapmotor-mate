"""Two small ways data goes to the wrong place (30/08 audit).

CSV FORMULA INJECTION. Mate's exports write free-text columns straight out. A cell beginning
`=`, `+`, `-` or `@` is a FORMULA to Excel, LibreOffice and Sheets — so opening an export can run
what the text says. It is not only the owner's own notes: `location_name` comes from OpenStreetMap
and Open Charge Map, which anybody can edit, and an export is exactly what gets mailed to someone
else when a charge looks wrong.

The neutralisation is the standard one — prefix the cell with an apostrophe — applied ONLY to
strings, because a numeric column may legitimately start with a minus and must stay a number.

CAR-IMAGE CACHE. The parsed ~39-layer package is memoised under `len(package_bytes)`. Two cars
whose packages happen to weigh the same byte count share the entry, and the second is served the
first one's picture. The length is a cheap proxy for identity that is not one.

The image-cache half runs anywhere; the CSV half reads `_csv_safe` out of `main`, which needs
fastapi — absent from the minimal CI environment, so those skip there rather than fail.
"""
import car_image
import pytest


# ── CSV ─────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("evil", ["=1+1", "+1", "-1+1", "@SUM(A1)", "=HYPERLINK(\"http://x\")"])
def test_a_formula_in_a_text_cell_is_neutralised(evil):
    pytest.importorskip("fastapi", reason="_csv_safe lives in web.main")
    from main import _csv_safe
    out = _csv_safe(evil)
    assert out.startswith("'"), f"{evil!r} would be a formula, got {out!r}"
    assert out[1:] == evil, "the text itself must survive intact — this is display, not censorship"


def test_ordinary_text_is_left_alone():
    pytest.importorskip("fastapi", reason="_csv_safe lives in web.main")
    from main import _csv_safe
    for ok in ["Ionity Milano", "casa", "", "note with = inside", "3 phase"]:
        assert _csv_safe(ok) == ok


def test_numbers_are_never_touched():
    """A negative number starts with '-' and must stay a number, not become text."""
    pytest.importorskip("fastapi", reason="_csv_safe lives in web.main")
    from main import _csv_safe
    for n in (-1.5, -3, 0, 42.0, None, True):
        assert _csv_safe(n) is n


def test_every_csv_writer_that_carries_free_text_neutralises_it():
    """One fix on one writer is how this defect comes back. The generic export and the beta
    bundle's logbook both carry text a human typed; the research trip/fuel writers carry only
    numbers and timestamps, and are deliberately left alone."""
    import pathlib, re
    src = pathlib.Path("web/main.py").read_text()
    for line in src.splitlines():
        if "writerows" not in line or "note" not in line:
            continue
        assert "_csv_safe" in line, f"free text written raw:\n    {line.strip()}"


# ── image cache ─────────────────────────────────────────────────────────────────
def test_two_packages_of_the_same_size_are_not_the_same_car(monkeypatch):
    """Same byte count, different bytes: the second car must be decoded, not served the first."""
    decoded = []

    class _Pkg:
        @staticmethod
        def from_zip(b):
            decoded.append(b)
            return f"pkg-{b[:1].hex()}"

    import sys, types
    mod = types.ModuleType("leapmotor_api.image")
    mod.CarImagePackage = _Pkg
    monkeypatch.setitem(sys.modules, "leapmotor_api.image", mod)
    car_image.clear_cache()

    a, b = b"\xaa" + b"\x00" * 999, b"\xbb" + b"\x00" * 999
    assert len(a) == len(b)
    first = car_image._package(a)
    second = car_image._package(b)

    assert first != second, "two different packages came back as one car's image"
    assert len(decoded) == 2


def test_the_same_package_is_still_decoded_only_once(monkeypatch):
    """The cache must keep earning its keep: decoding is the costly part."""
    decoded = []

    class _Pkg:
        @staticmethod
        def from_zip(b):
            decoded.append(b)
            return "pkg"

    import sys, types
    mod = types.ModuleType("leapmotor_api.image")
    mod.CarImagePackage = _Pkg
    monkeypatch.setitem(sys.modules, "leapmotor_api.image", mod)
    car_image.clear_cache()

    same = b"\xcc" * 500
    car_image._package(same)
    car_image._package(same)
    assert len(decoded) == 1
