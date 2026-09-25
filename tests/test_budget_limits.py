import asyncio
from types import SimpleNamespace

from evals.evaluation import agent_loop


class _FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class _FakeClient:
    def __init__(self, responses):
        self.messages = _FakeMessages(responses)


class _FakeConnection:
    async def call_tool(self, name, arguments):
        return {"name": name, "arguments": arguments, "ok": True}


def _tool_response(call_id, *, input_tokens=10, output_tokens=5):
    return SimpleNamespace(
        stop_reason="tool_use",
        content=[
            SimpleNamespace(
                type="tool_use",
                name="demo_tool",
                input={"value": call_id},
                id=f"call-{call_id}",
            )
        ],
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        ),
    )


def _final_response(*, input_tokens=7, output_tokens=3):
    return SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text="<response>ok</response>")],
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        ),
    )


def test_agent_loop_fails_closed_at_model_turn_limit():
    client = _FakeClient([_tool_response(1), _tool_response(2), _final_response()])
    response, trace, violation, usage = asyncio.run(
        agent_loop(
            client,
            "test-model",
            "question",
            [],
            _FakeConnection(),
            max_model_turns=2,
            max_output_tokens=123,
        )
    )

    assert response is None
    assert violation == (
        "model turn limit reached (2); "
        "evaluation stopped before another billable model call"
    )
    assert len(client.messages.calls) == 2
    assert all(call["max_tokens"] == 123 for call in client.messages.calls)
    assert len(trace) == 2
    assert usage["calls"] == 2
    assert usage["input_tokens"] == 20
    assert usage["output_tokens"] == 10


def test_agent_loop_records_usage_for_successful_run():
    client = _FakeClient([_tool_response(1), _final_response()])
    response, trace, violation, usage = asyncio.run(
        agent_loop(
            client,
            "test-model",
            "question",
            [],
            _FakeConnection(),
            max_model_turns=3,
            max_output_tokens=321,
        )
    )

    assert response == "<response>ok</response>"
    assert violation is None
    assert len(trace) == 1
    assert usage["calls"] == 2
    assert usage["input_tokens"] == 17
    assert usage["output_tokens"] == 8
    assert all(call["max_tokens"] == 321 for call in client.messages.calls)
