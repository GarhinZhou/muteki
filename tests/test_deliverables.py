"""Durable EXP script preservation for the post-solve results page."""

from __future__ import annotations

import json
from pathlib import Path

from muteki.solver.deliverables import (
    discover_deliverables,
    load_preserved_deliverables,
    preserve_winner_deliverables,
)


def _workspace(tmp_path: Path) -> Path:
    return tmp_path / "sessions" / "run-x" / "workspace"


def test_preserve_and_load_winner_exp(tmp_path):
    workspace = _workspace(tmp_path)
    workdir = workspace / "workers" / "cli-py-1"
    scratch = workdir / "scratch"
    scratch.mkdir(parents=True)
    (scratch / "exp.py").write_text(
        "from pwn import *\nremote('target', 1337)\n", encoding="utf-8")
    (scratch / "rce.sh").write_text(
        "#!/bin/bash\ncurl -s http://target/pwn\n", encoding="utf-8")
    (scratch / "notes.txt").write_text("not source", encoding="utf-8")
    (scratch / "blob.bin").write_bytes(b"\x00\x01\x02")
    (scratch / "huge.py").write_text("A" * 300_000, encoding="utf-8")

    files = preserve_winner_deliverables(workspace, workdir)

    names = [item["name"] for item in files]
    assert set(names) == {"exp.py", "rce.sh"}
    loaded = load_preserved_deliverables(workspace)
    by_name = {item["name"]: item for item in loaded}
    assert set(by_name) == {"exp.py", "rce.sh"}
    assert by_name["exp.py"]["language"] == "python"
    assert "pwn" in by_name["exp.py"]["content"]
    assert by_name["rce.sh"]["language"] == "bash"
    assert by_name["exp.py"]["source"] == "scratch/exp.py"
    manifest = json.loads(
        (workspace / "final" / "exp" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["worker_workdir"] == "workers/cli-py-1"
    assert {row["name"] for row in manifest["files"]} == {"exp.py", "rce.sh"}


def test_preserve_skips_symlink_escape_and_is_empty_without_candidates(tmp_path):
    workspace = _workspace(tmp_path)
    workdir = workspace / "workers" / "cli-py-2"
    scratch = workdir / "scratch"
    scratch.mkdir(parents=True)
    outside = tmp_path / "outside.py"
    outside.write_text("print('outside')\n", encoding="utf-8")
    (scratch / "link.py").symlink_to(outside)
    (scratch / "notes.md").write_text("notes", encoding="utf-8")

    files = preserve_winner_deliverables(workspace, workdir)

    assert files == []
    assert load_preserved_deliverables(workspace) == []


def test_preserve_does_not_follow_symlinked_final_dir(tmp_path):
    workspace = _workspace(tmp_path)
    workdir = workspace / "workers" / "cli-py-4"
    scratch = workdir / "scratch"
    scratch.mkdir(parents=True)
    (scratch / "exp.py").write_text("print('x')\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "final").symlink_to(outside)

    files = preserve_winner_deliverables(workspace, workdir)

    assert files == []
    assert list(outside.iterdir()) == []


def test_discover_deliverables_falls_back_to_winner_workdir(tmp_path):
    workspace = _workspace(tmp_path)
    workdir = workspace / "workers" / "cli-py-3"
    scratch = workdir / "scratch"
    scratch.mkdir(parents=True)
    (scratch / "poc.py").write_text(
        "import requests\nrequests.get('http://target/flag')\n", encoding="utf-8")

    # No manifest yet: the endpoint fallback scans the still-present winner cwd.
    found = discover_deliverables(workspace, workdir=workdir)

    assert [item["name"] for item in found] == ["poc.py"]
    assert "requests" in found[0]["content"]
    assert found[0]["language"] == "python"
