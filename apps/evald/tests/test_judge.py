"""The judge: rubric integrity, verdict parsing, and position-swap debiasing."""

from __future__ import annotations

from typing import Any

import pytest

from evald.corpus.schema import CorpusItem
from evald.judge.judge import PairwiseJudge
from evald.judge.rubric import CRITERIA, RUBRIC_VERSION, UnknownTaskError, build_prompt
from evald.judge.schema import (
    UnparseableVerdictError,
    combine_positions,
    parse_reply,
    to_candidate_verdict,
)


def item(task: str = "summarization") -> CorpusItem:
    return CorpusItem(
        slug=f"{task}-0000",
        task_type=task,  # type: ignore[arg-type]
        split="test",
        verifiable=False,
        messages=[{"role": "user", "content": "Summarise the article."}],
        source_dataset="abisee/cnn_dailymail",
        source_id="s1",
        source_license="Apache-2.0",
    )


class TestRubric:
    def test_covers_every_task_type_in_the_corpus(self) -> None:
        from evald.cli import load_corpus

        tasks = {i.task_type for i in load_corpus("../../corpus/items.jsonl")}
        assert tasks <= set(CRITERIA), f"no rubric criteria for {tasks - set(CRITERIA)}"

    def test_refuses_an_unknown_task_rather_than_guessing(self) -> None:
        with pytest.raises(UnknownTaskError):
            build_prompt("astrology", "q", "a", "b")

    def test_prompt_contains_request_both_responses_and_the_contract(self) -> None:
        prompt = build_prompt("summarization", "THE REQUEST TEXT", "FIRST", "SECOND")
        for needle in ("THE REQUEST TEXT", "FIRST", "SECOND", '"verdict"', "tie"):
            assert needle in prompt

    def test_prompt_never_reveals_which_model_produced_what(self) -> None:
        prompt = build_prompt("summarization", "q", "resp one", "resp two")
        for leak in ("claude", "opus", "gpt", "groq", "haiku", "sonnet"):
            assert leak not in prompt.lower()

    def test_warns_against_the_known_judge_failure_modes(self) -> None:
        # Length and confidence bias are the two best-documented LLM-judge
        # failure modes; the rubric must name them explicitly.
        assert "length" in build_prompt("summarization", "q", "a", "b").lower()
        assert "confiden" in build_prompt("summarization", "q", "a", "b").lower()

    def test_version_is_pinned(self) -> None:
        # Changing rubric text without bumping this silently invalidates kappa.
        assert RUBRIC_VERSION == "v1"


class TestParseReply:
    def test_parses_a_clean_object(self) -> None:
        reply = parse_reply('{"reasoning": "A is faithful", "verdict": "A", "confidence": 0.8}')
        assert reply.verdict == "A"
        assert reply.confidence == 0.8

    def test_tolerates_markdown_fences(self) -> None:
        # Models wrap JSON in fences despite instructions; discarding a good
        # verdict over punctuation would inflate the unparseable rate.
        reply = parse_reply('```json\n{"verdict": "B", "reasoning": "x"}\n```')
        assert reply.verdict == "B"

    def test_tolerates_prose_around_the_object(self) -> None:
        reply = parse_reply('Here is my verdict:\n{"verdict": "tie", "reasoning": "close"}\nDone.')
        assert reply.verdict == "tie"

    def test_defaults_confidence_when_omitted(self) -> None:
        assert parse_reply('{"verdict": "A"}').confidence == 0.5

    @pytest.mark.parametrize(
        "raw", ["", "   ", "I think A is better.", '{"verdict": "C"}', '{"no_verdict": 1}', "null"]
    )
    def test_rejects_anything_that_is_not_a_verdict(self, raw: str) -> None:
        with pytest.raises(UnparseableVerdictError):
            parse_reply(raw)

    def test_rejects_an_out_of_range_confidence(self) -> None:
        with pytest.raises(UnparseableVerdictError):
            parse_reply('{"verdict": "A", "confidence": 5}')


class TestPositionTranslation:
    def test_candidate_in_slot_a_winning_is_a_win(self) -> None:
        assert to_candidate_verdict("A", "A") == "win"

    def test_candidate_in_slot_b_winning_is_a_win(self) -> None:
        assert to_candidate_verdict("B", "B") == "win"

    def test_candidate_in_slot_a_losing_is_a_loss(self) -> None:
        assert to_candidate_verdict("B", "A") == "loss"

    def test_tie_is_a_tie_regardless_of_slot(self) -> None:
        assert to_candidate_verdict("tie", "A") == "tie"
        assert to_candidate_verdict("tie", "B") == "tie"

    def test_agreement_across_positions_stands(self) -> None:
        assert combine_positions("win", "win") == "win"
        assert combine_positions("loss", "loss") == "loss"

    def test_disagreement_across_positions_collapses_to_tie(self) -> None:
        # The answer depended on ORDER, not content. The honest reading is that
        # the judge could not tell them apart.
        assert combine_positions("win", "loss") == "tie"
        assert combine_positions("win", "tie") == "tie"


