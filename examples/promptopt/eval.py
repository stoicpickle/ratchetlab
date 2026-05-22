#!/usr/bin/env python3
"""
Evaluator for prompt optimization.

Editable artifact: system_prompt.md.
Protected reality: this evaluator plus dev/holdout JSONL sets.

By default this uses OpenAI's Responses API with Structured Outputs. Set:

  OPENAI_API_KEY=...

Optional env vars:

  RATCHET_OPENAI_MODEL=<model>
  RATCHET_PROMPTOPT_PROVIDER=mock   # offline smoke mode, not for real scoring
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
PROMPT = HERE / "system_prompt.md"
DEV_SET = HERE / "dev_set.jsonl"
HOLDOUT_SET = HERE / "holdout_set.jsonl"

REQUIRED_KEYS = ("customer_name", "requested_action", "urgency", "needs_human_review")
URGENCY_VALUES = {"low", "medium", "high"}
DEFAULT_MODEL = "gpt-5.4-mini"

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "customer_name": {"type": ["string", "null"]},
        "requested_action": {"type": ["string", "null"]},
        "urgency": {"type": "string", "enum": ["low", "medium", "high"]},
        "needs_human_review": {"type": "boolean"},
    },
    "required": list(REQUIRED_KEYS),
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def extract_response_text(payload: dict[str, Any]) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str):
        return output_text

    parts: list[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                parts.append(content["text"])
            if content.get("refusal"):
                raise RuntimeError(f"model_refusal: {content['refusal']}")
    if parts:
        return "".join(parts)
    raise RuntimeError("OpenAI response did not include output text")


def call_openai(system_prompt: str, user_input: str) -> dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required unless RATCHET_PROMPTOPT_PROVIDER=mock")

    model = os.environ.get("RATCHET_OPENAI_MODEL", DEFAULT_MODEL)
    body = {
        "model": model,
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "ticket_extraction",
                "strict": True,
                "schema": OUTPUT_SCHEMA,
            }
        },
        "max_output_tokens": 300,
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"openai_http_{exc.code}: {detail[:1000]}") from exc
    payload = json.loads(raw)
    return json.loads(extract_response_text(payload))


def mock_extract(user_input: str) -> dict[str, Any]:
    text = user_input.strip()
    name_match = re.search(r"\b([A-Z][a-z]+)\b(?= from\b| asked\b| called\b| says\b)", text)
    customer_name = name_match.group(1) if name_match else None

    lower = text.lower()
    if any(word in lower for word in ["urgent", "asap", "blocked", "down", "today", "safety", "payroll", "shipment held"]):
        urgency = "high"
    elif any(word in lower for word in ["no rush", "when you can", "next week", "fyi"]):
        urgency = "low"
    else:
        urgency = "medium"

    needs_human_review = urgency == "high" or any(word in lower for word in ["refund", "legal", "cancel", "escalate"])
    action = lower
    for prefix in ["please ", "customer says they want to ", "customer wants to ", "can you ", "could you "]:
        action = action.replace(prefix, "")
    return {
        "customer_name": customer_name,
        "requested_action": action.strip(" ."),
        "urgency": urgency,
        "needs_human_review": needs_human_review,
    }


def call_model(system_prompt: str, user_input: str) -> dict[str, Any]:
    provider = os.environ.get("RATCHET_PROMPTOPT_PROVIDER", "openai").lower().strip()
    if provider == "mock":
        return mock_extract(user_input)
    if provider != "openai":
        raise RuntimeError(f"Unsupported RATCHET_PROMPTOPT_PROVIDER: {provider}")
    return call_openai(system_prompt, user_input)


def normalize(value: Any) -> Any:
    if isinstance(value, str):
        return " ".join(value.lower().strip().split())
    if isinstance(value, dict):
        return {k: normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    return value


def validate_output(got: Any) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not isinstance(got, dict):
        return False, ["output_not_object"]
    missing = [key for key in REQUIRED_KEYS if key not in got]
    extra = [key for key in got if key not in REQUIRED_KEYS]
    if missing:
        errors.append("missing_keys:" + ",".join(missing))
    if extra:
        errors.append("extra_keys:" + ",".join(extra))
    if "customer_name" in got and got["customer_name"] is not None and not isinstance(got["customer_name"], str):
        errors.append("customer_name_type")
    if "requested_action" in got and got["requested_action"] is not None and not isinstance(got["requested_action"], str):
        errors.append("requested_action_type")
    if got.get("urgency") not in URGENCY_VALUES:
        errors.append("urgency_value")
    if not isinstance(got.get("needs_human_review"), bool):
        errors.append("needs_human_review_type")
    return not errors, errors


def field_score(got: dict[str, Any], expected: dict[str, Any]) -> float:
    correct = 0
    for key in REQUIRED_KEYS:
        if normalize(got.get(key)) == normalize(expected.get(key)):
            correct += 1
    return correct / len(REQUIRED_KEYS)


def evaluate_rows(system_prompt: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    scores: list[float] = []
    failures: list[dict[str, Any]] = []
    invalid_count = 0
    started = time.perf_counter()

    for row in rows:
        try:
            got = call_model(system_prompt, row["input"])
            valid, errors = validate_output(got)
            if not valid:
                invalid_count += 1
                score = 0.0
            else:
                score = field_score(got, row["expected"])
        except Exception as exc:
            got = {"error": repr(exc)}
            errors = ["exception"]
            invalid_count += 1
            score = 0.0
        scores.append(score)
        if score < 1.0:
            failures.append({"id": row["id"], "score": score, "errors": errors, "got": got, "expected": row["expected"]})

    accuracy = sum(scores) / max(1, len(scores))
    return {
        "accuracy": accuracy,
        "cases": len(rows),
        "invalid_count": invalid_count,
        "failures": failures[:5],
        "latency_ms": (time.perf_counter() - started) * 1000.0,
    }


def main() -> None:
    system_prompt = PROMPT.read_text(encoding="utf-8")
    dev = evaluate_rows(system_prompt, load_jsonl(DEV_SET))
    holdout = evaluate_rows(system_prompt, load_jsonl(HOLDOUT_SET))
    valid_json = dev["invalid_count"] == 0 and holdout["invalid_count"] == 0
    score = (0.75 * dev["accuracy"]) + (0.25 * holdout["accuracy"])
    print(json.dumps({
        "score": score,
        "accuracy": score,
        "dev_accuracy": dev["accuracy"],
        "holdout_accuracy": holdout["accuracy"],
        "cases": dev["cases"] + holdout["cases"],
        "model": os.environ.get("RATCHET_OPENAI_MODEL", DEFAULT_MODEL),
        "provider": os.environ.get("RATCHET_PROMPTOPT_PROVIDER", "openai"),
        "gates": {
            "valid_json": valid_json,
            "schema": valid_json,
        },
        "metrics": {
            "dev_latency_ms": dev["latency_ms"],
            "holdout_latency_ms": holdout["latency_ms"],
            "invalid_count": dev["invalid_count"] + holdout["invalid_count"],
        },
        "failures": {
            "dev": dev["failures"],
            "holdout": holdout["failures"][:3],
        },
    }))


if __name__ == "__main__":
    main()
