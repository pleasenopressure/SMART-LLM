from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

Message = Dict[str, str]


def _resolve_api_key(api_key_file: str, provider: str) -> str:
    path = Path(api_key_file)
    candidates = [path]
    if path.suffix != ".txt":
        candidates.append(Path(f"{api_key_file}.txt"))

    for candidate in candidates:
        if candidate.is_file():
            value = candidate.read_text(encoding="utf-8").strip()
            if value:
                return value

    env_candidates = [
        f"{provider.upper()}_API_KEY",
        "OPENAI_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "DEEPSEEK_API_KEY",
    ]
    for env_name in env_candidates:
        value = os.environ.get(env_name)
        if value and value.strip():
            return value.strip()

    raise FileNotFoundError(
        f"Could not find API key file '{api_key_file}' or any supported API key environment variable."
    )


def _normalize_provider(provider: str, model: str) -> str:
    normalized = (provider or "auto").strip().lower()
    if normalized != "auto":
        return normalized

    lowered_model = (model or "").strip().lower()
    if lowered_model.startswith("gemini") or "gemini" in lowered_model:
        return "gemini"
    if lowered_model.startswith("deepseek") or "deepseek" in lowered_model:
        return "deepseek"
    return "openai"


def _clean_text(text: str) -> str:
    stripped = text.strip()
    fenced = re.fullmatch(r"```(?:[a-zA-Z0-9_+-]+)?\s*(.*?)\s*```", stripped, flags=re.S)
    if fenced:
        return fenced.group(1).strip()
    return stripped


def _request_json(url: str, payload: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=180) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw)
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM request failed with HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"LLM request failed: {exc}") from exc


def _as_messages(prompt: Union[str, Sequence[Message]]) -> List[Message]:
    if isinstance(prompt, str):
        return [{"role": "user", "content": prompt}]
    return [dict(message) for message in prompt]


def _extract_openai_text(response: Dict[str, Any]) -> str:
    choices = response.get("choices", [])
    if not choices:
        raise RuntimeError(f"Unexpected OpenAI-compatible response: {response}")
    choice = choices[0]
    message = choice.get("message", {})
    text = message.get("content") or choice.get("text") or ""
    return _clean_text(text)


def _extract_gemini_text(response: Dict[str, Any]) -> str:
    candidates = response.get("candidates", [])
    if not candidates:
        raise RuntimeError(f"Unexpected Gemini response: {response}")
    content = candidates[0].get("content", {})
    parts = content.get("parts", [])
    text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
    return _clean_text(text)


def _messages_to_gemini(messages: Sequence[Message]) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    system_messages: List[str] = []
    contents: List[Dict[str, Any]] = []

    for message in messages:
        role = (message.get("role") or "user").lower()
        content = message.get("content", "")
        if role == "system":
            if content:
                system_messages.append(content)
            continue

        gemini_role = "user" if role == "user" else "model"
        contents.append({"role": gemini_role, "parts": [{"text": content}]})

    if not contents:
        contents.append({"role": "user", "parts": [{"text": ""}]})

    system_instruction = "\n\n".join(system_messages).strip() or None
    return system_instruction, contents


@dataclass
class LLMClient:
    provider: str
    model: str
    api_key: str
    base_url: Optional[str] = None

    @classmethod
    def from_config(
        cls,
        provider: str,
        model: str,
        api_key_file: str = "api_key",
        base_url: Optional[str] = None,
    ) -> "LLMClient":
        normalized_provider = _normalize_provider(provider, model)
        api_key = _resolve_api_key(api_key_file, normalized_provider)
        return cls(
            provider=normalized_provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
        )

    def generate(
        self,
        prompt: Union[str, Sequence[Message]],
        max_tokens: int = 128,
        temperature: float = 0,
        stop: Optional[Union[str, Sequence[str]]] = None,
        logprobs: int = 1,
        frequency_penalty: float = 0,
    ) -> Tuple[Dict[str, Any], str]:
        messages = _as_messages(prompt)

        if self.provider in {"openai", "deepseek"}:
            response = self._generate_openai_compatible(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stop=stop,
                frequency_penalty=frequency_penalty,
            )
            return response, _extract_openai_text(response)

        if self.provider == "gemini":
            response = self._generate_gemini(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stop=stop,
            )
            return response, _extract_gemini_text(response)

        raise ValueError(f"Unsupported LLM provider: {self.provider}")

    def _openai_base_url(self) -> str:
        if self.base_url:
            return self.base_url.rstrip("/")
        if self.provider == "deepseek":
            return "https://api.deepseek.com/v1"
        return "https://api.openai.com/v1"

    def _generate_openai_compatible(
        self,
        messages: Sequence[Message],
        max_tokens: int,
        temperature: float,
        stop: Optional[Union[str, Sequence[str]]],
        frequency_penalty: float,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if stop is not None:
            payload["stop"] = stop
        if frequency_penalty is not None:
            payload["frequency_penalty"] = frequency_penalty

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self._openai_base_url()}/chat/completions"
        return _request_json(url, payload, headers)

    def _generate_gemini(
        self,
        messages: Sequence[Message],
        max_tokens: int,
        temperature: float,
        stop: Optional[Union[str, Sequence[str]]],
    ) -> Dict[str, Any]:
        system_instruction, contents = _messages_to_gemini(messages)
        payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": temperature,
            },
        }
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        if stop is not None:
            if isinstance(stop, str):
                payload["generationConfig"]["stopSequences"] = [stop]
            else:
                payload["generationConfig"]["stopSequences"] = list(stop)

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )
        headers = {"Content-Type": "application/json"}
        return _request_json(url, payload, headers)
