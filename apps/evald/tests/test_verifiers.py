"""The verifiers are the objective anchor for judge validation.

They must be strict enough that a wrong answer cannot pass, and lenient enough
that formatting differences do not fail a correct one.
"""

from __future__ import annotations

import pytest

from evald.corpus.verifiers import (
    extract_choice,
    extract_final_number,
    verify,
    verify_choice,
    verify_final_number,
)


class TestFinalNumber:
    @pytest.mark.parametrize(
        "text",
        [
            "So she makes 18 dollars.\n#### 18",
            "The final answer is 18",
            "Final answer: 18",
            "\\boxed{18}",
            "... therefore 18",
            "#### 18.00",
            "#### $18",
            "#### 18.0",
        ],
    )
    def test_accepts_every_common_answer_format(self, text: str) -> None:
        assert verify_final_number(text, "18")

    def test_takes_the_last_number_not_an_intermediate_one(self) -> None:
        # A chain of thought is full of intermediate numbers; only the last counts.
        cot = "16 eggs, eats 3, bakes 4, so 16-3-4 = 9. At $2 each that is 9*2 = 18.\n#### 18"
        assert verify_final_number(cot, "18")
        assert not verify_final_number(cot, "9")

    def test_handles_thousands_separators(self) -> None:
        assert verify_final_number("#### 1,234", "1234")
        assert verify_final_number("#### 1234", "1,234")

    def test_handles_negative_answers(self) -> None:
        assert verify_final_number("#### -42", "-42")

    def test_rejects_a_wrong_answer(self) -> None:
        assert not verify_final_number("#### 19", "18")

    def test_rejects_empty_or_numberless_output(self) -> None:
        assert not verify_final_number("", "18")
        assert not verify_final_number("I cannot solve this.", "18")

    def test_marker_wins_over_later_stray_text(self) -> None:
        assert extract_final_number("blah 99 blah\n#### 18") == "18"

    def test_treats_integral_floats_as_integers(self) -> None:
        assert extract_final_number("#### 18.000") == "18"


class TestChoiceLetter:
    @pytest.mark.parametrize(
        "text",
        ["#### D", "The answer is D", "Answer: D", "\\boxed{D}", "answer is (D)", "D"],
    )
    def test_accepts_every_common_choice_format(self, text: str) -> None:
        assert verify_choice(text, "D")

    def test_is_case_insensitive(self) -> None:
        assert verify_choice("#### d", "D")

    def test_supports_the_ten_options_mmlu_pro_uses(self) -> None:
        # MMLU-Pro goes up to J, unlike MMLU's four options.
        for letter in "ABCDEFGHIJ":
            assert verify_choice(f"#### {letter}", letter)

    def test_takes_the_last_marked_answer_after_reasoning(self) -> None:
        assert extract_choice("Option A looks plausible, but B is wrong.\nAnswer: C") == "C"

    def test_rejects_a_wrong_letter(self) -> None:
        assert not verify_choice("#### A", "B")

    def test_rejects_output_with_no_choice(self) -> None:
        assert extract_choice("") is None
        assert not verify_choice("", "A")


def test_registry_dispatch() -> None:
    assert verify("final_number", "#### 7", "7")
    assert verify("choice_letter", "#### B", "B")
    with pytest.raises(KeyError, match="unknown verifier"):
        verify("nope", "x", "y")
