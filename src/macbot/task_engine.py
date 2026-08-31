"""Durable, bounded Task-mode execution owned by MacBot."""

from __future__ import annotations

import json
import queue
import threading
import time
from functools import partial
from typing import Any

from .capabilities import (
    CapabilityAuthority,
    CapabilityBroker,
    CapabilityDefinition,
)
from .events import EventJournal
from .history import HistoryStore
from .intent import IntentRouter
from .llm import LocalLLM
from .tasks import StepState, TaskPlan, TaskState
from .tools import SCHEMAS, Tools
from .validation import validate_chat_message


class TaskEngine:
    MAX_STEPS = 12
    MAX_REPLANS = 2
    DEADLINE_SECONDS = 600

    def __init__(
        self,
        history: HistoryStore,
        events: EventJournal,
        tools: Tools,
        planner: IntentRouter,
        llm: LocalLLM,
    ):
        self.history = history
        self.events = events
        self.tools = tools
        self.planner = planner
        self.llm = llm
        self.queue: queue.Queue[str | None] = queue.Queue(maxsize=1)
        self.stopping = threading.Event()
        self.cancelled: dict[str, threading.Event] = {}
        self.paused: dict[str, threading.Event] = {}
        self.lock = threading.RLock()
        definitions = [
            CapabilityDefinition(
                name,
                description,
                {key: "string" for key in arguments},
                partial(self.tools._execute, name),
            )
            for name, (description, arguments) in SCHEMAS.items()
            if name in self.tools.settings.tools.enabled
        ]
        self.broker = CapabilityBroker(history, definitions)
        self.recovery = history.recover_inflight_steps()
        self.worker = threading.Thread(target=self._loop, name="task-worker", daemon=True)
        self.worker.start()

    def create(self, text: str, session_id: str) -> dict[str, Any]:
        text = validate_chat_message(text)
        cancel = threading.Event()
        intent = self.planner.route(text, cancel)
        if intent.mode != "act" or not intent.actions:
            explanation = intent.clarification or "This request does not contain a bounded Task."
            raise ValueError(explanation)
        if len(intent.actions) > self.MAX_STEPS:
            raise ValueError(f"Task exceeds the {self.MAX_STEPS}-step execution budget")
        task = TaskPlan.from_intent(session_id, "task-" + str(time.time_ns()), text, intent)
        payload = task.as_dict()
        payload.update(
            {
                "title": text[:160],
                "step_budget": self.MAX_STEPS,
                "replan_budget": self.MAX_REPLANS,
                "deadline_ns": time.time_ns() + self.DEADLINE_SECONDS * 1_000_000_000,
            }
        )
        steps = task.durable_steps()
        payload["capability_manifest"] = self._authority_manifest(steps)
        self.history.create_task(payload, [step.as_dict() for step in steps])
        proposal = self.history.transition_task(task.task_id, TaskState.AWAITING_AUTHORIZATION)
        self._publish_task(
            proposal,
            "proposed",
            authority=self._authority_manifest(steps),
        )
        return {**proposal, "steps": [step.as_dict() for step in steps]}

    def authorize(self, task_id: str, session_id: str, approve: bool) -> dict[str, Any]:
        task = self._owned(task_id, session_id)
        if task["state"] != TaskState.AWAITING_AUTHORIZATION.value:
            raise ValueError("Task is not awaiting authorization")
        if not approve:
            task = self.history.transition_task(task_id, TaskState.CANCELLED)
            self._publish_task(task, "cancelled")
            return task
        task = self.history.transition_task(task_id, TaskState.QUEUED)
        with self.lock:
            self.cancelled[task_id] = threading.Event()
            self.paused[task_id] = threading.Event()
        try:
            self.queue.put_nowait(task_id)
        except queue.Full as exc:
            self.history.transition_task(
                task_id, TaskState.BLOCKED, error="Another Task already owns the execution lane"
            )
            raise RuntimeError("Another Task already owns the execution lane") from exc
        self._publish_task(task, "queued")
        return task

    def pause(self, task_id: str, session_id: str) -> dict[str, Any]:
        task = self._owned(task_id, session_id)
        if task["state"] != TaskState.RUNNING.value:
            raise ValueError("Only a running Task can be paused")
        self.paused.setdefault(task_id, threading.Event()).set()
        return self.history.transition_task(task_id, TaskState.PAUSE_REQUESTED)

    def resume(self, task_id: str, session_id: str) -> dict[str, Any]:
        task = self._owned(task_id, session_id)
        if task["state"] != TaskState.PAUSED.value:
            raise ValueError("Only a paused Task can resume")
        self.paused.setdefault(task_id, threading.Event()).clear()
        task = self.history.transition_task(task_id, TaskState.QUEUED)
        self.queue.put_nowait(task_id)
        return task

    def cancel(self, task_id: str, session_id: str) -> dict[str, Any]:
        task = self._owned(task_id, session_id)
        if task["state"] in {
            TaskState.COMPLETED.value,
            TaskState.PARTIAL.value,
            TaskState.FAILED.value,
            TaskState.CANCELLED.value,
        }:
            return task
        if task["state"] == TaskState.PAUSED.value:
            task = self.history.transition_task(task_id, TaskState.CANCEL_REQUESTED)
        elif task["state"] in {TaskState.RUNNING.value, TaskState.PAUSE_REQUESTED.value}:
            task = self.history.transition_task(task_id, TaskState.CANCEL_REQUESTED)
        else:
            task = self.history.transition_task(task_id, TaskState.CANCELLED)
            return task
        self.cancelled.setdefault(task_id, threading.Event()).set()
        return task

    def pause_for_conversation(self) -> None:
        for task in self.history.list_tasks("native", limit=1):
            if task["state"] == TaskState.RUNNING.value:
                self.pause(task["task_id"], "native")

    def list(self, session_id: str) -> list[dict[str, Any]]:
        return self.history.list_tasks(session_id)

    def close(self) -> None:
        self.stopping.set()
        try:
            self.queue.put_nowait(None)
        except queue.Full:
            pass
        self.worker.join(timeout=3)

    def _owned(self, task_id: str, session_id: str) -> dict[str, Any]:
        task = self.history.load_task(task_id)
        if not task or task.get("session_id") != session_id:
            raise PermissionError("Task does not belong to this session")
        return task

    @staticmethod
    def _authority_manifest(steps) -> dict[str, Any]:
        return {
            "tools": sorted({step.capability for step in steps}),
            "targets": [step.arguments for step in steps],
            "data_scopes": sorted(
                {
                    "local_documents" if step.capability == "rag_search" else "external_network"
                    for step in steps
                    if step.capability in {"rag_search", "web_search", "weather"}
                }
            ),
            "maximum_steps": TaskEngine.MAX_STEPS,
            "deadline_seconds": TaskEngine.DEADLINE_SECONDS,
        }

    def _loop(self) -> None:
        while not self.stopping.is_set():
            try:
                task_id = self.queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if task_id is None:
                self.queue.task_done()
                return
            try:
                self._run(task_id)
            except Exception as exc:
                task = self.history.load_task(task_id)
                if task and task["state"] not in {
                    TaskState.BLOCKED.value,
                    TaskState.PARTIAL.value,
                    TaskState.FAILED.value,
                    TaskState.CANCELLED.value,
                    TaskState.COMPLETED.value,
                }:
                    self.history.transition_task(
                        task_id, TaskState.FAILED, error=f"{type(exc).__name__}: {exc}"
                    )
                latest = self.history.load_task(task_id)
                if latest:
                    self._publish_task(
                        latest,
                        "failed",
                        error=type(exc).__name__,
                        message=str(exc),
                    )
            finally:
                self.queue.task_done()

    def _run(self, task_id: str) -> None:
        task = self.history.load_task(task_id)
        if not task:
            return
        session_id = task["session_id"]
        task = self.history.transition_task(task_id, TaskState.RUNNING)
        self._publish_task(task, "started")
        successes = 0
        failures = 0
        for step in self.history.load_steps(task_id):
            if step["state"] == StepState.SUCCEEDED.value:
                successes += 1
                continue
            if step["state"] in {
                StepState.FAILED.value,
                StepState.SKIPPED.value,
                StepState.UNKNOWN_EFFECT.value,
            }:
                failures += 1
                continue
            cancel = self.cancelled.setdefault(task_id, threading.Event())
            pause = self.paused.setdefault(task_id, threading.Event())
            if cancel.is_set():
                break
            if pause.is_set():
                current = self.history.load_task(task_id)
                if current and current["state"] == TaskState.PAUSE_REQUESTED.value:
                    self.history.transition_task(task_id, TaskState.PAUSED)
                self._publish_task(self.history.load_task(task_id) or task, "paused")
                return
            if time.time_ns() >= int(task["deadline_ns"]):
                self.history.transition_task(
                    task_id, TaskState.BLOCKED, error="Task deadline exhausted"
                )
                self._publish_task(
                    self.history.load_task(task_id) or task, "blocked", reason="deadline"
                )
                return
            authority = (
                CapabilityAuthority.READ
                if step["safety_class"] == "read"
                else CapabilityAuthority.APPROVAL_REQUIRED
            )
            receipt = self.broker.issue(step, authority)
            self.events.publish(session_id, task_id, "running", "task_step_started", step=step)
            try:
                result = self.broker.execute(step, receipt)
                successes += 1
                self.events.publish(
                    session_id,
                    task_id,
                    "running",
                    "task_step_succeeded",
                    step_id=step["step_id"],
                    result=result,
                )
            except Exception as exc:
                failures += 1
                persisted = self.history.load_steps(task_id)
                current = next(item for item in persisted if item["step_id"] == step["step_id"])
                self.events.publish(
                    session_id,
                    task_id,
                    "running",
                    "task_step_failed",
                    step_id=step["step_id"],
                    step_state=current["state"],
                    error=type(exc).__name__,
                    message=str(exc),
                )
                if current["state"] == StepState.UNKNOWN_EFFECT.value:
                    self.history.transition_task(task_id, TaskState.BLOCKED, error=str(exc))
                    return
        current = self.history.load_task(task_id)
        if not current:
            return
        if self.paused.setdefault(task_id, threading.Event()).is_set():
            if current["state"] == TaskState.PAUSE_REQUESTED.value:
                paused = self.history.transition_task(task_id, TaskState.PAUSED)
                self._publish_task(paused, "paused")
            return
        if current["state"] == TaskState.CANCEL_REQUESTED.value:
            state = TaskState.PARTIAL if successes else TaskState.CANCELLED
            result = self._safe_result(task_id, state)
        else:
            state = (
                TaskState.PARTIAL
                if successes and failures
                else TaskState.FAILED
                if failures
                else TaskState.COMPLETED
            )
            result = self._safe_result(task_id, state)
        task = self.history.transition_task(task_id, state, result=result)
        self._publish_task(task, "finished", result=result)

    def _publish_task(self, task: dict[str, Any], event: str, **data: Any) -> None:
        commands = {
            TaskState.AWAITING_AUTHORIZATION.value: ["authorize", "deny"],
            TaskState.RUNNING.value: ["pause", "cancel"],
            TaskState.PAUSE_REQUESTED.value: ["cancel"],
            TaskState.PAUSED.value: ["resume", "cancel"],
            TaskState.QUEUED.value: ["cancel"],
            TaskState.BLOCKED.value: ["cancel"],
        }.get(str(task["state"]), [])
        result = task.get("result") or {}
        display = {
            "task_id": task["task_id"],
            "turn_id": task.get("turn_id"),
            "title": task.get("title", "MacBot Task"),
            "detail": result.get("summary") or task.get("error") or event.replace("_", " ").title(),
            "state": task["state"],
            "source": "MacBot Task Engine",
            "commands": commands,
            "steps": self.history.load_steps(task["task_id"]),
        }
        self.events.publish(
            task["session_id"],
            task["task_id"],
            str(task["state"]),
            "task",
            task=display,
            event=event,
            **data,
        )

    def _result(self, task_id: str, state: TaskState) -> dict[str, Any]:
        steps = self.history.load_steps(task_id)
        observations = [
            {
                "tool": step["capability"],
                "state": step["state"],
                "result": step.get("result"),
                "error": step.get("error"),
            }
            for step in steps
        ]
        if (
            state in {TaskState.CANCELLED, TaskState.PARTIAL}
            and self.cancelled.setdefault(task_id, threading.Event()).is_set()
        ):
            return {
                "state": state.value,
                "summary": "Task cancelled. Completed effects and unfinished steps are listed below.",
                "steps": observations,
            }
        prompt = [
            {
                "role": "system",
                "content": "Summarize this bounded Task using only the supplied untrusted observations. Preserve URLs and document provenance. State failures, blocked work, and partial completion plainly. Do not claim any new action.",
            },
            {
                "role": "user",
                "content": "UNTRUSTED_TASK_OBSERVATIONS\n"
                + json.dumps(observations, ensure_ascii=False),
            },
        ]
        cancel = self.cancelled.setdefault(task_id, threading.Event())
        summary = "".join(
            str(chunk.get("content") or "") for chunk in self.llm.stream(prompt, [], cancel)
        )
        return {"state": state.value, "summary": summary.strip(), "steps": observations}

    def _safe_result(self, task_id: str, state: TaskState) -> dict[str, Any]:
        """Keep recorded tool truth authoritative if response synthesis fails."""
        try:
            return self._result(task_id, state)
        except Exception as exc:
            observations = [
                {
                    "tool": step["capability"],
                    "state": step["state"],
                    "result": step.get("result"),
                    "error": step.get("error"),
                }
                for step in self.history.load_steps(task_id)
            ]
            return {
                "state": state.value,
                "summary": (
                    "Task execution finished, but the final response could not be generated. "
                    "The recorded step outcomes below remain authoritative."
                ),
                "response_error": f"{type(exc).__name__}: {exc}",
                "steps": observations,
            }
