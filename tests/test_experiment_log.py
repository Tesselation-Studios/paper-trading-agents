#!/usr/bin/env python3
"""
Unit tests for scripts/experiment_log.py -- structured run log for
controlled tick-replay experiments (2026-08-02). Real files under tmp_path,
no mocking.
"""
import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import experiment_log as el  # noqa: E402


@pytest.fixture
def experiments_dir(tmp_path, monkeypatch):
    d = tmp_path / "experiments"
    monkeypatch.setattr(el, "EXPERIMENTS_DIR", d)
    return d


def _append(experiment_id, run_id, date, params, result):
    return el.main([
        "append", experiment_id,
        "--run-id", run_id,
        "--date", date,
        "--params", json.dumps(params),
        "--result", json.dumps(result),
    ])


def test_append_creates_log_file(experiments_dir, capsys):
    rc = _append("exp1", "run-a", "2026-06-01", {"weight": 1.0}, {"final_pnl": 12.5})
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "ok"
    assert out["total_runs"] == 1

    log_path = experiments_dir / "exp1.jsonl"
    assert log_path.exists()
    lines = log_path.read_text().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["run_id"] == "run-a"
    assert entry["date"] == "2026-06-01"
    assert entry["params"] == {"weight": 1.0}
    assert entry["result"] == {"final_pnl": 12.5}


def test_append_multiple_runs_same_experiment(experiments_dir):
    _append("exp1", "run-a", "2026-06-01", {"weight": 1.0}, {"final_pnl": 12.5})
    rc = _append("exp1", "run-b", "2026-06-01", {"weight": 2.0}, {"final_pnl": -3.2})
    assert rc == 0

    runs = el._load_runs("exp1")
    assert len(runs) == 2
    assert {r["run_id"] for r in runs} == {"run-a", "run-b"}


def test_append_duplicate_run_id_rejected(experiments_dir, capsys):
    _append("exp1", "run-a", "2026-06-01", {"weight": 1.0}, {"final_pnl": 12.5})
    capsys.readouterr()
    rc = _append("exp1", "run-a", "2026-06-01", {"weight": 9.0}, {"final_pnl": 0.0})
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "error"
    assert "already logged" in out["reason"]

    # Original entry untouched -- append is append-only, never rewrites.
    runs = el._load_runs("exp1")
    assert len(runs) == 1
    assert runs[0]["params"] == {"weight": 1.0}


def test_append_invalid_params_json_rejected(experiments_dir, capsys):
    rc = el.main(["append", "exp1", "--run-id", "run-a", "--date", "2026-06-01",
                  "--params", "{not valid json", "--result", "{}"])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "error"
    assert not (experiments_dir / "exp1.jsonl").exists()


def test_list_empty_experiment_returns_no_runs(experiments_dir, capsys):
    rc = el.main(["list", "nonexistent-exp"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["n_runs"] == 0
    assert out["runs"] == []


def test_list_returns_all_runs(experiments_dir, capsys):
    _append("exp1", "run-a", "2026-06-01", {"weight": 1.0}, {"final_pnl": 12.5})
    _append("exp1", "run-b", "2026-06-01", {"weight": 2.0}, {"final_pnl": -3.2})
    capsys.readouterr()

    rc = el.main(["list", "exp1"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["n_runs"] == 2


def test_diff_two_runs(experiments_dir, capsys):
    _append("exp1", "run-a", "2026-06-01", {"weight": 1.0}, {"final_pnl": 12.5})
    _append("exp1", "run-b", "2026-06-01", {"weight": 2.0}, {"final_pnl": -3.2})
    capsys.readouterr()

    rc = el.main(["diff", "exp1", "run-a", "run-b"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["same_date"] is True
    assert out["a"]["params"] == {"weight": 1.0}
    assert out["b"]["params"] == {"weight": 2.0}


def test_diff_different_dates_flagged(experiments_dir, capsys):
    _append("exp1", "run-a", "2026-06-01", {"weight": 1.0}, {"final_pnl": 12.5})
    _append("exp1", "run-b", "2026-06-02", {"weight": 1.0}, {"final_pnl": 5.0})
    capsys.readouterr()

    rc = el.main(["diff", "exp1", "run-a", "run-b"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["same_date"] is False


def test_diff_missing_run_id(experiments_dir, capsys):
    _append("exp1", "run-a", "2026-06-01", {"weight": 1.0}, {"final_pnl": 12.5})
    capsys.readouterr()

    rc = el.main(["diff", "exp1", "run-a", "nonexistent-run"])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "error"
    assert "nonexistent-run" in out["reason"]


def test_runs_isolated_across_experiment_ids(experiments_dir):
    _append("exp1", "run-a", "2026-06-01", {"weight": 1.0}, {"final_pnl": 12.5})
    _append("exp2", "run-a", "2026-06-01", {"weight": 9.0}, {"final_pnl": 0.0})

    assert len(el._load_runs("exp1")) == 1
    assert len(el._load_runs("exp2")) == 1
    assert el._load_runs("exp1")[0]["params"] == {"weight": 1.0}
    assert el._load_runs("exp2")[0]["params"] == {"weight": 9.0}
