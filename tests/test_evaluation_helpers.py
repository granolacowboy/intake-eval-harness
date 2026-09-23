from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evals"))

from evaluation import (  # noqa: E402
    evaluate_trace_constraints,
    extract_xml_content,
    parse_env_vars,
    parse_evaluation_file,
    parse_headers,
    score_answer,
)


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
