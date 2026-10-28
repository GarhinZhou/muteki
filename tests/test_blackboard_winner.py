"""Blackboard-flag winner attribution.

Regression for the deepsleep run: a Worker submits a valid flag through
`muteki-blackboard submit-flag`, the Coordinator cancels it in the same tick, and
the task reaps as CancelledError instead of returning a SolveOutcome. Finalize
used to fall back to winner="coordinator", which has no workdir, so
`preserve_winner_deliverables` never ran and the Results page showed no EXP.
"""

import json
from pathlib import Path
from typing import Any

from muteki.models.solve_graph import Challenge, SolveGraph
from muteki.solver.types import SolveOutcome
from muteki.swarm.coordinator_flags import _FlagsBusMixin
from muteki.swarm.coordinator_loop import _CoordinatorLoopMixin

FLAG = "flag{deepsleep}"


class _FakeGraph:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events

    def events_since(self, after_seq: int, kinds: list[str] | None = None):
        return list(self._events)


class _Harness(_FlagsBusMixin):
    def __init__(self, events: list[dict[str, Any]], meta: dict[str, dict]) -> None:
        self.shared_graph = _FakeGraph(events)
        self.challenge = Challenge(id="c1", name="deepsleep", category="pwn")
        self._found_flags = [FLAG]
        self._worker_artifact_meta = meta


def test_promote_uses_flag_source_worker_and_keeps_workdir():
    harness = _Harness(
        [
            {"seq": 1, "actor": "coordinator", "payload": {"flag": FLAG}},
            {"seq": 2, "actor": "cli-kimi-13", "payload": {"flag": FLAG}},
        ],
        {
            "cli-kimi-13": {
                "workdir": "/run/workspace/workers/cli-seat-13",
                "engine": "kimi",
                "profile": {"id": "seat_kimi_589c90ce"},
            }
        },
    )
    per_solver: dict[str, SolveOutcome] = {}

    winner, flag = harness._promote_blackboard_winner(per_solver, FLAG)

    assert winner == "cli-kimi-13"
    assert flag == FLAG
    outcome = per_solver["cli-kimi-13"]
    assert outcome.solved is True
    assert outcome.flag == FLAG
    assert outcome.workdir == "/run/workspace/workers/cli-seat-13"
    assert outcome.engine == "kimi"
    assert outcome.runtime_profile == {"id": "seat_kimi_589c90ce"}


def test_promote_keeps_clean_outcome_when_worker_already_reaped():
    harness = _Harness(
        [{"seq": 1, "actor": "cli-kimi-13", "payload": {"flag": FLAG}}],
        {},
    )
    clean = SolveOutcome(
        solved=True, flag=FLAG, steps=3,
        graph=SolveGraph(challenge=harness.challenge),
        workdir="/run/workspace/workers/clean",
    )
    per_solver = {"cli-kimi-13": clean}

    winner, _flag = harness._promote_blackboard_winner(per_solver, FLAG)

    assert winner == "cli-kimi-13"
    assert per_solver["cli-kimi-13"] is clean


def test_promote_falls_back_when_source_worker_is_unknown():
    harness = _Harness([], {})

    winner, flag = harness._promote_blackboard_winner({}, FLAG)

    assert winner == "coordinator"
    assert flag == FLAG


def test_persist_winner_copies_exp_for_cancelled_blackboard_winner(tmp_path):
    challenge = Challenge(id="run-deepsleep", name="deepsleep", category="pwn")
    workspace = tmp_path / "workspace"
    workdir = workspace / "workers" / "cli-seat-13"
    (workdir / "scratch").mkdir(parents=True)
    (workdir / "scratch" / "exp.py").write_text(
        "from pwn import *\n# deepsleep exploit\n", encoding="utf-8")
    (workspace / "graph").mkdir(parents=True)

    captured: dict[str, Any] = {}

    class _PersistHarness(_CoordinatorLoopMixin):
        _graph_dir = workspace / "graph"
        _winner_continuation_writer = staticmethod(
            lambda payload: captured.update(payload))
        _found_flags = [FLAG]

        def __init__(self) -> None:
            self.challenge = challenge

        def _runtime_metadata_for(self, outcome=None):
            return {}

    outcome = SolveOutcome(
        solved=True,
        flag=FLAG,
        steps=0,
        graph=SolveGraph(challenge=challenge),
        flags=[FLAG],
        engine="kimi",
        workdir=str(workdir),
        runtime_profile={"id": "seat_kimi_589c90ce"},
    )

    _CoordinatorLoopMixin._persist_winner(
        _PersistHarness(), outcome, FLAG, worker_id="cli-kimi-13")

    manifest = json.loads(
        (workspace / "final" / "exp" / "manifest.json").read_text())
    assert [row["name"] for row in manifest["files"]] == ["exp.py"]
    assert captured["worker_id"] == "cli-kimi-13"
    assert captured["challenge"]["name"] == "deepsleep"
    assert captured["workdir"] == str(workdir)
