"""Bedrock wrapper for reference stages.

Three properties that only matter at workload scale, and so are easy to leave
until they hurt:

1. **Prompt caching.** A long instruction block reused across dozens of calls
   is billed once at write cost and then at roughly a tenth for reads. The
   lever is `cache_control` on the shared prefix; it is worth an order of
   magnitude on a batched extraction and nothing at all on a single call.
2. **Bounded concurrency with backoff.** The applied Haiku quota here is 50
   requests/minute against a 10,000 default. Unbounded fan-out throttles
   immediately, and naive retries make it worse. A semaphore plus jittered
   exponential backoff is the whole fix.
3. **Structured output with retries.** Models return prose around JSON often
   enough that parsing must be defensive. Retry with the parse error fed back
   is far more effective than retrying the same prompt unchanged.
"""
from __future__ import annotations

import json
import random
import re
import threading
import time

from botocore.config import Config

from _aws import config, session

# Below the applied 50 req/min quota, leaving headroom for the retry budget.
MAX_CONCURRENCY = 6
MAX_ATTEMPTS = 5

# A cacheable prefix shorter than the model's minimum is accepted by the API
# and then silently ignored - no error, no warning, and usage simply reports
# zero cache activity. That is the worst failure shape available: the code
# looks correct, the cost saving never arrives, and nothing points at why.
#
# The number below is measured, not documented. Anthropic publishes a 2,048
# token minimum for Haiku, but bisecting Haiku 4.5 through a Bedrock
# application inference profile puts the real boundary at 4,096: a 4,082
# token prefix does not cache and a 4,887 token one does. Trusting the
# published figure buys a prompt that looks cached, bills in full, and is
# roughly a 10x overspend on a batched extraction.
#
# Re-measure when changing model or region rather than assuming this holds.
MIN_CACHEABLE_TOKENS = 4096
_warned_short_prefix = set()

_sem = threading.Semaphore(MAX_CONCURRENCY)
_client = None
_lock = threading.Lock()

# Rough running totals, printed by stages for cost visibility.
usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0,
         "cache_writes": 0, "cache_reads": 0, "throttles": 0}


def client():
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                _client = session().client(
                    "bedrock-runtime",
                    # boto3 retries would race our own backoff; we own it.
                    config=Config(retries={"max_attempts": 1}, read_timeout=120),
                )
    return _client


def _record(body: dict) -> None:
    u = body.get("usage", {}) or {}
    with _lock:
        usage["calls"] += 1
        usage["input_tokens"] += u.get("input_tokens", 0)
        usage["output_tokens"] += u.get("output_tokens", 0)
        usage["cache_writes"] += u.get("cache_creation_input_tokens", 0)
        usage["cache_reads"] += u.get("cache_read_input_tokens", 0)


def invoke(system_prefix: str, user_text: str, *, model_key: str = "model_profile_arn",
           max_tokens: int = 1024, cache_prefix: bool = True) -> str:
    """One model call, throttle-aware.

    `system_prefix` is the part reused across calls in a batch - marking it
    cacheable is what makes batched extraction affordable.
    """
    system = [{"type": "text", "text": system_prefix}]
    if cache_prefix:
        approx_tokens = len(system_prefix) // 4
        if approx_tokens < MIN_CACHEABLE_TOKENS:
            key = system_prefix[:60]
            if key not in _warned_short_prefix:
                _warned_short_prefix.add(key)
                print(f"WARNING: cacheable prefix is ~{approx_tokens:,} tokens, "
                      f"below the {MIN_CACHEABLE_TOKENS:,}-token minimum. "
                      f"cache_control will be ignored and every call billed in "
                      f"full. Lengthen the shared prefix or stop marking it.")
        system[0]["cache_control"] = {"type": "ephemeral"}

    payload = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user_text}],
    }

    delay = 1.0
    last_error = None
    for attempt in range(MAX_ATTEMPTS):
        with _sem:
            try:
                resp = client().invoke_model(
                    modelId=config()[model_key],
                    body=json.dumps(payload),
                )
                body = json.loads(resp["body"].read())
                _record(body)
                return body["content"][0]["text"]
            except Exception as exc:  # noqa: BLE001 - retry policy is the point
                name = type(exc).__name__
                last_error = exc
                throttled = "Throttl" in name or "TooManyRequests" in name
                if not throttled or attempt == MAX_ATTEMPTS - 1:
                    raise
                with _lock:
                    usage["throttles"] += 1
        # Jitter matters: without it, a fan-out retries in lockstep and
        # throttles again at exactly the same moment.
        time.sleep(delay + random.uniform(0, 0.4 * delay))
        delay *= 2
    raise last_error  # unreachable, kept for clarity


_JSON = re.compile(r"\{.*\}|\[.*\]", re.S)


def invoke_json(system_prefix: str, user_text: str, **kw):
    """Structured output with a repair round.

    On a parse failure the error is handed back to the model rather than the
    same prompt being retried unchanged - the second attempt then usually
    succeeds, because the model is told what was wrong.
    """
    text = invoke(system_prefix, user_text, **kw)
    try:
        return json.loads(_JSON.search(text).group(0))
    except Exception as exc:  # noqa: BLE001
        repair = (
            f"{user_text}\n\nYour previous reply could not be parsed as JSON "
            f"({exc}). Reply with valid JSON only - no prose, no code fences."
        )
        text = invoke(system_prefix, repair, **kw)
        return json.loads(_JSON.search(text).group(0))


def summary() -> str:
    cached = usage["cache_reads"]
    total_in = usage["input_tokens"] + usage["cache_reads"] + usage["cache_writes"]
    pct = (100.0 * cached / total_in) if total_in else 0.0
    return (f"{usage['calls']} calls | in {usage['input_tokens']:,} "
            f"out {usage['output_tokens']:,} | cache write {usage['cache_writes']:,} "
            f"read {usage['cache_reads']:,} ({pct:.0f}% of input served from cache) "
            f"| throttles {usage['throttles']}")
