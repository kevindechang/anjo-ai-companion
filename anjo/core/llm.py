"""Shared Anthropic client with Bedrock support.

Three modes (picked at import time):
  1. Bearer token  — AWS_BEARER_TOKEN_BEDROCK is set → direct HTTP, no boto3 needed
  2. Bedrock/boto3 — CLAUDE_CODE_USE_BEDROCK=1 but no bearer token → AnthropicBedrock
  3. Standard API  — ANTHROPIC_API_KEY → anthropic.Anthropic
"""

from __future__ import annotations

import base64
import json
import os
import struct
import threading
import urllib.request

import anthropic

# ── Mode detection ────────────────────────────────────────────────────────────
_BEARER_TOKEN = os.environ.get("AWS_BEARER_TOKEN_BEDROCK", "")
USE_BEDROCK = os.environ.get("CLAUDE_CODE_USE_BEDROCK", "0") == "1"
_AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

if USE_BEDROCK:
    MODEL = os.environ.get("ANTHROPIC_MEDIUM_MODEL", "us.anthropic.claude-sonnet-4-6")
    MODEL_BACKGROUND = os.environ.get(
        "ANTHROPIC_SMALL_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    )
else:
    MODEL = "claude-sonnet-4-6"
    MODEL_BACKGROUND = "claude-haiku-4-5-20251001"


# ── Bearer-token Bedrock client ───────────────────────────────────────────────


def _bedrock_url(model: str, stream: bool) -> str:
    suffix = "invoke-with-response-stream" if stream else "invoke"
    return f"https://bedrock-runtime.{_AWS_REGION}.amazonaws.com/model/{model}/{suffix}"


def _normalize_system(system) -> str | list:
    """Strip cache_control from system blocks; return plain string."""
    if isinstance(system, str) or system is None:
        return system
    parts = []
    for block in system:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block["text"])
        elif isinstance(block, str):
            parts.append(block)
    return "\n\n".join(parts)


def _parse_eventstream(raw: bytes):
    """Parse AWS binary EventStream, yield decoded event dicts."""
    offset = 0
    while offset < len(raw):
        if offset + 12 > len(raw):
            break
        total_len = struct.unpack(">I", raw[offset : offset + 4])[0]
        headers_len = struct.unpack(">I", raw[offset + 4 : offset + 8])[0]
        if total_len < 16 or offset + total_len > len(raw):
            break
        payload_bytes = raw[offset + 12 + headers_len : offset + total_len - 4]
        if payload_bytes:
            try:
                wrapper = json.loads(payload_bytes)
                # Bedrock wraps the actual event JSON in {"bytes": "<base64>"}
                if "bytes" in wrapper:
                    inner = json.loads(base64.b64decode(wrapper["bytes"]))
                    yield inner
                else:
                    yield wrapper
            except (json.JSONDecodeError, Exception):
                pass
        offset += total_len


class _SimpleUsage:
    def __init__(self, input_tokens: int, output_tokens: int):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _SimpleContent:
    def __init__(self, text: str):
        self.text = text


class _SimpleMessage:
    def __init__(self, text: str, usage: _SimpleUsage):
        self.content = [_SimpleContent(text)]
        self.usage = usage


