"""
Ouroboros — LLM client.

Primary backend: MiniMax (api.minimaxi.com/anthropic) — enabled via MINIMAX_API_KEY env var.
Uses Anthropic-compatible /v1/messages API format for MiniMax.
Fallback backend: OpenRouter — used when MiniMax is unavailable or fails.

Contract: chat(), default_model(), available_models(), add_usage().

Model routing:
- 'minimax/' prefix → MiniMax Anthropic API (with OpenRouter fallback on failure)
- anything else → OpenRouter directly
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

DEFAULT_LIGHT_MODEL = "google/gemini-3-pro-preview"

# MiniMax backend — Anthropic-compatible endpoint
MINIMAX_BASE_URL = os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/anthropic")
MINIMAX_MODEL_PREFIX = "minimax/"

# OpenRouter fallback model when MiniMax fails
OPENROUTER_FALLBACK_MODEL = "anthropic/claude-sonnet-4.6"

# MiniMax pricing (USD per 1M tokens) — based on MiniMax official pricing
MINIMAX_PRICING = {
    "MiniMax-M2.5": (0.15, 0.60),   # input, output
    "MiniMax-M2": (0.10, 0.40),
}



def normalize_reasoning_effort(value: str, default: str = "medium") -> str:
    allowed = {"none", "minimal", "low", "medium", "high", "xhigh"}
    v = str(value or "").strip().lower()
    return v if v in allowed else default


def reasoning_rank(value: str) -> int:
    order = {"none": 0, "minimal": 1, "low": 2, "medium": 3, "high": 4, "xhigh": 5}
    return int(order.get(str(value or "").strip().lower(), 3))


def add_usage(total: Dict[str, Any], usage: Dict[str, Any]) -> None:
    """Accumulate usage from one LLM call into a running total."""
    for k in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens", "cache_write_tokens"):
        total[k] = int(total.get(k) or 0) + int(usage.get(k) or 0)
    if usage.get("cost"):
        total["cost"] = float(total.get("cost") or 0) + float(usage["cost"])




def _strip_cache_from_content(content: Any) -> Any:
    """Remove cache_control from content blocks."""
    if isinstance(content, list):
        return [
            {k: v for k, v in block.items() if k != "cache_control"}
            if isinstance(block, dict) else block
            for block in content
        ]
    return content

def fetch_openrouter_pricing() -> Dict[str, Tuple[float, float, float]]:
    """
    Fetch current pricing from OpenRouter API.

    Returns dict of {model_id: (input_per_1m, cached_per_1m, output_per_1m)}.
    Returns empty dict on failure.
    """
    try:
        import requests
    except ImportError:
        log.warning("requests not installed, cannot fetch pricing")
        return {}

    try:
        url = "https://openrouter.ai/api/v1/models"
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()

        data = resp.json()
        models = data.get("data", [])

        prefixes = ("anthropic/", "openai/", "google/", "meta-llama/", "x-ai/", "qwen/")

        pricing_dict = {}
        for model in models:
            model_id = model.get("id", "")
            if not model_id.startswith(prefixes):
                continue

            pricing = model.get("pricing", {})
            if not pricing or not pricing.get("prompt"):
                continue

            raw_prompt = float(pricing.get("prompt", 0))
            raw_completion = float(pricing.get("completion", 0))
            raw_cached_str = pricing.get("input_cache_read")
            raw_cached = float(raw_cached_str) if raw_cached_str else None

            prompt_price = round(raw_prompt * 1_000_000, 4)
            completion_price = round(raw_completion * 1_000_000, 4)
            if raw_cached is not None:
                cached_price = round(raw_cached * 1_000_000, 4)
            else:
                cached_price = round(prompt_price * 0.1, 4)

            if prompt_price > 1000 or completion_price > 1000:
                log.warning(f"Skipping {model_id}: prices seem wrong")
                continue

            pricing_dict[model_id] = (prompt_price, cached_price, completion_price)

        log.info(f"Fetched pricing for {len(pricing_dict)} models from OpenRouter")
        return pricing_dict

    except Exception as e:
        log.warning(f"Failed to fetch OpenRouter pricing: {e}")
        return {}




def _calculate_minimax_cost(prompt_tokens: int, completion_tokens: int, model: str) -> Optional[float]:
    """Calculate cost for MiniMax API call based on token usage and model pricing.
    
    Returns cost in USD, or None if model pricing not found.
    """
    lookup_model = model
    if lookup_model.startswith("minimax/"):
        lookup_model = lookup_model[len("minimax/"):]
    
    pricing = MINIMAX_PRICING.get(lookup_model)
    if pricing:
        prompt_price, completion_price = pricing
        prompt_cost = (prompt_tokens / 1_000_000) * prompt_price
        completion_cost = (completion_tokens / 1_000_000) * completion_price
        return round(prompt_cost + completion_cost, 6)
    return None


def _strip_cache_from_content(content: Any) -> Any:
    """Remove cache_control blocks from message content.
    
    MiniMax doesn't support cache_control, so we strip it.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return content
    
    result = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "cache_control":
            continue
        if isinstance(block, dict):
            cleaned = {k: v for k, v in block.items() if k != "cache_control"}
            if cleaned:
                result.append(cleaned)
        else:
            result.append(block)
    return result if result else None

