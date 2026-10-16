"""Assignment 2, Parts F and G: build the results table from runs/ and the tracker.

    python -m eval.lab02            # print the tables; --write also fills reports/LAB_02.json

Success is Assignment 1's grounded check, eval/check_triage.check, read from the
comments that carry each run's run_id. Every other number comes from the traces.
Runs counted: this assignment's (run_start.lab "02"), on a real model, finished
(not ended by quota or rate limits); Part F's on the six evaluation issues, Part
G's the notes runs on 4203, 4204 and 4206.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict

import yaml

from eval.check_triage import check
from harness import LAB, ROOT
from harness.formats import UNFINISHED, normalize_path, parse_triage_comment
from harness.runner import is_free
from harness.trace import read_trace
from harness.tracker import Tracker

CONFIGS = ("agent", "planned_off", "planned_self", "planned_external")
NOTES_ISSUES = (4203, 4204, 4206)


def configuration(start: dict) -> str | None:
    if start.get("way") == "agent":
        return "agent"
    if start.get("way") == "planned":
        return f"planned_{start.get('critique') or 'off'}"
    return None


def load(eval_issues: set[int]) -> tuple[dict, list]:
    runs, notes = {c: [] for c in CONFIGS}, []
    for path in sorted((ROOT / "runs").glob("*.jsonl")):
        if path.name.startswith("_"):
            continue
        records = read_trace(path)
        start, end = records[0], records[-1]
        if start.get("type") != "run_start" or end.get("type") != "run_end":
            continue
        if start.get("lab") != LAB or is_free(start.get("backend") or ""):
            continue
        if any(r.get("type") == "end" and r.get("terminal_reason") in UNFINISHED for r in records):
            continue
        run = {"run_id": start["run_id"], "issue": start["issue"], "sweep": start["sweep"],
               "notes": start.get("notes") or "off", "records": records}
        if start.get("way") == "notes":
            if start["issue"] in NOTES_ISSUES:
                notes.append(run)
        elif configuration(start) in CONFIGS and start["issue"] in eval_issues:
            runs[configuration(start)].append(run)
    return runs, notes


def nearest_rank(values: list[float], pct: float):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(pct / 100 * len(ordered)) - 1)]


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def exact_repeat(records: list[dict]) -> bool:
    from agent.detectors import repeat_check
    return bool(repeat_check(records))


def right(body: str | None, answer: dict) -> bool:
    label, file = parse_triage_comment(body or "")
    return label == answer["label"] and file == normalize_path(answer["file"])


def calls_of(records):
    return [r for r in records if r.get("type") == "model_call"]


def share(records, purposes) -> float:
    calls = calls_of(records)
    total = sum(c["usage"]["prompt_tokens"] for c in calls)
    part = sum(c["usage"]["prompt_tokens"] for c in calls if c.get("purpose") in purposes)
    return part / total if total else 0.0


def plan_call_share(records) -> float:
    """The first plan call alone, over all the run's prompt tokens."""
    calls = calls_of(records)
    total = sum(c["usage"]["prompt_tokens"] for c in calls)
    first = next((c for c in calls if c.get("purpose") == "plan"), None)
    return first["usage"]["prompt_tokens"] / total if first and total else 0.0


def summarize(config: str, runs: list, ok: dict, issues: list, answers: dict, tracker: Tracker) -> dict:
    planned, second = config != "agent", config in ("planned_self", "planned_external")
    per_sweep = defaultdict(int)
    for r in runs:
        per_sweep[r["sweep"]] += ok[r["run_id"]]
    tokens = [sum(c["usage"]["prompt_tokens"] + c["usage"]["output_tokens"] for c in calls_of(r["records"]))
              for r in runs]
    latency = [r["records"][-1].get("elapsed_s") or 0.0 for r in runs]
    revisions = [sum(1 for x in r["records"] if x.get("type") == "revision") for r in runs]
    rounds, changed, fixed, broken, held = [], 0, 0, 0, 0
    for r in runs:
        lines = [x for x in r["records"] if x.get("type") == "critique"]
        rounds.append(len(lines))
        if any(x.get("changed") for x in lines):
            changed += 1
            before = right(lines[0].get("draft"), answers[r["issue"]])
            fixed += (not before) and ok[r["run_id"]]
            broken += before and not ok[r["run_id"]]
        held += bool(lines) and lines[-1].get("result") == "fail" and not tracker.comments(run_id=r["run_id"])
    return {
        "runs": len(runs),
        "passes": sum(ok[r["run_id"]] for r in runs),
        "per_issue": {str(i): sum(ok[r["run_id"]] for r in runs if r["issue"] == i) for i in issues},
        "sweep_min": min(per_sweep.values()) if per_sweep else None,
        "sweep_max": max(per_sweep.values()) if per_sweep else None,
        "mean_tokens": mean(tokens),
        "p50_latency_s": nearest_rank(latency, 50),
        "max_latency_s": max(latency, default=None),
        "plan_overhead_share": mean([share(r["records"], ("plan", "revise")) for r in runs]) if planned else None,
        "critique_overhead_share": mean([share(r["records"], ("critique",)) for r in runs]) if second else None,
        "runs_with_repeat": sum(exact_repeat(r["records"]) for r in runs),
        "off_plan": sum(1 for r in runs for x in r["records"] if x.get("type") == "off_plan") if planned else None,
        "revisions_mean": mean(revisions) if planned else None,
        "revisions_max": max(revisions, default=None) if planned else None,
        "rounds_mean": mean(rounds) if second else None,
        "rounds_max": max(rounds, default=None) if second else None,
        "drafts_changed": changed if second else None,
        "drafts_fixed": fixed if second else None,
        "drafts_broken": broken if second else None,
        "drafts_held": held if second else None,
    }