class _StreamContext:
    """Context manager matching the anthropic MessageStreamManager interface."""

    def __init__(
        self, token: str, model: str, max_tokens: int, system, messages: list, thinking=None, **_
    ):
        self._token = token
        self._model = model
        self._max_tokens = max_tokens
        self._system = system
        self._messages = messages
        self._thinking = thinking
        self._chunks: list[str] = []
        self._input_tok = 0
        self._output_tok = 0
        self._fetched = False

    def __enter__(self):
        self._fetch()
        return self

    def __exit__(self, *_):
        pass

    def _fetch(self):
        if self._fetched:
            return
        self._fetched = True

        body: dict = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": self._max_tokens,
            "messages": self._messages,
        }
        sys = _normalize_system(self._system)
        if sys:
            body["system"] = sys
        if self._thinking:
            body["thinking"] = self._thinking

        payload = json.dumps(body).encode()
        req = urllib.request.Request(
            _bedrock_url(self._model, stream=True),
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self._token}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read()

        for event in _parse_eventstream(raw):
            etype = event.get("type", "")
            if etype == "content_block_delta":
                delta = event.get("delta", {})
                if delta.get("type") == "text_delta":
                    text = delta.get("text", "")
                    if text:
                        self._chunks.append(text)
            elif etype == "message_start":
                usage = event.get("message", {}).get("usage", {})
                self._input_tok = usage.get("input_tokens", 0)
            elif etype == "message_delta":
                usage = event.get("usage", {})
                self._output_tok = usage.get("output_tokens", 0)

    @property
    def text_stream(self):
        yield from self._chunks

    def get_final_message(self):
        return _SimpleMessage(
            "".join(self._chunks), _SimpleUsage(self._input_tok, self._output_tok)
        )


class _BearerMessages:
    def __init__(self, token: str):
        self._token = token

    def create(self, model: str, max_tokens: int, system=None, messages=None, thinking=None, **_):
        body: dict = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "messages": messages or [],
        }
        sys = _normalize_system(system)
        if sys:
            body["system"] = sys
        if thinking:
            body["thinking"] = thinking

        payload = json.dumps(body).encode()
        req = urllib.request.Request(
            _bedrock_url(model, stream=False),
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self._token}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())

        text = data["content"][0]["text"] if data.get("content") else ""
        usage = data.get("usage", {})
        return _SimpleMessage(
            text, _SimpleUsage(usage.get("input_tokens", 0), usage.get("output_tokens", 0))
        )

    def stream(
        self, model: str, max_tokens: int, system=None, messages=None, thinking=None, **kwargs
    ):
        return _StreamContext(
            self._token, model, max_tokens, system, messages or [], thinking=thinking
        )


class _BearerClient:
    def __init__(self, token: str):
        self.messages = _BearerMessages(token)


# ── Boto3 Bedrock wrapper (strips incompatible params) ────────────────────────


class _BedrockWrappedMessages:
    """Wraps AnthropicBedrock.messages to strip params Bedrock doesn't support."""

    def __init__(self, inner):
        self._inner = inner

    def create(self, model, max_tokens, system=None, messages=None, thinking=None, **_):
        kwargs: dict = dict(
            model=model,
            max_tokens=max_tokens,
            system=_normalize_system(system) or anthropic.NOT_GIVEN,
            messages=messages or [],
        )
        if thinking:
            kwargs["thinking"] = thinking
        return self._inner.create(**kwargs)

    def stream(self, model, max_tokens, system=None, messages=None, thinking=None, **_):
        kwargs: dict = dict(
            model=model,
            max_tokens=max_tokens,
            system=_normalize_system(system) or anthropic.NOT_GIVEN,
            messages=messages or [],
        )
        if thinking:
            kwargs["thinking"] = thinking
        return self._inner.stream(**kwargs)


class _BedrockWrappedClient:
    def __init__(self, inner):
        self.messages = _BedrockWrappedMessages(inner.messages)


# ── Singleton factory ─────────────────────────────────────────────────────────

_client = None
_client_lock = threading.Lock()


def get_client():
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                if USE_BEDROCK and _BEARER_TOKEN:
                    _client = _BearerClient(_BEARER_TOKEN)
                elif USE_BEDROCK:
                    _client = _BedrockWrappedClient(
                        anthropic.AnthropicBedrock(aws_region=_AWS_REGION, max_retries=3)
                    )
                else:
                    api_key = os.environ.get("ANTHROPIC_API_KEY")
                    if not api_key:
                        raise RuntimeError("ANTHROPIC_API_KEY is not set in environment")
                    _client = anthropic.Anthropic(api_key=api_key, max_retries=3)
    return _client
