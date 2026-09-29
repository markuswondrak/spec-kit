# Hand-over: #4680 composition PR — review round at head `97874f3c`

This branch (`chore/4680-review-handover`) is **transport only**. It was cut
from `feat/4680-composition-streamlined` at `97874f3c` and adds nothing but
this `handover/` folder. **Do not merge it and do not commit `handover/` to the
PR branch.** Apply the work to `feat/4680-composition-streamlined` instead.

## What the next agent must do

1. Check out the PR branch (not this one):

   ```bash
   git fetch origin
   git checkout feat/4680-composition-streamlined
   git pull --ff-only          # must be at 97874f3c or later
   uv sync --extra test
   ```

2. Apply the prototype fix, then review it (it is a starting point, not final):

   ```bash
   git show origin/chore/4680-review-handover:handover/4680-review/prototype-fix.patch > /tmp/fix.patch
   git apply /tmp/fix.patch
   ```

3. Add the three regression tests (see "Tests to add"). Each must **fail on
   `97874f3c`** and pass with the fix — record both runs as before/after
   evidence (required by CONTRIBUTING "Testing deterministic behavior").
4. Run the suites on Linux (see "Validation"). Windows was not usable, see
   "Environment caveats".
5. Commit to `feat/4680-composition-streamlined` with the required trailers
   (see "Commit / reply requirements"), push, and post **one** top-level
   review-round summary reply.

## Review findings (all three reproduced)

### 1. Public `current_step_id` of a paused fan-out item is unqualified

Reviewer (@mnriem): when a root fan-out item pauses, `main` reports
`current_step_id: "fan:item:0"`; the PR reports `"item"` although the persisted
result is keyed `fan:item:0`. Keep the public ID qualified; the tree may track
the local leaf ID separately.

Reproduced:

| | `current_step_id` | `step_results` keys |
|---|---|---|
| `origin/main` | `'fan:item:0'` | `fan`, `fan:item:0` |
| PR `97874f3c` | `'item'` | `fan`, `fan:item:0` |

Root cause, two places:

- `src/specify_cli/workflows/_execution.py`, `Execution.step()` (phase
  `ready`/`blocked` branch, ~line 454) sets `self.state.current_step_id = name`
  (local name) instead of `qualified`.
- `src/specify_cli/workflows/engine.py`, `_execute_tree` (~line 1252): the
  `finally:` block (added in `97874f3c` by Copilot Autofix) overwrites it with
  `active_step(state.execution)[0][-1]`, which is again the local leaf name.

Prototype fix (in the patch):

- In `step()`: `if len(ancestry) == 1: current_step_id = qualified`.
  **Why not `if public`:** `fan_out()` runs items with `public=False`, the same
  flag used for private workflow-call scopes, so `public` cannot tell them
  apart. Workflow calls extend `ancestry` (`(*ancestry, target)`, ~line 734);
  fan-out items do not. So `len(ancestry) == 1` means "root workflow scope".
- Remove the `finally` override in `engine.py` (and the now-unused
  `active_step` import there).

**Open decision to confirm with the user/reviewer:** with this rule, a pause
*inside a nested workflow call* reports `current_step_id == "call"` (the public
call step) instead of the inner `"review"`. `call` is the key present in public
`step_results`; the gate payload (`_gate_outcome` in `_commands.py`) still
resolves the inner gate via `active_step()` and reports `scope_path`. Check
`tests/specify_cli/workflows/test_command_status.py::test_composed_gate_status_and_resume`
still passes. Also consider nested fan-out (`outer:outer-item:0:inner:0` is
intentionally private — see `test_replay_keeps_private_nested_fan_out_aliases_private`);
decide whether a pause there should report an ID that is not in public
`step_results`.

Note: `_gate_outcome` also uses `path[-1]` as the gate `step_id`, so a gate
inside a fan-out item reports `step_id: "item"` in the gate payload. Main
reports the qualified ID there too. Decide whether to align it (it would need
to derive the qualified ID, not the local leaf); mention it in the reply either
way.

### 2. Gate `message` loses its type

Reviewer: for a validated gate with `message: "{{ inputs.notice }}"` and numeric
`notice: 42`, main persists `output.message == 42`; the PR persists `"42"`.
Keep the typed result; convert to text only when presenting.

Reproduced: main `42 (int)`, PR `'42' (str)`.

Root cause: the PR added `if message is not None: message = str(message)` in
`src/specify_cli/workflows/step/gate/__init__.py` `execute()` (~line 43).

Fix (in the patch): delete those 4 lines. Presentation already stringifies:
`GateStep._compose_prompt()` does `str(message)`, and `_gate_outcome()` in
`_commands.py` does `None if message is None else str(message)`. Verify the
run is still JSON-serialisable (ints are fine) and update any PR test that
asserted the string form.

### 3. Fan-out worker exception does not stop the sliding window

Reviewer (inline on `_execution.py`): a worker exception does not set
`halted`, and futures are consumed in item order. If a higher-index worker
raises while earlier items are still running, each earlier completion submits
another item before the raising future is reached. Wrap the worker so it sets
`halted` before re-raising. Reviewer's suggested change (accept as-is):

