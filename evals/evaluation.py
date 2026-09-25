"""MCP Server Evaluation Harness.

Runs a fixed suite against an MCP server with Claude, scores the final answer,
and optionally enforces deterministic constraints over the observed tool trace.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from anthropic import Anthropic

try:
    from .assertions import (
        evaluate_extended_constraints,
        parse_positive_float,
        parse_required_calls,
        parse_result_assertions,
    )
    from .connections import create_connection
    from .reporters import compare_baseline, write_json_evidence, write_junit
except ImportError:
    from assertions import (
        evaluate_extended_constraints,
        parse_positive_float,
        parse_required_calls,
        parse_result_assertions,
    )
    from connections import create_connection
    from reporters import compare_baseline, write_json_evidence, write_junit


EVALUATION_PROMPT = """You are an AI assistant with access to tools.

When given a task, you MUST:
1. Use the available tools to complete the task
2. Provide summary of each step in your approach, wrapped in <summary> tags
3. Provide feedback on the tools provided, wrapped in <feedback> tags
4. Provide your final response, wrapped in <response> tags

Summary Requirements:
- In your <summary> tags, you must explain:
  - The steps you took to complete the task
  - Which tools you used, in what order, and why
  - The inputs you provided to each tool
  - The outputs you received from each tool
  - A summary for how you arrived at the response

Feedback Requirements:
- In your <feedback> tags, provide constructive feedback on the tools:
  - Comment on tool names: Are they clear and descriptive?
  - Comment on input parameters: Are they well-documented? Are required vs optional parameters clear?
  - Comment on descriptions: Do they accurately describe what the tool does?
  - Comment on any errors encountered during tool usage
  - Identify specific areas for improvement and explain WHY they would help

