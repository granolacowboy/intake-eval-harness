"""Deterministic assertions over MCP tool traces."""
from __future__ import annotations

import json
from typing import Any


def parse_positive_float(text: str | None, field: str) -> float | None:
    if text is None or not text.strip():
        return None
    value = float(text.strip())
    if value < 0:
        raise ValueError(f"{field} must be >= 0")
    return value


def parse_required_calls(parent: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for elem in parent.findall("./required_calls/call"):
        tool = (elem.attrib.get("tool") or "").strip()
        if not tool:
            raise ValueError("required_calls/call requires a tool attribute")
        args_elem = elem.find("arguments")
        arguments: dict[str, Any] | None = None
        if args_elem is not None and (args_elem.text or "").strip():
            parsed = json.loads((args_elem.text or "").strip())
            if not isinstance(parsed, dict):
                raise ValueError("required call arguments must be a JSON object")
            arguments = parsed
        calls.append({"tool": tool, "arguments": arguments})
    return calls


def parse_result_assertions(parent: Any) -> list[dict[str, str]]:
    assertions: list[dict[str, str]] = []
    for elem in parent.findall("./result_assertions/contains"):
        tool = (elem.attrib.get("tool") or "").strip()
        text = (elem.text or "").strip()
        if not tool or not text:
            raise ValueError("result_assertions/contains requires tool and text")
        assertions.append({"tool": tool, "contains": text})
    return assertions


def is_subset(expected: Any, actual: Any) -> bool:
    """Recursive subset match used for deterministic tool-argument assertions."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        return all(key in actual and is_subset(value, actual[key]) for key, value in expected.items())
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(expected) != len(actual):
            return False
        return all(is_subset(e, a) for e, a in zip(expected, actual))
    return expected == actual


def evaluate_extended_constraints(
    qa_pair: dict[str, Any],
    tool_trace: list[dict[str, Any]],
    total_duration: float | None = None,
) -> list[str]:
    violations: list[str] = []

    max_duration_s = qa_pair.get("max_duration_s")
    if max_duration_s is not None and total_duration is not None and total_duration > max_duration_s:
        violations.append(f"task duration exceeded: {total_duration:.3f}s > {max_duration_s:.3f}s")

    max_tool_duration_s = qa_pair.get("max_tool_duration_s")
    if max_tool_duration_s is not None:
        for event in tool_trace:
            if event.get("duration_s", 0.0) > max_tool_duration_s:
                violations.append(
                    f"tool duration exceeded: {event['name']} {event['duration_s']:.3f}s > {max_tool_duration_s:.3f}s"
                )

    for required in qa_pair.get("required_calls", []):
        candidates = [event for event in tool_trace if event.get("name") == required["tool"]]
        if required.get("arguments") is not None:
            candidates = [
                event for event in candidates
                if is_subset(required["arguments"], event.get("input", {}))
            ]
        if not candidates:
            detail = ""
            if required.get("arguments") is not None:
                detail = f" with arguments containing {json.dumps(required['arguments'], sort_keys=True)}"
            violations.append(f"required call not observed: {required['tool']}{detail}")

    for assertion in qa_pair.get("result_assertions", []):
        candidates = [
            event for event in tool_trace
            if event.get("name") == assertion["tool"]
            and assertion["contains"] in (event.get("result_text") or "")
        ]
        if not candidates:
            violations.append(
                f"required tool result text not observed: {assertion['tool']} contains {assertion['contains']!r}"
            )

    return violations
