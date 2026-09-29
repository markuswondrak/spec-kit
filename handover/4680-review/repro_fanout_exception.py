import tempfile, threading, time
from pathlib import Path
from specify_cli.workflows import STEP_REGISTRY
from specify_cli.workflows.base import StepBase, StepResult
from specify_cli.workflows.engine import WorkflowDefinition, WorkflowEngine
started=[]; lock=threading.Lock()
r0=threading.Event(); r1=threading.Event(); raised=threading.Event()
class Blow(StepBase):
    type_key="blow"
    def execute(self, config, context):
        with lock: started.append(context.item)
        if context.item == 0:
            raised.wait(5); r0.wait(5)
        elif context.item == 1:
            raised.wait(5); r1.wait(5)
        elif context.item == 2:
            raised.set(); r0.set()
            threading.Timer(0.5, r1.set).start()
            raise RuntimeError("boom")
        return StepResult(output={"v": context.item})
STEP_REGISTRY["blow"]=Blow()
with tempfile.TemporaryDirectory() as d:
    try:
        WorkflowEngine(Path(d)).execute(WorkflowDefinition({"workflow":{"id":"x","name":"x"},"steps":[{"id":"fan","type":"fan-out","items":[0,1,2,3,4,5],"max_concurrency":3,"step":{"id":"t","type":"blow"}}]}))
        print("no exception")
    except RuntimeError as e: print("raised", e)
print("started", sorted(started))
