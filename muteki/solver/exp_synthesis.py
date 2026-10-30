"""Synthesize a runnable EXP from confirmed native tool history.

Some agents solve in inline shell/python heredocs and never write a ``.py`` file
into their cwd. The results page previously fell back to "no EXP" even though
the exact commands exist in the native session log (for example Kimi's
``agents/main/wire.jsonl``). This module extracts those commands, writes a
durable ``final/exp/exp.py`` / ``exp.sh`` pair, and lets the existing
``/api/runs/{id}/exp`` route serve them like any other deliverable.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Optional

from muteki.solver.conversation_export import (
    _iter_native_files,
    _native_roots,
)

_EXP_DIR = Path("final") / "exp"
_MANIFEST = "manifest.json"
_MAX_FILE_BYTES = 256_000

_HEREDOC_RE = re.compile(
    r"python(?:3(?:\.\d+)?)?\s*(?:-\s*)?<<-?\s*"
    r"['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?\s*\n"
    r"(.*?)\n[ \t]*\1(?:\s|$)",
    re.DOTALL,
)
_CAT_HEREDOC_RE = re.compile(
    r"cat\s*>\s*([^\s;]+\.py)\s*<<-?\s*"
    r"['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?\s*\n"
    r"(.*?)\n[ \t]*\2(?:\s|$)",
    re.DOTALL,
)
_PY_C_RE = re.compile(
    r"python(?:3(?:\.\d+)?)?\s+-c\s+([\"'])(.*?)\1",
    re.DOTALL,
)

_SKIP_COMMAND_TOKENS = (
    "blackboard.py", "read-facts", "read-deadends", "read-review",
    "read-directives", "read-routes", "read-flags", "write-fact",
    "mark-deadend", "submit-flag", "write-poc",
)
_SOLVE_COMMAND_HINTS = (
    "python", "python3", "pwn", "nc ", "ncat", "socat", "curl", "ssh ",
    "gcc ", "objdump", "readelf", "cat >", "echo ", "printf ", "perl ",
    "ruby ", "node ", "go run", "sage ",
)


def _safe_bytes(text: str) -> bytes:
    raw = text.encode("utf-8", "replace")
    if len(raw) <= _MAX_FILE_BYTES:
        return raw
    return raw[:_MAX_FILE_BYTES] + b"\n# [truncated]\n"


def _iter_json_lines(paths: Iterable[Path]) -> Iterable[Any]:
    for path in paths:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except (json.JSONDecodeError, TypeError):
                        continue
        except OSError:
            continue


def _commands_from_obj(node: Any, out: list[str]) -> None:
    """Walk a native event object and collect tool command strings only."""
    if isinstance(node, dict):
        node_type = str(node.get("type") or "")
        name = str(node.get("name") or node.get("tool") or "")
        args = node.get("args")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (json.JSONDecodeError, TypeError):
                args = None
        if isinstance(args, dict):
            command = args.get("command") or args.get("cmd") or args.get("script")
            if isinstance(command, str) and command.strip():
                out.append(command)
        # Codex item events and generic command_execution records.
        if node_type in {"command_execution", "tool.call", "tool_use"} or name in {
                "Bash", "bash", "shell", "Shell", "terminal"}:
            command = node.get("command")
            if isinstance(command, str) and command.strip():
                out.append(command)
            payload = node.get("input")
            if isinstance(payload, dict):
                command = payload.get("command") or payload.get("cmd")
                if isinstance(command, str) and command.strip():
                    out.append(command)
        for value in node.values():
            if isinstance(value, (dict, list)):
                _commands_from_obj(value, out)
    elif isinstance(node, list):
        for value in node:
            _commands_from_obj(value, out)


def commands_from_native(paths: Iterable[Path]) -> list[str]:
    out: list[str] = []
    for obj in _iter_json_lines(paths):
        _commands_from_obj(obj, out)
    seen: set[str] = set()
    unique: list[str] = []
    for command in out:
        text = command.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        unique.append(text)
    return unique


def _is_solve_command(command: str) -> bool:
    low = command.lower()
    if any(token in low for token in _SKIP_COMMAND_TOKENS):
        return False
    if "read " in low or "cat chall.py" in low or "cat output.txt" in low:
        return False
    return any(token in low for token in _SOLVE_COMMAND_HINTS)


def _python_blocks(commands: Iterable[str]) -> list[str]:
    blocks: list[str] = []
    for command in commands:
        for match in _HEREDOC_RE.finditer(command):
            body = match.group(2).strip("\n")
            if body.strip():
                blocks.append(body)
        for match in _CAT_HEREDOC_RE.finditer(command):
            body = match.group(3).strip("\n")
            if body.strip():
                blocks.append(body)
        for match in _PY_C_RE.finditer(command):
            body = match.group(2)
            if body.strip():
                blocks.append(body)
    return blocks


def _make_python(blocks: list[str]) -> str:
    lines = [
        "#!/usr/bin/env python3",
        "# Auto-generated from the Worker's confirmed native tool history.",
        "# Each block below was actually executed during the solve.",
        "",
    ]
    for index, block in enumerate(blocks, 1):
        lines.append(f"# ---- executed block {index} ----")
        lines.append(block.rstrip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _make_shell(commands: list[str]) -> str:
    lines = [
        "#!/usr/bin/env bash",
        "# Auto-generated from the Worker's confirmed native tool history.",
        "set -euo pipefail",
        "",
    ]
    for index, command in enumerate(commands, 1):
        lines.append(f"# ---- executed command {index} ----")
        lines.append(command.rstrip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _write_manifest(
    exp_dir: Path,
    *,
    workdir_rel: str,
    files: list[dict[str, Any]],
) -> None:
    payload = {
        "version": 1,
        "worker_workdir": workdir_rel,
        "generator": "native-tool-history",
        "files": files,
    }
    tmp = exp_dir / (".manifest-" + hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12] + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(exp_dir / _MANIFEST)


def _existing_files(exp_dir: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads((exp_dir / _MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    return [
        row for row in (payload.get("files") or [])
        if isinstance(row, dict) and row.get("name")
    ]


def ensure_synthesized_exp(
    *,
    workspace_root: str | Path,
    solver_id: str,
    engine: str,
    session: str = "",
    workdir: Optional[str | Path] = None,
    sessions_root: Optional[str | Path] = None,
) -> list[dict[str, Any]]:
    """Create final/exp from native history if no real EXP is present.

    Returns the same shape as ``load_preserved_deliverables`` (including content)
    so the Web route can serve it directly.
    """
    workspace = Path(workspace_root).resolve()
    exp_dir = workspace / _EXP_DIR
    existing = _existing_files(exp_dir)
    if existing:
        loaded = _load_files(exp_dir, existing)
        if loaded:
            return loaded
        # Stale manifest: entries exist but the files were swept. Fall through
        # and synthesize from the captured conversation instead of giving up.

    captured = None
    clean_solver = str(solver_id or "").strip()
    if clean_solver:
        candidate = workspace / "conversations" / clean_solver
        if candidate.is_dir() and not candidate.is_symlink():
            captured = candidate
    paths: list[Path] = []
    if captured is not None:
        for path in captured.rglob("*"):
            if path.is_file() and not path.is_symlink():
                paths.append(path)

    account_root = (
        Path(sessions_root).resolve() / "_secrets" / "accounts"
        if sessions_root is not None else None
    )
    for root, per_worker in _native_roots(
            engine, Path(workdir) if workdir else None, None,
            account_root=account_root):
        for path in _iter_native_files(
                root, session, per_worker_root=per_worker):
            if path not in paths:
                paths.append(path)

    commands = commands_from_native(paths)
    solve_commands = [c for c in commands if _is_solve_command(c)]
    if not solve_commands:
        return []

    blocks = _python_blocks(solve_commands)
    py_text = _make_python(blocks) if blocks else ""
    sh_text = _make_shell(solve_commands)
    if not py_text and not sh_text:
        return []

    try:
        if exp_dir.is_symlink():
            return []
        exp_dir.mkdir(parents=True, exist_ok=True)
        if exp_dir.is_symlink() or not _within(exp_dir, workspace):
            return []
    except OSError:
        return []

    files: list[dict[str, Any]] = []
    if py_text:
        raw = _safe_bytes(py_text)
        (exp_dir / "exp.py").write_bytes(raw)
        files.append({
            "name": "exp.py",
            "source": "synthesized-from-native-conversation",
            "language": "python",
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        })
    if sh_text:
        raw = _safe_bytes(sh_text)
        (exp_dir / "exp.sh").write_bytes(raw)
        files.append({
            "name": "exp.sh",
            "source": "synthesized-from-native-conversation",
            "language": "bash",
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        })
    if not files:
        return []
    try:
        worker_rel = ""
        if workdir:
            candidate = Path(workdir).resolve()
            if _within(candidate, workspace):
                worker_rel = str(candidate.relative_to(workspace))
        _write_manifest(exp_dir, workdir_rel=worker_rel, files=files)
    except OSError:
        return []
    return _load_files(exp_dir, files)


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _load_files(exp_dir: Path, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        name = str(row.get("name") or "")
        if not name or Path(name).name != name:
            continue
        path = exp_dir / name
        try:
            resolved = path.resolve()
            if not _within(resolved, exp_dir):
                continue
            raw = resolved.read_bytes()
        except OSError:
            continue
        out.append({
            "name": name,
            "source": str(row.get("source") or ""),
            "language": str(row.get("language") or "text"),
            "size": len(raw),
            "content": raw.decode("utf-8", "replace"),
        })
    return out
