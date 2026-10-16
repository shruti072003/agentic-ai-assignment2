# ARCHITECTURE.md · RepoMind on the seven layers

For each layer: what does this job, and where is it enforced. Cite code as a file
path and line number joined by a colon, inside backticks; a checker opens every
reference (`checks/FORMATS.md`, section 8). A layer held only by a sentence in a
prompt (any file under `prompts/`) is **EMPTY**, and so is a layer nothing does
yet. Status is one of: present, partial (say what is missing), or **EMPTY**. Each
assignment brings this file up to date.

What each layer is for (Lecture 1, slides 40 to 42):

- **Model**: reasoning, choosing tools, generating text.
- **Harness**: runs the loop, builds each prompt, trims old history.
- **Tools**: where the system acts on the world.
- **Memory & State**: what survives a step, a session, a restart.
- **Control & Policy**: what the system may do, and when it must ask.
- **Observability**: a step-by-step record of what it did.
- **Evaluation**: measuring quality, before and after release.

The table below is RepoMind as Assignment 2 leaves it.

| Layer | What does this job | Where it is enforced | Status |
|---|---|---|---|
| Model | The configured model, called only through the metered client | `harness/client.py:116` | present |
| Harness | The loop, with three bounds checked in code before every call; for the planned way, the validator's four checks before anything runs (`agent/validator.py:38`), the reject exit that re-plans and then escalates with no tool call (`ways/planned.py:215`, `ways/planned.py:231`), the plan logged before the first tool call (`ways/planned.py:195`), the boundary that refuses an off-plan call (`ways/planned.py:285`, `ways/planned.py:286`), the one revision checkpoint (`ways/planned.py:398`) and the bounded second pass (`ways/planned.py:429`) | `agent/loop.py:38`, `agent/loop.py:41`, `agent/loop.py:44`, `agent/validator.py:38`, `ways/planned.py:215`, `ways/planned.py:285` | present |
| Tools | Four tools behind one registry (read_file arrived with Assignment 2's starter); post_comment stamps the run_id | `tools/__init__.py:52`, `harness/tracker.py:60` | present |
| Memory & State | Within a run: the plan and its step statuses, and in the notes way a progress file outside the conversation and a compacted history; nothing survives a run | `ways/notes.py:76`, `ways/notes.py:113` | partial: per run only, and only in the notes way |
| Control & Policy | Nothing decides which writes need a person; the prompt asks politely | none | **EMPTY** |
| Observability | One trace line per model call and per tool call | `agent/loop.py:50` | partial: traces, no alerts |
| Evaluation | The grounded check reads the tracker, never the agent | `eval/check_triage.py:7` | partial: offline only |

**Empty layers: 1 of 7** (Control & Policy), and three are partial. Assignment 2
gave the Harness a plan it validates and cannot leave, and gave Memory & State a
per-run progress file.

Earlier, as Assignment 1 left it: **empty layers: 2 of 7**, and two more were partial. Lecture 1, slide 46 expects
four of the seven to be missing from a first build; Assignment 1 filled
Observability and Evaluation just enough to measure.
