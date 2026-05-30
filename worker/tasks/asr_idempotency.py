"""Retry-idempotency decisions for the ASR money path.

Celery runs ``process_audio`` / ``process_youtube`` with
``autoretry_for=(Exception,)``, which re-runs the *whole* task from the top on
any failure. The ASR step calls a paid external service and then records cost
across several rows (``AsrUserQuota``, the free-quota period, and ``ASRUsage``).
Without care, a crash partway through leaves two money-path hazards:

* **Double charge** -- crash after the paid transcribe but before transcripts
  are persisted; the retry re-transcribes (and the provider charges again).
* **Under charge** -- crash after transcripts are persisted but before the cost
  rows are committed; a transcript-keyed retry guard skips the whole cost block,
  so the paid call is never recorded.

The fix keys the retry decision on durable markers instead of transcript
presence alone:

* a terminal ``success`` ``ASRUsage`` row means the paid call *and* cost
  recording both completed;
* a ``processing`` ``ASRUsage`` *claim* row is written before the paid call, so
  a crashed-mid-flight attempt is detectable for billing reconciliation.

``decide_asr_action`` is intentionally pure (booleans in, enum out) so the
branching logic is unit-testable without a database. The caller derives the
three booleans from cheap existence queries.
"""

from __future__ import annotations

from enum import StrEnum


class AsrRetryAction(StrEnum):
    """What the worker should do for the ASR stage on a (possibly retried) run."""

    #: A terminal ``success`` ASRUsage marker exists -- the paid call and cost
    #: recording already completed. Reuse existing transcripts; charge nothing.
    SKIP_ALL = "skip_all"

    #: Transcripts exist but cost was never finalized (post-transcript crash).
    #: Reuse transcripts (no re-charge for the paid call) and record cost once.
    FINALIZE_COST = "finalize_cost"

    #: A ``processing`` claim exists with no transcripts -- a prior paid attempt
    #: crashed mid/post-transcribe and may have charged the provider. Re-run the
    #: paid call (no resume facility exists) but reuse the claim row so our books
    #: stay at a single record, and log loudly for billing reconciliation.
    RESUME_AFTER_CLAIM = "resume_after_claim"

    #: Clean slate -- first attempt. Write a claim, transcribe, finalize cost.
    FULL_RUN = "full_run"


def decide_asr_action(
    *,
    has_success_usage: bool,
    has_transcripts: bool,
    has_processing_claim: bool,
) -> AsrRetryAction:
    """Decide how to handle the ASR stage given what prior attempts persisted.

    Precedence (most-complete state wins):

    1. ``has_success_usage`` -> :attr:`AsrRetryAction.SKIP_ALL`
    2. ``has_transcripts``   -> :attr:`AsrRetryAction.FINALIZE_COST`
    3. ``has_processing_claim`` -> :attr:`AsrRetryAction.RESUME_AFTER_CLAIM`
    4. otherwise             -> :attr:`AsrRetryAction.FULL_RUN`
    """

    if has_success_usage:
        return AsrRetryAction.SKIP_ALL
    if has_transcripts:
        return AsrRetryAction.FINALIZE_COST
    if has_processing_claim:
        return AsrRetryAction.RESUME_AFTER_CLAIM
    return AsrRetryAction.FULL_RUN
