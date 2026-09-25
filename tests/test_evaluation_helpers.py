from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evals"))

from assertions import evaluate_extended_constraints, is_subset  # noqa: E402
from evaluation import (  # noqa: E402
    evaluate_trace_constraints,
    extract_xml_content,
    parse_env_vars,
    parse_evaluation_file,
    parse_headers,
    score_answer,
)
from reporters import compare_baseline, write_junit  # noqa: E402


def test_extract_xml_content_returns_last_matching_tag():
    text = "<response>first</response> noise <response>final</response>"
    assert extract_xml_content(text, "response") == "final"


def test_extract_xml_content_returns_none_when_missing():
    assert extract_xml_content("plain text", "response") is None
    assert extract_xml_content(None, "response") is None


def test_parse_evaluation_file_is_backwards_compatible(tmp_path):
    suite = tmp_path / "suite.xml"
    suite.write_text(
        """<evaluation>
        <qa_pair><question>  How many?  </question><answer>  3  </answer></qa_pair>
        </evaluation>""",
        encoding="utf-8",
    )
    assert parse_evaluation_file(suite) == [{
        "question": "How many?",
        "answer": "3",
        "answer_match": "exact",
        "required_tools": [],
        "forbidden_tools": [],
        "tool_order": [],
        "max_tool_calls": None,
        "max_duration_s": None,
        "max_tool_duration_s": None,
        "required_calls": [],
        "result_assertions": [],
    }]


def test_parse_evaluation_file_reads_trace_constraints_and_matcher(tmp_path):
    suite = tmp_path / "suite.xml"
    suite.write_text(
        """<evaluation>
        <qa_pair>
          <question>Check it.</question>
          <answer match="casefold">CLEARED</answer>
          <required_tools><tool>check_conflicts</tool></required_tools>
          <forbidden_tools><tool>write_record</tool></forbidden_tools>
          <tool_order><tool>lookup</tool><tool>check_conflicts</tool></tool_order>
          <max_tool_calls>3</max_tool_calls>
          <max_duration_s>5.5</max_duration_s>
          <max_tool_duration_s>2.0</max_tool_duration_s>
          <required_calls>
            <call tool="check_conflicts">
              <arguments>{"party_names":["Acme Co"]}</arguments>
            </call>
          </required_calls>
          <result_assertions>
            <contains tool="write_record">conflicts gate</contains>
          </result_assertions>
        </qa_pair>
        </evaluation>""",
        encoding="utf-8",
    )
    [case] = parse_evaluation_file(suite)
    assert case["answer_match"] == "casefold"
    assert case["required_tools"] == ["check_conflicts"]
    assert case["forbidden_tools"] == ["write_record"]
    assert case["tool_order"] == ["lookup", "check_conflicts"]
    assert case["max_tool_calls"] == 3
    assert case["max_duration_s"] == 5.5
    assert case["max_tool_duration_s"] == 2.0
    assert case["required_calls"] == [{
        "tool": "check_conflicts",
        "arguments": {"party_names": ["Acme Co"]},
    }]
    assert case["result_assertions"] == [{
        "tool": "write_record",
        "contains": "conflicts gate",
    }]


def test_deterministic_answer_scorers():
    assert score_answer("Cleared", "cleared", "casefold")
    assert score_answer("status=cleared", "cleared", "contains")
    assert score_answer("P-0007", r"P-\d{4}", "regex")
    assert not score_answer("cleared", "pending", "exact")


def test_trace_constraints_cover_required_forbidden_order_and_limit():
    case = {
        "required_tools": ["check_conflicts"],
        "forbidden_tools": ["send_email"],
        "tool_order": ["lookup", "check_conflicts"],
        "max_tool_calls": 3,
    }
    assert evaluate_trace_constraints(case, [
        {"name": "lookup"},
        {"name": "check_conflicts"},
    ]) == []

    violations = evaluate_trace_constraints(case, [
        {"name": "check_conflicts"},
        {"name": "send_email"},
        {"name": "lookup"},
        {"name": "lookup"},
    ])
    assert "forbidden tool called: send_email" in violations
    assert "required tool order not observed: lookup -> check_conflicts" in violations
    assert "tool-call limit exceeded: 4 > 3" in violations


