"""Collect a run's complete conversation record for post-match upload.

The canonical record is Muteki's own ``sessions/<run>.jsonl`` event stream: it
contains every Worker's reasoning, tool calls, terminal output, facts and flags.
Native CLI agents keep a second, richer transcript in their own state dirs
(Claude ``~/.claude/projects/*.jsonl``, Codex ``CODEX_HOME/sessions``, ...).
This module captures those native files while a Worker's scratch tree still
exists, then packages everything into one ZIP.

The archive is deliberately conservative about paths and secrets:

* only files under the run workspace or a known engine session root are read;
* symlinks are never followed;
* credential/config files are skipped by name;
* every entry is bounded by per-file and total-size caps.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from muteki.core.path_ids import encode_run_id
from muteki.solver.workspace import workspace_root_for_worker

CONVERSATION_DIR = "conversations"
ARCHIVE_DIR = "exports"
MANIFEST_NAME = "manifest.json"
README_NAME = "README.md"

MEDIA_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg",
    ".mp4", ".webm", ".mov", ".mkv", ".avi",
})

NATIVE_SUFFIXES = frozenset({".jsonl", ".ndjson", ".json"})
WORKER_DOC_SUFFIXES = frozenset({".md", ".txt", ".log", ".jsonl"})

# These names are never conversation payloads. In particular, never copy auth
# material out of an engine home into an operator-facing archive.
_SKIP_NAMES = frozenset({
    "auth.json", "credentials.json", "config.toml", "settings.json",
    "settings.local.json", "token", "tokens", ".env", ".netrc",
    "id_rsa", "id_ed25519",
})
_SKIP_TOKENS = frozenset({
    "auth", "credential", "secret", "token", "password", "apikey", "api-key",
})
_SKIP_DIR_TOKENS = frozenset({
    "auth", "credentials", "credential", "secrets", "secret", "tokens",
    "config", "configs", "providers", "settings",
})

MAX_NATIVE_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_WORKER_MEDIA_BYTES = 128 * 1024 * 1024
MAX_TRANSCRIPT_BYTES = 24 * 1024 * 1024
MAX_TRANSCRIPT_EVENT_CHARS = 4000

_PATH_ENV_KEYS = (
    "HOME", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "KIMI_CODE_HOME", "GROK_HOME",
    "XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "DSH_SESSION_ROOT",
    "MUTEKI_AGENT_STATE_DIR",
)


def _safe_component(value: Any, fallback: str = "worker") -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("._")
    return (text or fallback)[:120]


def _safe_arcname(value: str) -> str:
    parts = [p for p in str(value).replace("\\", "/").split("/") if p]
    return "/".join(part for part in parts if part not in {".", ".."})


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _path_env(env: Optional[Mapping[str, Any]]) -> dict[str, Path]:
    source = env if env is not None else os.environ
    out: dict[str, Path] = {}
    for key in _PATH_ENV_KEYS:
        raw = str(source.get(key) or "").strip()
        if raw:
            out[key] = Path(raw).expanduser()
    if "HOME" not in out:
        out["HOME"] = Path.home()
    return out


def _is_sensitive(path: Path) -> bool:
    name = path.name.lower()
    if name in _SKIP_NAMES or name.startswith(".env"):
        return True
    if any(token in name for token in _SKIP_TOKENS):
        return True
    return any(part.lower() in _SKIP_DIR_TOKENS for part in path.parts[-6:])


def _native_roots(
    engine: str,
    workdir: Optional[Path],
    env: Optional[Mapping[str, Any]],
    *,
    account_root: Optional[Path] = None,
) -> list[tuple[Path, bool]]:
    """Return (root, per_worker_root) candidates for one engine.

    ``per_worker_root`` means the directory belongs only to this Worker (a
    ``.muteki-*`` state dir), so it is safe to include every session file there
    even when the engine did not expose a session id.
    """
    paths = _path_env(env)
    home = paths["HOME"]
    roots: list[tuple[Path, bool]] = []
    seen: set[str] = set()

    def add(path: Optional[Path], per_worker: bool = False) -> None:
        if path is None:
            return
        try:
            candidate = path.expanduser()
            key = str(candidate.resolve())
        except OSError:
            return
        if key in seen:
            return
        seen.add(key)
        roots.append((candidate, per_worker))

    e = str(engine or "").strip().lower()
    if e == "claude":
        add(paths.get("CLAUDE_CONFIG_DIR"))
        add((workdir / ".muteki-claude-config") if workdir else None, True)
        add(home / ".claude")
    elif e == "codex":
        add(paths.get("CODEX_HOME"))
        add(home / ".codex")
    elif e == "kimi":
        add(paths.get("KIMI_CODE_HOME"))
        add(home / ".kimi-code")
        add(home / ".kimi")
    elif e == "grok":
        add(paths.get("GROK_HOME"))
        add(home / ".grok")
    elif e == "opencode":
        add((workdir / ".muteki-opencode") if workdir else None, True)
        add(Path(paths["MUTEKI_AGENT_STATE_DIR"]) if paths.get("MUTEKI_AGENT_STATE_DIR") else None, True)
        add(paths.get("XDG_DATA_HOME") / "opencode" if paths.get("XDG_DATA_HOME") else None)
        add(home / ".local" / "share" / "opencode")
    elif e == "pi":
        add((workdir / ".muteki-agent-state") if workdir else None, True)
        add(Path(paths["MUTEKI_AGENT_STATE_DIR"]) if paths.get("MUTEKI_AGENT_STATE_DIR") else None, True)
        add(home / ".pi")
    elif e == "omp":
        add((workdir / ".muteki-agent-state") if workdir else None, True)
        add(Path(paths["MUTEKI_AGENT_STATE_DIR"]) if paths.get("MUTEKI_AGENT_STATE_DIR") else None, True)
        add(home / ".omp")
    elif e == "cursor":
        add(home / ".cursor")
    elif e == "dsh":
        add(Path(paths["DSH_SESSION_ROOT"]) if paths.get("DSH_SESSION_ROOT") else None, True)
        add((workdir / ".muteki-dsh-sessions") if workdir else None, True)
        add(Path(paths["MUTEKI_AGENT_STATE_DIR"]) if paths.get("MUTEKI_AGENT_STATE_DIR") else None, True)
    else:
        add(home / f".{e}" if e else None)

    if workdir is not None:
        try:
            workspace = (
                workdir.parent.parent
                if workdir.parent.name == "workers"
                else workdir.parent
            )
            # Container workers see the account projection at this run-local
            # path; native Codex/Kimi/Grok sessions live below it.
            add(workspace / ".muteki_accounts")
        except (OSError, ValueError):
            pass

    if account_root is not None and account_root.is_dir():
        try:
            for account in sorted(account_root.iterdir()):
                if not account.is_dir() or account.is_symlink():
                    continue
                for child in ("codex-home", "kimi-home", "grok-home", "cursor-home"):
                    candidate = account / child
                    if candidate.is_dir():
                        add(candidate)
        except OSError:
            pass
    return roots


def _iter_native_files(
    root: Path,
    session: str,
    *,
    per_worker_root: bool,
) -> Iterable[Path]:
    if not root.is_dir() or root.is_symlink():
        return
    want = re.sub(r"[^A-Za-z0-9_.-]+", "", str(session or ""))
    seen: set[str] = set()
    try:
        candidates: Iterable[Path]
        if per_worker_root:
            # This state directory belongs to exactly one Worker; every session
            # file there is part of this conversation, even if the engine did not
            # expose a session id.
            candidates = root.rglob("*")
        elif want and len(want) >= 6:
            candidates = root.rglob(f"*{want}*")
        else:
            return
        for path in candidates:
            try:
                if not _within(path, root):
                    continue
                # Kimi/Grok-style engines store one session as a DIRECTORY
                # (e.g. sessions/<id>/agents/main/wire.jsonl). The directory name
                # carries the session id; recurse into it and collect the actual
                # conversation files.
                if path.is_dir() and not path.is_symlink():
                    for child in path.rglob("*"):
                        try:
                            if not child.is_file() or child.is_symlink():
                                continue
                            if not _within(child, root):
                                continue
                            if child.suffix.lower() not in NATIVE_SUFFIXES:
                                continue
                            if _is_sensitive(child):
                                continue
                            resolved = str(child.resolve())
                            if resolved in seen:
                                continue
                            seen.add(resolved)
                            yield child
                        except OSError:
                            continue
                    continue
                if not path.is_file() or path.is_symlink():
                    continue
                if path.suffix.lower() not in NATIVE_SUFFIXES:
                    continue
                if _is_sensitive(path):
                    continue
                resolved = str(path.resolve())
                if resolved in seen:
                    continue
                seen.add(resolved)
                yield path
            except OSError:
                continue
    except OSError:
        return


def _iter_media(root: Path, *, max_bytes: int = MAX_WORKER_MEDIA_BYTES) -> Iterable[Path]:
    if not root.is_dir() or root.is_symlink():
        return
    total = 0
    try:
        for path in root.rglob("*"):
            try:
                if not path.is_file() or path.is_symlink():
                    continue
                if not _within(path, root):
                    continue
                if path.suffix.lower() not in MEDIA_SUFFIXES:
                    continue
                size = path.stat().st_size
                if size <= 0 or size > max_bytes or total + size > max_bytes:
                    continue
                total += size
                yield path
            except OSError:
                continue
    except OSError:
        return


def _copy_native(
    src: Path,
    dest: Path,
    *,
    source_label: str,
    used: set[str],
    records: list[dict[str, Any]],
    remaining: list[int],
) -> None:
    try:
        size = src.stat().st_size
    except OSError:
        return
    if size <= 0 or size > MAX_NATIVE_FILE_BYTES or size > remaining[0]:
        return
    name = f"{source_label}-{src.name}"
    if name in used:
        name = f"{source_label}-{hashlib.sha256(str(src).encode()).hexdigest()[:8]}-{src.name}"
    if name in used:
        return
    used.add(name)
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / _safe_component(name, "session-jsonl")
    try:
        shutil.copyfile(src, target)
        os.chmod(target, 0o644)
    except OSError:
        return
    remaining[0] -= size
    try:
        archive_path = (
            f"{CONVERSATION_DIR}/"
            f"{target.relative_to(dest.parent.parent).as_posix()}"
        )
    except ValueError:
        archive_path = target.name
    records.append({
        "kind": "native-session" if dest.name == "native" else "worker-file",
        "source": str(src),
        "archive_path": archive_path,
        "size": size,
        "sha256": _sha256(target),
    })


def preserve_worker_conversation(
    *,
    workdir: str | Path,
    solver_id: str,
    engine: str,
    session: str = "",
    env: Optional[Mapping[str, Any]] = None,
    identity: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Copy one Worker's native session files before its scratch is swept.

    Returns a small metadata dict (also written as ``worker.json``). This is
    best-effort: any filesystem error leaves the durable Muteki event log as the
    fallback record and never fails the Worker.
    """
    try:
        wd = Path(workdir).resolve()
        workspace = workspace_root_for_worker(wd).resolve()
    except (OSError, ValueError):
        return {}
    if not wd.is_dir() or not _within(wd, workspace / "workers"):
        return {}

    worker_key = _safe_component(solver_id)
    dest = workspace / CONVERSATION_DIR / worker_key
    native_dir = dest / "native"
    worker_files_dir = dest / "worker-files"
    try:
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        native_dir.mkdir(parents=True, exist_ok=True)
        worker_files_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return {}

    records: list[dict[str, Any]] = []
    remaining = [MAX_TOTAL_BYTES]
    used: set[str] = set()
    for root, per_worker in _native_roots(engine, wd, env):
        for src in _iter_native_files(
                root, session, per_worker_root=per_worker):
            _copy_native(
                src, native_dir,
                source_label=_safe_component(root.name or "native"),
                used=used, records=records, remaining=remaining,
            )
            # Media that lives next to a native session is part of the same
            # conversation (Claude/Cursor may save screenshots or recordings).
            try:
                for media in src.parent.iterdir():
                    if (media.is_file() and not media.is_symlink()
                            and media.suffix.lower() in MEDIA_SUFFIXES):
                        _copy_native(
                            media,
                            dest / "native-media",
                            source_label=_safe_component(root.name or "native"),
                            used=used, records=records, remaining=remaining,
                        )
            except OSError:
                pass

    for src in _iter_media(wd):
        _copy_native(
            src, worker_files_dir, source_label="worker",
            used=used, records=records, remaining=remaining,
        )
    # The board snapshot / operator notes are part of the conversation context
    # even for engines that do not expose a native session file.
    try:
        for src in sorted(wd.iterdir(), key=lambda p: p.name):
            if (not src.is_file() or src.is_symlink()
                    or src.suffix.lower() not in WORKER_DOC_SUFFIXES
                    or _is_sensitive(src)):
                continue
            _copy_native(
                src, worker_files_dir, source_label="worker-doc",
                used=used, records=records, remaining=remaining,
            )
    except OSError:
        pass

    meta = {
        "version": 1,
        "solver_id": str(solver_id),
        "engine": str(engine),
        "session": str(session or ""),
        "workdir": str(wd),
        "identity": dict(identity or {}),
        "captured_at": time.time(),
        "files": records,
        "status": "captured" if records else "no_native_session_found",
    }
    try:
        (dest / "worker.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return meta


def _collect_workers(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    workers: dict[str, dict[str, Any]] = {}
    for ev in events:
        sid = str(ev.get("solver_id") or "").strip()
        if not sid:
            continue
        row = workers.setdefault(sid, {
            "solver_id": sid,
            "engine": "",
            "session": "",
            "profile_id": "",
            "profile_label": "",
            "model": "",
            "roles": [],
            "event_count": 0,
            "first_ts": 0.0,
            "last_ts": 0.0,
            "native_files": [],
        })
        row["event_count"] += 1
        ts = float(ev.get("ts") or 0.0)
        if ts:
            row["first_ts"] = min(row["first_ts"] or ts, ts)
            row["last_ts"] = max(row["last_ts"], ts)
        payload = ev.get("payload") or {}
        for key in ("engine", "session", "profile_id", "profile_label", "model"):
            value = str(payload.get(key) or "").strip()
            if value:
                row[key] = value
        role = str(payload.get("worker_role") or "").strip()
        if role and role not in row["roles"]:
            row["roles"].append(role)
    return workers


def _event_text(ev: dict[str, Any]) -> str:
    et = str(ev.get("event_type") or "")
    p = ev.get("payload") or {}
    if et in {"text.delta", "reasoning.delta"}:
        return str(p.get("text") or "")
    if et == "tool.start":
        return f"[tool] {p.get('tool') or ''}"
    if et == "tool.args":
        return f"[tool args] {p.get('args') or p.get('text') or ''}"
    if et == "tool.result":
        result = p.get("result")
        if isinstance(result, (dict, list)):
            return f"[tool result] {json.dumps(result, ensure_ascii=False, default=str)}"
        return f"[tool result] {result or ''}"
    if et == "terminal.output":
        return str(p.get("text") or p.get("data") or "")
    if et == "blackboard.delta":
        kind = str(p.get("kind") or "")
        fields = {k: p[k] for k in ("fact", "flag", "reason", "goal", "result", "worker") if p.get(k)}
        return f"[blackboard:{kind}] " + json.dumps(fields, ensure_ascii=False, default=str)
    if et in {"run.started", "run.finished"}:
        return f"[{et}] " + json.dumps(p, ensure_ascii=False, default=str)
    if et in {"hitl.request", "hitl.response", "worker.status", "worker.lifecycle"}:
        return f"[{et}] " + json.dumps(p, ensure_ascii=False, default=str)
    return ""


def _render_transcript(events: list[dict[str, Any]], workers: dict[str, dict[str, Any]]) -> str:
    lines = [
        "# Muteki conversation transcript",
        "",
        "This is the human-readable index. The complete lossless stream is in "
        "`muteki/events.jsonl`; native CLI sessions are under `conversations/` "
        "and `native/`.",
        "",
        "## Workers",
        "",
        "| solver | engine | session | profile | model | events |",
        "|---|---|---|---|---|---|",
    ]
    for sid, row in sorted(workers.items()):
        lines.append(
            f"| {sid} | {row.get('engine') or '-'} | {row.get('session') or '-'} | "
            f"{row.get('profile_label') or row.get('profile_id') or '-'} | "
            f"{row.get('model') or '-'} | {row.get('event_count', 0)} |"
        )
    lines.extend(["", "## Timeline", ""])
    total = 0
    for ev in events:
        et = str(ev.get("event_type") or "")
        if et in {"reasoning.delta", "text.delta", "tool.start", "tool.args",
                  "tool.result", "terminal.output", "blackboard.delta",
                  "run.started", "run.finished", "hitl.request",
                  "hitl.response", "worker.status", "worker.lifecycle"}:
            text = _event_text(ev).strip()
        else:
            continue
        if not text:
            continue
        text = text[:MAX_TRANSCRIPT_EVENT_CHARS]
        ts = float(ev.get("ts") or 0.0)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else "-"
        sid = str(ev.get("solver_id") or "coordinator")
        line = f"### {stamp} · {sid} · {et}\n\n{text}\n"
        total += len(line.encode("utf-8"))
        if total > MAX_TRANSCRIPT_BYTES:
            lines.append("\n_[transcript truncated; use events.jsonl for the full record]_\n")
            break
        lines.append(line)
    return "\n".join(lines)


class _ArchiveWriter:
    def __init__(self, zf: zipfile.ZipFile) -> None:
        self.zf = zf
        self.files: list[dict[str, Any]] = []
        self.added: set[str] = set()
        self.total = 0

    def add_bytes(self, arcname: str, data: bytes) -> None:
        name = _safe_arcname(arcname)
        if not name or name in self.added or len(data) > MAX_TOTAL_BYTES:
            return
        if self.total + len(data) > MAX_TOTAL_BYTES:
            return
        self.zf.writestr(name, data)
        self.added.add(name)
        self.total += len(data)
        self.files.append({
            "path": name, "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        })

    def add_file(
        self, arcname: str, src: Path, *, source: str = "",
        max_file_bytes: int = MAX_NATIVE_FILE_BYTES,
    ) -> None:
        name = _safe_arcname(arcname)
        try:
            if (not name or name in self.added or not src.is_file()
                    or src.is_symlink()):
                return
            if any(part in {"__pycache__", ".git", ".venv", "node_modules"}
                   for part in src.parts):
                return
            size = src.stat().st_size
            if size <= 0 or size > max_file_bytes or self.total + size > MAX_TOTAL_BYTES:
                return
            self.zf.write(src, name)
            self.added.add(name)
            self.total += size
            self.files.append({
                "path": name, "size": size, "sha256": _sha256(src),
                "source": source or str(src),
            })
        except OSError:
            return


def _archive_media(
    writer: _ArchiveWriter,
    root: Path,
    arc_root: str,
) -> None:
    for src in _iter_media(root):
        try:
            rel = src.relative_to(root)
        except ValueError:
            continue
        writer.add_file(f"{arc_root}/{rel.as_posix()}", src)


def build_conversation_archive(
    *,
    sessions_root: str | Path,
    run_id: str,
    workspace_root: str | Path,
    challenge_name: str,
    category: str,
    events: list[dict[str, Any]],
    flags: Optional[list[str]] = None,
    expected_flags: int = 1,
    winner: Optional[dict[str, Any]] = None,
) -> Path:
    """Build one ZIP and return its temporary path.

    The caller owns deletion (the web route attaches a BackgroundTask).
    """
    sessions = Path(sessions_root)
    workspace = Path(workspace_root).resolve()
    run_key = _safe_component(run_id, "run")
    archive_dir = workspace / ARCHIVE_DIR
    try:
        archive_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        archive_dir = Path(tempfile.mkdtemp(prefix="muteki-conversations-"))
    fd = tempfile.NamedTemporaryFile(
        dir=str(archive_dir), prefix=f".{run_key}-", suffix=".zip", delete=False)
    fd.close()
    archive_path = Path(fd.name)

    workers = _collect_workers(events)
    conversations = workspace / CONVERSATION_DIR
    if conversations.is_dir() and not conversations.is_symlink():
        try:
            for child in sorted(conversations.iterdir(), key=lambda p: p.name):
                if not child.is_dir() or child.is_symlink():
                    continue
                sid = str(child.name)
                row = workers.setdefault(sid, {
                    "solver_id": sid, "engine": "", "session": "",
                    "profile_id": "", "profile_label": "", "model": "",
                    "roles": [], "event_count": 0, "first_ts": 0.0,
                    "last_ts": 0.0, "native_files": [],
                })
                try:
                    meta = json.loads((child / "worker.json").read_text(encoding="utf-8"))
                except (OSError, ValueError, TypeError):
                    meta = {}
                row["engine"] = str(meta.get("engine") or row["engine"])
                row["session"] = str(meta.get("session") or row["session"])
                if isinstance(meta.get("identity"), dict):
                    row["profile_id"] = str(meta["identity"].get("profile_id") or row["profile_id"])
                    row["profile_label"] = str(meta["identity"].get("profile_label") or row["profile_label"])
                    row["model"] = str(meta["identity"].get("model") or row["model"])
                row["native_files"] = [
                    str(item.get("archive_path") or "")
                    for item in (meta.get("files") or [])
                    if isinstance(item, dict)
                    and item.get("kind") == "native-session"
                ]
        except OSError:
            pass

    workers_list = list(workers.values())
    now = time.time()
    manifest = {
        "version": 1,
        "kind": "muteki-conversation-archive",
        "run_id": run_id,
        "challenge": {"name": challenge_name, "category": category},
        "flags": list(flags or []),
        "expected_flags": int(expected_flags or 1),
        "generated_at": now,
        "workers": workers_list,
        "notes": [
            "muteki/events.jsonl is the complete lossless Muteki event stream.",
            "conversations/<worker>/ contains native session files captured at Worker exit.",
            "native/<worker>/ contains a best-effort fallback for older runs.",
            "media/ contains images/videos retained in the run workspace.",
        ],
    }
    readme = (
        "# Muteki conversation archive\n\n"
        "This archive contains the complete conversation record for one run.\n\n"
        "- `muteki/events.jsonl` — every Muteki event (all Workers, reasoning, "
        "tool calls, terminal output, facts, flags).\n"
        "- `muteki/transcript.md` — human-readable timeline.\n"
        "## Workers\n\n"
        + "\n".join(
            f"- `{row.get('solver_id')}` · engine={row.get('engine') or '-'} · "
            f"session={row.get('session') or '-'} · events={row.get('event_count', 0)}"
            for row in sorted(workers_list, key=lambda r: str(r.get("solver_id")))
        )
        + "\n\n"
        "- `conversations/<worker>/native/` — native CLI session JSONL captured "
        "while the Worker scratch directory still existed.\n"
        "- `native/<worker>/` — fallback native-session lookup for older runs.\n"
        "- `media/` — images and videos retained in the run workspace.\n"
        "- `muteki/writeup.md` — the generated post-solve report, if present.\n"
        "- `manifest.json` — machine-readable index with sizes and SHA-256.\n"
    )

    # Inline-heredoc solves have no source file; synthesize one from native
    # history before packaging so extras/exp is present even if the operator
    # never opened the EXP panel.
    if isinstance(winner, dict):
        try:
            from muteki.solver.exp_synthesis import ensure_synthesized_exp

            rel = str(winner.get("workdir_rel") or "").strip()
            candidate = (workspace / rel).resolve() if rel else None
            workdir = None
            if candidate is not None and _within(candidate, workspace):
                workdir = candidate
            ensure_synthesized_exp(
                workspace_root=workspace,
                solver_id=str(winner.get("worker_id") or ""),
                engine=str(winner.get("engine") or ""),
                session=str(winner.get("session") or ""),
                workdir=workdir,
                sessions_root=sessions,
            )
        except Exception:
            pass

    try:
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            writer = _ArchiveWriter(zf)
            event_file = sessions / f"{encode_run_id(run_id)}.jsonl"
            if event_file.is_file() and not event_file.is_symlink():
                writer.add_file(
                    "muteki/events.jsonl", event_file,
                    max_file_bytes=MAX_TOTAL_BYTES)
            if "muteki/events.jsonl" not in writer.added:
                raw = "".join(
                    json.dumps(ev, ensure_ascii=False) + "\n" for ev in events
                ).encode("utf-8")
                writer.add_bytes("muteki/events.jsonl", raw)
            writer.add_bytes("muteki/transcript.md",
                             _render_transcript(events, workers).encode("utf-8"))
            writeup = workspace / "writeup.md"
            if writeup.is_file() and not writeup.is_symlink():
                writer.add_file("muteki/writeup.md", writeup)
            workspace_manifest = workspace / "manifest.json"
            if workspace_manifest.is_file() and not workspace_manifest.is_symlink():
                writer.add_file("muteki/workspace-manifest.json", workspace_manifest)
            if winner:
                writer.add_bytes(
                    "muteki/winner-continuation.json",
                    json.dumps(winner, ensure_ascii=False, indent=2).encode("utf-8"))

            if conversations.is_dir() and not conversations.is_symlink():
                for src in sorted(conversations.rglob("*")):
                    if not src.is_file() or src.is_symlink():
                        continue
                    try:
                        rel = src.relative_to(conversations)
                    except ValueError:
                        continue
                    writer.add_file(f"conversations/{rel.as_posix()}", src)

            # Fallback native lookup for runs created before capture-at-exit.
            account_root = sessions / "_secrets" / "accounts"
            for row in workers_list:
                sid = str(row.get("solver_id") or "")
                if not sid or row.get("native_files"):
                    continue
                engine = str(row.get("engine") or "")
                session = str(row.get("session") or "")
                if not engine and not session:
                    continue
                for root, per_worker in _native_roots(
                        engine, None, None, account_root=account_root):
                    for src in _iter_native_files(
                            root, session, per_worker_root=per_worker):
                        arc = f"native/{_safe_component(sid)}/{_safe_component(root.name or 'native')}/{src.name}"
                        writer.add_file(arc, src)
                        if arc not in row["native_files"]:
                            row["native_files"].append(arc)

            for sub, arc in (
                ("uploads", "media/uploads"),
                ("arts", "media/arts"),
                ("workers", "media/workers"),
                ("homes", "media/homes"),
                ("final", "extras/final"),
            ):
                root = workspace / sub
                if root.is_dir() and not root.is_symlink():
                    _archive_media(writer, root, arc)
            # final/exp contains scripts, not media only; include them explicitly.
            exp_root = workspace / "final" / "exp"
            if exp_root.is_dir() and not exp_root.is_symlink():
                for src in sorted(exp_root.rglob("*")):
                    if src.is_file() and not src.is_symlink():
                        try:
                            rel = src.relative_to(exp_root)
                        except ValueError:
                            continue
                        writer.add_file(f"extras/exp/{rel.as_posix()}", src)

            writer.add_bytes(README_NAME, readme.encode("utf-8"))
            manifest["archive_files"] = writer.files
            writer.add_bytes(
                MANIFEST_NAME,
                json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    except BaseException:
        try:
            archive_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return archive_path
