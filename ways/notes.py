"""Way 6, notes: the planned way with a progress file and a compaction. This file is yours.

Assignment 2, Part G. Your planned way with the second pass off, plus two things,
so that Part F's configurations stay as they were (checks/FORMATS.md, section 10):

    python run.py --way notes --notes off --issues 4203,4204,4206 --runs 2
    python run.py --way notes --notes on --issues 4203,4204,4206 --runs 2

  1. A progress file, ctx.progress_path (runs/<run_id>.progress.json): JSON outside
     the conversation, written after every step, holding at least
       "steps_done"  the ids of the steps that have run, in order
       "paths"       every path a search returned or a read read, in order
     and never a search term: terms come from the issue's text. Say in the file what
     it records (an "about" line, or labelled keys): a bare list of paths can be read
     as a to-do list. With ctx.notes == "on", every executor call and the questions'
     call after its first write show the model the file's text, exactly as written
     (not the planner, not the reviser). With "off", it is still written, and never
     shown.
  2. A history and a compaction: every model call sees the run's history so far,
     its step results oldest first. Once that history passes COMPACT_AT tokens, the
     oldest entries become a model-written summary and the recent ones stay as
     they were. Write ctx.trace.event("compaction", threshold=COMPACT_AT, tokens=...)
     each time. It runs whatever ctx.notes says. A low threshold is a test setting
     here, not advice.

Then, at the end of the run, however it ended (comment posted, escalated, halted),
one model call asks six closed questions, each "Has this run seen `<path>`: did a
search return it, or did you read it?", and returns six answers, yes or no: about
the three earliest paths the run saw (its tool_call lines returned them; all of
them, if it saw fewer), and paths it did not see for the rest. If the run has not
compacted yet, compact its history once first. Write each answer as
ctx.trace.event("question", path=..., answer=...), the six together.

Part G's 12 runs are the ones on issues 4203, 4204 and 4206; a notes run on a
development issue is development, and reports/LAB_02.json's context leaves it out.
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

from tools.search_repo import EXCLUDED_DIRS
from ways.planned import Executor

COMPACT_AT = 1000   # Part G: the context size, in tokens, past which you compact; low enough that every run does
KEEP_RECENT = 1     # history entries a compaction leaves as they were
QUESTIONS = 6
ASK_SEEN = 3        # the earliest paths the run saw, asked about first

ABOUT = ("RepoMind's progress file for this run, written by its code after every step. steps_done: the "
         "plan steps that have run, in order. paths_seen: every file path a search returned or a read read "
         "in this run, in the order first seen. It is a record of what this run has already seen, not a "
         "list of things to do.")


def tokens(text: str) -> int:
    return len(text) // 4


class NotesExecutor(Executor):
    """The planned way with the second pass off, a progress file, and a compacting history."""

    def __init__(self, ctx):
        super().__init__(ctx)
        self.entries: list[str] = []       # the history: step results, oldest first
        self.summary: str | None = None    # what compaction made of the older entries
        self.compactions = 0
        self.steps_done: list = []
        self.paths_seen: list[str] = []
        self.asked = False

    # -- the progress file ---------------------------------------------------------------
    def write_progress(self) -> None:
        state = {"about": ABOUT, "steps_done": self.steps_done, "paths_seen": self.paths_seen,
                 "paths": self.paths_seen}
        self.ctx.progress_path.parent.mkdir(parents=True, exist_ok=True)
        self.ctx.progress_path.write_text(json.dumps(state, indent=1))

    def call(self, step: dict, tool_input: dict) -> dict:
        record = super().call(step, tool_input)
        self.paths_seen += [p for p in record["paths"] if p not in self.paths_seen]
        self.write_progress()            # up to date before the next tool call, even a retry
        args = json.dumps(record["args"])
        self.entries.append(f"Step {record['id']} ({record['tool']} {args}) returned:\n{record['content']}")
        return record

    def after_step(self, step: dict, record: dict) -> None:
        if step["id"] not in self.steps_done:
            self.steps_done.append(step["id"])
        self.write_progress()

    def notes_text(self) -> str:
        if self.ctx.notes != "on" or not self.ctx.progress_path.exists():
            return ""
        return f"Your progress file, {self.ctx.progress_path.name}:\n{self.ctx.progress_path.read_text()}"

    # -- the history, and its compaction ------------------------------------------------------
    def history(self, step: dict) -> list[str]:
        if self.size() > COMPACT_AT:
            self.compact()
        return self.context_entries()

    def context_entries(self) -> list[str]:
        head = [f"Summary of this run's earlier steps:\n{self.summary}"] if self.summary else []
        return head + self.entries

    def size(self) -> int:
        return tokens("\n\n".join(self.context_entries()))

    def compact(self) -> None:
        """The oldest entries become one model-written summary; the most recent stay."""
        before = self.size()
        keep = self.entries[-KEEP_RECENT:] if len(self.entries) > KEEP_RECENT else []
        old = ([f"Earlier summary:\n{self.summary}"] if self.summary else []) + self.entries[:len(self.entries) - len(keep)]
        self.n += 1
        response = self.ctx.client.create(
            system=self.ctx.prompt("compact.md"),
            messages=[{"role": "user", "content": "Summarize these step results:\n\n" + ("\n\n".join(old) or "(none yet)")}])
        self.trace.model_call(self.n, response, purpose=None)
        self.summary = response.text.strip() or "(empty summary)"
        self.entries = keep
        self.compactions += 1
        self.trace.event("compaction", threshold=COMPACT_AT, tokens=before, kept=len(keep), summarized=len(old))

    def extra_context(self) -> str:
        return self.notes_text()

    # -- the six questions ---------------------------------------------------------------------
    def before_draft(self) -> None:
        self.ask()

    def before_end(self, reason: str) -> None:
        self.ask()

    def mode(self) -> str:
        return "off"                      # Part G holds the second pass off

    def ask(self) -> None:
        if self.asked:
            return
        self.asked = True
        if not self.compactions:
            self.compact()
        seen = self.trace_paths()
        asked_seen = seen[:ASK_SEEN]
        unseen = [p for p in self.repo_files() if p not in seen]
        rng = random.Random(self.ctx.run_id)
        paths = asked_seen + rng.sample(unseen, QUESTIONS - len(asked_seen))
        rng.shuffle(paths)
        lines = "\n".join(f"{i}. Has this run seen {p}: did a search return it, or did you read it?"
                          for i, p in enumerate(paths, 1))
        parts = self.context_entries()
        notes = self.notes_text()
        if notes:
            parts.append(notes)
        parts.append(f"Answer each of these six questions about this run, yes or no.\n{lines}\n\n"
                     "Reply with six lines, one per question, each its number and yes or no, such as \"1. no\".")
        self.n += 1
        response = self.ctx.client.create(system=self.ctx.prompt("questions.md"),
                                          messages=[{"role": "user", "content": "\n\n".join(parts)}])
        self.trace.model_call(self.n, response, purpose=None)
        answers = parse_answers(response.text, len(paths))
        for path, answer in zip(paths, answers):
            self.trace.event("question", path=path, answer=answer)

    def trace_paths(self) -> list[str]:
        from harness.trace import read_trace
        seen = []
        for line in read_trace(self.trace.path):
            if line.get("type") == "tool_call":
                seen += [p for p in line.get("paths") or [] if p not in seen]
        return seen

    def repo_files(self) -> list[str]:
        root = Path(self.ctx.root) / "fixtures" / "repo"
        return sorted(p.relative_to(root).as_posix() for p in root.rglob("*")
                      if p.is_file() and not set(p.relative_to(root).parts) & EXCLUDED_DIRS)


def parse_answers(text: str, n: int) -> list[str]:
    """yes or no per numbered line; a lone yes or no answers all; anything unreadable is no."""
    found = {int(m.group(1)): m.group(2).lower()
             for m in re.finditer(r"(?m)^\s*(\d+)\s*[.):-]?\s*\**\s*(yes|no)\b", text, re.IGNORECASE)}
    if not found:
        lone = re.fullmatch(r"\s*(yes|no)\W*", text or "", re.IGNORECASE)
        return [lone.group(1).lower() if lone else "no"] * n
    return [found.get(i, "no") for i in range(1, n + 1)]


def run(ctx) -> None:
    NotesExecutor(ctx).run()
