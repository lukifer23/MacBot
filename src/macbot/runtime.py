"""The sole owner of turns, history, approvals, capture, and ordered speech."""

from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .auth import AuthStore
from .config import Settings
from .events import EventJournal
from .llm import LocalLLM
from .native_audio import NativeAudio
from .speech import SileroVAD, Synthesizer, Transcriber
from .tools import READ_ONLY, Tools
from .validation import validate_chat_message


@dataclass
class Turn:
    id: str
    session_id: str
    text: str | None
    speak: bool
    audio: np.ndarray | None = None
    cancelled: threading.Event = field(default_factory=threading.Event)
    speech_done: threading.Event = field(default_factory=threading.Event)
    approval_done: threading.Event = field(default_factory=threading.Event)
    approval_result: dict | None = None
    action_id: str | None = None
    speech_error: str | None = None
    generation: int = 0
    submitted_ns: int = field(default_factory=time.monotonic_ns)
    speech_end_ns: int | None = None
    first_audio_scheduled_ns: int | None = None
    terminal: bool = False
    synthesis_only: bool = False
    state: str = "accepted"
    phase: str = "queued"
    stt_ms: float | None = None
    tts_first_chunk_ms: float | None = None


class Runtime:
    def __init__(self, settings: Settings, *, load_speech: bool = True):
        self.settings = settings
        self.auth = AuthStore(settings.data_dir)
        self.tools = Tools(settings, self.auth)
        self.llm = LocalLLM(settings, self.auth)
        self.events = EventJournal()
        self.audio = NativeAudio(settings, self._audio_event)
        self.transcriber = Transcriber(settings) if load_speech else None
        self.synth = Synthesizer(settings) if load_speech else None
        self.vad = SileroVAD(settings) if load_speech else None
        self.turns: queue.Queue[Turn | None] = queue.Queue(maxsize=4)
        self.speech: queue.Queue[tuple[Turn, str | None] | None] = queue.Queue(maxsize=4)
        # Model-token budgeting bounds this complete-message history. Tool calls
        # and their results stay together; a message-count deque could orphan them.
        self.history: list[dict] = []
        self.metrics: deque[dict] = deque(maxlen=256)
        self.lock = threading.RLock()
        self.audio_owner = threading.RLock()
        self.browser_capture: tuple[str, float] | None = None
        self.current: Turn | None = None
        self.stopping = threading.Event()
        self.listening = False
        self.capture_session = "local"
        self.capture_epoch = 0
        self.last_activity = time.monotonic()
        self.error_count = 0
        self.last_error: str | None = None
        self.threads = [
            threading.Thread(target=f, name=name, daemon=True)
            for f, name in (
                (self._turn_loop, "turn-worker"),
                (self._speech_loop, "speech-worker"),
                (self._capture_loop, "capture-worker"),
            )
        ]
        for thread in self.threads:
            thread.start()

    def _emit(self, turn: Turn, state: str, kind: str = "state", **data):
        with self.lock:
            if turn.cancelled.is_set() and state != "interrupted":
                return
            if kind == "state" or state in {
                "approval_required",
                "completed",
                "interrupted",
                "failed",
            }:
                turn.state = state
            if kind in {"transcribing", "generating", "speaking", "approval", "tool"}:
                turn.phase = kind
            if state in {"completed", "interrupted", "failed"}:
                turn.phase = state
            if state == "failed":
                self.error_count += 1
                self.last_error = str(data.get("message", data.get("error", "Operation failed")))
            self.events.publish(turn.session_id, turn.id, state, kind, **data)

    def _audio_event(self, event: dict):
        with self.lock:
            turn = self.current
            if (
                turn
                and event.get("event") == "playback_scheduled"
                and event.get("generation") == turn.generation
                and turn.first_audio_scheduled_ns is None
            ):
                turn.first_audio_scheduled_ns = time.monotonic_ns()
                self._emit(turn, "running", "speaking")
            elif event.get("event") in {"error", "overflow"}:
                self.events.publish("local", turn.id if turn else "", "failed", "audio", **event)

    def submit(
        self,
        text: str | None = None,
        *,
        audio: np.ndarray | None = None,
        speak: bool = True,
        session_id: str = "local",
        speech_end_ns: int | None = None,
        synthesis_only: bool = False,
    ) -> Turn:
        if text is not None:
            text = validate_chat_message(text)
        if not isinstance(speak, bool):
            raise ValueError("speak must be boolean")
        if text is None and audio is None:
            raise ValueError("A message or audio is required")
        with self.lock:
            if self.browser_capture and time.monotonic() >= self.browser_capture[1]:
                self.browser_capture = None
            if self.browser_capture and speak:
                raise RuntimeError("Finish browser recording before requesting speech playback")
            self.interrupt()
            turn = Turn(
                uuid.uuid4().hex,
                session_id,
                text,
                speak,
                audio=audio,
                generation=self.audio.generation,
                speech_end_ns=speech_end_ns,
                synthesis_only=synthesis_only,
            )
            self.current = turn
            self.last_activity = time.monotonic()
            try:
                self.turns.put_nowait(turn)
            except queue.Full:
                self.current = None
                raise RuntimeError("Turn queue is full") from None
            self._emit(turn, "accepted")
            return turn

    def interrupt(self):
        with self.lock:
            turn = self.current
            if turn and not turn.terminal and not turn.cancelled.is_set():
                turn.cancelled.set()
                turn.approval_done.set()
                self.tools.invalidate(turn.id)
                requested = time.monotonic_ns()
                self.audio.cancel()
                self.llm.cancel()
                self.events.publish(
                    turn.session_id, turn.id, "interrupted", "state", requested_ns=requested
                )
                turn.terminal = True
                turn.state = turn.phase = "interrupted"
            # Superseded queued turns never execute; preserve the new turn's queue slot.
            while True:
                try:
                    stale = self.turns.get_nowait()
                    if stale:
                        stale.cancelled.set()
                    self.turns.task_done()
                except queue.Empty:
                    break

    def decide(
        self, action_id: str, turn_id: str, approve: bool, session_id: str = "local"
    ) -> dict:
        with self.lock:
            turn = self.current
            if (
                not turn
                or turn.id != turn_id
                or turn.cancelled.is_set()
                or turn.action_id != action_id
            ):
                raise PermissionError("No matching pending action")
            action = self.tools.consume(action_id, session_id, turn_id)
            turn.action_id = None
        # An approved OS action may already be executing when interrupted; it
        # cannot be rolled back. Never block cancellation behind its subprocess.
        try:
            if not approve or turn.cancelled.is_set():
                result = {"status": "denied", "tool": action.name}
            else:
                result = self.tools._execute(action.name, json.loads(action.arguments_json))
        except Exception as exc:
            result = {"status": "failed", "tool": action.name, "error": str(exc)}
        with self.lock:
            turn.approval_result = result
            turn.approval_done.set()
        return result

    def listen(self, enabled: bool, session_id: str = "local"):
        if enabled:
            if self.browser_capture and time.monotonic() >= self.browser_capture[1]:
                self.browser_capture = None
            if self.browser_capture:
                raise RuntimeError("Finish browser recording before starting native capture")
            if not self.transcriber or not self.vad:
                raise RuntimeError("Speech models are not loaded")
            with self.audio_owner:
                self.audio.launch(capture=True)
            if not self.audio.aec:
                raise RuntimeError("Native echo cancellation is not active")
            self.last_activity = time.monotonic()
            self.capture_session = session_id
        elif self.audio.process:
            self.audio.command("capture", enabled=False)
        self.listening = enabled
        self.capture_epoch += 1
        self.events.publish("local", "", "running", "listening", enabled=enabled)

    def browser_recording(self, enabled: bool, session_id: str):
        with self.lock:
            if enabled:
                if (
                    self.browser_capture
                    and self.browser_capture[1] > time.monotonic()
                    and self.browser_capture[0] != session_id
                ):
                    raise PermissionError("Browser capture belongs to a different session")
                self.interrupt()
                self.listening = False
                self.capture_epoch += 1
                self.browser_capture = (
                    session_id,
                    time.monotonic() + self.settings.audio.max_utterance_sec + 15,
                )
            elif self.browser_capture:
                if self.browser_capture[0] != session_id:
                    raise PermissionError("Browser capture belongs to a different session")
                self.browser_capture = None
        if enabled:
            # Releasing the native device, not merely muting its samples, ensures
            # browser and native microphones are never opened simultaneously.
            with self.audio_owner:
                self.audio.close()
            self.events.publish("local", "", "running", "listening", enabled=False)

    def clear(self):
        with self.lock:
            self.interrupt()
            self.history.clear()
            self.llm.context_stats = {}
            self.events.publish("local", "", "completed", "cleared")

    def _queue_speech(self, turn: Turn, text: str | None):
        if not turn.speak:
            return
        while not turn.cancelled.is_set() and not self.stopping.is_set():
            try:
                self.speech.put((turn, text), timeout=0.05)
                return
            except queue.Full:
                continue

    def _speech_loop(self):
        while not self.stopping.is_set():
            try:
                item = self.speech.get(timeout=0.1)
            except queue.Empty:
                continue
            if item is None:
                self.speech.task_done()
                return
            turn, text = item
            try:
                if turn.cancelled.is_set():
                    continue
                if text is None:
                    self.audio.drain(turn.cancelled)
                    turn.speech_done.set()
                else:
                    if not self.synth:
                        raise RuntimeError("TTS model is not loaded")
                    with self.audio_owner:
                        if turn.cancelled.is_set():
                            continue
                        if self.browser_capture:
                            raise RuntimeError("Browser microphone owns capture")
                        if not self.audio.ready:
                            self.audio.launch(capture=self.listening)
                    synthesis_started = time.monotonic_ns()
                    for samples, rate in self.synth.chunks(text, turn.cancelled):
                        if turn.tts_first_chunk_ms is None:
                            turn.tts_first_chunk_ms = (
                                time.monotonic_ns() - synthesis_started
                            ) / 1e6
                        self.audio.play(samples, rate, turn.cancelled, turn.generation)
            except Exception as exc:
                turn.speech_error = str(exc)
                turn.speech_done.set()
            finally:
                self.speech.task_done()

    def _turn_loop(self):
        while not self.stopping.is_set():
            try:
                turn = self.turns.get(timeout=0.1)
            except queue.Empty:
                continue
            if turn is None:
                self.turns.task_done()
                return
            try:
                if not turn.cancelled.is_set():
                    self._process(turn)
            except Exception as exc:
                self._emit(turn, "failed", error=type(exc).__name__, message=str(exc))
                self.tools.invalidate(turn.id)
                if not turn.cancelled.is_set():
                    self.audio.cancel()
                turn.cancelled.set()
                turn.approval_done.set()
                turn.terminal = True
            finally:
                self.turns.task_done()

    def _process(self, turn: Turn):
        self._emit(turn, "running")
        if turn.synthesis_only:
            self._queue_speech(turn, turn.text)
            self._queue_speech(turn, None)
            while not turn.speech_done.wait(0.05):
                if turn.cancelled.is_set() or self.stopping.is_set():
                    return
            if turn.speech_error:
                raise RuntimeError(turn.speech_error)
            turn.terminal = True
            self._emit(turn, "completed")
            return
        if turn.audio is not None:
            if not self.transcriber:
                raise RuntimeError("STT model is not loaded")
            self._emit(turn, "running", "transcribing")
            stt_started = time.monotonic_ns()
            turn.text = self.transcriber.transcribe(turn.audio)
            turn.stt_ms = (time.monotonic_ns() - stt_started) / 1e6
            turn.audio = None
            self._emit(turn, "running", "transcription", text=turn.text)
        if turn.cancelled.is_set():
            return
        if not turn.text or not turn.text.strip():
            self._emit(turn, "completed", "no_speech")
            turn.terminal = True
            return
        if turn.text.strip().lower().rstrip(".!?") in {"confirm action", "cancel action"}:
            self._emit(
                turn, "completed", "text", text="Actions must be confirmed in the dashboard."
            )
            turn.terminal = True
            return
        self._emit(turn, "running", "user", text=turn.text)
        messages = [
            {"role": "system", "content": self.settings.system_prompt},
            *list(self.history),
            {"role": "user", "content": turn.text},
        ]
        first_token_ns = None
        for _round in range(4):
            self._emit(turn, "running", "generating")
            calls: dict[int, dict] = {}
            buffer = ""
            round_text = ""
            for delta in self.llm.stream(messages, self.tools.definitions(), turn.cancelled):
                if turn.cancelled.is_set():
                    return
                if "_context" in delta:
                    self._emit(turn, "running", "context", **delta["_context"])
                for fragment in delta.get("tool_calls", []):
                    index = fragment.get("index", 0)
                    if not isinstance(index, int) or not 0 <= index < 4:
                        raise ValueError("Too many model tool calls")
                    call = calls.setdefault(
                        index,
                        {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                    )
                    if fragment.get("id"):
                        call["id"] = fragment["id"]
                    for key in ("name", "arguments"):
                        call["function"][key] += fragment.get("function", {}).get(key, "")
                    if len(call["function"]["arguments"]) > 10000:
                        raise ValueError("Tool argument limit exceeded")
                content = delta.get("content") or ""
                if content:
                    if first_token_ns is None:
                        first_token_ns = time.monotonic_ns()
                    round_text += content
                    buffer += content
                    self._emit(turn, "running", "delta", text=content)
                    if any(c in content for c in ".?!\n") or len(buffer) >= 100:
                        self._queue_speech(turn, buffer)
                        buffer = ""
            if turn.cancelled.is_set():
                return
            if buffer:
                self._queue_speech(turn, buffer)
            if not calls:
                if not round_text.strip():
                    raise RuntimeError("Model returned an empty response")
                messages.append({"role": "assistant", "content": round_text})
                break
            complete_calls = list(calls.values())
            messages.append(
                {"role": "assistant", "content": round_text or None, "tool_calls": complete_calls}
            )
            for call in complete_calls:
                if turn.cancelled.is_set():
                    return
                name = call["function"]["name"]
                args = json.loads(call["function"]["arguments"])
                self.tools.validate(name, args)
                if name in READ_ONLY:
                    self._emit(turn, "running", "tool", tool=name)
                    result = self.tools.read(name, args)
                else:
                    with self.lock:
                        if turn.cancelled.is_set():
                            return
                        turn.approval_done.clear()
                        turn.approval_result = None
                        pending = self.tools.request(turn.session_id, turn.id, name, args)
                        turn.action_id = pending.id
                    self._emit(
                        turn,
                        "approval_required",
                        "approval",
                        action_id=pending.id,
                        tool=name,
                        arguments=args,
                        expires_in=self.settings.tools.approval_seconds,
                        expires_at=time.time() + self.settings.tools.approval_seconds,
                    )
                    self._queue_speech(
                        turn, "Please review and confirm this action in the dashboard."
                    )
                    if not turn.approval_done.wait(self.settings.tools.approval_seconds):
                        self.tools.invalidate(turn.id)
                        result = {"status": "denied", "reason": "Approval expired"}
                    else:
                        if turn.cancelled.is_set():
                            return
                        result = turn.approval_result or {"status": "denied"}
                    turn.action_id = None
                self._emit(
                    turn,
                    "denied" if result.get("status") == "denied" else "running",
                    "tool_result",
                    tool=name,
                    result=result,
                )
                messages.append(
                    {"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result)}
                )
        else:
            raise RuntimeError("Tool round limit reached")
        if turn.speak:
            self._queue_speech(turn, None)
            while not turn.speech_done.wait(0.05):
                if turn.cancelled.is_set() or self.stopping.is_set():
                    return
            if turn.speech_error:
                raise RuntimeError(turn.speech_error)
        if turn.cancelled.is_set():
            return
        with self.lock:
            if turn.cancelled.is_set():
                return
            self.history = messages[1:]
            turn.terminal = True
        metric = {
            "turn_id": turn.id,
            "total_ms": (time.monotonic_ns() - turn.submitted_ns) / 1e6,
            "ttft_ms": (first_token_ns - turn.submitted_ns) / 1e6 if first_token_ns else None,
            "first_audio_scheduled_ms": (
                turn.first_audio_scheduled_ns - (turn.speech_end_ns or turn.submitted_ns)
            )
            / 1e6
            if turn.first_audio_scheduled_ns
            else None,
            "audio_dropped": self.audio.dropped,
            "stt_ms": turn.stt_ms,
            "tts_first_chunk_ms": turn.tts_first_chunk_ms,
        }
        with self.lock:
            self.metrics.append(metric)
        self._emit(turn, "completed", metrics=metric)

    def _capture_loop(self):
        pending = np.empty(0, dtype=np.float32)
        pre: deque[np.ndarray] = deque(maxlen=max(1, self.settings.audio.pre_roll_ms // 32))
        utterance: list[np.ndarray] = []
        active = False
        voiced = quiet = 0
        speech_end_ns = None
        capture_epoch = -1
        while not self.stopping.is_set():
            try:
                chunk = self.audio.capture.get(timeout=0.1)
            except queue.Empty:
                continue
            if capture_epoch != self.capture_epoch:
                capture_epoch = self.capture_epoch
                pending = np.empty(0, dtype=np.float32)
                pre.clear()
                utterance = []
                active = False
                voiced = quiet = 0
                if self.vad:
                    self.vad.reset()
            if not self.listening or not self.vad:
                pending = np.empty(0, dtype=np.float32)
                utterance = []
                active = False
                pre.clear()
                continue
            if time.monotonic() - self.last_activity > self.settings.audio.idle_timeout_sec:
                self.listen(False)
                continue
            pending = np.concatenate((pending, chunk))
            while len(pending) >= 512:
                frame, pending = pending[:512].copy(), pending[512:]
                speech = self.vad.probability(frame) >= self.settings.audio.vad_threshold
                if speech:
                    voiced += 32
                    quiet = 0
                    speech_end_ns = time.monotonic_ns()
                else:
                    quiet += 32
                    voiced = 0
                if not active:
                    pre.append(frame)
                    if voiced < self.settings.audio.speech_start_ms:
                        continue
                    active = True
                    utterance = list(pre)
                    pre.clear()
                    self.interrupt()
                    self.last_activity = time.monotonic()
                else:
                    utterance.append(frame)
                if (
                    quiet >= self.settings.audio.endpoint_ms
                    or len(utterance) * 512 >= self.settings.audio.max_utterance_sec * 16000
                ):
                    audio = np.concatenate(utterance)[
                        : self.settings.audio.max_utterance_sec * 16000
                    ]
                    utterance = []
                    active = False
                    voiced = quiet = 0
                    try:
                        self.submit(
                            audio=audio,
                            speech_end_ns=speech_end_ns,
                            session_id=self.capture_session,
                        )
                    except Exception as exc:
                        self.events.publish("local", "", "failed", "audio", message=str(exc))

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "listening": self.listening,
                "aec": self.audio.aec,
                "audio_ready": self.audio.ready,
                "stt_loaded": self.transcriber is not None,
                "tts_loaded": self.synth is not None,
                "turn_queue": self.turns.qsize(),
                "speech_queue": self.speech.qsize(),
                "turn_id": self.current.id if self.current else None,
                "turn_state": self.current.state if self.current else "idle",
                "phase": self.current.phase if self.current else "idle",
                "audio_dropped": self.audio.dropped,
                "audio_queue": self.audio.capture.qsize(),
                "playback_chunks": self.audio.inflight,
                "errors": self.error_count,
                "last_error": self.last_error,
                "models": self.settings.models.model_dump(),
                "browser_recording": bool(
                    self.browser_capture and self.browser_capture[1] > time.monotonic()
                ),
                "epoch": self.events.epoch,
                "cursor": self.events.seq,
                "metrics": list(self.metrics),
                "context": dict(self.llm.context_stats),
            }

    def close(self):
        self.listening = False
        self.interrupt()
        self.stopping.set()
        self.audio.close()
        for thread in self.threads:
            thread.join(timeout=5)
        self.llm.close()
        self.tools.close()
        if self.transcriber:
            self.transcriber.close()
        self.events.close()
        self.auth.close()