Response Requirements:
- Your response should be concise and directly address what was asked
- Always wrap your final response in <response> tags
- If you cannot solve the task return <response>NOT_FOUND</response>
- For numeric responses, provide just the number
- For IDs, provide just the ID
- For names or text, provide the exact text requested
- Your response should go last"""


def _child_texts(parent: ET.Element, path: str) -> list[str]:
    return [(elem.text or "").strip() for elem in parent.findall(path) if (elem.text or "").strip()]


def _exact(actual: str, expected: str) -> bool:
    return actual == expected


def _casefold(actual: str, expected: str) -> bool:
    return actual.casefold() == expected.casefold()


def _contains(actual: str, expected: str) -> bool:
    return expected in actual


def _regex(actual: str, expected: str) -> bool:
    return re.fullmatch(expected, actual) is not None


SCORERS: dict[str, Callable[[str, str], bool]] = {
    "exact": _exact,
    "casefold": _casefold,
    "contains": _contains,
    "regex": _regex,
}


def parse_evaluation_file(file_path: Path) -> list[dict[str, Any]]:
    """Parse an XML suite. Two-field question/answer suites remain valid."""
    try:
        tree = ET.parse(file_path)
        root = tree.getroot()
        evaluations: list[dict[str, Any]] = []

        for qa_pair in root.findall(".//qa_pair"):
            question_elem = qa_pair.find("question")
            answer_elem = qa_pair.find("answer")
            if question_elem is None or answer_elem is None:
                continue

            max_calls_elem = qa_pair.find("max_tool_calls")
            max_tool_calls = None
            if max_calls_elem is not None and (max_calls_elem.text or "").strip():
                max_tool_calls = int((max_calls_elem.text or "").strip())
                if max_tool_calls < 0:
                    raise ValueError("max_tool_calls must be >= 0")

            match_mode = answer_elem.attrib.get("match", "exact").strip().lower()
            if match_mode not in SCORERS:
                raise ValueError(
                    f"unknown answer matcher {match_mode!r}; expected one of {', '.join(sorted(SCORERS))}"
                )

            evaluations.append({
                "question": (question_elem.text or "").strip(),
                "answer": (answer_elem.text or "").strip(),
                "answer_match": match_mode,
                "required_tools": _child_texts(qa_pair, "./required_tools/tool"),
                "forbidden_tools": _child_texts(qa_pair, "./forbidden_tools/tool"),
                "tool_order": _child_texts(qa_pair, "./tool_order/tool"),
                "max_tool_calls": max_tool_calls,
                "max_duration_s": parse_positive_float(
                    qa_pair.findtext("max_duration_s"), "max_duration_s"
                ),
                "max_tool_duration_s": parse_positive_float(
                    qa_pair.findtext("max_tool_duration_s"), "max_tool_duration_s"
                ),
                "required_calls": parse_required_calls(qa_pair),
                "result_assertions": parse_result_assertions(qa_pair),
            })

        return evaluations
    except Exception as exc:
        print(f"Error parsing evaluation file {file_path}: {exc}")
        return []


def extract_xml_content(text: str | None, tag: str) -> str | None:
    if not text:
        return None
    pattern = rf"<{tag}>(.*?)</{tag}>"
    matches = re.findall(pattern, text, re.DOTALL)
    return matches[-1].strip() if matches else None


def score_answer(actual: str | None, expected: str, mode: str = "exact") -> bool:
    if actual is None:
        return False
    try:
        scorer = SCORERS[mode]
    except KeyError as exc:
        raise ValueError(f"Unknown scorer: {mode}") from exc
    return scorer(actual, expected)


def evaluate_trace_constraints(
    qa_pair: dict[str, Any], tool_trace: list[dict[str, Any]]
) -> list[str]:
    names = [event["name"] for event in tool_trace]
    violations: list[str] = []

    for required in qa_pair.get("required_tools", []):
        if required not in names:
            violations.append(f"required tool not called: {required}")

    for forbidden in qa_pair.get("forbidden_tools", []):
        if forbidden in names:
            violations.append(f"forbidden tool called: {forbidden}")

    expected_order = qa_pair.get("tool_order", [])
    if expected_order:
        cursor = 0
        for name in names:
            if cursor < len(expected_order) and name == expected_order[cursor]:
                cursor += 1
        if cursor != len(expected_order):
            violations.append("required tool order not observed: " + " -> ".join(expected_order))

    max_tool_calls = qa_pair.get("max_tool_calls")
    if max_tool_calls is not None and len(names) > max_tool_calls:
        violations.append(f"tool-call limit exceeded: {len(names)} > {max_tool_calls}")

    return violations


def _public_trace(
    tool_trace: list[dict[str, Any]],
    include_inputs: bool,
    include_results: bool,
) -> list[dict[str, Any]]:
    public: list[dict[str, Any]] = []
    for event in tool_trace:
        item = {
            "name": event["name"],
            "duration_s": round(event["duration_s"], 4),
            "ok": event["ok"],
        }
        if include_inputs:
            item["input"] = event["input"]
        if include_results:
            item["result"] = event.get("result_text")
        elif event.get("result_text") is not None:
            item["result_sha256"] = hashlib.sha256(
                event["result_text"].encode("utf-8")
            ).hexdigest()
        if event.get("error"):
            item["error"] = event["error"]
        public.append(item)
    return public


async def agent_loop(
    client: Anthropic,
    model: str,
    question: str,
    tools: list[dict[str, Any]],
    connection: Any,
    *,
    max_model_turns: int = 4,
    max_output_tokens: int = 4096,
    max_input_tokens: int | None = None,
) -> tuple[str | None, list[dict[str, Any]], str | None, dict[str, int]]:
    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
    tool_trace: list[dict[str, Any]] = []
    model_usage = {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "preflight_calls": 0,
        "max_preflight_input_tokens": 0,
    }

    async def preflight_input_limit() -> str | None:
        if max_input_tokens is None:
            return None
        counter = getattr(client.messages, "count_tokens", None)
        if counter is None:
            return (
                "input-token preflight unavailable in installed Anthropic SDK; "
                "refusing billable model call"
            )
        count = await asyncio.to_thread(
            counter,
            model=model,
            system=EVALUATION_PROMPT,
            messages=messages,
            tools=tools,
        )
        input_tokens = int(getattr(count, "input_tokens", 0) or 0)
        model_usage["preflight_calls"] += 1
        model_usage["max_preflight_input_tokens"] = max(
            model_usage["max_preflight_input_tokens"], input_tokens
        )
        if input_tokens > max_input_tokens:
            return (
                f"input token limit exceeded ({input_tokens} > {max_input_tokens}); "
                "evaluation stopped before billable model call"
            )
        return None

    def record_usage(response: Any) -> None:
        model_usage["calls"] += 1
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        for field in (
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        ):
            model_usage[field] += int(getattr(usage, field, 0) or 0)

    input_limit_violation = await preflight_input_limit()
    if input_limit_violation:
        return None, tool_trace, input_limit_violation, model_usage

    response = await asyncio.to_thread(
        client.messages.create,
        model=model,
        max_tokens=max_output_tokens,
        system=EVALUATION_PROMPT,
        messages=messages,
        tools=tools,
    )
    record_usage(response)
    messages.append({"role": "assistant", "content": response.content})

    while response.stop_reason == "tool_use":
        tool_blocks = [block for block in response.content if block.type == "tool_use"]
        if not tool_blocks:
            break

        tool_results: list[dict[str, Any]] = []
        for tool_use in tool_blocks:
            tool_start_ts = time.time()
            ok = True
            error = None
            try:
                tool_result = await connection.call_tool(tool_use.name, tool_use.input)
                tool_response = json.dumps(tool_result) if isinstance(tool_result, (dict, list)) else str(tool_result)
            except Exception as exc:
                ok = False
                error = str(exc)
                tool_response = f"Error executing tool {tool_use.name}: {exc}\n" + traceback.format_exc()

            tool_trace.append({
                "name": tool_use.name,
                "input": tool_use.input,
                "duration_s": time.time() - tool_start_ts,
                "ok": ok,
                "error": error,
                "result_text": tool_response,
            })
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use.id,
                "content": tool_response,
            })

        messages.append({"role": "user", "content": tool_results})
        if model_usage["calls"] >= max_model_turns:
            return (
                None,
                tool_trace,
                (
                    f"model turn limit reached ({max_model_turns}); "
                    "evaluation stopped before another billable model call"
                ),
                model_usage,
            )

        input_limit_violation = await preflight_input_limit()
        if input_limit_violation:
            return None, tool_trace, input_limit_violation, model_usage

        response = await asyncio.to_thread(
            client.messages.create,
            model=model,
            max_tokens=max_output_tokens,
            system=EVALUATION_PROMPT,
            messages=messages,
            tools=tools,
        )
        record_usage(response)
        messages.append({"role": "assistant", "content": response.content})

    response_text = "\n".join(block.text for block in response.content if hasattr(block, "text")) or None
    return response_text, tool_trace, None, model_usage


async def evaluate_single_task(
    client: Anthropic,
    model: str,
    qa_pair: dict[str, Any],
    tools: list[dict[str, Any]],
    connection: Any,
    task_index: int,
    include_tool_inputs: bool = False,
    include_tool_results: bool = False,
    max_model_turns: int = 4,
    max_output_tokens: int = 4096,
    max_input_tokens: int | None = None,
) -> dict[str, Any]:
    start_time = time.time()
    print(f"Task {task_index + 1}: {qa_pair['question']}")
    response, tool_trace, model_limit_violation, model_usage = await agent_loop(
        client,
        model,
        qa_pair["question"],
        tools,
        connection,
        max_model_turns=max_model_turns,
        max_output_tokens=max_output_tokens,
        max_input_tokens=max_input_tokens,
    )

    response_value = extract_xml_content(response, "response")
    summary = extract_xml_content(response, "summary")
    feedback = extract_xml_content(response, "feedback")
    answer_correct = score_answer(response_value, qa_pair["answer"], qa_pair.get("answer_match", "exact"))
    total_duration = time.time() - start_time
    trace_violations = evaluate_trace_constraints(qa_pair, tool_trace)
    trace_violations.extend(
        evaluate_extended_constraints(qa_pair, tool_trace, total_duration)
    )
    if model_limit_violation:
        trace_violations.append(model_limit_violation)
    trace_valid = not trace_violations

    return {
        "question": qa_pair["question"],
        "expected": qa_pair["answer"],
        "actual": response_value,
        "answer_match": qa_pair.get("answer_match", "exact"),
        "answer_correct": answer_correct,
        "trace_valid": trace_valid,
        "trace_violations": trace_violations,
        "score": int(answer_correct and trace_valid),
        "passed": bool(answer_correct and trace_valid),
        "total_duration": total_duration,
        "tool_trace": _public_trace(
            tool_trace, include_tool_inputs, include_tool_results
        ),
        "num_tool_calls": len(tool_trace),
        "model_usage": model_usage,
        "summary": summary,
        "feedback": feedback,
    }


def detect_harness_revision() -> str:
    if os.getenv("GITHUB_SHA"):
        return os.environ["GITHUB_SHA"]
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


REPORT_HEADER = """
# Evaluation Report

