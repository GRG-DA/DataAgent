"""Leaked model reasoning ("thinking") must never reach the user-visible answer.

The local reasoning model sometimes prepends its monologue to the visible
answer even with ``think: False``. Two shapes occur in practice:

1. A complete ``<think ...>...</think>`` pair.
2. A stray closing tag with no opening tag — raw monologue followed by
   ``</think>`` and then the real answer.

Both the polished answer (``_polish_with_llm``) and the small-talk stream
must strip the leak and fall back to deterministic text when nothing usable
remains.
"""

import pytest

from agent.orchestrator import Orchestrator, _strip_think_leak


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeCompletion:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeLLMClient:
    def __init__(self, content):
        self.content = content

    async def chat(self, messages, options=None, **kwargs):
        return _FakeCompletion(self.content)


class _QuestionResponse:
    def __init__(self, question="Show me the key trends in this dataset"):
        self.question = question


def _orchestrator(content):
    orch = Orchestrator(None, None, None, None, None)
    orch.llm_client = _FakeLLMClient(content)
    return orch


class TestStripThinkLeak:
    def test_paired_block_is_removed(self):
        text = "<think>weighing the options</think>The answer is 42."
        assert _strip_think_leak(text) == "The answer is 42."

    def test_paired_block_multiline_with_attrs(self):
        text = '<think reason="draft">line one\nline two</think>\n\nFinal answer.'
        assert _strip_think_leak(text) == "Final answer."

    def test_stray_closing_tag_keeps_only_text_after_it(self):
        text = (
            "The user wants me to rewrite the draft analysis into a clear "
            "answer. Let me write it.</think>The dataset shows no trend - "
            "a single record."
        )
        assert _strip_think_leak(text) == (
            "The dataset shows no trend - a single record."
        )

    def test_stray_closing_tag_with_space_before_gt(self):
        text = "hidden reasoning</think >\n\nAnswer text."
        assert _strip_think_leak(text) == "Answer text."

    def test_unclosed_opening_tag_marks_text_suspect(self):
        text = "Thinking about it... <think the draft is sparse"
        assert _strip_think_leak(text) == ""

    def test_clean_markdown_unchanged(self):
        text = "## Key metrics\n\n| a | b |\n|---|---|\n| 1 | 2 |"
        assert _strip_think_leak(text) == text


class TestPolishStripsLeak:
    @pytest.mark.asyncio
    async def test_polished_answer_has_no_reasoning_prefix(self):
        content = (
            "The user wants me to rewrite the draft analysis into a clear "
            "answer. But the draft is sparse. Let me write it.</think>"
            "The dataset shows a single zero-value record, so no trend "
            "can be established from it."
        )
        out = await _orchestrator(content)._polish_with_llm(
            "Draft: count=1", _QuestionResponse()
        )
        assert out == (
            "The dataset shows a single zero-value record, so no trend "
            "can be established from it."
        )

    @pytest.mark.asyncio
    async def test_reasoning_only_falls_back_to_deterministic_answer(self):
        content = "The user wants me to rewrite the draft. Let me write it.</think>"
        out = await _orchestrator(content)._polish_with_llm(
            "Deterministic draft answer.", _QuestionResponse()
        )
        assert out == "Deterministic draft answer."

    @pytest.mark.asyncio
    async def test_clean_polish_passes_through(self):
        content = "## Answer\n\nThe table has exactly one row, so no trend exists yet."
        out = await _orchestrator(content)._polish_with_llm(
            "draft", _QuestionResponse()
        )
        assert out == content
