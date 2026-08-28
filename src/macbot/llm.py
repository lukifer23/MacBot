"""Streaming local inference adapters, with explicit cancellation and tool results."""

from __future__ import annotations

import copy
import json
import os
import socket
import threading
from typing import Iterator

import httpx

from .auth import AuthStore
from .config import Settings
from .provision import model_dir


class LocalLLM:
    def __init__(self, settings: Settings, auth: AuthStore):
        self.settings, self.auth = settings, auth
        self.client = httpx.Client(timeout=httpx.Timeout(30, connect=3), trust_env=False)
        self.response: httpx.Response | None = None
        self.lock = threading.Lock()
        self.model = self.tokenizer = None
        if settings.models.llm_backend == "mlx":
            os.environ["HF_HUB_OFFLINE"] = "1"
            from mlx_lm import load

            loaded = load(str(model_dir(settings, settings.models.llm)))
            self.model, self.tokenizer = loaded[0], loaded[1]

    def stream(
        self, messages: list[dict], definitions: list[dict], cancel: threading.Event
    ) -> Iterator[dict]:
        if self.model is not None:
            yield from self._mlx(messages, definitions, cancel)
            return
        body: dict = {
            "model": "local",
            "messages": messages,
            "stream": True,
            "temperature": self.settings.models.temperature,
            "max_tokens": self.settings.models.max_tokens,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if definitions:
            body["tools"] = definitions
            body["tool_choice"] = "auto"
        body["messages"] = list(messages)
        while not cancel.is_set():
            formatted = self.client.post(
                self.settings.models.llm_url + "/apply-template",
                json=body,
                headers=self.auth.headers("llm"),
            )
            formatted.raise_for_status()
            tokens = self.client.post(
                self.settings.models.llm_url + "/tokenize",
                json={"content": formatted.json()["prompt"], "parse_special": True},
                headers=self.auth.headers("llm"),
            )
            tokens.raise_for_status()
            if (
                len(tokens.json()["tokens"]) + self.settings.models.max_tokens
                <= self.settings.models.context_length
            ):
                break
            self._drop_oldest_turn(body["messages"])
        if cancel.is_set():
            return
        try:
            with self.client.stream(
                "POST",
                self.settings.models.llm_url + "/v1/chat/completions",
                json=body,
                headers=self.auth.headers("llm"),
            ) as response:
                with self.lock:
                    self.response = response
                response.raise_for_status()
                for line in response.iter_lines():
                    if cancel.is_set():
                        return
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        return
                    obj = json.loads(payload)
                    if "error" in obj:
                        raise RuntimeError("LLM returned an inference error")
                    choices = obj.get("choices", [])
                    if choices:
                        yield choices[0].get("delta", {})
        except (httpx.HTTPError, OSError):
            if not cancel.is_set():
                raise
        finally:
            with self.lock:
                self.response = None

    def _mlx(self, messages: list[dict], definitions: list[dict], cancel: threading.Event):
        from mlx_lm import stream_generate
        from mlx_lm.sample_utils import make_sampler

        assert self.tokenizer is not None and self.model is not None
        # MLX's raw model stream is used for benchmark parity; native tool markers
        # are accumulated and parsed, never executed as Python.
        formatted = copy.deepcopy(messages)
        liquid = self.settings.models.llm.startswith("lfm-")
        for message in formatted:
            for call in message.get("tool_calls", []):
                if isinstance(call["function"]["arguments"], str):
                    call["function"]["arguments"] = json.loads(call["function"]["arguments"])
            if liquid and message.get("tool_calls"):
                expressions = [
                    c["function"]["name"]
                    + "("
                    + ", ".join(k + "=" + repr(v) for k, v in c["function"]["arguments"].items())
                    + ")"
                    for c in message["tool_calls"]
                ]
                message["content"] = (
                    (message.get("content") or "")
                    + "<|tool_call_start|>["
                    + ", ".join(expressions)
                    + "]<|tool_call_end|>"
                )
        prompt = self.tokenizer.apply_chat_template(
            formatted,
            tools=([d["function"] for d in definitions] if liquid else definitions) or None,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        while (
            len(self.tokenizer.encode(prompt, add_special_tokens=False))
            + self.settings.models.max_tokens
            > self.settings.models.context_length
        ):
            self._drop_oldest_turn(formatted)
            prompt = self.tokenizer.apply_chat_template(
                formatted,
                tools=([d["function"] for d in definitions] if liquid else definitions) or None,
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=False,
            )
        buffer = ""
        tool_mode = False
        first = True
        for chunk in stream_generate(
            self.model,
            self.tokenizer,
            prompt,
            max_tokens=self.settings.models.max_tokens,
            sampler=make_sampler(temp=self.settings.models.temperature),
        ):
            if cancel.is_set():
                return
            if first and chunk.text:
                first = False
                yield {"_first_token": True}
            buffer += chunk.text
            if not tool_mode and ("<|tool_call_start|>" in buffer or "<tool_call>" in buffer):
                tool_mode = True
            if not tool_mode and "<" not in buffer:
                yield {"content": buffer}
                buffer = ""
        if tool_mode:
            from .model_tool_parser import parse_calls

            for index, call in enumerate(parse_calls(buffer)):
                yield {
                    "tool_calls": [
                        {"index": index, "id": f"mlx-{index}", "type": "function", "function": call}
                    ]
                }
        elif buffer:
            yield {"content": buffer}

    @staticmethod
    def _drop_oldest_turn(messages: list[dict]) -> None:
        users = [i for i, message in enumerate(messages) if message["role"] == "user"]
        if len(users) < 2:
            raise ValueError("Current turn and tool results exceed the configured context window")
        del messages[users[0] : users[1]]

    def cancel(self):
        with self.lock:
            response = self.response
        if response is not None:
            # close() alone does not wake a blocked read in another thread on
            # macOS. Shut down this response's owned socket before closing it.
            stream = response.extensions.get("network_stream")
            sock = stream.get_extra_info("socket") if stream is not None else None
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            response.close()

    def close(self):
        self.cancel()
        self.client.close()