class TestPairwiseJudge:
    @staticmethod
    def scripted(*replies: str) -> Any:
        calls: list[str] = []
        queue = list(replies)

        def complete(model: str, messages: list[dict[str, str]], params: dict[str, Any]) -> str:
            calls.append(messages[0]["content"])
            return queue.pop(0) if queue else queue_default

        queue_default = '{"verdict": "tie"}'
        complete.calls = calls  # type: ignore[attr-defined]
        return complete

    def test_runs_both_orderings(self) -> None:
        complete = self.scripted('{"verdict": "A"}', '{"verdict": "B"}')
        judge = PairwiseJudge("claude-opus-5", complete)
        judge.compare(item(), "CAND", "REF", "claude-haiku-4-5", "claude-opus-5")
        assert len(complete.calls) == 2

    def test_swaps_the_responses_between_orderings(self) -> None:
        complete = self.scripted('{"verdict": "A"}', '{"verdict": "B"}')
        PairwiseJudge("claude-opus-5", complete).compare(
            item(), "CANDIDATE_TEXT", "REFERENCE_TEXT", "claude-haiku-4-5", "claude-opus-5"
        )
        first, second = complete.calls
        # Whichever slot the candidate had first, it must have the other second.
        assert first.index("CANDIDATE_TEXT") != second.index("CANDIDATE_TEXT")

    def test_consistent_judge_yields_a_decisive_verdict(self) -> None:
        # Candidate wins in whichever slot it occupies: verdicts agree.
        def complete(model: str, messages: list[dict[str, str]], params: dict[str, Any]) -> str:
            text = messages[0]["content"]
            cand_in_a = text.index("CAND") < text.index("REF")
            return '{"verdict": "A"}' if cand_in_a else '{"verdict": "B"}'

        j = PairwiseJudge("claude-opus-5", complete).compare(
            item(), "CAND", "REF", "claude-haiku-4-5", "claude-opus-5"
        )
        assert j.verdict == "win"
        assert j.position_flip is False

    def test_position_biased_judge_is_caught_and_reported(self) -> None:
        # A judge that always says "A" regardless of content is pure position
        # bias. Debiasing must turn that into a tie AND flag the flip.
        def always_a(model: str, messages: list[dict[str, str]], params: dict[str, Any]) -> str:
            return '{"verdict": "A"}'

        j = PairwiseJudge("claude-opus-5", always_a).compare(
            item(), "CAND", "REF", "claude-haiku-4-5", "claude-opus-5"
        )
        assert j.verdict == "tie"
        assert j.position_flip is True

    def test_retries_once_on_unparseable_then_succeeds(self) -> None:
        complete = self.scripted("garbage", '{"verdict": "A"}', '{"verdict": "A"}')
        j = PairwiseJudge("claude-opus-5", complete, max_attempts=2).compare(
            item(), "CAND", "REF", "claude-haiku-4-5", "claude-opus-5"
        )
        assert j.unparseable is False

    def test_gives_up_after_the_retry_and_excludes_the_item(self) -> None:
        # DESIGN.md failure mode #11: record unparseable, never guess a verdict.
        def garbage(model: str, messages: list[dict[str, str]], params: dict[str, Any]) -> str:
            return "not json at all"

        j = PairwiseJudge("claude-opus-5", garbage, max_attempts=2).compare(
            item(), "CAND", "REF", "claude-haiku-4-5", "claude-opus-5"
        )
        assert j.unparseable is True
        assert j.verdict is None
        assert j.usable is False

    def test_survives_a_transport_error(self) -> None:
        def boom(model: str, messages: list[dict[str, str]], params: dict[str, Any]) -> str:
            raise RuntimeError("gateway exploded")

        j = PairwiseJudge("claude-opus-5", boom, max_attempts=2).compare(
            item(), "CAND", "REF", "claude-haiku-4-5", "claude-opus-5"
        )
        assert j.unparseable is True

    def test_records_provenance_on_every_judgment(self) -> None:
        j = PairwiseJudge(
            "claude-opus-5", self.scripted('{"verdict":"tie"}', '{"verdict":"tie"}')
        ).compare(item(), "CAND", "REF", "claude-haiku-4-5", "claude-opus-5")
        assert j.judge_model == "claude-opus-5"
        assert j.rubric_version == RUBRIC_VERSION
        assert j.candidate_model == "claude-haiku-4-5"
        assert j.reference_model == "claude-opus-5"

    def test_is_deterministic_for_a_given_seed(self) -> None:
        def complete(model: str, messages: list[dict[str, str]], params: dict[str, Any]) -> str:
            return '{"verdict": "A"}'

        a = PairwiseJudge("m", complete).compare(item(), "C", "R", "cand", "ref", seed=7)
        b = PairwiseJudge("m", complete).compare(item(), "C", "R", "cand", "ref", seed=7)
        assert [p.candidate_was for p in a.positions] == [p.candidate_was for p in b.positions]

    def test_caps_the_judge_token_budget(self) -> None:
        # Adaptive-thinking models would otherwise turn a cheap classification
        # into an expensive essay.
        seen: list[dict[str, Any]] = []

        def complete(model: str, messages: list[dict[str, str]], params: dict[str, Any]) -> str:
            seen.append(params)
            return '{"verdict": "tie"}'

        PairwiseJudge("claude-opus-5", complete, max_tokens=700).compare(
            item(), "C", "R", "cand", "ref"
        )
        assert all(p["max_tokens"] == 700 for p in seen)
