"""Inline-heredoc solves: synthesize EXP from captured native tool history."""

import json
from pathlib import Path

from muteki.solver.exp_synthesis import ensure_synthesized_exp


def test_synthesize_exp_from_captured_kimi_wire(tmp_path):
    workspace = tmp_path / "workspace"
    captured = workspace / "conversations" / "cli-kimi" / "native"
    captured.mkdir(parents=True)
    wire = captured / "wire.jsonl"
    events = [
        {
            "type": "context.append_loop_event",
            "event": {
                "type": "tool.call",
                "name": "Bash",
                "args": {
                    "command": (
                        "python3 - <<'EOF'\n"
                        "state = 0xe8edcc3b102b68396e398e3d9d4256e5\n"
                        "print(hex(state))\n"
                        "EOF"
                    )
                },
            },
        },
        {
            "type": "context.append_loop_event",
            "event": {
                "type": "tool.call",
                "name": "Bash",
                "args": {
                    "command": (
                        "python3 - <<'EOF'\n"
                        "from hashlib import sha256\n"
                        "print(sha256(b'x').hexdigest())\n"
                        "EOF"
                    )
                },
            },
        },
        {
            "type": "context.append_loop_event",
            "event": {
                "type": "tool.call",
                "name": "Bash",
                "args": {"command": "python3 \"$MUTEKI_BLACKBOARD_SCRIPT\" read-facts"},
            },
        },
    ]
    wire.write_text(
        "".join(json.dumps(row) + "\n" for row in events), encoding="utf-8")

    files = ensure_synthesized_exp(
        workspace_root=workspace,
        solver_id="cli-kimi",
        engine="kimi",
        session="",
        sessions_root=tmp_path / "sessions",
    )

    names = {row["name"] for row in files}
    assert names == {"exp.py", "exp.sh"}
    py = (workspace / "final" / "exp" / "exp.py").read_text()
    sh = (workspace / "final" / "exp" / "exp.sh").read_text()
    assert "0xe8edcc3b102b68396e398e3d9d4256e5" in py
    assert "sha256(b'x')" in py
    assert "python3 - <<'EOF'" in sh
    assert "MUTEKI_BLACKBOARD_SCRIPT" not in sh
    manifest = json.loads((workspace / "final" / "exp" / "manifest.json").read_text())
    assert {row["name"] for row in manifest["files"]} == {"exp.py", "exp.sh"}


def test_stale_empty_manifest_is_recovered(tmp_path):
    """An empty or stale manifest must not suppress synthesis."""
    workspace = tmp_path / "workspace"
    captured = workspace / "conversations" / "cli-kimi" / "native"
    captured.mkdir(parents=True)
    (captured / "wire.jsonl").write_text(
        json.dumps({
            "type": "tool.call",
            "name": "Bash",
            "args": {"command": "python3 - <<'EOF'\nprint('ok')\nEOF"},
        }) + "\n",
        encoding="utf-8",
    )
    exp_dir = workspace / "final" / "exp"
    exp_dir.mkdir(parents=True)
    (exp_dir / "manifest.json").write_text(
        json.dumps({"version": 1, "files": [{"name": "exp.py"}]}),
        encoding="utf-8",
    )

    files = ensure_synthesized_exp(
        workspace_root=workspace,
        solver_id="cli-kimi",
        engine="kimi",
    )

    assert {row["name"] for row in files} == {"exp.py", "exp.sh"}
    assert (exp_dir / "exp.py").is_file()
