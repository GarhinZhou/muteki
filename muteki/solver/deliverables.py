"""Durable EXP/deliverable preservation for the post-solve results page.

The winning Worker's cwd is ephemeral: coordinator finalization sweeps
non-winner scratch trees, and a resumed/archived run must still be able to show
and download the actual exploit script. At solve time we copy a small, ranked
set of source files into ``workspace/final/exp/`` and write a manifest. The Web
API reads only that manifest (or, for older runs, a still-present winner cwd).

This module is deliberately conservative: source-like files only, bounded size,
no symlinks, no traversal outside the winner cwd.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

EXP_SUBDIR = Path("final") / "exp"
MANIFEST_NAME = "manifest.json"
MAX_FILES = 5
MAX_FILE_BYTES = 256_000

_CODE_SUFFIXES = {
    ".py": "python",
    ".sage": "python",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".js": "javascript",
    ".mjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".rb": "ruby",
    ".go": "go",
    ".rs": "rust",
    ".pl": "perl",
    ".php": "php",
    ".lua": "lua",
    ".java": "java",
    ".kt": "kotlin",
    ".swift": "swift",
    ".asm": "asm",
    ".s": "asm",
    ".ps1": "powershell",
}

_SKIP_DIRS = {
    "arts", "inputs", "shared", "objects", "links", "logs", "tmp", "homes",
    "graph", "final", "workers", "node_modules", ".git", ".venv", "__pycache__",
}
_EXP_PREFIXES = (
    "exp", "exploit", "poc", "solve", "solver", "rce", "payload",
    "attack", "shell", "brute", "ret2", "run_remote", "runner", "verify",
)
_EXPLOIT_HINT_RE = re.compile(
    r"\b(pwn|pwntools|remote\(|socket|requests\.|execve|shellcode|ROP|"
    r"flag\{|cat flag|/bin/sh|payload)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ExpCandidate:
    path: Path
    rel: str
    language: str
    size: int
    sha256: str
    text: str
    score: int


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def _is_binary(raw: bytes) -> bool:
    return b"\x00" in raw[:8192]


def _language_for(name: str) -> str:
    return _CODE_SUFFIXES.get(Path(name).suffix.lower(), "")


def _safe_name(name: str, used: set[str]) -> str:
    base = Path(name).name or "exp.txt"
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._-") or "exp.txt"
    candidate = cleaned
    index = 2
    while candidate in used:
        candidate = f"{index}-{cleaned}"
        index += 1
    used.add(candidate)
    return candidate


def _iter_source_files(workdir: Path) -> Iterable[Path]:
    # Crypto/forensics workers often publish their reusable solver under the
    # team board's `shared/` directory rather than `scratch/`. Include it as an
    # explicit root (it remains excluded from the recursive workdir walk below).
    roots = [workdir / "scratch", workdir / "shared", workdir]
    seen: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            current = Path(dirpath)
            try:
                rel_dir = current.relative_to(workdir)
            except ValueError:
                continue
            if len(rel_dir.parts) > 3:
                dirnames[:] = []
                continue
            dirnames[:] = [
                d for d in dirnames
                if d not in _SKIP_DIRS and not d.startswith(".")
            ]
            for filename in filenames:
                if filename.startswith("."):
                    continue
                path = current / filename
                if path in seen:
                    continue
                seen.add(path)
                yield path


def _score(path: Path, text: str) -> int:
    name = path.name.lower()
    score = 0
    if any(
        name.startswith(prefix)
        or f"_{prefix}" in name
        or f"-{prefix}" in name
        for prefix in _EXP_PREFIXES
    ):
        score += 20
    suffix = path.suffix.lower()
    if suffix == ".py":
        score += 6
    elif suffix in {".sh", ".bash", ".zsh"}:
        score += 5
    else:
        score += 2
    if path.parent.name == "scratch":
        score += 4
    if _EXPLOIT_HINT_RE.search(text[:20000]):
        score += 5
    return score


def _candidates(workdir: Path, *, max_files: int = MAX_FILES) -> list[ExpCandidate]:
    workdir = workdir.resolve()
    out: list[ExpCandidate] = []
    for path in _iter_source_files(workdir):
        if path.is_symlink():
            continue
        language = _language_for(path.name)
        if not language:
            continue
        try:
            resolved = path.resolve()
            if not _within(resolved, workdir):
                continue
            size = resolved.stat().st_size
            if size <= 0 or size > MAX_FILE_BYTES:
                continue
            raw = resolved.read_bytes()
        except OSError:
            continue
        if _is_binary(raw):
            continue
        text = raw.decode("utf-8", errors="replace")
        try:
            rel = str(resolved.relative_to(workdir))
        except ValueError:
            rel = path.name
        out.append(ExpCandidate(
            path=resolved,
            rel=rel,
            language=language,
            size=size,
            sha256=hashlib.sha256(raw).hexdigest(),
            text=text,
            score=_score(path, text),
        ))
    out.sort(key=lambda item: (-item.score, item.size, item.rel))
    return out[:max_files]


def _atomic_write(path: Path, payload: str) -> None:
    temporary = path.with_name(f".{path.name}-{os.getpid()}.tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)


def preserve_winner_deliverables(
    workspace_root: str | Path,
    workdir: str | Path,
    *,
    max_files: int = MAX_FILES,
) -> list[dict[str, Any]]:
    """Copy the winning Worker's EXP-like source files into a durable directory.

    Best-effort by design: callers run this during finalization and must never
    fail a solved run because a scratch file vanished or was unreadable.
    """
    workspace = Path(workspace_root).resolve()
    worker_dir = Path(workdir).resolve()
    if not worker_dir.is_dir() or not _within(worker_dir, workspace / "workers"):
        return []

    # Workers can write inside the run workspace. Never let a pre-created
    # symlink (or a non-directory) redirect the durable copy outside it.
    final_dir = workspace / "final"
    if final_dir.is_symlink() or not _within(final_dir, workspace):
        return []
    final_dir.mkdir(parents=True, exist_ok=True)
    exp_dir = final_dir / "exp"
    if exp_dir.is_symlink():
        try:
            exp_dir.unlink()
        except OSError:
            return []
    if exp_dir.exists() and not exp_dir.is_dir():
        return []
    if not _within(exp_dir, workspace):
        return []

    candidates = _candidates(worker_dir, max_files=max_files)
    if not candidates:
        # Some workers publish reusable scripts through the run-level shared
        # directory (or a symlink to it), so the winner cwd can be empty.
        shared_root = workspace / "shared"
        if shared_root.is_dir() and not shared_root.is_symlink():
            candidates = _candidates(shared_root, max_files=max_files)
    if exp_dir.exists():
        shutil.rmtree(exp_dir, ignore_errors=True)
    exp_dir.mkdir(parents=True, exist_ok=True)

    used: set[str] = set()
    files: list[dict[str, Any]] = []
    for item in candidates:
        name = _safe_name(item.path.name, used)
        destination = exp_dir / name
        try:
            shutil.copyfile(item.path, destination)
            os.chmod(destination, 0o644)
        except OSError:
            continue
        files.append({
            "name": name,
            "source": item.rel,
            "language": item.language,
            "size": item.size,
            "sha256": item.sha256,
        })
    try:
        worker_rel = str(worker_dir.relative_to(workspace))
    except ValueError:
        worker_rel = ""
    _atomic_write(exp_dir / MANIFEST_NAME, json.dumps({
        "version": 1,
        "worker_workdir": worker_rel,
        "files": files,
    }, ensure_ascii=False, indent=2))
    return files


def load_preserved_deliverables(
    workspace_root: str | Path,
    *,
    max_bytes: int = MAX_FILE_BYTES,
    with_content: bool = True,
) -> list[dict[str, Any]]:
    """Read the durable EXP manifest written by :func:`preserve_winner_deliverables`."""
    workspace = Path(workspace_root).resolve()
    exp_dir = workspace / EXP_SUBDIR
    if exp_dir.is_symlink() or not _within(exp_dir, workspace):
        return []
    try:
        payload = json.loads((exp_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return []
    out: list[dict[str, Any]] = []
    for row in list(payload.get("files") or []):
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "")
        if not name or Path(name).name != name:
            continue
        path = exp_dir / name
        try:
            resolved = path.resolve()
            if not _within(resolved, exp_dir):
                continue
            size = resolved.stat().st_size
            if size <= 0 or size > max_bytes:
                continue
            raw = resolved.read_bytes()
        except OSError:
            continue
        if _is_binary(raw):
            continue
        item = {
            "name": name,
            "source": str(row.get("source") or ""),
            "language": str(row.get("language") or _language_for(name)),
            "size": size,
        }
        if with_content:
            item["content"] = raw.decode("utf-8", errors="replace")
        out.append(item)
    return out


def discover_deliverables(
    workspace_root: str | Path,
    *,
    workdir: str | Path | None = None,
    max_files: int = MAX_FILES,
    max_bytes: int = MAX_FILE_BYTES,
) -> list[dict[str, Any]]:
    """Fallback discovery for runs that predate the durable manifest.

    Scans the supplied winner cwd, or every ``workers/*`` cwd when no explicit
    workdir is known. This keeps old runs with surviving scratch directories
    useful without ever reading outside the run workspace.
    """
    workspace = Path(workspace_root).resolve()
    candidates: list[ExpCandidate] = []
    if workdir is not None:
        worker_dir = Path(workdir).resolve()
        if worker_dir.is_dir() and _within(worker_dir, workspace / "workers"):
            candidates = _candidates(worker_dir, max_files=max_files)
    else:
        workers = workspace / "workers"
        if workers.is_dir():
            for child in sorted(workers.iterdir(), key=lambda p: p.name):
                if not child.is_dir() or child.is_symlink():
                    continue
                if not _within(child, workers):
                    continue
                candidates.extend(_candidates(child, max_files=max_files))
            candidates.sort(key=lambda item: (-item.score, item.size, item.rel))
            candidates = candidates[:max_files]
    if not candidates:
        shared_root = workspace / "shared"
        if shared_root.is_dir() and not shared_root.is_symlink():
            candidates = _candidates(shared_root, max_files=max_files)
    out: list[dict[str, Any]] = []
    for item in candidates:
        if item.size > max_bytes:
            continue
        out.append({
            "name": item.path.name,
            "source": item.rel,
            "language": item.language,
            "size": item.size,
            "content": item.text,
        })
    return out
