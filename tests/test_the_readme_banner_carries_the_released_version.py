"""The README's version banner is the sixth place a release has to touch, and nothing watched it.

Measured on 24/09/2026: the banner had read **v3.15.9 for eight releases** (v3.16.0 → v3.18.1)
without a single test going red. @jcconca noticed it and opened PR #305 to align it.

`test_release_version_is_consistent_across_runtime_image_changelog_and_manuals` checks five places
— MATE_VERSION, the Dockerfile label, the CHANGELOG and the five manuals — and this one was not
among them. It is the first thing anyone reads on the repository's front page, and it also links
the release notes: stale, it points a reader at the wrong document.

Both languages, because the banner is written twice.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _released_version() -> str:
    main = (ROOT / "web" / "main.py").read_text(encoding="utf-8")
    m = re.search(r'^MATE_VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+(?:-rc\.[0-9]+)?)"', main, re.M)
    assert m, "MATE_VERSION is not where every release expects it"
    return m.group(1)


def _banners() -> list:
    """Every `**vX.Y.Z:**` line in the README — the English one near the top and the Italian one."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    return re.findall(r"^\*\*v([0-9]+\.[0-9]+\.[0-9]+(?:-rc\.[0-9]+)?):\*\*", readme, re.M)


def test_the_readme_says_the_version_that_is_released():
    v = _released_version()
    found = _banners()
    assert found, "the README has no version banner any more"
    assert set(found) == {v}, f"README banner(s) at {sorted(set(found))}, MATE_VERSION is {v}"


def test_both_languages_carry_it():
    assert len(_banners()) == 2, "the banner is written twice — English and Italian"


def test_the_banner_links_the_notes_of_that_same_release():
    """A banner with the right number and last release's link is still wrong."""
    v = _released_version()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    links = re.findall(r"docs/releases/v([0-9]+\.[0-9]+\.[0-9]+(?:-rc\.[0-9]+)?)\.md", readme)
    assert links, "the banner no longer links the release notes"
    assert set(links) == {v}, f"the README links notes for {sorted(set(links))}, released is {v}"


def test_those_notes_exist_and_carry_an_italian_section():
    """Every release's notes are written in both languages; the Italian banner links `#italiano`."""
    v = _released_version()
    notes = ROOT / "docs" / "releases" / f"v{v}.md"
    assert notes.exists(), f"{notes.name} is missing — the banner links a document that is not there"
    text = notes.read_text(encoding="utf-8")
    assert re.search(r"^##\s+Italiano\s*$", text, re.M), "the release notes have no Italiano section"