class LLMClient:
    """LLM client with MiniMax as primary and OpenRouter as fallback.

    Primary: MiniMax (api.minimaxi.com/anthropic) — Anthropic messages API format.
    Fallback: OpenRouter — used when MiniMax is unavailable or fails.

    Model routing:
    - 'minimax/' prefix → MiniMax (with automatic OpenRouter fallback on error)
    - anything else → OpenRouter directly
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://openrouter.ai/api/v1",
    ):
        self._api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        self._base_url = base_url
        self._client = None

        # MiniMax backend
        self._minimax_api_key = os.environ.get("MINIMAX_API_KEY", "")
        self._minimax_base_url = os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/anthropic")

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(
                base_url=self._base_url,
                api_key=self._api_key,
                default_headers={
                    "HTTP-Referer": "https://colab.research.google.com/",
                    "X-Title": "Ouroboros",
                },
            )
        return self._client

    def _fetch_generation_cost(self, generation_id: str) -> Optional[float]:
        """Fetch cost from OpenRouter Generation API as fallback."""
        try:
            import requests
            url = f"{self._base_url.rstrip('/')}/generation?id={generation_id}"
            resp = requests.get(url, headers={"Authorization": f"Bearer {self._api_key}"}, timeout=5)
            if resp.status_code == 200:
                data = resp.json().get("data") or {}
                cost = data.get("total_cost") or data.get("usage", {}).get("cost")
                if cost is not None:
                    return float(cost)
            time.sleep(0.5)
            resp = requests.get(url, headers={"Authorization": f"Bearer {self._api_key}"}, timeout=5)
            if resp.status_code == 200:
                data = resp.json().get("data") or {}
                cost = data.get("total_cost") or data.get("usage", {}).get("cost")
                if cost is not None:
                    return float(cost)
        except Exception:
            log.debug("Failed to fetch generation cost from OpenRouter", exc_info=True)
        return None

    def _chat_minimax(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 16384,
        tool_choice: str = "auto",
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Send a chat request to MiniMax via Anthropic-compatible messages API.

        Converts OpenAI-style messages/tools to Anthropic format, sends to
        MINIMAX_BASE_URL/v1/messages, then converts response back to OpenAI format.
        """
        if not self._minimax_api_key:
            raise ValueError(
                "MINIMAX_API_KEY environment variable is not set. "
                "Please set it to use MiniMax models."
            )

        import requests as _req

        # --- Convert OpenAI messages → Anthropic format ---
        system_parts: List[str] = []
        anthropic_messages: List[Dict[str, Any]] = []

        _strip_cache = _strip_cache_from_content

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content")

            if role == "system":
                # Hoist system messages to top-level system field
                if isinstance(content, str):
                    system_parts.append(content)
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            system_parts.append(block.get("text", ""))
                        elif isinstance(block, str):
                            system_parts.append(block)
                continue

            if role == "tool":
                # OpenAI tool result → Anthropic user/tool_result
                anthropic_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.get("tool_call_id", ""),
                        "content": str(content or ""),
                    }],
                })
                continue

            if role == "assistant" and msg.get("tool_calls"):
                # Assistant with tool calls → Anthropic tool_use blocks
                blocks: List[Dict[str, Any]] = []
                clean_content = _strip_cache_from_content(content)
                if clean_content:
                    if isinstance(clean_content, str) and clean_content.strip():
                        blocks.append({"type": "text", "text": clean_content})
                    elif isinstance(clean_content, list):
                        blocks.extend(clean_content)
                for tc in msg["tool_calls"]:
                    args = tc.get("function", {}).get("arguments", "{}")
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except Exception:
                            args = {}
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": tc.get("function", {}).get("name", ""),
                        "input": args,
                    })
                anthropic_messages.append({"role": "assistant", "content": blocks})
                continue

            if role in ("user", "assistant"):
                clean_content = _strip_cache_from_content(content)
                if isinstance(clean_content, str):
                    anthropic_messages.append({"role": role, "content": clean_content})
                elif isinstance(clean_content, list):
                    anthropic_messages.append({"role": role, "content": clean_content})
                else:
                    anthropic_messages.append({"role": role, "content": str(clean_content or "")})

        # Merge consecutive same-role messages (Anthropic requires alternating)
        merged: List[Dict[str, Any]] = []
        for m in anthropic_messages:
            if merged and merged[-1]["role"] == m["role"]:
                prev = merged[-1]
                pc, cc = prev["content"], m["content"]
                if isinstance(pc, str) and isinstance(cc, str):
                    prev["content"] = pc + "\n" + cc
                elif isinstance(pc, list) and isinstance(cc, list):
                    prev["content"] = pc + cc
                elif isinstance(pc, list):
                    prev["content"] = pc + [{"type": "text", "text": str(cc)}]
                else:
                    prev["content"] = [{"type": "text", "text": str(pc)}] + (
                        cc if isinstance(cc, list) else [{"type": "text", "text": str(cc)}]
                    )
            else:
                merged.append({"role": m["role"], "content": m["content"]})

        # Build Anthropic request payload
        payload: Dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": merged,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)

        # Convert OpenAI tools → Anthropic tools format
        if tools:
            anthropic_tools = []
            for t in tools:
                if t.get("type") == "function":
                    fn = t.get("function", {})
                    anthropic_tools.append({
                        "name": fn.get("name", ""),
                        "description": fn.get("description", ""),
                        "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
                    })
            if anthropic_tools:
                payload["tools"] = anthropic_tools
                payload["tool_choice"] = {"type": "auto"}

        # Send request to MiniMax Anthropic endpoint
        url = f"{self._minimax_base_url.rstrip('/')}/v1/messages"
        headers = {
            "Authorization": f"Bearer {self._minimax_api_key}",
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }

        resp = _req.post(url, headers=headers, json=payload, timeout=120)
        try:
            # Check for rate limit first
            if resp.status_code == 429:
                self._minimax_rate_limited = True
                raise RuntimeError("MiniMax API rate limited (429)")
            
            resp.raise_for_status()
        except Exception as e:
            raise RuntimeError(f"MiniMax API error {resp.status_code}: {resp.text[:500]}") from e

        data = resp.json()

        # Parse Anthropic response → OpenAI-compatible message dict
        out_msg: Dict[str, Any] = {"role": "assistant", "content": None, "tool_calls": []}
        content_blocks = data.get("content") or []
        text_parts: List[str] = []
        tool_calls: List[Dict[str, Any]] = []

        for block in content_blocks:
            btype = block.get("type")
            if btype == "text":
                text_parts.append(block.get("text", ""))
            elif btype == "tool_use":
                args = block.get("input", {})
                tool_calls.append({
                    "id": block.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(args) if not isinstance(args, str) else args,
                    },
                })

        out_msg["content"] = "\n".join(text_parts) if text_parts else None
        if tool_calls:
            out_msg["tool_calls"] = tool_calls

        # Parse usage — Anthropic format uses input_tokens/output_tokens
        usage_raw = data.get("usage") or {}
        usage: Dict[str, Any] = {
            "prompt_tokens": int(usage_raw.get("input_tokens") or 0),
            "completion_tokens": int(usage_raw.get("output_tokens") or 0),
            "total_tokens": int(
                (usage_raw.get("input_tokens") or 0) + (usage_raw.get("output_tokens") or 0)
            ),
        }

        # Calculate cost based on MINIMAX_PRICING
        # Defensive: strip prefix if accidentally included
        lookup_model = model[len(MINIMAX_MODEL_PREFIX):] if model.startswith(MINIMAX_MODEL_PREFIX) else model
        pricing = MINIMAX_PRICING.get(lookup_model)
        if pricing:
            prompt_price, completion_price = pricing
            prompt_cost = (usage["prompt_tokens"] / 1_000_000) * prompt_price
            completion_cost = (usage["completion_tokens"] / 1_000_000) * completion_price
            usage["cost"] = round(prompt_cost + completion_cost, 6)
            usage["_model"] = f"minimax/{lookup_model}"
        else:
            log.warning(f"MiniMax pricing lookup failed for model: {lookup_model} (original: {model}). Available pricing keys: {list(MINIMAX_PRICING.keys())}")

        return out_msg, usage

    def _chat_openrouter(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        reasoning_effort: str,
        max_tokens: int,
        tool_choice: str,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Send a chat request to OpenRouter API."""
        client = self._get_client()
        effort = normalize_reasoning_effort(reasoning_effort)

        extra_body: Dict[str, Any] = {
            "reasoning": {"effort": effort, "exclude": True},
        }

        if model.startswith("anthropic/"):
            extra_body["provider"] = {
                "order": ["Anthropic"],
                "allow_fallbacks": False,
                "require_parameters": True,
            }

        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "extra_body": extra_body,
        }
        if tools:
            tools_with_cache = [t for t in tools]
            if tools_with_cache:
                last_tool = {**tools_with_cache[-1]}
                last_tool["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
                tools_with_cache[-1] = last_tool
            kwargs["tools"] = tools_with_cache
            kwargs["tool_choice"] = tool_choice

        resp = client.chat.completions.create(**kwargs)
        resp_dict = resp.model_dump()
        usage = resp_dict.get("usage") or {}
        choices = resp_dict.get("choices") or [{}]
        msg = (choices[0] if choices else {}).get("message") or {}

        if not usage.get("cached_tokens"):
            prompt_details = usage.get("prompt_tokens_details") or {}
            if isinstance(prompt_details, dict) and prompt_details.get("cached_tokens"):
                usage["cached_tokens"] = int(prompt_details["cached_tokens"])

        if not usage.get("cache_write_tokens"):
            prompt_details_for_write = usage.get("prompt_tokens_details") or {}
            if isinstance(prompt_details_for_write, dict):
                cache_write = (
                    prompt_details_for_write.get("cache_write_tokens")
                    or prompt_details_for_write.get("cache_creation_tokens")
                    or prompt_details_for_write.get("cache_creation_input_tokens")
                )
                if cache_write:
                    usage["cache_write_tokens"] = int(cache_write)

        if not usage.get("cost"):
            gen_id = resp_dict.get("id") or ""
            if gen_id:
                cost = self._fetch_generation_cost(gen_id)
                if cost is not None:
                    usage["cost"] = cost

        return msg, usage

    def chat(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        reasoning_effort: str = "medium",
        max_tokens: int = 16384,
        tool_choice: str = "auto",
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Single LLM call. Returns: (response_message_dict, usage_dict with cost).

        Routing:
        - 'minimax/' prefix → MiniMax Anthropic API, with automatic OpenRouter fallback on error.
        - Other models → OpenRouter directly.
        """
        if model.startswith(MINIMAX_MODEL_PREFIX):
            real_model = model[len(MINIMAX_MODEL_PREFIX):]
            try:
                return self._chat_minimax(real_model, messages, tools, max_tokens, tool_choice)
            except Exception as e:
                # CRITICAL: Do NOT fallback on rate limit - stop everything and alert owner
                if self._minimax_rate_limited or "rate limited" in str(e).lower():
                    log.critical("MiniMax is rate limited! Cannot fallback to OpenRouter - stopping to preserve budget.")
                    raise RuntimeError("MINIMAX_RATE_LIMITED: All tasks stopped. Owner alerted.")
                
                # For other errors, fallback to OpenRouter
                fallback_model = os.environ.get("OUROBOROS_FALLBACK_MODEL", OPENROUTER_FALLBACK_MODEL)
                log.warning("MiniMax call failed (%s), falling back to OpenRouter/%s", e, fallback_model)
                msg, usage = self._chat_openrouter(
                    fallback_model, messages, tools, reasoning_effort, max_tokens, tool_choice
                )
                usage["_fallback"] = True
                usage["_fallback_reason"] = str(e)
                return msg, usage

        return self._chat_openrouter(model, messages, tools, reasoning_effort, max_tokens, tool_choice)

    def vision_query(
        self,
        prompt: str,
        images: List[Dict[str, Any]],
        model: str = None,  # Use MiniMax if available, otherwise OpenRouter
        max_tokens: int = 1024,
        reasoning_effort: str = "low",
    ) -> Tuple[str, Dict[str, Any]]:
        # Auto-select model: MiniMax if available, otherwise fallback
        if model is None:
            if self._minimax_api_key:
                model = "MiniMax-M2.5"
            else:
                model = "anthropic/claude-sonnet-4.6"
        
        """
        Send a vision query to an LLM. Lightweight — no tools, no loop.

        Args:
            prompt: Text instruction for the model
            images: List of image dicts. Each dict must have either:
                - {"url": "https://..."} — for URL images
                - {"base64": "<b64>", "mime": "image/png"} — for base64 images
            model: VLM-capable model ID
            max_tokens: Max response tokens
            reasoning_effort: Effort level

        Returns:
            (text_response, usage_dict)
        """
        content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        for img in images:
            if "url" in img:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": img["url"]},
                })
            elif "base64" in img:
                mime = img.get("mime", "image/png")
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{img['base64']}"},
                })
            else:
                log.warning("vision_query: skipping image with unknown format: %s", list(img.keys()))

        messages = [{"role": "user", "content": content}]
        response_msg, usage = self.chat(
            messages=messages,
            model=model,
            tools=None,
            reasoning_effort=reasoning_effort,
            max_tokens=max_tokens,
        )
        text = response_msg.get("content") or ""
        return text, usage

    def default_model(self) -> str:
        """Return the default model.

        Priority:
        1. OUROBOROS_MODEL env var (explicit override)
        2. minimax/MiniMax-M2.5 if MINIMAX_API_KEY is set
        3. anthropic/claude-sonnet-4.6 (OpenRouter fallback)
        """
        explicit = os.environ.get("OUROBOROS_MODEL", "")
        if explicit:
            return explicit
        if self._minimax_api_key:
            return "minimax/MiniMax-M2.5"
        return "anthropic/claude-sonnet-4.6"

    def available_models(self) -> List[str]:
        """Return list of available model IDs."""
        models = [
            "anthropic/claude-sonnet-4.6",
            "anthropic/claude-opus-4",
            "anthropic/claude-haiku-3-5",
            "openai/gpt-4o",
            "openai/o3",
            "google/gemini-2.5-pro-preview",
            "google/gemini-3-pro-preview",
        ]
        if self._minimax_api_key:
            models.insert(0, "minimax/MiniMax-M2.5")
            models.insert(1, "minimax/MiniMax-M2")
        return models

    # ------------------------------------------------------------------
    # Pricing helpers
    # ------------------------------------------------------------------

    # Static pricing table (per 1M tokens): {model: (input, cached_input, output)}
    MODEL_PRICING: Dict[str, Tuple[float, float, float]] = {
        # Anthropic
        "anthropic/claude-opus-4": (15.0, 1.5, 75.0),
        "anthropic/claude-opus-4-5": (15.0, 1.5, 75.0),
        "anthropic/claude-sonnet-4.6": (3.0, 0.3, 15.0),
        "anthropic/claude-sonnet-4-5": (3.0, 0.3, 15.0),
        "anthropic/claude-sonnet-4": (3.0, 0.3, 15.0),
        "anthropic/claude-haiku-3-5": (0.8, 0.08, 4.0),
        "anthropic/claude-3-5-sonnet": (3.0, 0.3, 15.0),
        "anthropic/claude-3-5-haiku": (0.8, 0.08, 4.0),
        "anthropic/claude-3-opus": (15.0, 1.5, 75.0),
        # OpenAI
        "openai/gpt-4o": (2.5, 1.25, 10.0),
        "openai/gpt-4o-mini": (0.15, 0.075, 0.6),
        "openai/o3": (10.0, 2.5, 40.0),
        "openai/o3-mini": (1.1, 0.55, 4.4),
        "openai/o4-mini": (1.1, 0.55, 4.4),
        "openai/o1": (15.0, 7.5, 60.0),
        # Google
        "google/gemini-2.5-pro-preview": (1.25, 0.31, 10.0),
        "google/gemini-3-pro-preview": (1.25, 0.31, 10.0),
        "google/gemini-2.0-flash": (0.1, 0.025, 0.4),
        "google/gemini-2.5-flash-preview": (0.15, 0.0375, 0.6),
        "google/gemini-flash-1.5": (0.075, 0.01875, 0.3),
        # Meta / xAI / Qwen
        "meta-llama/llama-3.3-70b-instruct": (0.1, 0.025, 0.3),
        "x-ai/grok-3": (3.0, 0.75, 15.0),
        "x-ai/grok-3-mini": (0.3, 0.075, 0.5),
        "qwen/qwen3-235b-a22b": (0.14, 0.035, 0.6),
        # MiniMax (estimated — no public per-token pricing listed)
        "minimax/MiniMax-M2.5": (0.8, 0.2, 3.2),
        "minimax/MiniMax-M2": (0.4, 0.1, 1.6),
    }

    def estimate_cost(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cached_tokens: int = 0,
        live_pricing: Optional[Dict[str, Tuple[float, float, float]]] = None,
    ) -> float:
        """Estimate cost for a given number of tokens.

        Uses live_pricing if provided (from fetch_openrouter_pricing()),
        otherwise falls back to static MODEL_PRICING table.
        """
        pricing = (live_pricing or {}).get(model) or self.MODEL_PRICING.get(model)
        if not pricing:
            return 0.0

        input_price, cached_price, output_price = pricing
        non_cached = max(0, prompt_tokens - cached_tokens)
        cost = (
            (non_cached * input_price / 1_000_000)
            + (cached_tokens * cached_price / 1_000_000)
            + (completion_tokens * output_price / 1_000_000)
        )
        return round(cost, 8)
