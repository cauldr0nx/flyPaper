"""Scope parsing, against the shapes real published bug bounty scopes actually contain.

Every rejected shape below was found in a survey of 164 published program scope tables
(1,930 assets). The hostnames here are synthetic stand-ins with the same structure - the
real ones belong to the programs, not in this repository.

The property under test throughout is that a scope file **cannot authorise more, or less,
than the operator meant**. Matching too much is obviously dangerous. Matching too little is
dangerous in a quieter way: the operator believes an asset is covered, stage two silently
skips it, and they conclude it was tested.
"""

from __future__ import annotations

import pytest

from flypaper.stage2.program_scope import convert_program_scope
from flypaper.stage2.scope import Scope, ScopeSyntaxError, check_pattern

# --- what a host pattern may be --------------------------------------------------------------


@pytest.mark.parametrize(
    "pattern",
    [
        "example.com",
        "api.example.com",
        "*.example.com",
        "*.example.co.uk",  # multi-label public suffix
        "a.b.c.d.example.com",
        "127.0.0.1:8123",  # host:port, seen in real scopes
        "192.0.2.10",
        "xn--80ak6aa92e.com",  # punycode IDN
        "EXAMPLE.com",  # case folded on the way in
    ],
)
def test_valid_host_patterns_are_accepted(pattern):
    assert check_pattern(pattern) == pattern.strip().lower()


# --- and what it may not be, with the reason -----------------------------------------------------


@pytest.mark.parametrize(
    ("pattern", "because"),
    [
        ("https://example.com/c", "URL"),
        ("http://example.com/en-ca/", "URL"),
        ("example.com/*", "path"),
        ("example.com/admin/*/stub*/*", "path"),
        ("198.51.100.0/24", "CIDR"),
        ("www.a.example.com,www.b.example.com", "comma"),
        ("Some Product Codebase", "whitespace"),
        ("api-{date}.example.com", "placeholder"),
        ("<locale>.example.com", "placeholder"),
        ("*partial-example.com", "leading"),
        ("wiki*.example.com", "leading"),
        ("", "empty"),
    ],
)
def test_shapes_that_are_not_host_patterns_are_refused(pattern, because):
    with pytest.raises(ScopeSyntaxError, match=because):
        check_pattern(pattern)


def test_a_path_scoped_asset_is_never_silently_widened():
    """The most dangerous quiet failure this converter could have.

    `example.com/admin/*` authorises one path. Dropping the path to keep the host would
    turn that into the whole host, so it is refused instead.
    """
    with pytest.raises(ScopeSyntaxError) as exc:
        check_pattern("example.com/admin/*")
    assert "wider" in str(exc.value)


def test_a_pattern_that_could_never_match_is_an_error_not_a_no_op():
    """Silently keeping it would tell the operator an asset is covered when it is not."""
    for dead in ("api-{date}.example.com", "a.example.com,b.example.com", "Some Codebase"):
        with pytest.raises(ScopeSyntaxError):
            check_pattern(dead)


# --- scope files -------------------------------------------------------------------------------


def test_scope_file_reports_the_offending_line_number(tmp_path):
    path = tmp_path / "scope.txt"
    path.write_text("good.example.com\n# a comment\n198.51.100.0/24\n")
    with pytest.raises(ScopeSyntaxError, match=r":3:"):
        Scope.from_file(path)


def test_duplicate_patterns_collapse(tmp_path):
    path = tmp_path / "scope.txt"
    path.write_text("a.example.com\na.example.com\nA.EXAMPLE.COM\n")
    assert Scope.from_file(path).patterns == ("a.example.com",)


# --- converting a program's scope table -----------------------------------------------------------


HEADER = "identifier,asset_type,eligible_for_submission\n"


def write_csv(tmp_path, rows: str):
    path = tmp_path / "program-scope.csv"
    path.write_text(HEADER + rows)
    return path


def test_ineligible_assets_are_dropped(tmp_path):
    """In the real survey, 537 of 1,930 assets were ineligible and sat in the same table."""
    path = write_csv(
        tmp_path,
        "in.example.com,URL,true\nout.example.com,URL,false\nblank.example.com,URL,\n",
    )
    report = convert_program_scope(path)
    assert report.patterns == ["in.example.com"]
    assert report.counts["ineligible"] == 2
    assert any("eligible" in why for _, _, why in report.skipped)


def test_non_web_asset_types_are_dropped(tmp_path):
    path = write_csv(
        tmp_path,
        "com.example.app,GOOGLE_PLAY_APP_ID,true\n"
        "1234567890,APPLE_STORE_APP_ID,true\n"
        "198.51.100.0/24,CIDR,true\n"
        "web.example.com,URL,true\n",
    )
    report = convert_program_scope(path)
    assert report.patterns == ["web.example.com"]
    assert report.counts["non_web"] == 3


def test_path_scoped_assets_are_skipped_not_widened(tmp_path):
    """The whole point. `example.com/admin/*` must not become `example.com`."""
    path = write_csv(tmp_path, "example.com/admin/*,URL,true\nfine.example.com,URL,true\n")
    report = convert_program_scope(path)
    assert report.patterns == ["fine.example.com"]
    assert "example.com" not in report.patterns
    assert report.counts["unusable"] == 1


def test_conversion_deduplicates_but_keeps_order(tmp_path):
    path = write_csv(
        tmp_path,
        "b.example.com,URL,true\na.example.com,WILDCARD,true\nb.example.com,OTHER,true\n",
    )
    assert convert_program_scope(path).patterns == ["b.example.com", "a.example.com"]


def test_uncovered_apexes_are_listed_not_assumed(tmp_path):
    """`*.example.com` does not cover `example.com`, and programs differ on whether theirs
    does. The gap is surfaced as a comment rather than silently included or dropped."""
    path = write_csv(tmp_path, "*.example.com,WILDCARD,true\n")
    report = convert_program_scope(path)
    assert report.uncovered_apexes == ["example.com"]

    out = tmp_path / "scope.txt"
    report.write(out)
    text = out.read_text()
    assert "# example.com" in text
    # Commented out, so loading the file does not authorise the apex.
    assert Scope.from_file(out).patterns == ("*.example.com",)


def test_an_apex_already_listed_is_not_repeated(tmp_path):
    path = write_csv(tmp_path, "*.example.com,WILDCARD,true\nexample.com,URL,true\n")
    assert convert_program_scope(path).uncovered_apexes == []


def test_a_table_with_nothing_eligible_yields_nothing(tmp_path):
    """Seen for real: 25 assets, 24 ineligible, 1 unusable. The right answer is no scope."""
    path = write_csv(tmp_path, "a.example.com,URL,false\nb.example.com/x,URL,true\n")
    report = convert_program_scope(path)
    assert report.patterns == []
    assert "0 host patterns" in report.summary()


def test_generated_file_round_trips_through_the_scope_loader(tmp_path):
    path = write_csv(
        tmp_path,
        "*.example.com,WILDCARD,true\n"
        "api.example.net,URL,true\n"
        "example.com/admin/*,URL,true\n"
        "dropped.example.org,URL,false\n",
    )
    out = tmp_path / "scope.txt"
    convert_program_scope(path).write(out, source="test")
    scope = Scope.from_file(out)

    assert scope.decide("https://a.example.com/x").allowed
    assert scope.decide("https://api.example.net/x").allowed
    assert not scope.decide("https://example.com/admin/x").allowed
    assert not scope.decide("https://dropped.example.org/").allowed
    assert not scope.decide("https://example.com/").allowed
