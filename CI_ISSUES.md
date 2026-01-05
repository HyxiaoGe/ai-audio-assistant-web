# CI Issues and Fix Plan

Context:
- Local CI-style checks run with Python 3.11 in a venv.
- Docker build passed.
- This document captures current failures and recommended priority.

## P0 - Unblock test execution

1) Pytest collection fails due to import path
- Symptom: `ModuleNotFoundError: No module named 'app'`
- Affected tests:
  - tests/services/test_asr_tencent.py
  - tests/services/test_llm_doubao.py
  - tests/test_cost_optimizer.py
  - tests/test_load_balancer.py
  - tests/test_monitoring.py
  - tests/test_moonshot_integration.py
  - tests/test_smart_factory.py
  - tests/worker/test_process_audio.py
- Impact: tests do not run at all.
- Recommended fix: make project importable in tests (install as package or set PYTHONPATH).
- Status: Added `tests/conftest.py` to prepend repo root to `sys.path`; pytest now passes locally.

2) SmartFactory LLM calls require model_id in tests
- Symptom: `ValueError: model_id is required for llm services` in SmartFactory tests.
- Affected tests:
  - tests/test_smart_factory.py
- Impact: SmartFactory tests fail after enforcing model_id for LLM.
- Recommended fix: update test calls to pass model_id; make registry mocks accept extra kwargs.
- Status: Updated tests to pass `model_id` and accept `**kwargs`; tests now pass.

## P1 - Formatting and import order

2) Black formatting failures
- Symptom: `black --check` reports 54 files would be reformatted.
- Impact: CI format check fails.
- Recommended fix: run `black` and commit formatting changes.
- Status: Ran `black app/ worker/ tests/`; formatting now passes locally.

3) Black formatting failure on Volcengine ASR
- Symptom: CI `black` reformatted `app/services/asr/volcengine.py`.
- Impact: CI format check fails.
- Recommended fix: apply Black formatting and commit.
- Status: Fixed and committed (`chore: format volcengine asr`).

3) isort import order failures
- Symptom: `isort --check-only` reports multiple files with incorrect import order.
- Impact: CI import sorting check fails.
- Recommended fix: run `isort` (often combined with black pass).
- Status: Ran `isort app/ worker/ tests/`; import order now passes locally.

## P2 - Lint errors (flake8)

4) Flake8 errors across codebase
- Representative issues:
  - Unused imports (F401)
  - Bare `except` (E722)
  - Redefinitions (F811)
  - Undefined names (F821)
  - Style errors (E122, E128)
- Impact: CI lint step fails; some issues may hide real defects.
- Recommended fix: fix incrementally after formatting.
- Status: Fixed reported flake8 issues; `flake8 app/ worker/ tests/ --max-line-length=100` passes locally.

## P3 - Type check errors (mypy)

5) Mypy errors across codebase
- Symptom: 76 errors in 13 files, e.g. missing attributes, type mismatches, abstract class instantiation.
- Impact: CI type check fails; potential correctness risks.
- Recommended fix: resolve after lint cleanup to reduce noise.
- Status: Fixed reported mypy issues; `mypy app/ worker/` now passes locally.

## Notes

- These issues pre-existed and are not caused by the dependency-source change.
- Pytest run showed deprecation warnings from `asyncio.iscoroutinefunction` (Python 3.14).
  - Status: Replaced with `inspect.iscoroutinefunction` in `app/core/fault_tolerance.py`.
- Aliyun ASR/OSS dependencies (`dashscope`, `oss2`) are now explicit in `pyproject.toml`; optional imports were reverted to strict imports.
- Full test run passes after installing new dependencies.
- Volcengine ASR + TOS smoke tests passed (presigned PUT/GET and ASR transcription).
- After each fix, update this document to mark items resolved and any remaining sub-issues.
