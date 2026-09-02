"""Durable, bounded Task-mode execution owned by MacBot."""

from __future__ import annotations

import hashlib
import json
import queue
import threading
import time
import uuid
from functools import partial
from typing import Any

from .capabilities import (
    CapabilityAuthority,
    CapabilityBroker,
    CapabilityDefinition,
    RequestContext,
)
from .events import EventJournal
from .history import HistoryStore
from .intent import IntentRouter
from .llm import LocalLLM
from .task_protocol import legal_commands
from .tasks import StepState, TaskPlan, TaskState, TaskStep
from .tools import SCHEMAS, TASK_RELEASE_CAPABILITIES, Tools
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
        self.active_task_id: str | None = None
        definitions = [
            CapabilityDefinition(
                name,
                description,
                {key: "string" for key in arguments},
                partial(self.tools._execute, name),
            )
            for name, (description, arguments) in SCHEMAS.items()
            if name in self.tools.settings.tools.enabled and name in TASK_RELEASE_CAPABILITIES
        ]
        self.broker = CapabilityBroker(history, definitions)
        self.recovery = history.recover_inflight_steps()
        self.worker = threading.Thread(target=self._loop, name="task-worker", daemon=True)
        self.worker.start()

    def create(self, text: str, session_id: str) -> dict[str, Any]:
        text = validate_chat_message(text)
        created_ns = time.time_ns()
        task_id = uuid.uuid4().hex
        turn_id = "task-" + str(created_ns)
        envelope = {
            "task_id": task_id,
            "session_id": session_id,
            "turn_id": turn_id,
            "user_text_hash": hashlib.sha256(text.encode()).hexdigest(),
            "original_text": text,
            "title": text[:160],
            "mode": "act",
            "actions": [],
            "clarification": "",
            "state": TaskState.PROPOSED.value,
            "created_ns": created_ns,
            "step_budget": self.MAX_STEPS,
            "replan_budget": self.MAX_REPLANS,
            "deadline_ns": created_ns + self.DEADLINE_SECONDS * 1_000_000_000,
            "planning_attempts": 1,
        }
        self.history.create_task(envelope, [])
        cancel = threading.Event()
        try:
            intent = self.planner.route(text, cancel)
        except Exception as exc:
            failed = self.history.transition_task(
                task_id, TaskState.FAILED, error=f"{type(exc).__name__}: {exc}"
            )
            self._publish_task(failed, "planning_failed", error=type(exc).__name__)
            raise
        if intent.mode != "act" or not intent.actions:
            explanation = intent.clarification or "This request does not contain a bounded Task."
            failed = self.history.transition_task(task_id, TaskState.FAILED, error=explanation)
            self._publish_task(failed, "planning_failed", message=explanation)
            raise ValueError(explanation)
        if len(intent.actions) > self.MAX_STEPS:
            failed = self.history.transition_task(
                task_id,
                TaskState.FAILED,
                error=f"Task exceeds the {self.MAX_STEPS}-step execution budget",
            )
            self._publish_task(failed, "planning_failed", reason="step_budget")
            raise ValueError(f"Task exceeds the {self.MAX_STEPS}-step execution budget")
        task = TaskPlan.from_intent(session_id, turn_id, text, intent)
        task.task_id = task_id
        task.created_ns = created_ns
        payload = task.as_dict()
        payload.update(
            {
                **envelope,
                "actions": payload["actions"],
                "step_budget": self.MAX_STEPS,
                "replan_budget": self.MAX_REPLANS,
                "deadline_ns": envelope["deadline_ns"],
                "planning_attempts": 1,
            }
        )
        steps = task.durable_steps()
        payload["capability_manifest"] = self._authority_manifest(steps)
        self.history.attach_task_plan(task_id, payload, [step.as_dict() for step in steps])
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
            blocked = self.history.transition_task(
                task_id, TaskState.BLOCKED, error="Another Task already owns the execution lane"
            )
            self._publish_task(blocked, "blocked", reason="execution_lane_busy")
            raise RuntimeError("Another Task already owns the execution lane") from exc
        self._publish_task(task, "queued")
        return task

    def pause(self, task_id: str, session_id: str) -> dict[str, Any]:
        task = self._owned(task_id, session_id)
        if task["state"] != TaskState.RUNNING.value:
            raise ValueError("Only a running Task can be paused")
        self.paused.setdefault(task_id, threading.Event()).set()
        try:
            return self.history.transition_task(
                task_id,
                TaskState.PAUSE_REQUESTED,
                expected_revision=int(task.get("revision", 0)),
            )
        except RuntimeError:
            current = self._owned(task_id, session_id)
            if current["state"] in {TaskState.PAUSE_REQUESTED.value, TaskState.PAUSED.value}:
                return current
            raise ValueError("Task completed before it could be paused") from None

    def resume(self, task_id: str, session_id: str) -> dict[str, Any]:
        task = self._owned(task_id, session_id)
        if task["state"] != TaskState.PAUSED.value:
            raise ValueError("Only a paused Task can resume")
        self.paused.setdefault(task_id, threading.Event()).clear()
        task = self.history.transition_task(task_id, TaskState.QUEUED)
        try:
            self.queue.put_nowait(task_id)
        except queue.Full as exc:
            task = self.history.transition_task(
                task_id, TaskState.BLOCKED, error="Another Task already owns the execution lane"
            )
            self._publish_task(task, "blocked", reason="execution_lane_busy")
            raise RuntimeError("Another Task already owns the execution lane") from exc
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
        revision = int(task.get("revision", 0))
        if task["state"] == TaskState.PAUSED.value:
            task = self.history.transition_task(
                task_id, TaskState.CANCEL_REQUESTED, expected_revision=revision
            )
        elif task["state"] in {TaskState.RUNNING.value, TaskState.PAUSE_REQUESTED.value}:
            task = self.history.transition_task(
                task_id, TaskState.CANCEL_REQUESTED, expected_revision=revision
            )
        else:
            task = self.history.transition_task(
                task_id, TaskState.CANCELLED, expected_revision=revision
            )
            return task
        self.cancelled.setdefault(task_id, threading.Event()).set()
        return task

    def pause_for_conversation(self) -> None:
        with self.lock:
            task_id = self.active_task_id
        if not task_id:
            return
        task = self.history.load_task(task_id)
        if task and task["state"] == TaskState.RUNNING.value:
            try:
                self.pause(task_id, str(task["session_id"]))
            except ValueError:
                pass

    def list(self, session_id: str) -> list[dict[str, Any]]:
        return [self.snapshot(task) for task in self.history.list_tasks(session_id)]

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
                    if step.capability in {"rag_search", "web_search", "web_fetch"}
                }
            ),
            "side_effect_classes": sorted({step.safety_class for step in steps}),
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
                with self.lock:
                    self.active_task_id = task_id
                self._run(task_id)
            except Exception as exc:
                latest = self._resolve_failure(task_id, exc)
                if latest:
                    self._publish_task(
                        latest,
                        "failed",
                        error=type(exc).__name__,
                        message=str(exc),
                    )
            finally:
                with self.lock:
                    if self.active_task_id == task_id:
                        self.active_task_id = None
                    latest = self.history.load_task(task_id)
                    if latest and latest["state"] in {
                        TaskState.COMPLETED.value,
                        TaskState.PARTIAL.value,
                        TaskState.FAILED.value,
                        TaskState.CANCELLED.value,
                    }:
                        self.cancelled.pop(task_id, None)
                        self.paused.pop(task_id, None)
                self.queue.task_done()

    def _run(self, task_id: str) -> None:
        task = self.history.load_task(task_id)
        if not task or task["state"] != TaskState.QUEUED.value:
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
                blocked = self._resolve_control(
                    task_id, TaskState.BLOCKED, "Task deadline exhausted"
                )
                self._publish_task(blocked, "blocked", reason="deadline")
                return
            completed = {
                item["step_id"]: item["state"] for item in self.history.load_steps(task_id)
            }
            unmet = [
                dependency
                for dependency in step.get("depends_on", [])
                if completed.get(dependency) != StepState.SUCCEEDED.value
            ]
            if unmet:
                self.history.transition_step(
                    step["step_id"],
                    StepState.SKIPPED,
                    error="Dependency did not succeed",
                    details={"unmet_dependencies": unmet},
                )
                failures += 1
                continue
            authority = (
                CapabilityAuthority.READ
                if step["safety_class"] == "read"
                else CapabilityAuthority.APPROVAL_REQUIRED
            )
            while True:
                step = self.history.increment_step_attempt(step["step_id"])
                receipt = self.broker.issue(step, authority)
                self.events.publish(
                    session_id,
                    task_id,
                    "running",
                    "task_step_started",
                    step_id=step["step_id"],
                    attempt=step["attempts"],
                )
                try:
                    result = self.broker.execute(
                        step,
                        receipt,
                        RequestContext(
                            request_id=task_id,
                            task_id=task_id,
                            step_id=str(step["step_id"]),
                            attempt_id=uuid.uuid4().hex,
                            deadline_ns=int(task["deadline_ns"]),
                            cancellation=cancel,
                            authorization_version=int(task.get("revision", 0)),
                        ),
                    )
                    successes += 1
                    self.events.publish(
                        session_id,
                        task_id,
                        "running",
                        "task_step_succeeded",
                        step_id=step["step_id"],
                        result=result,
                    )
                    break
                except Exception as exc:
                    persisted = self.history.load_steps(task_id)
                    current = next(item for item in persisted if item["step_id"] == step["step_id"])
                    retry = (
                        current["state"] == StepState.BLOCKED.value
                        and bool(current.get("retryable"))
                        and int(current.get("attempts", 0)) < int(current.get("max_attempts", 1))
                        and not cancel.is_set()
                        and time.time_ns() < int(task["deadline_ns"])
                    )
                    self.events.publish(
                        session_id,
                        task_id,
                        "running",
                        "task_step_failed",
                        step_id=step["step_id"],
                        step_state=current["state"],
                        failure_class=current.get("failure_class"),
                        retrying=retry,
                        error=type(exc).__name__,
                        message=str(exc),
                    )
                    if retry:
                        continue
                    failures += 1
                    if current["state"] == StepState.UNKNOWN_EFFECT.value:
                        blocked = self._resolve_control(task_id, TaskState.BLOCKED, str(exc))
                        self._publish_task(blocked, "blocked", reason="unknown_effect")
                        return
                    break
        current = self.history.load_task(task_id)
        if not current:
            return
        if self.paused.setdefault(task_id, threading.Event()).is_set():
            if current["state"] == TaskState.PAUSE_REQUESTED.value:
                paused = self._resolve_control(task_id, TaskState.PAUSED)
                self._publish_task(paused, "paused")
            return
        if self._needs_more_evidence(task_id, failures) and self._request_replan(task_id):
            return
        desired = (
            TaskState.PARTIAL
            if successes and failures
            else TaskState.FAILED
            if failures
            else TaskState.COMPLETED
        )
        result = self._safe_result(task_id, desired)
        task = self._resolve_terminal(task_id, desired, result, successes)
        self._publish_task(task, "finished", result=task.get("result", result))

    def _needs_more_evidence(self, task_id: str, failures: int) -> bool:
        if failures:
            return True
        for step in self.history.load_steps(task_id):
            result = step.get("result")
            if isinstance(result, dict) and result.get("status") in {
                "empty",
                "no_answer",
                "not_configured",
            }:
                return True
        return False

    def _request_replan(self, task_id: str) -> bool:
        replan = getattr(self.planner, "replan", None)
        task = self.history.load_task(task_id)
        if not callable(replan) or not task or int(task.get("replan_budget", 0)) <= 0:
            return False
        cancel = self.cancelled.setdefault(task_id, threading.Event())
        if cancel.is_set() or time.time_ns() >= int(task["deadline_ns"]):
            return False
        observations = [
            {
                "capability": step["capability"],
                "arguments": step["arguments"],
                "state": step["state"],
                "result": step.get("result"),
                "error": step.get("error"),
                "failure_class": step.get("failure_class"),
            }
            for step in self.history.load_steps(task_id)
        ]
        intent = replan(str(task["original_text"]), observations, cancel)
        if cancel.is_set() or intent.mode != "act" or not intent.actions:
            return False
        existing = {step["idempotency_key"] for step in self.history.load_steps(task_id)}
        actions = [action for action in intent.actions if action.idempotency_key not in existing]
        if not actions:
            return False
        start = len(observations)
        steps = [
            TaskStep(
                task_id=task_id,
                session_id=str(task["session_id"]),
                turn_id=str(task["turn_id"]),
                ordinal=start + index,
                capability=action.name,
                arguments=dict(action.arguments),
                safety_class=action.safety_class,
                idempotency_key=action.idempotency_key,
            )
            for index, action in enumerate(actions)
        ]
        proposed_manifest = self._authority_manifest(steps)
        previous_manifest = task.get("capability_manifest") or {}
        authority_diff = self._authority_diff(previous_manifest, proposed_manifest)
        material = any(bool(value) for value in authority_diff.values())
        replanned = self.history.append_replan(
            task_id,
            [step.as_dict() for step in steps],
            capability_manifest=self._merge_authority(previous_manifest, proposed_manifest),
            requires_authorization=material,
        )
        self._publish_task(
            replanned,
            "replan_authorization_required" if material else "replanned_within_authority",
            authority=replanned["capability_manifest"],
            authority_diff=authority_diff,
            observations=observations,
        )
        if not material:
            try:
                self.queue.put_nowait(task_id)
            except queue.Full as exc:
                blocked = self.history.transition_task(
                    task_id,
                    TaskState.BLOCKED,
                    error="Replanned Task could not reacquire the execution lane",
                )
                self._publish_task(blocked, "blocked", reason="execution_lane_busy")
                raise RuntimeError("Replanned Task could not reacquire the execution lane") from exc
        return True

    @staticmethod
    def _authority_diff(previous: dict[str, Any], proposed: dict[str, Any]) -> dict[str, Any]:
        prior_targets = {
            json.dumps(item, sort_keys=True, separators=(",", ":"))
            for item in previous.get("targets", [])
        }
        return {
            "new_capabilities": sorted(
                set(proposed.get("tools", [])) - set(previous.get("tools", []))
            ),
            "new_targets": [
                item
                for item in proposed.get("targets", [])
                if json.dumps(item, sort_keys=True, separators=(",", ":")) not in prior_targets
            ],
            "new_data_scopes": sorted(
                set(proposed.get("data_scopes", [])) - set(previous.get("data_scopes", []))
            ),
            "new_side_effect_classes": sorted(
                set(proposed.get("side_effect_classes", []))
                - set(previous.get("side_effect_classes", []))
            ),
            "deadline_extension": int(proposed.get("deadline_seconds", 0))
            > int(previous.get("deadline_seconds", 0)),
        }

    @staticmethod
    def _merge_authority(previous: dict[str, Any], proposed: dict[str, Any]) -> dict[str, Any]:
        targets: list[Any] = []
        seen: set[str] = set()
        for item in list(previous.get("targets", [])) + list(proposed.get("targets", [])):
            key = json.dumps(item, sort_keys=True, separators=(",", ":"))
            if key not in seen:
                seen.add(key)
                targets.append(item)
        return {
            "tools": sorted(set(previous.get("tools", [])) | set(proposed.get("tools", []))),
            "targets": targets,
            "data_scopes": sorted(
                set(previous.get("data_scopes", [])) | set(proposed.get("data_scopes", []))
            ),
            "side_effect_classes": sorted(
                set(previous.get("side_effect_classes", []))
                | set(proposed.get("side_effect_classes", []))
            ),
            "maximum_steps": TaskEngine.MAX_STEPS,
            "deadline_seconds": max(
                int(previous.get("deadline_seconds", 0)),
                int(proposed.get("deadline_seconds", 0)),
            ),
        }

    def _success_count(self, task_id: str) -> int:
        return sum(
            step["state"] == StepState.SUCCEEDED.value for step in self.history.load_steps(task_id)
        )

    def _resolve_control(
        self,
        task_id: str,
        desired: TaskState,
        error: str | None = None,
    ) -> dict[str, Any]:
        """Resolve a control race without ever applying an illegal transition."""
        while True:
            task = self.history.load_task(task_id)
            if task is None:
                raise KeyError("Unknown task")
            state = TaskState(task["state"])
            if state in {
                TaskState.COMPLETED,
                TaskState.PARTIAL,
                TaskState.FAILED,
                TaskState.CANCELLED,
                TaskState.BLOCKED,
                TaskState.PAUSED,
            }:
                return task
            target = desired
            if state is TaskState.CANCEL_REQUESTED:
                target = TaskState.PARTIAL if self._success_count(task_id) else TaskState.CANCELLED
            elif state is TaskState.PAUSE_REQUESTED:
                target = TaskState.PAUSED
            try:
                return self.history.transition_task(
                    task_id,
                    target,
                    error=error,
                    expected_revision=int(task.get("revision", 0)),
                )
            except RuntimeError as exc:
                if str(exc) != "Task changed concurrently":
                    raise

    def _resolve_terminal(
        self,
        task_id: str,
        desired: TaskState,
        result: dict[str, Any],
        successes: int,
    ) -> dict[str, Any]:
        while True:
            task = self.history.load_task(task_id)
            if task is None:
                raise KeyError("Unknown task")
            state = TaskState(task["state"])
            if state in {
                TaskState.COMPLETED,
                TaskState.PARTIAL,
                TaskState.FAILED,
                TaskState.CANCELLED,
            }:
                return task
            if state is TaskState.PAUSE_REQUESTED:
                return self._resolve_control(task_id, TaskState.PAUSED)
            target = desired
            if state is TaskState.CANCEL_REQUESTED:
                target = TaskState.PARTIAL if successes else TaskState.CANCELLED
            resolved = dict(result)
            resolved["state"] = target.value
            try:
                return self.history.transition_task(
                    task_id,
                    target,
                    result=resolved,
                    expected_revision=int(task.get("revision", 0)),
                )
            except RuntimeError as exc:
                if str(exc) != "Task changed concurrently":
                    raise

    def _resolve_failure(self, task_id: str, exc: Exception) -> dict[str, Any] | None:
        """Keep the worker alive even when failure races with pause or cancel."""
        try:
            task = self.history.load_task(task_id)
            if task is None:
                return None
            state = TaskState(task["state"])
            if state is TaskState.CANCEL_REQUESTED:
                return self._resolve_control(task_id, TaskState.CANCELLED)
            if state is TaskState.PAUSE_REQUESTED:
                return self._resolve_control(task_id, TaskState.PAUSED)
            if state in {
                TaskState.COMPLETED,
                TaskState.PARTIAL,
                TaskState.FAILED,
                TaskState.CANCELLED,
                TaskState.BLOCKED,
                TaskState.PAUSED,
            }:
                return task
            return self.history.transition_task(
                task_id,
                TaskState.FAILED,
                error=f"{type(exc).__name__}: {exc}",
                expected_revision=int(task.get("revision", 0)),
            )
        except Exception:
            # The worker must remain available; the durable recovery pass owns any
            # state that another thread changed while this resolver ran.
            return self.history.load_task(task_id)

    def _publish_task(self, task: dict[str, Any], event: str, **data: Any) -> None:
        display = self.snapshot(task, event=event)
        self.events.publish(
            task["session_id"],
            task["task_id"],
            str(task["state"]),
            "task",
            task=display,
            event=event,
            **data,
        )

    def snapshot(self, task: dict[str, Any], *, event: str = "updated") -> dict[str, Any]:
        result = task.get("result") or {}
        return {
            **task,
            "task_id": task["task_id"],
            "turn_id": task.get("turn_id"),
            "title": task.get("title", "MacBot Task"),
            "detail": result.get("summary") or task.get("error") or event.replace("_", " ").title(),
            "state": task["state"],
            "source": "MacBot Task Engine",
            "commands": legal_commands(str(task["state"])),
            "steps": task.get("steps") or self.history.load_steps(task["task_id"]),
        }

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
            str(chunk.get("content") or "")
            for chunk in self.llm.stream(
                prompt,
                [],
                cancel,
                request_id=f"{task_id}:summary",
                request_kind="task",
            )
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
