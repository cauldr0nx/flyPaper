"""Parser tests.

The fixtures under `tests/fixtures/` are real records captured from ffuf v2.1.0-dev against
a locally hosted ffufme container during the M1 ingest spike (reports/m1-ingest.md). The
hand-written cases below cover the shapes a capture cannot conveniently contain: truncated
lines, unknown fields, wrong types.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from flypaper.ingest.ffuf import (
    AUTO,
    BASE64,
    PLAIN,
    FfufResult,
    ParseStats,
    detect_encoding,
    iter_ndjson,
    load_batch,
    parse_record,
)
from flypaper.ingest.stream import iter_batch, iter_tail

FIXTURES = Path(__file__).parent / "fixtures"
STDOUT_JSONL = FIXTURES / "ffuf-json-stdout.jsonl"
BATCH_JSON = FIXTURES / "ffuf-of-json-batch.json"


def _stdout_records() -> list[FfufResult]:
    return list(iter_ndjson(STDOUT_JSONL.read_text(encoding="utf-8").splitlines()))


# --- the two real shapes ------------------------------------------------------------------


def test_stdout_ndjson_fixture_parses_completely():
    stats = ParseStats()
    results = list(iter_ndjson(STDOUT_JSONL.read_text(encoding="utf-8").splitlines(), stats=stats))
    assert stats.malformed == 0
    assert stats.ok == len(results) > 0
    assert not stats.unknown_fields
    assert not stats.coercion_failures


def test_stdout_inputs_are_base64_decoded():
    words = {r.word for r in _stdout_records()}
    assert "admin" in words, words


def test_batch_file_inputs_are_not_decoded():
    """`-of json` writes plain strings. Decoding them would corrupt every word."""
    results = load_batch(BATCH_JSON)
    assert results
    words = {r.word for r in results}
    assert all(w.isprintable() for w in words), words
    # `logout` is valid base64 that round-trips; treating the file as base64 would eat it.
    assert "logout" in words, words


def test_batch_and_stream_agree_on_a_word_that_is_valid_base64():
    """The 5.2% failure mode the per-value sniffing heuristic had, pinned as a test."""
    word = "database"
    from_stream = parse_record({"input": {"FUZZ": "ZGF0YWJhc2U="}, "status": 404}, encoding=BASE64)
    from_file = parse_record({"input": {"FUZZ": word}, "status": 404}, encoding=PLAIN)
    assert from_stream.word == from_file.word == word


# --- field semantics ----------------------------------------------------------------------


def test_duration_is_nanoseconds_not_milliseconds():
    r = parse_record({"input": {}, "duration": 834568})
    assert r.duration_ms == pytest.approx(0.834568)


def test_ffufhash_is_exposed_separately_from_the_word():
    r = parse_record({"input": {"FFUFHASH": "OWExMzgy", "FUZZ": "YWRtaW4="}})
    assert r.ffufhash == "9a1382"
    assert r.word == "admin"


def test_word_prefers_a_non_ffufhash_keyword_when_fuzz_is_absent():
    r = parse_record({"input": {"FFUFHASH": "MTg3MDY2", "W1": "YWRtaW4="}})
    assert r.word == "admin"


def test_word_is_empty_when_there_are_no_keywords():
    assert parse_record({"input": {}}).word == ""


def test_real_records_carry_an_ffufhash():
    """ffuf injects FFUFHASH into every record, which is what makes callback correlation
    possible later. If that ever stops being true, the delayed-reward work needs to know."""
    assert all(r.ffufhash for r in _stdout_records())


# --- tolerance ------------------------------------------------------------------------------


def test_malformed_lines_are_counted_and_skipped_not_fatal():
    lines = [
        '{"input":{"FUZZ":"YWRtaW4="},"status":200,"length":10,"words":1,"lines":1}',
        '{"input":{"FUZZ":"YWRtaW4="},"status":200,',  # truncated by a killed pipe
        "not json at all",
        "[1, 2, 3]",  # valid JSON, wrong type
        "",
        '{"input":{"FUZZ":"bG9naW4="},"status":404}',
    ]
    stats = ParseStats()
    results = list(iter_ndjson(lines, stats=stats))
    assert len(results) == 2
    assert stats.ok == 2
    assert stats.malformed == 3
    assert stats.empty == 1


def test_unknown_fields_are_kept_and_reported_once():
    warn = io.StringIO()
    stats = ParseStats()
    lines = [
        json.dumps({"input": {"FUZZ": "YWRtaW4="}, "status": 200, "tls": {"version": "1.3"}}),
        json.dumps({"input": {"FUZZ": "bG9naW4="}, "status": 200, "tls": {"version": "1.2"}}),
    ]
    results = list(iter_ndjson(lines, stats=stats, warn_stream=warn))
    assert stats.unknown_fields == {"tls"}
    assert results[0].extra["tls"] == {"version": "1.3"}
    # Two records, one warning: a 10,000-record stream must not emit 10,000 lines.
    assert warn.getvalue().count("unrecognised ffuf field") == 1


def test_missing_fields_take_documented_defaults():
    r = parse_record({"input": {"FUZZ": "YWRtaW4="}})
    assert (r.status, r.length, r.words, r.lines) == (0, 0, 0, 0)
    assert r.content_type == r.redirect_location == r.host == r.result_file == ""
    assert r.duration_ms == 0.0
    assert r.scraper == {}


def test_wrong_types_are_coerced_or_counted_never_raised():
    stats = ParseStats()
    r = parse_record(
        {"input": "not-a-map", "status": "not-a-number", "length": "12", "duration": None},
        stats=stats,
    )
    assert r.inputs == {}
    assert r.status == 0
    assert r.length == 12  # a numeric string is a coercion, not a failure
    assert stats.coercion_failures == {"status": 1}


def test_non_utf8_input_bytes_do_not_crash():
    import base64 as b64

    r = parse_record({"input": {"FUZZ": b64.b64encode(b"\xff\xfe").decode()}}, encoding=BASE64)
    assert isinstance(r.word, str)


def test_scraper_data_is_normalised_to_lists():
    r = parse_record({"input": {}, "scraper": {"emails": ["a@b.c"], "title": "one"}})
    assert r.scraper == {"emails": ["a@b.c"], "title": ["one"]}


# --- batch shapes ----------------------------------------------------------------------------


def test_batch_accepts_a_bare_array(tmp_path):
    path = tmp_path / "bare.json"
    path.write_text(json.dumps([{"input": {"FUZZ": "admin"}, "status": 200}]))
    assert load_batch(path)[0].word == "admin"


def test_batch_falls_back_to_ndjson_for_a_json_redirected_to_a_file(tmp_path):
    """`ffuf -json > out.json` is NDJSON in a file called .json, and people do this."""
    path = tmp_path / "out.json"
    path.write_text(
        '{"input":{"FUZZ":"YWRtaW4="},"status":200}\n{"input":{"FUZZ":"bG9naW4="},"status":404}\n'
    )
    words = [r.word for r in load_batch(path)]
    assert words == ["admin", "login"]


def test_batch_of_an_empty_results_list_is_empty(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text(json.dumps({"commandline": "ffuf", "time": "now", "results": []}))
    assert load_batch(path) == []


def test_empty_stream_yields_nothing():
    stats = ParseStats()
    assert list(iter_ndjson([], stats=stats)) == []
    assert stats.total == 0


# --- encoding detection -----------------------------------------------------------------------


def test_detect_encoding_calls_a_plain_stream_plain():
    records = [{"input": {"FFUFHASH": "187066", "FUZZ": "logout"}}]
    assert detect_encoding(records) == PLAIN


def test_detect_encoding_calls_a_base64_stream_base64():
    records = [{"input": {"FFUFHASH": "OWExMzgy", "FUZZ": "YWRtaW4="}}]
    assert detect_encoding(records) == BASE64


def test_auto_mode_on_the_real_batch_fixture_keeps_words_intact():
    records = json.loads(BATCH_JSON.read_text(encoding="utf-8"))["results"]
    assert detect_encoding(records) == PLAIN


def test_auto_mode_on_the_real_stdout_fixture_decodes():
    lines = STDOUT_JSONL.read_text(encoding="utf-8").splitlines()
    results = list(iter_ndjson(lines, encoding=AUTO, warn_stream=io.StringIO()))
    assert any(r.word == "admin" for r in results)


# --- stream strategies -------------------------------------------------------------------------


def test_iter_batch_matches_load_batch():
    assert [r.url for r in iter_batch(BATCH_JSON)] == [r.url for r in load_batch(BATCH_JSON)]


def test_iter_tail_reads_existing_content_then_stops_when_idle(tmp_path):
    path = tmp_path / "live.jsonl"
    path.write_text('{"input":{"FUZZ":"YWRtaW4="},"status":200}\n')
    results = list(iter_tail(path, poll=0.01, stop_after_idle=0.05))
    assert [r.word for r in results] == ["admin"]


def test_iter_tail_does_not_split_a_record_still_being_written(tmp_path):
    """The writer may be mid-line; a partial record must be held back, not parsed."""
    path = tmp_path / "partial.jsonl"
    path.write_text('{"input":{"FUZZ":"YWRtaW4="},"status":200}\n{"input":{"FUZZ":"bG9n')
    stats = ParseStats()
    results = list(iter_tail(path, poll=0.01, stop_after_idle=0.05, stats=stats))
    assert [r.word for r in results] == ["admin"]
    assert stats.malformed == 1  # the trailing fragment, counted at EOF and not lost
