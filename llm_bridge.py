#!/usr/bin/env python3
"""
IdeaFlow LLM Bridge — OpenRouter backend for the NOVELTY_LLM_CLASSIFY_FAST_CMD
and NOVELTY_LLM_REASONING_LARGE_CMD env vars expected by the IdeaSpark pipeline.

Protocol (matching the ResearchStudio contract):
  Reads stdin for:
    <<SYSTEM>>
    <system prompt>
    <<USER>>
    <user message>

  Returns JSON on stdout.

Usage (classify_fast — small model, 120s timeout):
  export NOVELTY_LLM_CLASSIFY_FAST_CMD="python3 /abs/path/llm_bridge.py --mode classify-fast"
  echo '<<SYSTEM>>
  You are a classifier...
  <<USER>>
  Classify this...
  ' | $NOVELTY_LLM_CLASSIFY_FAST_CMD

Usage (reasoning_large — big model, 300s timeout):
  export NOVELTY_LLM_REASONING_LARGE_CMD="python3 /abs/path/llm_bridge.py --mode reasoning-large"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _resolve_api_key() -> str:
    """Get OPENROUTER_API_KEY from env, falling back to /home/enigma/.kinox/env."""
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if key:
        return key
    kinox = "/home/enigma/.kinox/env"
    if os.path.isfile(kinox):
        for line in open(kinox):
            if line.startswith("OPENROUTER_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


OPENROUTER_API_KEY = _resolve_api_key()
OPENROUTER_BASE = "https://openrouter.ai/api/v1/chat/completions"

# Model mapping — classify_fast uses a cheap model, reasoning_large uses the
# primary model. Tune these to your budget/quality tradeoff.
DEFAULT_FAST_MODEL = "deepseek/deepseek-v4-flash"  # shared primary model (reliable JSON output)
DEFAULT_LARGE_MODEL = "deepseek/deepseek-v4-flash"  # primary reasoning model

# HTTP headers for OpenRouter
HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
    "HTTP-Referer": "https://github.com/nousresearch/hermes",
    "X-Title": "IdeaFlow",
}


# ---------------------------------------------------------------------------
# Parse stdin
# ---------------------------------------------------------------------------

def parse_stdin() -> tuple[str, str]:
    """Parse <<SYSTEM>>...<<USER>>... format from stdin.

    Returns (system_prompt, user_message).
    """
    raw = sys.stdin.read()
    if not raw:
        print("ERROR: empty stdin", file=sys.stderr)
        sys.exit(1)

    # Split on <<SYSTEM>> and <<USER>> markers
    parts = raw.split("<<SYSTEM>>")
    if len(parts) < 2:
        print("ERROR: missing <<SYSTEM>> marker in stdin", file=sys.stderr)
        sys.exit(1)
    rest = parts[-1]

    user_parts = rest.split("<<USER>>")
    if len(user_parts) < 2:
        print("ERROR: missing <<USER>> marker in stdin", file=sys.stderr)
        sys.exit(1)

    system = user_parts[0].strip()
    user = "<<USER>>".join(user_parts[1:]).strip()

    return system, user


# ---------------------------------------------------------------------------
# OpenRouter call
# ---------------------------------------------------------------------------

def call_openrouter(
    system: str,
    user: str,
    model: str,
    timeout: int = 120,
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> dict[str, Any]:
    """Call OpenRouter and return the parsed JSON response."""
    if not OPENROUTER_API_KEY:
        print("ERROR: OPENROUTER_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        # response_format omitted — OpenRouter models vary in JSON-mode support
    }

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        OPENROUTER_BASE,
        data=data,
        headers=HEADERS,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        print(f"ERROR: OpenRouter HTTP {e.code}: {err_body[:500]}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"ERROR: OpenRouter URL error: {e.reason}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: OpenRouter call failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Extract content
    choices = result.get("choices", [])
    if not choices:
        print(f"ERROR: no choices in response: {json.dumps(result)[:300]}", file=sys.stderr)
        sys.exit(1)

    content = choices[0].get("message", {}).get("content", "")
    if not content:
        print("ERROR: empty content in response", file=sys.stderr)
        sys.exit(1)

    # Parse JSON from content (handle code fences)
    content = content.strip()
    if content.startswith("```"):
        # Strip code fences
        lines = content.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines).strip()

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # Not JSON — return as raw text
        return {"raw": content}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="IdeaFlow LLM Bridge")
    parser.add_argument(
        "--mode",
        choices=["classify-fast", "reasoning-large"],
        default="reasoning-large",
        help="Which model tier to use",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override model name (overrides --mode defaults)",
    )
    args = parser.parse_args()

    # Resolve model
    if args.model:
        model = args.model
    elif args.mode == "classify-fast":
        model = os.environ.get("IDEAS_FLOW_FAST_MODEL", DEFAULT_FAST_MODEL)
    else:
        model = os.environ.get("IDEAS_FLOW_LARGE_MODEL", DEFAULT_LARGE_MODEL)

    # Timeout
    timeout = int(os.environ.get("IDEAS_FLOW_TIMEOUT", "180"))

    # Parse input
    system, user = parse_stdin()

    # Call API
    result = call_openrouter(system, user, model, timeout=timeout)

    # Output JSON
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()