## Provenance

- **Generated (UTC)**: {generated_at}
- **Model**: `{model}`
- **Suite SHA-256**: `{suite_sha256}`
- **Harness revision**: `{harness_revision}`
- **Server revision**: `{server_revision}`
- **Run label**: {run_label}
- **Max model turns per task**: {max_model_turns}
- **Max input tokens per model call**: {max_input_tokens}
- **Max output tokens per model call**: {max_output_tokens}

## Summary

- **Passing tasks**: {correct}/{total} ({accuracy:.1f}%)
- **Answer-correct tasks**: {answer_correct}/{total}
- **Trace-valid tasks**: {trace_valid}/{total}
- **Average Task Duration**: {average_duration_s:.2f}s
- **Average Tool Calls per Task**: {average_tool_calls:.2f}
- **Total Tool Calls**: {total_tool_calls}
- **Total Model Calls**: {total_model_calls}
- **Input Tokens**: {total_input_tokens}
- **Output Tokens**: {total_output_tokens}

A task passes only when its answer matcher succeeds **and** every configured
tool-trace constraint is satisfied.

---
"""

TASK_TEMPLATE = """
### Task {task_num}

**Question**: {question}
**Ground Truth Answer**: `{expected_answer}`
**Answer Matcher**: `{answer_match}`
**Actual Answer**: `{actual_answer}`
**Answer Correct**: {answer_correct_indicator}
**Trace Valid**: {trace_valid_indicator}
**Overall Pass**: {correct_indicator}
**Trace Violations**: {trace_violations}
**Duration**: {total_duration:.2f}s
**Tool Trace**:

