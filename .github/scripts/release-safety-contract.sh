#!/usr/bin/env bash
set -euo pipefail

exec docker run --rm "${PR_IMAGE:?PR_IMAGE is required}" python tests/test_ci_cd_contract.py
