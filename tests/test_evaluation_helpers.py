from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evals"))

from evaluation import (  # noqa: E402
    extract_xml_content,
    parse_env_vars,
    parse_evaluation_file,
    parse_headers,
)


def test_extract_xml_content_returns_last_matching_tag():
    text = "<response>first</response> noise <response>final</response>"
    assert extract_xml_content(text, "response") == "final"


def test_extract_xml_content_returns_none_when_missing():
    assert extract_xml_content("plain text", "response") is None


def test_parse_evaluation_file_reads_question_answer_pairs(tmp_path):
    suite = tmp_path / "suite.xml"
    suite.write_text(
        """<evaluation>
        <qa_pair><question>  How many?  </question><answer>  3  </answer></qa_pair>
        <qa_pair><question>Name?</question><answer>Ada</answer></qa_pair>
        </evaluation>""",
        encoding="utf-8",
    )

    assert parse_evaluation_file(suite) == [
        {"question": "How many?", "answer": "3"},
        {"question": "Name?", "answer": "Ada"},
    ]


def test_parse_headers_splits_only_on_first_colon():
    assert parse_headers(["Authorization: Bearer token:with:colons"]) == {
        "Authorization": "Bearer token:with:colons"
    }


def test_parse_env_vars_splits_only_on_first_equals():
    assert parse_env_vars(["URL=https://example.test/?a=b"]) == {
        "URL": "https://example.test/?a=b"
    }
