"""Unit tests for the ASR retry-idempotency decision function.

These cover the money-path retry windows (D5-retry) closed in the worker:
- A terminal ``success`` ASRUsage marker means the paid call + cost recording
  already completed -> skip everything (no double charge).
- Transcripts present but no ``success`` marker means a prior attempt
  transcribed (and paid) but crashed before cost recording finalized -> reuse
  transcripts (no re-charge) and finalize cost exactly once (closes the
  post-transcript/pre-ASRUsage under-charge window).
- A lingering ``processing`` claim with no transcripts means a prior paid
  attempt crashed mid/post-transcribe before persisting -> detectable for
  billing reconciliation (closes the pre-commit double-charge window as far as
  is possible without provider task resume).

The function is pure (plain booleans in, enum out) so it is fully testable
without a database or any mocks.
"""

from __future__ import annotations

import pytest

from worker.tasks.asr_idempotency import AsrRetryAction, decide_asr_action


@pytest.mark.parametrize(
    ("has_success_usage", "has_transcripts", "has_processing_claim", "expected"),
    [
        # A terminal success marker always wins -> skip the paid call and cost.
        (True, False, False, AsrRetryAction.SKIP_ALL),
        (True, True, False, AsrRetryAction.SKIP_ALL),
        (True, False, True, AsrRetryAction.SKIP_ALL),
        (True, True, True, AsrRetryAction.SKIP_ALL),
        # No success marker but transcripts exist -> reuse them, finalize cost
        # exactly once (under-charge window).
        (False, True, False, AsrRetryAction.FINALIZE_COST),
        (False, True, True, AsrRetryAction.FINALIZE_COST),
        # No success, no transcripts, but a paid attempt was claimed -> a prior
        # paid attempt may have charged; flag for reconciliation (double-charge
        # window).
        (False, False, True, AsrRetryAction.RESUME_AFTER_CLAIM),
        # Clean slate -> first run.
        (False, False, False, AsrRetryAction.FULL_RUN),
    ],
)
def test_decide_asr_action(
    has_success_usage: bool,
    has_transcripts: bool,
    has_processing_claim: bool,
    expected: AsrRetryAction,
) -> None:
    assert (
        decide_asr_action(
            has_success_usage=has_success_usage,
            has_transcripts=has_transcripts,
            has_processing_claim=has_processing_claim,
        )
        == expected
    )


def test_success_marker_takes_precedence_over_transcripts() -> None:
    # Even if transcripts and a claim are also present, a success marker means
    # the whole paid+cost path already completed: never re-run it.
    assert (
        decide_asr_action(
            has_success_usage=True,
            has_transcripts=True,
            has_processing_claim=True,
        )
        == AsrRetryAction.SKIP_ALL
    )


def test_transcripts_take_precedence_over_claim() -> None:
    # Transcripts present means the paid call already produced output; finalize
    # cost rather than re-transcribing, regardless of the claim row.
    assert (
        decide_asr_action(
            has_success_usage=False,
            has_transcripts=True,
            has_processing_claim=True,
        )
        == AsrRetryAction.FINALIZE_COST
    )
