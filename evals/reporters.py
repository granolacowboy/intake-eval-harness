"""Machine-readable evaluation evidence and regression helpers."""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


def write_json_evidence(path: Path, evidence: dict[str, Any]) -> None:
    path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_junit(path: Path, evidence: dict[str, Any]) -> None:
    results = evidence["results"]
    suite = ET.Element(
        "testsuite",
        {
            "name": "mcp-eval",
            "tests": str(len(results)),
            "failures": str(sum(1 for result in results if not result["passed"])),
            "time": f"{sum(result['total_duration'] for result in results):.6f}",
        },
    )
    props = ET.SubElement(suite, "properties")
    for key, value in evidence["provenance"].items():
        ET.SubElement(props, "property", {"name": str(key), "value": str(value)})

    for index, result in enumerate(results, start=1):
        case = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": "mcp-eval",
                "name": f"task-{index}",
                "time": f"{result['total_duration']:.6f}",
            },
        )
        if not result["passed"]:
            failure = ET.SubElement(case, "failure", {"message": "evaluation assertions failed"})
            problems = []
            if not result["answer_correct"]:
                problems.append(
                    f"answer mismatch: expected={result['expected']!r} actual={result['actual']!r}"
                )
            problems.extend(result["trace_violations"])
            failure.text = "\n".join(problems)
        system_out = ET.SubElement(case, "system-out")
        system_out.text = json.dumps(
            {
                "question": result["question"],
                "tool_trace": result["tool_trace"],
            },
            sort_keys=True,
        )

    tree = ET.ElementTree(suite)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def compare_baseline(baseline_path: Path, evidence: dict[str, Any]) -> dict[str, Any]:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline_summary = baseline.get("summary") or {}
    current_summary = evidence["summary"]

    baseline_accuracy = float(baseline_summary.get("accuracy", 0.0))
    current_accuracy = float(current_summary["accuracy"])
    baseline_trace_valid = int(baseline_summary.get("trace_valid", 0))
    current_trace_valid = int(current_summary["trace_valid"])

    return {
        "baseline_accuracy": baseline_accuracy,
        "current_accuracy": current_accuracy,
        "accuracy_delta": current_accuracy - baseline_accuracy,
        "baseline_trace_valid": baseline_trace_valid,
        "current_trace_valid": current_trace_valid,
        "trace_valid_delta": current_trace_valid - baseline_trace_valid,
        "regressed": (
            current_accuracy < baseline_accuracy
            or current_trace_valid < baseline_trace_valid
        ),
    }
