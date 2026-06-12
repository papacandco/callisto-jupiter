import json
from datetime import datetime, timedelta, timezone

from callisto_jupiter.buffer import SampleBuffer


def _s(metric="cpu", value=1.0, collected_at=None):
    sample = {"metric_name": metric, "value": value, "unit": "percent"}
    if collected_at is not None:
        sample["collected_at"] = collected_at
    return sample


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_add_and_pending_preserve_order():
    buf = SampleBuffer(None, max_age_seconds=3600, max_samples=100)
    buf.add([_s(value=1), _s(value=2)])
    buf.add([_s(value=3)])
    assert [s["value"] for s in buf.pending()] == [1, 2, 3]
    assert buf.count() == 3


def test_drop_first_removes_from_front():
    buf = SampleBuffer(None, max_age_seconds=3600, max_samples=100)
    buf.add([_s(value=1), _s(value=2), _s(value=3)])
    buf.drop_first(2)
    assert [s["value"] for s in buf.pending()] == [3]


def test_prune_drops_samples_older_than_max_age():
    now = datetime(2026, 6, 12, 12, 0, 0, tzinfo=timezone.utc)
    old = _s(value="old", collected_at=_iso(now - timedelta(seconds=7200)))
    fresh = _s(value="fresh", collected_at=_iso(now - timedelta(seconds=10)))
    buf = SampleBuffer(None, max_age_seconds=3600, max_samples=100)
    buf.add([old, fresh])
    buf.prune(now=now)
    assert [s["value"] for s in buf.pending()] == ["fresh"]


def test_prune_keeps_unparseable_timestamps():
    now = datetime(2026, 6, 12, 12, 0, 0, tzinfo=timezone.utc)
    buf = SampleBuffer(None, max_age_seconds=3600, max_samples=100)
    buf.add([_s(value="no-ts"), _s(value="bad-ts", collected_at="not-a-date")])
    buf.prune(now=now)
    assert {s["value"] for s in buf.pending()} == {"no-ts", "bad-ts"}


def test_prune_caps_count_dropping_oldest_first():
    buf = SampleBuffer(None, max_age_seconds=3600, max_samples=2)
    buf.add([_s(value=1), _s(value=2), _s(value=3)])
    buf.prune()
    assert [s["value"] for s in buf.pending()] == [2, 3]


def test_persist_and_reload_roundtrip(tmp_path):
    path = str(tmp_path / "buffer.json")
    buf = SampleBuffer(path, max_age_seconds=3600, max_samples=100)
    buf.add([_s(value=1), _s(value=2)])
    buf.persist()

    reloaded = SampleBuffer(path, max_age_seconds=3600, max_samples=100)
    assert [s["value"] for s in reloaded.pending()] == [1, 2]


def test_persist_creates_parent_dir(tmp_path):
    path = str(tmp_path / "nested" / "dir" / "buffer.json")
    buf = SampleBuffer(path, max_age_seconds=3600, max_samples=100)
    buf.add([_s(value=1)])
    buf.persist()
    assert json.loads((tmp_path / "nested" / "dir" / "buffer.json").read_text()) == buf.pending()


def test_corrupt_file_starts_empty(tmp_path):
    path = tmp_path / "buffer.json"
    path.write_text("{ this is not valid json")
    buf = SampleBuffer(str(path), max_age_seconds=3600, max_samples=100)
    assert buf.count() == 0


def test_non_list_file_starts_empty(tmp_path):
    path = tmp_path / "buffer.json"
    path.write_text('{"samples": []}')
    buf = SampleBuffer(str(path), max_age_seconds=3600, max_samples=100)
    assert buf.count() == 0


def test_in_memory_mode_does_not_write(tmp_path):
    buf = SampleBuffer(None, max_age_seconds=3600, max_samples=100)
    buf.add([_s(value=1)])
    buf.persist()  # no path: no-op, must not raise
    assert list(tmp_path.iterdir()) == []


def test_persist_unwritable_path_does_not_raise(tmp_path):
    # A path whose parent is a file (not a dir) cannot be created; persist must
    # log and swallow the error, keeping samples in memory.
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    buf = SampleBuffer(str(blocker / "buffer.json"), max_age_seconds=3600, max_samples=100)
    buf.add([_s(value=1)])
    buf.persist()  # must not raise
    assert buf.count() == 1
