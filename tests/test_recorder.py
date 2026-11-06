"""Tests for the structured JSONL recorder."""
import json
from datetime import date, timedelta

import pytest

from src import config, recorder


@pytest.fixture(autouse=True)
def _reset_prune_state():
    recorder._PRUNED_DAY = None
    yield
    recorder._PRUNED_DAY = None


def test_record_is_noop_when_log_dir_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LOG_DIR", "")
    recorder.record("search", {"query": "q"})
    assert list(tmp_path.iterdir()) == []


def test_record_appends_jsonl_with_ts(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))
    monkeypatch.setattr(config, "LOG_RETENTION_DAYS", 30)

    recorder.record("search", {"event": "search", "query": "q1", "result_count": 2})
    recorder.record("search", {"event": "search", "query": "q2", "result_count": 0})

    files = list(tmp_path.glob("search-*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2

    first = json.loads(lines[0])
    assert first["query"] == "q1"
    assert first["result_count"] == 2
    assert "ts" in first


def test_record_separates_kinds(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))
    recorder.record("search", {"query": "q"})
    recorder.record("scrape", {"url": "https://a.com"})
    assert len(list(tmp_path.glob("search-*.jsonl"))) == 1
    assert len(list(tmp_path.glob("scrape-*.jsonl"))) == 1


def test_record_prunes_files_older_than_retention(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))
    monkeypatch.setattr(config, "LOG_RETENTION_DAYS", 3)

    old_day = date.today() - timedelta(days=10)
    old_file = tmp_path / f"search-{old_day:%Y%m%d}.jsonl"
    old_file.write_text("{}\n", encoding="utf-8")
    keep_file = tmp_path / f"search-{date.today():%Y%m%d}.jsonl"
    keep_file.write_text("{}\n", encoding="utf-8")

    recorder.record("search", {"query": "q"})

    assert not old_file.exists()
    assert keep_file.exists()
