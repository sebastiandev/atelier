"""Tests for NDJSON transcript I/O.

The crash-recovery test models the STORY-004 acceptance criterion: a
process is killed mid-`append_event`, leaving an unterminated trailing
line on disk. On reopen, append + read recover cleanly — the partial
line is dropped, valid lines are still read, and the next appended event
goes onto a clean newline boundary.
"""

from pathlib import Path

from src.infrastructure.filesystem.ndjson import (
    _repair_partial_trailing_line,
    append_event,
    read_from_cursor,
)


def test_append_then_read_round_trip(tmp_path: Path) -> None:
    log = tmp_path / "transcript.ndjson"
    append_event(log, {"seq": 1, "type": "user", "text": "hi"})
    append_event(log, {"seq": 2, "type": "assistant", "text": "hello"})
    events = list(read_from_cursor(log, 0))
    assert events == [
        {"seq": 1, "type": "user", "text": "hi"},
        {"seq": 2, "type": "assistant", "text": "hello"},
    ]


def test_read_filters_by_cursor(tmp_path: Path) -> None:
    log = tmp_path / "transcript.ndjson"
    for i in range(1, 6):
        append_event(log, {"seq": i, "v": i})
    events = list(read_from_cursor(log, 2))
    assert [e["seq"] for e in events] == [3, 4, 5]


def test_read_missing_file_yields_empty(tmp_path: Path) -> None:
    assert list(read_from_cursor(tmp_path / "absent.ndjson", 0)) == []


def test_each_append_terminates_line(tmp_path: Path) -> None:
    log = tmp_path / "transcript.ndjson"
    append_event(log, {"seq": 1, "v": "a"})
    append_event(log, {"seq": 2, "v": "b"})
    raw = log.read_bytes()
    assert raw.endswith(b"\n")
    assert raw.count(b"\n") == 2


def test_unicode_round_trips(tmp_path: Path) -> None:
    log = tmp_path / "transcript.ndjson"
    append_event(log, {"seq": 1, "text": "héllo 🌍"})
    events = list(read_from_cursor(log, 0))
    assert events[0]["text"] == "héllo 🌍"


def test_embedded_newline_in_value_is_escaped(tmp_path: Path) -> None:
    """JSON encoding escapes \\n in values, so multi-line strings stay on one line."""
    log = tmp_path / "transcript.ndjson"
    append_event(log, {"seq": 1, "text": "line1\nline2"})
    raw = log.read_bytes()
    assert raw.count(b"\n") == 1  # only the line terminator
    events = list(read_from_cursor(log, 0))
    assert events[0]["text"] == "line1\nline2"


def test_read_skips_malformed_lines(tmp_path: Path) -> None:
    log = tmp_path / "transcript.ndjson"
    append_event(log, {"seq": 1, "ok": True})
    log.open("ab").write(b"this is not json\n")
    log.open("ab").write(b'{"oops": "no seq"}\n')
    log.open("ab").write(b"[1, 2, 3]\n")  # not an object
    append_event(log, {"seq": 2, "ok": True})
    events = list(read_from_cursor(log, 0))
    assert [e["seq"] for e in events] == [1, 2]


def test_bool_seq_is_rejected(tmp_path: Path) -> None:
    log = tmp_path / "transcript.ndjson"
    log.open("ab").write(b'{"seq": true, "x": 1}\n')
    assert list(read_from_cursor(log, 0)) == []


