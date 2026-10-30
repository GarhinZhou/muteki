"""Conversation archive: native CLI capture + ZIP assembly."""

import json
import zipfile
from pathlib import Path

from muteki.solver.conversation_export import (
    build_conversation_archive,
    preserve_worker_conversation,
)


def test_preserve_worker_conversation_captures_native_session_and_media(tmp_path):
    workspace = tmp_path / "workspace"
    workdir = workspace / "workers" / "cli-claude-1"
    config = workdir / ".muteki-claude-config"
    session = config / "projects" / "encoded" / "sess-123456.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text(
        '{"type":"user","message":"hello"}\n'
        '{"type":"assistant","message":"world"}\n',
        encoding="utf-8",
    )
    (session.parent / "auth.json").write_text(
        '{"token":"must-not-be-exported"}', encoding="utf-8")
    (workdir / "screenshot.png").write_bytes(b"png-bytes")

    meta = preserve_worker_conversation(
        workdir=workdir,
        solver_id="cli-claude-1",
        engine="claude",
        session="sess-123456",
        env={"CLAUDE_CONFIG_DIR": str(config)},
        identity={"profile_id": "seat-claude"},
    )

    captured = workspace / "conversations" / "cli-claude-1"
    assert meta["status"] == "captured"
    assert (captured / "worker.json").is_file()
    assert list((captured / "native").rglob("*.jsonl"))
    assert (captured / "worker-files" / "worker-screenshot.png").is_file()
    assert not any("auth.json" in str(path) for path in captured.rglob("*"))
    worker = json.loads((captured / "worker.json").read_text())
    assert worker["session"] == "sess-123456"
    assert worker["engine"] == "claude"
    assert any(item["kind"] == "native-session" for item in worker["files"])


def test_capture_kimi_session_directory(tmp_path):
    workspace = tmp_path / "workspace"
    workdir = workspace / "workers" / "cli-kimi-1"
    home = tmp_path / "home"
    wire = (
        home / ".kimi-code" / "sessions" / "wd_cli-kimi-1" /
        "session_abcd1234" / "agents" / "main" / "wire.jsonl"
    )
    wire.parent.mkdir(parents=True)
    wire.write_text(
        '{"type":"context.append_loop_event","event":{"type":"tool.call",'
        '"name":"Bash","args":{"command":"echo hi"}}}\n',
        encoding="utf-8",
    )
    workdir.mkdir(parents=True)

    meta = preserve_worker_conversation(
        workdir=workdir,
        solver_id="cli-kimi-1",
        engine="kimi",
        session="session_abcd1234",
        env={"HOME": str(home)},
    )

    captured = workspace / "conversations" / "cli-kimi-1"
    assert meta["status"] == "captured"
    assert list((captured / "native").rglob("*wire.jsonl"))
    assert not (captured / "native" / "auth.json").exists()


def test_build_conversation_archive_includes_all_workers_and_media(tmp_path):
    sessions = tmp_path / "sessions"
    workspace = tmp_path / "workspace"
    sessions.mkdir()
    (workspace / "arts").mkdir(parents=True)
    (workspace / "arts" / "photo.png").write_bytes(b"photo")
    (workspace / "final" / "exp").mkdir(parents=True)
    (workspace / "final" / "exp" / "exp.py").write_text("print('exp')\n")
    (workspace / "writeup.md").write_text("# writeup\n")

    workdir = workspace / "workers" / "cli-a"
    native = workdir / ".muteki-claude-config" / "projects" / "x" / "sess-a.jsonl"
    native.parent.mkdir(parents=True)
    native.write_text('{"type":"assistant","message":"worker a"}\n')

    preserve_worker_conversation(
        workdir=workdir,
        solver_id="cli-a",
        engine="claude",
        session="sess-a",
        env={"CLAUDE_CONFIG_DIR": str(workdir / ".muteki-claude-config")},
    )

    events = [
        {"event_type": "run.started", "solver_id": None, "ts": 1,
         "payload": {"challenge": {"name": "demo"}}},
        {"event_type": "worker.status", "solver_id": "cli-a", "ts": 2,
         "payload": {"engine": "claude", "session": "sess-a"}},
        {"event_type": "reasoning.delta", "solver_id": "cli-a", "ts": 3,
         "payload": {"text": "worker a thinking"}},
        {"event_type": "worker.status", "solver_id": "cli-b", "ts": 4,
         "payload": {"engine": "codex", "session": ""}},
        {"event_type": "text.delta", "solver_id": "cli-b", "ts": 5,
         "payload": {"text": "worker b answer"}},
        {"event_type": "run.finished", "solver_id": None, "ts": 6,
         "payload": {"flag": "flag{demo}", "solved": True}},
    ]

    archive = build_conversation_archive(
        sessions_root=sessions,
        run_id="run-demo",
        workspace_root=workspace,
        challenge_name="demo",
        category="pwn",
        events=events,
        flags=["flag{demo}"],
        expected_flags=1,
    )
    try:
        with zipfile.ZipFile(archive) as zf:
            names = set(zf.namelist())
            assert "muteki/events.jsonl" in names
            assert "muteki/transcript.md" in names
            assert "muteki/writeup.md" in names
            assert "conversations/cli-a/native" in {
                str(Path(n).parent) for n in names
            } or any(n.startswith("conversations/cli-a/native/") for n in names)
            assert "extras/exp/exp.py" in names
            assert "media/arts/photo.png" in names
            assert "manifest.json" in names
            manifest = json.loads(zf.read("manifest.json"))
            worker_ids = {row["solver_id"] for row in manifest["workers"]}
            assert {"cli-a", "cli-b"} <= worker_ids
            assert manifest["challenge"]["name"] == "demo"
            assert manifest["flags"] == ["flag{demo}"]
            transcript = zf.read("muteki/transcript.md").decode()
            assert "worker a thinking" in transcript
            assert "worker b answer" in transcript
    finally:
        archive.unlink(missing_ok=True)


def test_archive_synthesizes_exp_for_inline_heredoc_solve(tmp_path):
    """LongTime regression: no EXP file, empty manifest, inline heredoc only."""
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    workspace = tmp_path / "workspace"
    captured = workspace / "conversations" / "cli-kimi-9" / "native"
    captured.mkdir(parents=True)
    (captured / "wire.jsonl").write_text(
        json.dumps({
            "type": "context.append_loop_event",
            "event": {
                "type": "tool.call",
                "name": "Bash",
                "args": {
                    "command": (
                        "python3 - <<'EOF'\n"
                        "print('flag{archive_synth}')\n"
                        "EOF"
                    )
                },
            },
        }) + "\n",
        encoding="utf-8",
    )
    exp_dir = workspace / "final" / "exp"
    exp_dir.mkdir(parents=True)
    (exp_dir / "manifest.json").write_text(
        json.dumps({"version": 1, "worker_workdir": "", "files": []}),
        encoding="utf-8",
    )

    archive = build_conversation_archive(
        sessions_root=sessions,
        run_id="run-inline",
        workspace_root=workspace,
        challenge_name="inline",
        category="crypto",
        events=[],
        flags=["flag{archive_synth}"],
        winner={
            "worker_id": "cli-kimi-9",
            "engine": "kimi",
            "session": "",
            "workdir_rel": "",
        },
    )
    try:
        with zipfile.ZipFile(archive) as zf:
            names = set(zf.namelist())
            assert "extras/exp/exp.py" in names
            assert "extras/exp/exp.sh" in names
            assert "flag{archive_synth}" in zf.read("extras/exp/exp.py").decode()
            assert not any("__pycache__" in name for name in names)
    finally:
        archive.unlink(missing_ok=True)