def plan_share_by_calls(runs: list) -> dict:
    """Plan overhead grouped by the run's number of model calls: {calls: (runs, mean share, mean plan-call share)}."""
    groups = defaultdict(list)
    for r in runs:
        groups[len(calls_of(r["records"]))].append(r)
    return {n: (len(rs), mean([share(r["records"], ("plan", "revise")) for r in rs]),
                mean([plan_call_share(r["records"]) for r in rs])) for n, rs in sorted(groups.items())}


def context_entry(run: dict) -> dict:
    seen, compacted, asked, questions = [], False, False, []
    for x in run["records"]:
        if x.get("type") == "tool_call":
            seen += [p for p in x.get("paths") or [] if p not in seen]
        elif x.get("type") == "compaction" and not asked:
            compacted = True
        elif x.get("type") == "question":
            asked = True
            questions.append({"path": x["path"], "truth": "yes" if x["path"] in seen else "no",
                              "answer": x["answer"]})
    return {"run_id": run["run_id"], "issue": run["issue"], "notes_shown": run["notes"] == "on",
            "compacted": compacted, "questions": questions,
            "right": sum(q["truth"] == q["answer"] for q in questions)}


def fmt(v, pct=False, digits=1):
    if v is None:
        return ""
    if pct:
        return f"{100 * v:.1f}%"
    if isinstance(v, float):
        return f"{v:,.{digits}f}"
    return f"{v:,}" if isinstance(v, int) else str(v)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--write", action="store_true", help="fill results and context in reports/LAB_02.json")
    a = p.parse_args()

    answers = {int(k): v for k, v in yaml.safe_load((ROOT / "fixtures/answers.yaml").read_text())["eval"].items()}
    issues = sorted(answers)
    tracker = Tracker(ROOT)
    runs, notes = load(set(issues))
    ok = {r["run_id"]: check(tracker, r["run_id"], r["issue"], answers[r["issue"]])
          for c in CONFIGS for r in runs[c]}
    results = {c: summarize(c, runs[c], ok, issues, answers, tracker) for c in CONFIGS}

    rows = [("Runs", "runs"), ("Passes (of 18)", "passes")]
    rows += [(f"  issue {i} (of 3)", ("per_issue", str(i))) for i in issues]
    rows += [("Sweep min / max", ("sweep",)), ("Tokens per run (mean)", "mean_tokens"),
             ("Latency p50 (s)", "p50_latency_s"), ("Latency max (s)", "max_latency_s"),
             ("Plan overhead share (plan + revise)", ("pct", "plan_overhead_share")),
             ("Second-pass overhead share", ("pct", "critique_overhead_share")),
             ("Runs with an exact repeat", "runs_with_repeat"), ("Off-plan attempts", "off_plan"),
             ("Revisions mean / max", ("pair", "revisions_mean", "revisions_max")),
             ("Second-pass rounds mean / max", ("pair", "rounds_mean", "rounds_max")),
             ("Drafts changed", "drafts_changed"), ("  failing to passing", "drafts_fixed"),
             ("  passing to failing", "drafts_broken"), ("Drafts held at the bound", "drafts_held")]
    print("| Measure | " + " | ".join(CONFIGS) + " |")
    print("|---|" + "---|" * len(CONFIGS))
    for label, key in rows:
        cells = []
        for c in CONFIGS:
            r = results[c]
            if isinstance(key, str):
                cells.append(fmt(r[key]))
            elif key[0] == "per_issue":
                cells.append(fmt(r["per_issue"][key[1]]))
            elif key[0] == "sweep":
                cells.append(f"{r['sweep_min']} / {r['sweep_max']}" if r["sweep_min"] is not None else "")
            elif key[0] == "pct":
                cells.append(fmt(r[key[1]], pct=True))
            else:
                cells.append(f"{fmt(r[key[1]], digits=2)} / {fmt(r[key[2]])}" if r[key[1]] is not None else "")
        print(f"| {label} | " + " | ".join(cells) + " |")

    print("\nPlan overhead by the run's number of model calls (runs, plan+revise share, plan call alone):")
    for c in CONFIGS[1:]:
        groups = plan_share_by_calls(runs[c])
        print(f"  {c}: " + "; ".join(f"{n} calls: {k} runs, {100 * s:.0f}%, {100 * q:.0f}%"
                                     for n, (k, s, q) in groups.items()))

    print("\nEscalations and terminal reasons:")
    for c in CONFIGS:
        reasons = defaultdict(int)
        for r in runs[c]:
            end = next((x for x in r["records"] if x.get("type") == "end"), {})
            reasons[end.get("terminal_reason")] += 1
        print(f"  {c}: {dict(reasons)}")

    context = [context_entry(r) for r in sorted(notes, key=lambda r: (r["notes"], r["issue"], r["sweep"]))]
    if context:
        print("\n| Run | Issue | File shown | Compacted | Right of 6 |\n|---|---|---|---|---|")
        for e in context:
            print(f"| {e['run_id']} | {e['issue']} | {'yes' if e['notes_shown'] else 'no'} | "
                  f"{'yes' if e['compacted'] else 'no'} | {e['right']} |")

    if a.write:
        path = ROOT / "reports" / "LAB_02.json"
        report = json.loads(path.read_text())
        report["results"] = results
        report["context"] = context
        path.write_text(json.dumps(report, indent=2) + "\n")
        print(f"\nwrote results and context to {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