```json
{tool_trace}
```

**Summary**
{summary}

**Feedback**
{feedback}

---
"""


async def run_evaluation(
    eval_path: Path,
    connection: Any,
    model: str = "claude-sonnet-4-6",
    *,
    server_revision: str = "unknown",
    run_label: str = "unspecified",
    include_tool_inputs: bool = False,
    include_tool_results: bool = False,
    max_model_turns: int = 4,
    max_output_tokens: int = 4096,
    max_input_tokens: int | None = None,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    print("🚀 Starting Evaluation")
    client = Anthropic()
    tools = await connection.list_tools()
    print(f"📋 Loaded {len(tools)} tools from MCP server")

    qa_pairs = parse_evaluation_file(eval_path)
    if not qa_pairs:
        raise ValueError("evaluation suite contains no valid qa_pair entries")
    print(f"📋 Loaded {len(qa_pairs)} evaluation tasks")

    results = []
    for i, qa_pair in enumerate(qa_pairs):
        print(f"Processing task {i + 1}/{len(qa_pairs)}")
        results.append(await evaluate_single_task(
            client, model, qa_pair, tools, connection, i,
            include_tool_inputs=include_tool_inputs,
            include_tool_results=include_tool_results,
            max_model_turns=max_model_turns,
            max_output_tokens=max_output_tokens,
            max_input_tokens=max_input_tokens,
        ))

    generated_at = datetime.now(timezone.utc).isoformat()
    suite_sha256 = hashlib.sha256(eval_path.read_bytes()).hexdigest()
    harness_revision = detect_harness_revision()

    correct = sum(r["score"] for r in results)
    answer_correct = sum(int(r["answer_correct"]) for r in results)
    trace_valid = sum(int(r["trace_valid"]) for r in results)
    accuracy = (correct / len(results)) * 100
    average_duration_s = sum(r["total_duration"] for r in results) / len(results)
    average_tool_calls = sum(r["num_tool_calls"] for r in results) / len(results)
    total_tool_calls = sum(r["num_tool_calls"] for r in results)
    total_model_calls = sum(r["model_usage"]["calls"] for r in results)
    total_input_tokens = sum(r["model_usage"]["input_tokens"] for r in results)
    total_output_tokens = sum(r["model_usage"]["output_tokens"] for r in results)
    total_cache_creation_input_tokens = sum(
        r["model_usage"]["cache_creation_input_tokens"] for r in results
    )
    total_cache_read_input_tokens = sum(
        r["model_usage"]["cache_read_input_tokens"] for r in results
    )

    report = REPORT_HEADER.format(
        generated_at=generated_at,
        model=model,
        suite_sha256=suite_sha256,
        harness_revision=harness_revision,
        server_revision=server_revision,
        run_label=run_label,
        correct=correct,
        total=len(results),
        accuracy=accuracy,
        answer_correct=answer_correct,
        trace_valid=trace_valid,
        average_duration_s=average_duration_s,
        average_tool_calls=average_tool_calls,
        total_tool_calls=total_tool_calls,
        total_model_calls=total_model_calls,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        max_model_turns=max_model_turns,
        max_input_tokens=max_input_tokens if max_input_tokens is not None else "not set",
        max_output_tokens=max_output_tokens,
    )

    report += "".join(
        TASK_TEMPLATE.format(
            task_num=i + 1,
            question=qa_pair["question"],
            expected_answer=qa_pair["answer"],
            answer_match=result["answer_match"],
            actual_answer=result["actual"] or "N/A",
            answer_correct_indicator="✅" if result["answer_correct"] else "❌",
            trace_valid_indicator="✅" if result["trace_valid"] else "❌",
            correct_indicator="✅" if result["score"] else "❌",
            trace_violations="; ".join(result["trace_violations"]) if result["trace_violations"] else "None",
            total_duration=result["total_duration"],
            tool_trace=json.dumps(result["tool_trace"], indent=2),
            summary=result["summary"] or "N/A",
            feedback=result["feedback"] or "N/A",
        )
        for i, (qa_pair, result) in enumerate(zip(qa_pairs, results))
    )

    metrics = {
        "passing": correct,
        "total": len(results),
        "accuracy": accuracy,
        "answer_correct": answer_correct,
        "trace_valid": trace_valid,
        "average_duration_s": average_duration_s,
        "average_tool_calls": average_tool_calls,
        "total_tool_calls": total_tool_calls,
        "total_model_calls": total_model_calls,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_cache_creation_input_tokens": total_cache_creation_input_tokens,
        "total_cache_read_input_tokens": total_cache_read_input_tokens,
    }
    evidence = {
        "schema_version": "1.0",
        "provenance": {
            "generated_at": generated_at,
            "model": model,
            "suite_sha256": suite_sha256,
            "harness_revision": harness_revision,
            "server_revision": server_revision,
            "run_label": run_label,
            "max_model_turns": max_model_turns,
            "max_input_tokens": max_input_tokens,
            "max_output_tokens": max_output_tokens,
        },
        "summary": metrics,
        "results": results,
    }
    return report, metrics, evidence


def parse_headers(header_list: list[str] | None) -> dict[str, str]:
    headers = {}
    if not header_list:
        return headers
    for header in header_list:
        if ":" in header:
            key, value = header.split(":", 1)
            headers[key.strip()] = value.strip()
        else:
            print(f"Warning: Ignoring malformed header: {header}")
    return headers


def parse_env_vars(env_list: list[str] | None) -> dict[str, str]:
    env = {}
    if not env_list:
        return env
    for env_var in env_list:
        if "=" in env_var:
            key, value = env_var.split("=", 1)
            env[key.strip()] = value.strip()
        else:
            print(f"Warning: Ignoring malformed environment variable: {env_var}")
    return env


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate MCP servers with answer and tool-trace assertions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("eval_file", type=Path, help="Path to evaluation XML file")
    parser.add_argument("-t", "--transport", choices=["stdio", "sse", "http"], default="stdio")
    parser.add_argument("-m", "--model", default="claude-sonnet-4-6", help="Claude model identifier")

    stdio_group = parser.add_argument_group("stdio options")
    stdio_group.add_argument("-c", "--command", help="Command to run MCP server")
    stdio_group.add_argument("-a", "--args", nargs="+", help="Arguments for the command")
    stdio_group.add_argument("-e", "--env", nargs="+", help="Environment variables in KEY=VALUE format")

    remote_group = parser.add_argument_group("sse/http options")
    remote_group.add_argument("-u", "--url", help="MCP server URL")
    remote_group.add_argument("-H", "--header", nargs="+", dest="headers", help="HTTP headers in 'Key: Value' format")

    parser.add_argument("-o", "--output", type=Path, help="Output Markdown report")
    parser.add_argument("--server-revision", default="unknown", help="Commit/tag/digest of evaluated server")
    parser.add_argument("--run-label", default="unspecified", help="Human-readable experiment label")
    parser.add_argument("--include-tool-inputs", action="store_true", help="Include tool inputs in report")
    parser.add_argument("--include-tool-results", action="store_true", help="Include raw tool results in report/evidence")
    parser.add_argument("--json-output", type=Path, help="Write machine-readable JSON evidence")
    parser.add_argument("--junit-output", type=Path, help="Write JUnit XML evidence")
    parser.add_argument("--baseline", type=Path, help="Compare against a prior JSON evidence file")
    parser.add_argument("--fail-on-regression", action="store_true", help="Fail if accuracy or trace-valid count regresses from --baseline")
    parser.add_argument("--fail-under", type=float, metavar="PERCENT", help="Fail if overall pass percentage is lower")
    parser.add_argument(
        "--max-model-turns",
        type=int,
        default=4,
        help="Maximum billable model calls per task before failing closed (default: 4)",
    )
    parser.add_argument(
        "--max-input-tokens",
        type=int,
        default=None,
        help="Preflight maximum input tokens per model call; fails closed before generation",
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=4096,
        help="Maximum output tokens requested from each model call (default: 4096)",
    )

    args = parser.parse_args()

    if not args.eval_file.exists():
        print(f"Error: Evaluation file not found: {args.eval_file}")
        return 1
    if args.fail_under is not None and not 0 <= args.fail_under <= 100:
        print("Error: --fail-under must be between 0 and 100")
        return 2
    if args.fail_on_regression and args.baseline is None:
        print("Error: --fail-on-regression requires --baseline")
        return 2
    if args.baseline is not None and not args.baseline.exists():
        print(f"Error: baseline evidence not found: {args.baseline}")
        return 2
    if args.max_model_turns < 1:
        print("Error: --max-model-turns must be at least 1")
        return 2
    if args.max_input_tokens is not None and args.max_input_tokens < 1:
        print("Error: --max-input-tokens must be at least 1")
        return 2
    if args.max_output_tokens < 1:
        print("Error: --max-output-tokens must be at least 1")
        return 2

    try:
        connection = create_connection(
            transport=args.transport,
            command=args.command,
            args=args.args,
            env=parse_env_vars(args.env) or None,
            url=args.url,
            headers=parse_headers(args.headers) or None,
        )
    except ValueError as exc:
        print(f"Error: {exc}")
        return 1

    print(f"🔗 Connecting to MCP server via {args.transport}...")
    async with connection:
        print("✅ Connected successfully")
        try:
            report, metrics, evidence = await run_evaluation(
                args.eval_file,
                connection,
                args.model,
                server_revision=args.server_revision,
                run_label=args.run_label,
                include_tool_inputs=args.include_tool_inputs,
                include_tool_results=args.include_tool_results,
                max_model_turns=args.max_model_turns,
                max_output_tokens=args.max_output_tokens,
                max_input_tokens=args.max_input_tokens,
            )
        except ValueError as exc:
            print(f"Error: {exc}")
            return 1

    if args.output:
        args.output.write_text(report, encoding="utf-8")
        print(f"\n✅ Report saved to {args.output}")
    else:
        print("\n" + report)

    if args.json_output:
        write_json_evidence(args.json_output, evidence)
        print(f"✅ JSON evidence saved to {args.json_output}")
    if args.junit_output:
        write_junit(args.junit_output, evidence)
        print(f"✅ JUnit evidence saved to {args.junit_output}")

    regression = None
    if args.baseline:
        regression = compare_baseline(args.baseline, evidence)
        print(
            "Baseline comparison: "
            f"accuracy Δ {regression['accuracy_delta']:+.1f} points; "
            f"trace-valid Δ {regression['trace_valid_delta']:+d}"
        )
        evidence["baseline_comparison"] = regression
        if args.json_output:
            write_json_evidence(args.json_output, evidence)

    if args.fail_on_regression and regression and regression["regressed"]:
        print("❌ Regression gate failed")
        return 4

    if args.fail_under is not None and metrics["accuracy"] < args.fail_under:
        print(f"❌ Quality gate failed: {metrics['accuracy']:.1f}% < {args.fail_under:.1f}%")
        return 3
    return 0


def cli() -> None:
    raise SystemExit(asyncio.run(main()))


if __name__ == "__main__":
    cli()