```python
def run_item_guarded(index):
    try:
        return run_item(index)
    except BaseException:
        halted.set()
        raise

with ThreadPoolExecutor(max_workers=workers) as pool:
    futures = {i: pool.submit(run_item_guarded, i) for i in range(workers)}
    ...
            futures[following] = pool.submit(run_item_guarded, following)
```

Reproduced with `repro_fanout_exception.py` (items 0..5, `max_concurrency: 3`,
item 2 raises while items 0 and 1 are still running):

| | started items |
|---|---|
| PR `97874f3c` | `[0, 1, 2, 3]` ← item 3 dispatched after item 2 already raised |
| with suggestion | `[0, 1, 2]` |

`origin/main` shows the same `[0, 1, 2, 3]`, so this was already present on main,
not introduced by the PR. It still gets fixed here because the PR owns this code now.

**Pitfall for the test:** with only 2 workers the bug is hidden. The next
item is queued, but the main thread immediately reaches the raising future
and `cancel()`s it before it starts. The test needs **at least two earlier
items still in flight** when the higher index raises (3 workers, raise at
index 2). Use `threading.Event`s, not sleeps, to make ordering deterministic
(the repro uses a 0.5 s `Timer` only to release item 1; replace it in the test).

## Tests to add (in `tests/workflows/test_composition_execution.py`)

Use the existing helpers there (`definition()`, `STEP_REGISTRY` via
`monkeypatch.setitem`). Gates pause in tests because stdin is not a TTY.

1. `test_paused_fan_out_item_reports_qualified_current_step_id`
   – fan-out `items: [1]`, template step returns `StepStatus.PAUSED`.
   Assert `state.current_step_id == "fan:item:0"`,
   `"fan:item:0" in state.step_results`, and the same after
   `RunState.load(...)` (persisted value). Optionally also assert the
   `step_started` event / `on_step_start` callback used the qualified ID.
2. `test_gate_message_keeps_typed_template_result`
   – `inputs: {notice: {type: number}}`, gate `message: "{{ inputs.notice }}"`,
   run `validate_workflow` (expect `[]`), execute with `{"notice": 42}`.
   Assert `state.step_results["gate"]["output"]["message"] == 42` and
   `isinstance(..., int)`; also assert the CLI/JSON gate payload still shows
   `"42"` (presentation-only coercion), e.g. via `_gate_outcome`.
3. `test_fan_out_worker_exception_stops_dispatch_before_earlier_items_finish`
   – as described in finding 3; assert the exception propagates and that
   item 3 never started (`started == {0, 1, 2}`).

Also make sure an existing test covers the nested-workflow-call pause and
asserts the chosen `current_step_id` behaviour (see open decision in 1).

## Validation

Run on Linux/macOS inside the repo venv (see AGENTS.md pitfall):

```bash
.venv/bin/python -m pytest tests/workflows tests/specify_cli/workflows tests/test_workflows.py -q
```

Record each exact command + result for the PR reply. Also run the repro
scripts on the fixed branch; all three cases must match main (case 3 must not
start item 3):

```bash
PYTHONPATH=src .venv/bin/python handover/4680-review/repro_cases.py </dev/null
PYTHONPATH=src .venv/bin/python handover/4680-review/repro_fanout_exception.py </dev/null
```

(`</dev/null` is needed so the gate pauses instead of prompting.) To compare
against main: `git worktree add /tmp/sk-main origin/main` and run with
`PYTHONPATH=/tmp/sk-main/src`.

## Environment caveats (why this was not finished on Windows)

- On the Windows machine, even the **unmodified** `97874f3c` had random
  failures in 7–10 composition/fan-out tests. Mostly
  `PermissionError: [WinError 5]` on the atomic `os.replace` of
  `.specify/workflows/runs/<id>/state.json` during concurrent checkpoints,
  then `CheckpointError: A previous checkpoint failed`. The set changes from
  run to run. On Linux, check whether any of these still fail. If they do,
  that's a real concurrency issue in checkpointing. It's outside this review
  round, but tell the user.
- Symlink tests fail on Windows without symlink privilege (expected, unrelated).
- No WSL/Docker was available, so no Linux run was possible there.

## Commit / reply requirements (CONTRIBUTING "Agent-authored Git and review activity")

- Every agent-authored commit needs an `Assisted-by:` trailer naming the
  real agent, model, and `autonomous`/`supervised`. Keep any
  `Co-authored-by:` trailers.
- Post one top-level summary reply for this review round. Include the fix
  commit SHA, the three before/after test results, and the validation commands
  with their results. Include an AI disclosure (on behalf of @markuswondrak,
  agent, model, mode, extent of AI involvement). Reply inline only where
  needed, e.g. the nested-call `current_step_id` decision or the pre-existing
  nature of finding 3. Do not resolve the reviewer's conversations.

## Files in this folder

- `prototype-fix.patch`: fixes for all 3 findings against `97874f3c`
  (`git apply --check` passes).
- `repro_cases.py`: reproduces findings 1 and 2 (prints `current_step_id` and
  the gate message type).
- `repro_fanout_exception.py`: reproduces finding 3 (prints started items).

## Provenance

Analysis and prototype were done with GitHub Copilot CLI (model: Claude Opus
5.5, interactive/supervised session) on behalf of @markuswondrak.
