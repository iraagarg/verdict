"""Calibration: agreement statistics, sampling, labelling harness, and the report."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import pytest

from evald.calibrate.labeler import HumanLabel, append_label, load_labels, run_session, set_requests
from evald.calibrate.report import KAPPA_GATE, CalibrationReport, Disagreement, diagnose
from evald.calibrate.runner import build_calibration_report
from evald.calibrate.sample import LabelPair, build_pairs
from evald.calibrate.stats import CLASSES, agreement, cohens_kappa, interpret, raw_agreement
from evald.cli import load_corpus
from evald.judge.judge import Judgment, PositionRun
from evald.judge.schema import JudgeReply


def keys(*presses: str) -> Any:
    """An input_fn that replays keystrokes, accepting the prompt argument."""
    queue = iter(presses)

    def press(_prompt: str = "") -> str:
        return next(queue)

    return press


def silent(_text: str = "") -> None:
    return None


CORPUS = load_corpus("../../corpus/items.jsonl")


class TestKappa:
    def test_perfect_agreement_is_one(self) -> None:
        labels = ["win", "tie", "loss"] * 20
        assert cohens_kappa(labels, labels) == pytest.approx(1.0)

    def test_a_judge_that_always_says_tie_scores_zero(self) -> None:
        # The whole reason for kappa over raw agreement. This judge is useless
        # but gets 33% raw agreement.
        human = ["win", "tie", "loss"] * 20
        judge = ["tie"] * 60
        assert raw_agreement(human, judge) == pytest.approx(1 / 3)
        assert cohens_kappa(human, judge) == 0.0

    def test_chance_level_agreement_is_near_zero(self) -> None:
        import random

        rng = random.Random(1)
        human = [rng.choice(CLASSES) for _ in range(600)]
        judge = [rng.choice(CLASSES) for _ in range(600)]
        assert abs(cohens_kappa(human, judge)) < 0.12

    def test_systematic_disagreement_is_negative(self) -> None:
        assert cohens_kappa(["win"] * 30 + ["loss"] * 30, ["loss"] * 30 + ["win"] * 30) < 0

    def test_is_undefined_but_reported_as_zero_when_both_use_one_class(self) -> None:
        assert cohens_kappa(["tie"] * 10, ["tie"] * 10) == 0.0

    def test_empty_input_does_not_explode(self) -> None:
        assert cohens_kappa([], []) == 0.0


class TestAgreementResult:
    def test_rejects_mismatched_lengths(self) -> None:
        with pytest.raises(ValueError, match="mismatched lengths"):
            agreement(["win"], ["win", "tie"])

    def test_rejects_an_unknown_class(self) -> None:
        with pytest.raises(ValueError, match="unknown class"):
            agreement(["excellent"], ["win"])

    def test_confusion_matrix_rows_are_human_labels(self) -> None:
        r = agreement(["win", "win", "tie"], ["win", "loss", "tie"], iterations=200)
        assert r.confusion["win"]["win"] == 1
        assert r.confusion["win"]["loss"] == 1
        assert r.confusion["tie"]["tie"] == 1

    def test_ci_brackets_the_point_estimate(self) -> None:
        human = ["win", "tie", "loss", "win", "tie"] * 20
        judge = ["win", "tie", "loss", "tie", "tie"] * 20
        r = agreement(human, judge, iterations=2000)
        assert r.kappa_ci_low <= r.kappa <= r.kappa_ci_high

    def test_ci_is_deterministic_for_a_seed(self) -> None:
        human = ["win", "tie", "loss"] * 30
        judge = ["win", "tie", "tie"] * 30
        a = agreement(human, judge, iterations=1000, seed=42)
        b = agreement(human, judge, iterations=1000, seed=42)
        assert (a.kappa_ci_low, a.kappa_ci_high) == (b.kappa_ci_low, b.kappa_ci_high)

    def test_gate_is_on_the_lower_bound_not_the_point_estimate(self) -> None:
        # A kappa of 0.62 with an interval down to 0.45 has demonstrated nothing.
        human = ["win", "tie", "loss", "win"] * 8
        judge = ["win", "tie", "win", "win"] * 8
        r = agreement(human, judge, iterations=2000)
        if r.kappa >= KAPPA_GATE > r.kappa_ci_low:
            assert r.clears_gate is False

    def test_wider_samples_give_tighter_intervals(self) -> None:
        small = agreement(["win", "tie", "loss"] * 7, ["win", "tie", "tie"] * 7, iterations=1500)
        large = agreement(["win", "tie", "loss"] * 70, ["win", "tie", "tie"] * 70, iterations=1500)
        assert (large.kappa_ci_high - large.kappa_ci_low) < (
            small.kappa_ci_high - small.kappa_ci_low
        )

    def test_interpretation_bands(self) -> None:
        assert interpret(-0.1) == "worse than chance"
        assert interpret(0.5) == "moderate"
        assert interpret(0.7) == "substantial"
        assert interpret(0.9) == "almost perfect"


class TestSampling:
    MODELS: ClassVar[list[str]] = ["claude-haiku-4-5", "openai/gpt-oss-20b", "gpt-5"]

    def test_only_samples_free_form_items(self) -> None:
        # The gradable slice is scored by an exact verifier; hand-labelling it
        # would spend the scarcest resource on the one thing already automated.
        pairs = build_pairs(CORPUS, self.MODELS, "claude-opus-5", n=60, seed=1)
        slugs = {p.slug for p in pairs}
        by_slug = {i.slug: i for i in CORPUS}
        assert all(not by_slug[s].verifiable for s in slugs)

    def test_covers_every_free_form_task(self) -> None:
        pairs = build_pairs(CORPUS, self.MODELS, "claude-opus-5", n=90, seed=1)
        assert {p.task_type for p in pairs} == {"summarization", "long_form_qa", "support_reply"}

    def test_covers_every_candidate_model(self) -> None:
        pairs = build_pairs(CORPUS, self.MODELS, "claude-opus-5", n=90, seed=1)
        assert {p.candidate_model for p in pairs} == set(self.MODELS)

    def test_respects_the_requested_size(self) -> None:
        assert len(build_pairs(CORPUS, self.MODELS, "claude-opus-5", n=40, seed=1)) <= 40

    def test_is_deterministic(self) -> None:
        a = build_pairs(CORPUS, self.MODELS, "claude-opus-5", n=50, seed=9)
        b = build_pairs(CORPUS, self.MODELS, "claude-opus-5", n=50, seed=9)
        assert [p.pair_id for p in a] == [p.pair_id for p in b]
        assert [p.candidate_shown_as for p in a] == [p.candidate_shown_as for p in b]

    def test_randomises_which_slot_the_candidate_occupies(self) -> None:
        # Otherwise the labeller's own position bias is baked in rather than measurable.
        pairs = build_pairs(CORPUS, self.MODELS, "claude-opus-5", n=120, seed=1)
        shown = [p.candidate_shown_as for p in pairs]
        assert 0.3 < shown.count("A") / len(shown) < 0.7

    def test_never_compares_the_reference_against_itself(self) -> None:
        pairs = build_pairs(CORPUS, self.MODELS, "claude-opus-5", n=60, seed=1)
        assert all(p.candidate_model != p.reference_model for p in pairs)


class TestPairTranslation:
    def test_choosing_the_candidates_slot_is_a_win(self) -> None:
        pair = LabelPair("p", "s", "summarization", "cand", "ref", candidate_shown_as="A")
        assert pair.to_verdict("A") == "win"
        assert pair.to_verdict("B") == "loss"

    def test_translation_flips_with_the_slot(self) -> None:
        pair = LabelPair("p", "s", "summarization", "cand", "ref", candidate_shown_as="B")
        assert pair.to_verdict("B") == "win"
        assert pair.to_verdict("A") == "loss"

    def test_tie_is_slot_independent(self) -> None:
        for slot in ("A", "B"):
            pair = LabelPair("p", "s", "summarization", "cand", "ref", candidate_shown_as=slot)
            assert pair.to_verdict("tie") == "tie"

    def test_texts_are_placed_in_the_declared_slot(self) -> None:
        pair = LabelPair("p", "s", "summarization", "cand", "ref", candidate_shown_as="B")
        a, b = pair.texts("CANDIDATE", "REFERENCE")
        assert (a, b) == ("REFERENCE", "CANDIDATE")


class TestLabellingHarness:
    @staticmethod
    def pairs() -> list[LabelPair]:
        return [
            LabelPair(f"p{i}", f"s{i}", "summarization", "cand", "ref", "A" if i % 2 else "B")
            for i in range(3)
        ]

    @staticmethod
    def texts() -> dict[str, tuple[str, str]]:
        return {f"p{i}": (f"CAND{i}", f"REF{i}") for i in range(3)}

    def test_records_a_label_per_decision(self, tmp_path: Path) -> None:
        path = tmp_path / "labels.jsonl"
        set_requests({f"p{i}": "the request" for i in range(3)})
        added = run_session(
            self.pairs(),
            self.texts(),
            path,
            input_fn=keys("a", "b", "t"),
            print_fn=silent,
        )
        assert added == 3
        assert len(load_labels(path)) == 3

    def test_translates_choices_into_candidate_relative_verdicts(self, tmp_path: Path) -> None:
        path = tmp_path / "labels.jsonl"
        set_requests({f"p{i}": "r" for i in range(3)})
        run_session(
            self.pairs(),
            self.texts(),
            path,
            input_fn=keys("a", "a", "a"),
            print_fn=silent,
        )
        labels = load_labels(path)
        # p0 shows candidate as B, so choosing A is a LOSS for the candidate.
        assert labels["p0"].verdict == "loss"
        # p1 shows candidate as A, so choosing A is a WIN.
        assert labels["p1"].verdict == "win"

    def test_resumes_and_never_re_asks_a_labelled_pair(self, tmp_path: Path) -> None:
        path = tmp_path / "labels.jsonl"
        set_requests({f"p{i}": "r" for i in range(3)})
        run_session(
            self.pairs(),
            self.texts(),
            path,
            input_fn=keys("a", "q"),
            print_fn=silent,
        )
        assert len(load_labels(path)) == 1

        asked: list[str] = []

        def record(prompt: str = "") -> str:
            asked.append(prompt)
            return "t"

        run_session(self.pairs(), self.texts(), path, input_fn=record, print_fn=silent)
        assert len(load_labels(path)) == 3
        assert len(asked) == 2  # only the two unlabelled pairs

    def test_quitting_preserves_everything_already_done(self, tmp_path: Path) -> None:
        path = tmp_path / "labels.jsonl"
        set_requests({f"p{i}": "r" for i in range(3)})
        run_session(
            self.pairs(),
            self.texts(),
            path,
            input_fn=keys("a", "b", "q"),
            print_fn=silent,
        )
        assert len(load_labels(path)) == 2

    def test_skip_leaves_the_pair_unlabelled(self, tmp_path: Path) -> None:
        path = tmp_path / "labels.jsonl"
        set_requests({f"p{i}": "r" for i in range(3)})
        run_session(
            self.pairs(),
            self.texts(),
            path,
            input_fn=keys("s", "s", "s"),
            print_fn=silent,
        )
        assert load_labels(path) == {}

    def test_rejects_invalid_keys_without_losing_the_pair(self, tmp_path: Path) -> None:
        path = tmp_path / "labels.jsonl"
        set_requests({f"p{i}": "r" for i in range(3)})
        run_session(
            self.pairs()[:1],
            {"p0": ("C", "R")},
            path,
            input_fn=keys("x", "9", "a"),
            print_fn=silent,
        )
        assert len(load_labels(path)) == 1

    def test_records_time_spent(self, tmp_path: Path) -> None:
        path = tmp_path / "labels.jsonl"
        append_label(path, HumanLabel("p", "s", "summarization", "c", "win", "A", "A", 1234, "now"))
        assert load_labels(path)["p"].elapsed_ms == 1234


def judgment(pair_id: str, verdict: str | None, flip: bool = False) -> Judgment:
    reply = JudgeReply(verdict="A", reasoning="because", confidence=0.7)
    return Judgment(
        slug=pair_id,
        task_type="summarization",
        candidate_model="cand",
        reference_model="ref",
        judge_model="claude-opus-5",
        rubric_version="v1",
        verdict=verdict,  # type: ignore[arg-type]
        positions=[PositionRun("A", reply, "win")],
        position_flip=flip,
        unparseable=verdict is None,
        confidence=0.7,
    )


class TestReport:
    @staticmethod
    def fixture(
        n: int, agree: int
    ) -> tuple[dict[str, HumanLabel], dict[str, Judgment], list[LabelPair]]:
        labels: dict[str, HumanLabel] = {}
        judgments: dict[str, Judgment] = {}
        pairs: list[LabelPair] = []
        classes = ["win", "tie", "loss"]
        for i in range(n):
            pid = f"p{i}"
            human = classes[i % 3]
            judge = human if i < agree else classes[(i + 1) % 3]
            labels[pid] = HumanLabel(
                pid, f"s{i}", "summarization", "cand", human, "A", "A", 8000, "now"
            )
            judgments[pid] = judgment(pid, judge)
            pairs.append(LabelPair(pid, f"s{i}", "summarization", "cand", "ref", "A"))
        return labels, judgments, pairs

    def build(self, n: int, agree: int) -> CalibrationReport:
        labels, judgments, pairs = self.fixture(n, agree)
        return build_calibration_report(
            labels=labels,
            judgments=judgments,
            pairs=pairs,
            judge_model="claude-opus-5",
            reference_model="claude-opus-5",
            rubric_version="v1",
            corpus_sha="f" * 64,
            labeler="iraa",
            seed=1,
            bootstrap_iterations=1000,
        )

    def test_round_trips_to_disk(self, tmp_path: Path) -> None:
        report = self.build(60, 60)
        assert CalibrationReport.read(report.write(tmp_path / "c.json")) == report

    def test_perfect_agreement_clears_the_gate(self) -> None:
        report = self.build(90, 90)
        assert report.kappa == pytest.approx(1.0)
        assert report.clears_gate is True

    def test_poor_agreement_fails_the_gate_and_is_not_softened(self) -> None:
        report = self.build(90, 30)
        assert report.clears_gate is False
        assert report.kappa < KAPPA_GATE
        assert any("does NOT clear the gate" in d for d in report.diagnosis)

    def test_records_every_disagreement_for_inspection(self) -> None:
        report = self.build(60, 40)
        assert len(report.disagreements) == 20
        assert all(d.human != d.judge for d in report.disagreements)

    def test_counts_unparseable_separately_from_disagreement(self) -> None:
        # An unparseable verdict must not be scored as a disagreement; it is a
        # missing measurement, not a wrong one.
        labels, judgments, pairs = self.fixture(30, 30)
        judgments["p0"] = judgment("p0", None)
        report = build_calibration_report(
            labels=labels,
            judgments=judgments,
            pairs=pairs,
            judge_model="m",
            reference_model="r",
            rubric_version="v1",
            corpus_sha="f" * 64,
            labeler="iraa",
            seed=1,
            bootstrap_iterations=500,
        )
        assert report.n_unparseable == 1
        assert report.n_compared == 29

    def test_reports_provenance(self) -> None:
        report = self.build(30, 30)
        assert report.rubric_version == "v1"
        assert report.judge_model == "claude-opus-5"
        assert report.corpus_sha256 == "f" * 64
        assert report.report_version == 1

    def test_summary_states_pass_or_fail_explicitly(self) -> None:
        assert "FAIL" in self.build(90, 30).summary()
        assert "PASS" in self.build(90, 90).summary()


class TestDiagnosis:
    @staticmethod
    def result(human: list[str], judge: list[str]) -> Any:
        return agreement(human, judge, iterations=800)

    def test_names_a_tie_happy_judge(self) -> None:
        human = ["win", "loss"] * 30
        judge = ["tie"] * 60
        out = diagnose(
            self.result(human, judge),
            [],
            0.0,
            {"tie": 0, "win": 30, "loss": 30},
            {"tie": 60, "win": 0, "loss": 0},
        )
        assert any("tie-happy" in d for d in out)

    def test_names_an_over_deciding_judge(self) -> None:
        human = ["tie"] * 60
        judge = ["win", "loss"] * 30
        out = diagnose(
            self.result(human, judge),
            [],
            0.0,
            {"tie": 60, "win": 0, "loss": 0},
            {"tie": 0, "win": 30, "loss": 30},
        )
        assert any("over-deciding" in d for d in out)

    def test_flags_a_high_position_flip_rate(self) -> None:
        out = diagnose(self.result(["win"] * 20, ["win"] * 20), [], 0.35, {}, {})
        assert any("flip rate" in d for d in out)

    def test_flags_bias_toward_the_candidate_as_the_dangerous_direction(self) -> None:
        disagreements = [
            Disagreement(
                pair_id=f"p{i}",
                slug=f"s{i}",
                task_type="summarization",
                candidate_model="c",
                human="tie",
                judge="win",
            )
            for i in range(10)
        ]
        out = diagnose(self.result(["tie"] * 10, ["win"] * 10), disagreements, 0.0, {}, {})
        assert any("too generous to the cheaper model" in d for d in out)

    def test_flags_suspiciously_fast_labels_as_possibly_the_humans_error(self) -> None:
        disagreements = [
            Disagreement(
                pair_id=f"p{i}",
                slug=f"s{i}",
                task_type="summarization",
                candidate_model="c",
                human="win",
                judge="loss",
                human_elapsed_ms=900,
            )
            for i in range(10)
        ]
        out = diagnose(self.result(["win"] * 20, ["loss"] * 20), disagreements, 0.0, {}, {})
        assert any("under 5 seconds" in d for d in out)

    def test_points_at_the_worst_task(self) -> None:
        disagreements = [
            Disagreement(
                pair_id=f"p{i}",
                slug=f"s{i}",
                task_type="support_reply",
                candidate_model="c",
                human="win",
                judge="tie",
            )
            for i in range(9)
        ] + [
            Disagreement(
                pair_id="px",
                slug="sx",
                task_type="summarization",
                candidate_model="c",
                human="win",
                judge="tie",
            )
        ]
        out = diagnose(self.result(["win"] * 20, ["tie"] * 20), disagreements, 0.0, {}, {})
        assert any("support_reply" in d for d in out)