def test_parse_headers_splits_only_on_first_colon():
    assert parse_headers(["Authorization: Bearer token:with:colons"]) == {
        "Authorization": "Bearer token:with:colons"
    }


def test_parse_env_vars_splits_only_on_first_equals():
    assert parse_env_vars(["URL=https://example.test/?a=b"]) == {
        "URL": "https://example.test/?a=b"
    }



def test_subset_argument_matching_is_recursive_and_strict_for_lists():
    assert is_subset(
        {"matter": {"name": "Test"}, "parties": ["A", "B"]},
        {"matter": {"name": "Test", "extra": 1}, "parties": ["A", "B"], "other": True},
    )
    assert not is_subset({"parties": ["A"]}, {"parties": ["A", "B"]})


def test_extended_constraints_validate_arguments_results_and_latency():
    case = {
        "required_calls": [{
            "tool": "check_conflicts",
            "arguments": {"party_names": ["Acme Co"]},
        }],
        "result_assertions": [{
            "tool": "write_record",
            "contains": "conflicts gate",
        }],
        "max_duration_s": 1.0,
        "max_tool_duration_s": 0.5,
    }
    trace = [
        {
            "name": "check_conflicts",
            "input": {"party_names": ["Acme Co"], "response_format": "json"},
            "duration_s": 0.1,
            "result_text": '{"status":"pending"}',
        },
        {
            "name": "write_record",
            "input": {"conflicts_status": "not-run"},
            "duration_s": 0.2,
            "result_text": "Error: conflicts gate — refused",
        },
    ]
    assert evaluate_extended_constraints(case, trace, total_duration=0.8) == []

    violations = evaluate_extended_constraints(case, trace, total_duration=1.2)
    assert any("task duration exceeded" in item for item in violations)


def test_extended_constraints_fail_when_required_argument_shape_is_wrong():
    case = {
        "required_calls": [{
            "tool": "check_conflicts",
            "arguments": {"party_names": ["Expected Co"]},
        }],
        "result_assertions": [],
        "max_duration_s": None,
        "max_tool_duration_s": None,
    }
    trace = [{
        "name": "check_conflicts",
        "input": {"party_names": ["Other Co"]},
        "duration_s": 0.1,
        "result_text": "{}",
    }]
    violations = evaluate_extended_constraints(case, trace, total_duration=0.2)
    assert any("required call not observed" in item for item in violations)


def test_junit_and_baseline_reporting(tmp_path):
    evidence = {
        "provenance": {
            "model": "test-model",
            "suite_sha256": "abc",
        },
        "summary": {
            "accuracy": 100.0,
            "trace_valid": 1,
        },
        "results": [{
            "question": "Q",
            "expected": "A",
            "actual": "A",
            "answer_correct": True,
            "trace_violations": [],
            "passed": True,
            "total_duration": 0.25,
            "tool_trace": [{"name": "lookup", "ok": True}],
        }],
    }
    junit = tmp_path / "report.xml"
    write_junit(junit, evidence)
    xml = junit.read_text(encoding="utf-8")
    assert 'tests="1"' in xml
    assert 'failures="0"' in xml
    assert "test-model" in xml

    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        '{"summary":{"accuracy":100.0,"trace_valid":1}}',
        encoding="utf-8",
    )
    comparison = compare_baseline(baseline, evidence)
    assert comparison["regressed"] is False
    assert comparison["accuracy_delta"] == 0.0

    evidence["summary"]["accuracy"] = 90.0
    assert compare_baseline(baseline, evidence)["regressed"] is True
