import tempfile
from pathlib import Path

import specify_cli
print("src:", Path(specify_cli.__file__).parent)
from specify_cli.workflows import STEP_REGISTRY
from specify_cli.workflows.base import StepBase, StepResult, StepStatus
from specify_cli.workflows.engine import WorkflowDefinition, WorkflowEngine, validate_workflow


def definition(name, steps, **fields):
    return WorkflowDefinition({"workflow": {"id": name, "name": name}, "steps": steps, **fields})


class Pause(StepBase):
    type_key = "pause-item"

    def execute(self, config, context):
        return StepResult(StepStatus.PAUSED)


STEP_REGISTRY["pause-item"] = Pause()

# 1) fan-out item pause -> current_step_id
with tempfile.TemporaryDirectory() as d:
    state = WorkflowEngine(Path(d)).execute(definition("p", [
        {"id": "fan", "type": "fan-out", "items": [1], "step": {"id": "item", "type": "pause-item"}},
    ]))
    print("1) status", state.status, "current_step_id", repr(state.current_step_id),
          "keys", sorted(state.step_results))

# 2) gate message typing
with tempfile.TemporaryDirectory() as d:
    wf = definition("g", [{"id": "gate", "type": "gate", "message": "{{ inputs.notice }}"}],
                    inputs={"notice": {"type": "number", "default": 42}})
    print("2) validate errors", validate_workflow(wf))
    state = WorkflowEngine(Path(d)).execute(wf, {"notice": 42})
    msg = state.step_results["gate"]["output"]["message"]
    print("2) status", state.status, "message", repr(msg), type(msg).__name__)

# Finding 3 (fan-out exception dispatch): see repro_fanout_exception.py
