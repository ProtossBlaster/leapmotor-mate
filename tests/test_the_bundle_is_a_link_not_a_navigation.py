"""The diagnostics bundle must be a real download LINK, not a page navigation (#252, @ghuaywen-ai).

> *"I can't download the diagnostic at the moment as I am connecting remotely and there is no
> response clicking the button."*

He is on a phone, inside Home Assistant's ingress — an iframe — and his screenshots come from the HA
app's webview. The button did:

    window.location.href = 'api/diagnostics/bundle?parts=…'

which navigates the whole frame to a `Content-Disposition: attachment`. A webview, and a sandboxed
iframe, drop that on the floor: nothing downloads, nothing errors, the button looks dead. Exactly
what he describes.

Two pages away, the CSV export has always been `<a href="…" download>` — a real link — and nobody
has ever reported it not working. The bundle was the only download in Mate that navigated.

This is not cosmetic: **without it, nobody who runs Mate from a phone can send a bundle at all**,
and the bundle is the first thing every diagnosis asks for.
"""
import pathlib
import re

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"
SETTINGS = WEB / "templates" / "settings.html"


def _src():
    return SETTINGS.read_text()


def test_the_bundle_is_reached_by_an_anchor_with_download():
    """The same shape as the CSV export next door, which works everywhere."""
    src = _src()
    m = re.search(r'<a[^>]*id="diag-dl"[^>]*>', src)
    assert m, "the bundle has no download link"
    tag = m.group(0)
    assert "download" in tag, "the link does not carry the download attribute"
    assert "api/diagnostics/bundle" in tag, f"the link points nowhere useful: {tag}"


def test_nothing_navigates_the_page_to_the_bundle_any_more():
    """The exact line that did nothing in his webview."""
    src = _src()
    assert "location.href = 'api/diagnostics/bundle" not in src
    assert 'location.href = "api/diagnostics/bundle' not in src


def test_the_link_starts_with_every_section_ticked():
    """The checkboxes are all checked on render, so the href must agree with them before anyone
    touches anything — a link that starts empty would download an empty bundle in one click."""
    tag = re.search(r'<a[^>]*id="diag-dl"[^>]*>', _src()).group(0)
    for part in ("info", "poller", "web", "signals"):
        assert part in tag, f"{part} missing from the initial link: {tag}"


def test_ticking_a_box_rewrites_the_link():
    """Whatever keeps the href in step, it has to exist and to name the link and the boxes."""
    src = _src()
    assert "diag-part" in src and "diagSyncDownload" in src, "nothing keeps the link in step"
    fn = src[src.index("function diagSyncDownload"):][:700]
    assert "diag-part" in fn and "diag-dl" in fn
    assert "checked" in fn, "the function does not read which sections are ticked"


def test_the_csv_export_still_shows_the_shape_this_copies():
    """If the export ever stops being a plain <a download>, this file's premise is gone."""
    charges = (WEB / "templates" / "charges.html").read_text()
    assert re.search(r'<a href="api/export/charges\.csv" download', charges)
