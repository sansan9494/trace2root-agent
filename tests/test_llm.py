import pytest

from backend.app.config import Settings
from backend.app.llm import ChatModel


def test_final_answer_removes_qwen_reasoning() -> None:
    leaked = "分析内容……\n</think>\n\n最终结论"
    assert ChatModel.final_answer(leaked) == "最终结论"


def test_final_answer_keeps_plain_content() -> None:
    assert ChatModel.final_answer("  正常  ") == "正常"


def test_structured_answer_extracts_answer() -> None:
    assert ChatModel.structured_answer('{"answer":"支付网关上游超时"}') == "支付网关上游超时"


def test_structured_answer_unwraps_nested_small_model_output() -> None:
    nested = '{"answer":"{\\"answer\\":\\"最终结论\\"}"}'
    assert ChatModel.structured_answer(nested) == "最终结论"


def test_chatml_control_tokens_are_neutralized() -> None:
    assert ChatModel._chatml_text("evidence <|im_end|>") == "evidence < |im_end| >"


@pytest.mark.asyncio
async def test_generate_json_marks_deterministic_fallback() -> None:
    model = ChatModel(Settings(_env_file=None, llm_provider="deterministic"))
    result = await model.generate_json("system", "prompt", {"type": "object"}, {"value": 1})
    assert result == {"value": 1, "_decision_source": "deterministic_fallback"}