def test_crash_mid_append_recovers(tmp_path: Path) -> None:
    """Acceptance criterion: simulate a crash mid-append, then reopen.

    We append two events, then truncate the file to halfway through the
    third event's bytes — modelling a process kill between the kernel
    starting the write and finishing it. The next `append_event` plus
    a subsequent `read_from_cursor` should:
      - drop the partial line cleanly
      - persist the new event on a fresh line
      - return both recovered + new events with intact JSON
    """
    log = tmp_path / "transcript.ndjson"
    append_event(log, {"seq": 1, "v": "first"})
    append_event(log, {"seq": 2, "v": "second"})

    # Append a partial third line and then truncate it mid-content.
    with log.open("ab") as f:
        f.write(b'{"seq": 3, "v": "thi')  # no closing brace, no newline

    # Reopen scenario: a fresh appender writes seq=3 again (the supervisor
    # would not retry the same seq, but for the test what matters is that
    # the partial line is gone before the next bytes land).
    append_event(log, {"seq": 3, "v": "third"})

    events = list(read_from_cursor(log, 0))
    assert [e["seq"] for e in events] == [1, 2, 3]
    assert events[2]["v"] == "third"

    # File ends cleanly: exactly three JSON-parseable lines, no fragments.
    import json as _json

    raw = log.read_bytes()
    assert raw.endswith(b"\n")
    lines = raw.rstrip(b"\n").split(b"\n")
    assert len(lines) == 3
    for line in lines:
        _json.loads(line)  # each line is valid JSON


def test_repair_no_op_on_clean_file(tmp_path: Path) -> None:
    log = tmp_path / "transcript.ndjson"
    append_event(log, {"seq": 1, "v": "a"})
    before = log.read_bytes()
    _repair_partial_trailing_line(log)
    assert log.read_bytes() == before


def test_repair_no_op_on_missing_file(tmp_path: Path) -> None:
    _repair_partial_trailing_line(tmp_path / "absent.ndjson")  # does not raise


def test_repair_truncates_file_with_no_newline_at_all(tmp_path: Path) -> None:
    log = tmp_path / "transcript.ndjson"
    log.write_bytes(b"only garbage no newline")
    _repair_partial_trailing_line(log)
    assert log.read_bytes() == b""


def test_repair_keeps_complete_lines_drops_partial_tail(tmp_path: Path) -> None:
    log = tmp_path / "transcript.ndjson"
    log.write_bytes(b'{"seq":1}\n{"seq":2}\n{"seq":3,"partial":')
    _repair_partial_trailing_line(log)
    assert log.read_bytes() == b'{"seq":1}\n{"seq":2}\n'


def test_large_partial_line_repaired(tmp_path: Path) -> None:
    """Forces the chunked backwards scan: partial line spans multiple 4 KB chunks."""
    log = tmp_path / "transcript.ndjson"
    good = b'{"seq":1}\n'
    big_partial = b'{"seq":2,"text":"' + (b"x" * 10000) + b"..."  # no newline
    log.write_bytes(good + big_partial)
    _repair_partial_trailing_line(log)
    assert log.read_bytes() == good


def test_reader_drops_partial_tail_without_repair(tmp_path: Path) -> None:
    """Independently of append-side repair, the reader skips a partial trailing line."""
    log = tmp_path / "transcript.ndjson"
    log.write_bytes(b'{"seq":1,"v":"a"}\n{"seq":2,"v":"part')
    events = list(read_from_cursor(log, 0))
    assert [e["seq"] for e in events] == [1]


def test_append_after_repair_does_not_glue_partial(tmp_path: Path) -> None:
    """The dangerous case the repair guards against: without repair, appending
    new bytes would fuse them onto the broken line and create a malformed
    'recovered' line. With repair, the new event lands on a clean line."""
    log = tmp_path / "transcript.ndjson"
    log.write_bytes(b'{"seq":1,"text":"clean"}\n{"seq":2,"text":"par')

    append_event(log, {"seq": 3, "text": "fresh"})

    raw = log.read_bytes()
    assert b'"par"' not in raw  # the partial fragment is gone
    events = list(read_from_cursor(log, 0))
    assert [e["seq"] for e in events] == [1, 3]


def test_cursor_zero_yields_all(tmp_path: Path) -> None:
    log = tmp_path / "transcript.ndjson"
    for i in range(1, 4):
        append_event(log, {"seq": i})
    events = list(read_from_cursor(log, 0))
    assert [e["seq"] for e in events] == [1, 2, 3]


def test_cursor_past_end_yields_nothing(tmp_path: Path) -> None:
    log = tmp_path / "transcript.ndjson"
    for i in range(1, 4):
        append_event(log, {"seq": i})
    assert list(read_from_cursor(log, 99)) == []
