"""CI/CD 安全边界与发布探活契约测试。

本文件只使用 Python 标准库，确保即使项目第三方依赖尚未安装，也能独立校验
GitHub Actions 配置的关键工程基线。
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
PR_CI = WORKFLOWS / "pr-ci.yml"
RELEASE = WORKFLOWS / "build-and-deploy.yml"

CHECKOUT_SHA = "d23441a48e516b6c34aea4fa41551a30e30af803"
LOGIN_SHA = "dbcb813823bdd20940b903addbd779551569679f"
CODEQL_SHA = "24c7eb380a2dc368f2d129e4c65e51d172983a1e"

USES_LINE = re.compile(
    r"^\s*(?:-\s*)?uses\s*:\s*"
    r"(?:(?P<single>'[^']+')|(?P<double>\"[^\"]+\")|(?P<bare>[^#\s]+))"
    r"\s*(?P<comment>#.*)?$"
)
USES_CANDIDATE = re.compile(r"^\s*(?:-\s*)?uses\b")
USES_KEY_TOKEN = re.compile(r"(?<![A-Za-z0-9_-])(?:uses|\"uses\"|'uses')\s*:")
PURE_COMMENT = re.compile(r"^\s*#")
EXTERNAL_ACTION = re.compile(
    r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.\-/]+)?@([0-9a-f]{40})$"
)
KNOWN_ACTION_PINS = {
    "actions/checkout": (CHECKOUT_SHA, "# v6"),
    "docker/login-action": (LOGIN_SHA, "# v4.6.0"),
    "github/codeql-action/init": (CODEQL_SHA, "# v4"),
    "github/codeql-action/analyze": (CODEQL_SHA, "# v4"),
}


def _read(path: Path) -> str:
    if not path.is_file():
        raise AssertionError(f"缺少文件：{path.relative_to(ROOT)}")
    return path.read_text(encoding="utf-8")


def _indented_block(text: str, key: str, indent: int) -> str:
    """返回简单 YAML 映射键的文本块，不依赖第三方 YAML 包。"""

    lines = text.splitlines()
    prefix = re.escape(" " * indent)
    start_pattern = re.compile(rf"^{prefix}{re.escape(key)}:\s*(?:#.*)?$")
    sibling_pattern = re.compile(rf"^{prefix}\S[^:]*:\s*(?:.*)$")
    for index, line in enumerate(lines):
        if not start_pattern.match(line):
            continue
        end = index + 1
        while end < len(lines) and not sibling_pattern.match(lines[end]):
            end += 1
        return "\n".join(lines[index:end])
    raise AssertionError(f"找不到 YAML 块：{key!r}（缩进 {indent}）")


def _trigger_names(text: str) -> set[str]:
    on_block = _indented_block(text, "on", 0)
    return set(re.findall(r"^  ([A-Za-z_][\w-]*):\s*(?:.*)$", on_block, re.MULTILINE))


def _job_block(text: str, job_id: str) -> str:
    jobs_block = _indented_block(text, "jobs", 0)
    return _indented_block(jobs_block, job_id, 2)


def _step_blocks(job: str) -> list[str]:
    matches = list(re.finditer(r"^      - name:\s*.*$", job, re.MULTILINE))
    blocks: list[str] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(job)
        blocks.append(job[match.start() : end])
    return blocks


def _job_ids(text: str) -> list[str]:
    jobs = _indented_block(text, "jobs", 0)
    return re.findall(r"^  ([A-Za-z_][\w-]*):\s*(?:.*)$", jobs, re.MULTILINE)


def _step_name(step: str) -> str:
    matched = re.fullmatch(r"      - name:\s*(.+?)\s*", step.splitlines()[0])
    if matched is None:
        raise AssertionError(f"步骤缺少 name：{step!r}")
    return matched.group(1)


def _step_by_name(job: str, name: str) -> str:
    matches = [step for step in _step_blocks(job) if _step_name(step) == name]
    if len(matches) != 1:
        raise AssertionError(f"步骤 {name!r} 应恰好出现一次，实际 {len(matches)} 次")
    return matches[0]


def _normalized_step_run(step: str) -> str:
    matched = re.search(r"(?m)^        run:\s*(.*)$", step)
    if matched is None:
        return ""
    run = step[matched.start() :]
    run = re.sub(r"\\\s*\n", " ", run)
    return re.sub(r"\s+", " ", run)


def _uses_step_block(lines: list[str], uses_index: int) -> str:
    uses_line = lines[uses_index]
    uses_indent = len(uses_line) - len(uses_line.lstrip())
    step_indent = uses_indent if uses_line.lstrip().startswith("- ") else uses_indent - 2
    end = uses_index + 1
    while end < len(lines):
        candidate = lines[end]
        candidate_indent = len(candidate) - len(candidate.lstrip())
        if candidate_indent == step_indent and candidate.lstrip().startswith("- "):
            break
        end += 1
    return "\n".join(lines[uses_index:end])


def _parse_uses_line(line: str) -> tuple[str, str] | None:
    if PURE_COMMENT.search(line):
        return None
    if not USES_CANDIDATE.search(line) and not USES_KEY_TOKEN.search(line):
        return None
    parsed = USES_LINE.fullmatch(line)
    if parsed is None:
        raise AssertionError(f"uses 键必须使用独立 block-style 行：{line!r}")
    raw_value = parsed.group("single") or parsed.group("double") or parsed.group("bare")
    value = raw_value[1:-1] if raw_value[:1] in {"'", '"'} else raw_value
    return value, parsed.group("comment") or ""


def _uses_occurrences(text: str) -> list[tuple[int, str, str]]:
    occurrences: list[tuple[int, str, str]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        parsed = _parse_uses_line(line)
        if parsed is not None:
            occurrences.append((line_number, *parsed))
    return occurrences


def _local_action_targets(repo_root: Path, value: str) -> list[Path]:
    root = repo_root.resolve()
    target = (root / value).resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise AssertionError(f"本地 Action 不能逃逸仓库根目录：{value}") from error

    if not target.exists():
        raise AssertionError(f"本地 Action 目标不存在：{value}")
    if target.is_file():
        return [target]

    manifests = [path for name in ("action.yml", "action.yaml") if (path := target / name).is_file()]
    if not manifests:
        raise AssertionError(f"本地 Action 目录缺少 action.yml/action.yaml：{value}")
    return manifests


def _collect_action_files(repo_root: Path) -> list[Path]:
    workflows = repo_root / ".github" / "workflows"
    actions = repo_root / ".github" / "actions"
    pending = set(workflows.glob("*.yml"))
    pending.update(workflows.glob("*.yaml"))
    pending.update(workflows.glob("*.disabled"))
    if actions.exists():
        pending.update(actions.rglob("action.yml"))
        pending.update(actions.rglob("action.yaml"))

    scanned: set[Path] = set()
    while pending:
        path = pending.pop().resolve()
        if path in scanned:
            continue
        if not path.is_file():
            raise AssertionError(f"待扫描 Action 文件不存在：{path}")
        scanned.add(path)
        for _, value, _ in _uses_occurrences(path.read_text(encoding="utf-8")):
            if value.startswith("./"):
                pending.update(_local_action_targets(repo_root, value))
    return sorted(scanned)


def _action_files() -> list[Path]:
    return _collect_action_files(ROOT)


def _assert_external_action_pin(value: str, comment: str) -> None:
    external = EXTERNAL_ACTION.fullmatch(value)
    if external is None:
        raise AssertionError(f"外部 Action 必须锁定 40 位小写 SHA：{value}")
    action_name, sha = value.rsplit("@", 1)
    expected = KNOWN_ACTION_PINS.get(action_name)
    if expected is not None:
        expected_sha, expected_comment = expected
        if (sha, comment) != (expected_sha, expected_comment):
            raise AssertionError(
                f"已知 Action pin 不匹配：{action_name} 需要 {expected_sha} {expected_comment}"
            )
        return
    if re.fullmatch(r"# v\S+", comment) is None:
        raise AssertionError(f"外部 Action 缺少版本注释：{value}")


def _validate_action_files(repo_root: Path) -> list[Path]:
    scanned = _collect_action_files(repo_root)
    for path in scanned:
        for line_number, value, comment in _uses_occurrences(path.read_text(encoding="utf-8")):
            if value.startswith("./"):
                continue
            try:
                _assert_external_action_pin(value, comment)
            except AssertionError as error:
                relative = path.relative_to(repo_root.resolve())
                raise AssertionError(f"{relative}:{line_number}: {error}") from error
    return scanned


class TestPullRequestWorkflow(unittest.TestCase):
    def test_pr_ci_has_a_single_safe_pull_request_trigger(self) -> None:
        text = _read(PR_CI)
        self.assertEqual(_trigger_names(text), {"pull_request"})
        pull_request = _indented_block(_indented_block(text, "on", 0), "pull_request", 2)
        self.assertRegex(pull_request, r"(?m)^    branches:\s*\[master\]\s*$")
        self.assertNotIn("pull_request_target", text)

    def test_pr_ci_has_stable_pull_request_concurrency(self) -> None:
        concurrency = _indented_block(_read(PR_CI), "concurrency", 0)
        self.assertEqual(
            concurrency.splitlines(),
            [
                "concurrency:",
                "  group: ai-audio-web-pr-${{ github.event.pull_request.number }}",
                "  cancel-in-progress: true",
            ],
        )

    def test_pr_ci_has_exact_read_only_permissions_and_one_job(self) -> None:
        text = _read(PR_CI)
        self.assertEqual(
            _indented_block(text, "permissions", 0).splitlines(),
            ["permissions:", "  contents: read"],
        )
        self.assertEqual(_job_ids(text), ["validate"])
        self.assertNotRegex(text, r"(?m)^\s*environment\s*:")
        self.assertNotRegex(text, r"(?m)^\s*secrets\s*:")
        self.assertNotRegex(text, r"\bsecrets\s*(?:\.|\[)")

    def test_pr_ci_uses_github_hosted_runner_without_release_capabilities(self) -> None:
        text = _read(PR_CI)
        job = _job_block(text, "validate")
        self.assertRegex(job, r"(?m)^    name: PR container validation\s*$")
        self.assertRegex(job, r"(?m)^    runs-on: ubuntu-latest\s*$")
        self.assertNotRegex(job, r"(?m)^    environment\s*:")

        uses = [(value, comment) for _, value, comment in _uses_occurrences(job)]
        self.assertEqual(uses, [(f"actions/checkout@{CHECKOUT_SHA}", "# v6")])
        for step in _step_blocks(job):
            run = _normalized_step_run(step)
            self.assertNotRegex(run, r"(?i)\bdocker\s+(?:login|push|compose)\b")
            self.assertNotRegex(run, r"(?i)\b(?:kubectl|helm|ssh|scp|rsync)\b")
            self.assertNotRegex(f"{_step_name(step)} {run}", r"(?i)\bdeploy(?:ment)?\b")

    def test_pr_ci_builds_and_validates_a_temporary_image(self) -> None:
        text = _read(PR_CI)
        self.assertRegex(text, r"(?m)^\s+PR_IMAGE:\s*[^\n]*-pr:[^\n]*$")
        self.assertRegex(text, r'docker build .*-t "\$\{PR_IMAGE\}"')
        self.assertRegex(text, r"ruff check app/ worker/ tests/")
        self.assertRegex(text, r"pytest tests/ -v")


class TestReleaseWorkflow(unittest.TestCase):
    def test_release_has_only_master_push_and_manual_triggers(self) -> None:
        text = _read(RELEASE)
        self.assertEqual(_trigger_names(text), {"push", "workflow_dispatch"})
        push = _indented_block(_indented_block(text, "on", 0), "push", 2)
        self.assertRegex(push, r"(?m)^    branches:\s*\[master\]\s*$")
        self.assertNotRegex(_indented_block(text, "on", 0), r"(?m)^  pull_request:")
        self.assertRegex(text, r"(?m)^  cancel-in-progress: false\s*$")

    def test_publish_and_deploy_jobs_have_separate_dev_environment_boundaries(self) -> None:
        text = _read(RELEASE)
        publish = _job_block(text, "publish")
        deploy = _job_block(text, "deploy")
        self.assertRegex(publish, r"(?m)^    name: Publish master image on Windows runner\s*$")
        self.assertRegex(publish, r"(?m)^    if: github\.ref == 'refs/heads/master'\s*$")
        self.assertRegex(publish, r"(?m)^    runs-on: \[self-hosted, Windows, X64\]\s*$")
        self.assertRegex(
            publish,
            r"(?ms)^    environment:\s*\n      name: dev\s*\n      deployment: false\s*$",
        )
        self.assertRegex(deploy, r"(?m)^    needs: publish\s*$")
        self.assertRegex(deploy, r"(?m)^    if: github\.ref == 'refs/heads/master'\s*$")
        self.assertRegex(deploy, r"(?m)^    runs-on: \[self-hosted, Linux, X64\]\s*$")
        self.assertRegex(deploy, r"(?m)^    environment: dev\s*$")

    def test_finalize_runs_for_publish_failures_and_owns_final_side_effects(self) -> None:
        text = _read(RELEASE)
        self.assertEqual(_job_ids(text), ["publish", "deploy", "finalize"])
        finalize = _job_block(text, "finalize")
        self.assertRegex(finalize, r"(?m)^    needs: \[publish, deploy\]\s*$")
        self.assertRegex(
            finalize,
            r"(?m)^    if: \$\{\{ always\(\) && github\.ref == 'refs/heads/master' \}\}\s*$",
        )
        self.assertRegex(finalize, r"(?m)^    runs-on: \[self-hosted, Linux, X64\]\s*$")
        self.assertRegex(
            finalize,
            r"(?ms)^    environment:\s*\n      name: dev\s*\n      deployment: false\s*$",
        )

        for job_id in ("publish", "deploy"):
            job = _job_block(text, job_id)
            names = [_step_name(step) for step in _step_blocks(job)]
            self.assertNotIn("Push CI/CD metrics", names)
            self.assertNotIn("通知飞书(部署结果)", names)
            self.assertNotIn("push-cicd-metrics.sh", job)
            self.assertNotIn("FEISHU_INFRA_WEBHOOK", job)

        finalize_names = [_step_name(step) for step in _step_blocks(finalize)]
        self.assertEqual(finalize_names.count("Push CI/CD metrics"), 1)
        self.assertEqual(finalize_names.count("通知飞书(部署结果)"), 1)
        self.assertIn("push-cicd-metrics.sh", finalize)
        self.assertIn("FEISHU_INFRA_WEBHOOK", finalize)

    def test_finalize_combines_job_results_and_uses_safe_timing_fallbacks(self) -> None:
        text = _read(RELEASE)
        deploy = _job_block(text, "deploy")
        self.assertRegex(
            deploy,
            r"(?ms)^    outputs:\s*\n      started_at: \$\{\{ steps\.deploy_start\.outputs\.started_at \}\}\s*$",
        )
        deploy_start = _step_by_name(deploy, "Record deploy start time")
        self.assertRegex(deploy_start, r"(?m)^        id: deploy_start\s*$")
        self.assertIn("started_at=", deploy_start)
        self.assertIn("$GITHUB_OUTPUT", deploy_start)

        finalize = _job_block(text, "finalize")
        resolve = _step_by_name(finalize, "Resolve final release status")
        self.assertIn("PUBLISH_RESULT: ${{ needs.publish.result }}", resolve)
        self.assertIn("DEPLOY_RESULT: ${{ needs.deploy.result }}", resolve)
        self.assertRegex(
            resolve,
            r'if \[ "\$PUBLISH_RESULT" = "success" \] && \[ "\$DEPLOY_RESULT" = "success" \]; then',
        )
        self.assertIn("final_status=success", resolve)
        self.assertIn("final_status=failure", resolve)
        self.assertIn('status=$final_status', resolve)
        self.assertIn('pipeline_started_at="${PUBLISH_STARTED_AT:-$(date +%s)}"', resolve)
        self.assertIn('deploy_started_at="${DEPLOY_STARTED_AT:-$pipeline_started_at}"', resolve)
        self.assertIn('runner_name="${PUBLISH_RUNNER_NAME:-unknown}"', resolve)

        metrics = _step_by_name(finalize, "Push CI/CD metrics")
        notification = _step_by_name(finalize, "通知飞书(部署结果)")
        for name, step in (("metrics", metrics), ("notification", notification)):
            with self.subTest(step=name):
                self.assertRegex(step, r"(?m)^        if: always\(\)\s*$")
                self.assertRegex(step, r"(?m)^        continue-on-error: true\s*$")
                self.assertRegex(step, r"(?m)^        timeout-minutes: 2\s*$")
        self.assertIn("${{ steps.final_status.outputs.status }}", metrics)
        self.assertIn("${{ steps.final_status.outputs.pipeline_started_at }}", metrics)
        self.assertIn("${{ env.IMAGE_NAME }}:${{ github.sha }}", metrics)
        self.assertGreaterEqual(metrics.count("${{ github.sha }}"), 2)
        self.assertIn("${{ steps.final_status.outputs.runner_name }}", metrics)
        self.assertIn("${{ steps.final_status.outputs.deploy_started_at }}", metrics)
        self.assertIn("JOB_STATUS: ${{ steps.final_status.outputs.status }}", notification)

    def test_each_release_job_has_one_non_logout_login_action(self) -> None:
        text = _read(RELEASE)
        self.assertNotRegex(text, r"(?:~|\$HOME|\$env:USERPROFILE)[/\\]\.docker")
        expected_login = f"docker/login-action@{LOGIN_SHA}"
        for job_id in ("publish", "deploy"):
            with self.subTest(job=job_id):
                job = _job_block(text, job_id)
                login_steps = [
                    step
                    for step in _step_blocks(job)
                    if any(value == expected_login for _, value, _ in _uses_occurrences(step))
                ]
                self.assertEqual(len(login_steps), 1)
                self.assertRegex(login_steps[0], r"(?m)^          logout:\s*false\s*$")
                for step in _step_blocks(job):
                    self.assertNotRegex(_normalized_step_run(step), r"(?i)\bdocker\s+login\b")

    def test_each_release_job_isolates_and_strictly_cleans_docker_credentials(self) -> None:
        text = _read(RELEASE)
        for job_id in ("publish", "deploy"):
            with self.subTest(job=job_id):
                job = _job_block(text, job_id)
                self.assertIn("DOCKER_CONFIG", job)
                self.assertIn("RUNNER_TEMP", job)
                self.assertIn("GITHUB_RUN_ID", job)
                self.assertIn("GITHUB_RUN_ATTEMPT", job)
                self.assertIn("GITHUB_JOB", job)
                steps = _step_blocks(job)
                self.assertTrue(steps, f"{job_id} 必须包含步骤")
                step_names = [_step_name(step) for step in steps]
                self.assertLess(
                    step_names.index("Isolate Docker credentials"),
                    step_names.index("Login to ACR"),
                )
                cleanup = steps[-1]
                isolate = _step_by_name(job, "Isolate Docker credentials")
                self.assertIn("GITHUB_RUN_ATTEMPT", isolate)
                self.assertIn("GITHUB_RUN_ATTEMPT", cleanup)
                expected_suffix = (
                    ".docker-$env:GITHUB_RUN_ID-$env:GITHUB_RUN_ATTEMPT-$env:GITHUB_JOB"
                    if job_id == "publish"
                    else ".docker-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}-${GITHUB_JOB}"
                )
                self.assertIn(expected_suffix, isolate)
                self.assertIn(expected_suffix, cleanup)
                self.assertRegex(cleanup, r"(?m)^      - name: Cleanup Docker credentials\s*$")
                self.assertRegex(cleanup, r"(?m)^        if: always\(\)\s*$")
                expected_assignment = re.search(
                    r"(?im)^(?!\s*#).*expected.*RUNNER_TEMP.*$", cleanup
                )
                config_validation = re.search(
                    r"(?im)^(?!\s*#).*DOCKER_CONFIG.*(?:!=|-ne).*expected.*$", cleanup
                )
                runner_temp_validation = re.search(
                    r"(?im)^(?!\s*#).*(?:StartsWith\(\$runnerTemp|case \"\$DOCKER_CONFIG\").*$",
                    cleanup,
                )
                deletion = re.search(
                    r"(?im)^(?!\s*#).*(?:Remove-Item.*configPath|rm -rf --.*DOCKER_CONFIG).*$",
                    cleanup,
                )
                for label, matched in (
                    ("expected 路径构造", expected_assignment),
                    ("DOCKER_CONFIG 精确比较", config_validation),
                    ("RUNNER_TEMP 边界校验", runner_temp_validation),
                    ("凭据删除", deletion),
                ):
                    self.assertIsNotNone(matched, f"{job_id} cleanup 缺少{label}")
                assert expected_assignment and config_validation and runner_temp_validation and deletion
                self.assertLess(
                    max(
                        expected_assignment.start(),
                        config_validation.start(),
                        runner_temp_validation.start(),
                    ),
                    deletion.start(),
                )

    def test_deploy_verifies_image_identity_and_readiness_inside_container(self) -> None:
        text = _read(RELEASE)
        deploy = _job_block(text, "deploy")
        expected_image = "${{ env.IMAGE_NAME }}:${{ github.sha }}"
        self.assertIn("docker inspect --format '{{.Config.Image}}' ai-audio-assistant-web-api", deploy)
        self.assertIn(expected_image, deploy)
        self.assertRegex(deploy, r"actual_image.*!=.*expected_image")
        self.assertRegex(
            deploy,
            r"docker exec ai-audio-assistant-web-api[^\n]*(?:\\\n[^\n]*)*"
            r"http://127\.0\.0\.1:8000/api/v1/readiness",
        )
        self.assertNotIn("127.0.0.1:8088", text)

    def test_legacy_deploy_is_archived_not_enabled(self) -> None:
        self.assertFalse((WORKFLOWS / "deploy.yml").exists())
        self.assertTrue((WORKFLOWS / "deploy.legacy.disabled").is_file())


class TestPinnedActions(unittest.TestCase):
    def test_uses_parser_covers_step_forms_and_rejects_unparseable_candidates(self) -> None:
        sha = "a" * 40
        valid_lines = (
            f"uses: owner/action@{sha} # v1",
            f"  - uses: owner/action@{sha} # v1.2.3",
            f"    uses: 'owner/action@{sha}' # v2",
            f'      - uses: "owner/action/path@{sha}" # v3',
        )
        for line in valid_lines:
            with self.subTest(line=line):
                self.assertRegex(line, USES_CANDIDATE)
                self.assertIsNotNone(_parse_uses_line(line))

        for line in ("uses owner/action@v1", "- uses:", "uses:: owner/action@v1"):
            with self.subTest(line=line):
                self.assertRegex(line, USES_CANDIDATE)
                with self.assertRaisesRegex(AssertionError, "独立 block-style"):
                    _parse_uses_line(line)

    def test_flow_mapping_and_quoted_uses_keys_are_rejected_but_comments_are_ignored(self) -> None:
        sha = "a" * 40
        forbidden = (
            f"- {{name: Checkout, uses: owner/action@{sha}}}",
            f'"uses": owner/action@{sha} # v1',
            f"'uses': owner/action@{sha} # v1",
            f"- \"uses\": owner/action@{sha} # v1",
            f"- {{'uses': owner/action@{sha}}}",
        )
        for line in forbidden:
            with self.subTest(line=line):
                self.assertRegex(line, USES_KEY_TOKEN)
                with self.assertRaisesRegex(AssertionError, "独立 block-style"):
                    _parse_uses_line(line)

        for line in (
            "# uses: owner/action@v1",
            "  # \"uses\": owner/action@v1",
            "\t# - {uses: owner/action@v1}",
        ):
            with self.subTest(line=line):
                self.assertIsNone(_parse_uses_line(line))

    def test_local_actions_are_resolved_recursively_with_unreferenced_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def write(relative: str, content: str) -> None:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

            write(".github/workflows/ci.yml", "steps:\n  - uses: ./custom/entry\n")
            write("custom/entry/action.yml", "steps:\n  - uses: ./custom/loop\n")
            write("custom/loop/action.yaml", "steps:\n  - uses: ./custom/file-action.yml\n")
            write(
                "custom/file-action.yml",
                f"steps:\n  - uses: ./custom/entry\n  - uses: owner/action@{'a' * 40} # v1\n",
            )
            write(".github/actions/unreferenced/action.yaml", "runs:\n  using: composite\n")

            scanned = {path.relative_to(root.resolve()).as_posix() for path in _validate_action_files(root)}
            self.assertEqual(
                scanned,
                {
                    ".github/actions/unreferenced/action.yaml",
                    ".github/workflows/ci.yml",
                    "custom/entry/action.yml",
                    "custom/file-action.yml",
                    "custom/loop/action.yaml",
                },
            )

    def test_local_action_targets_must_exist_stay_in_repo_and_have_a_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = root / ".github" / "workflows" / "ci.yml"
            workflow.parent.mkdir(parents=True)

            workflow.write_text("steps:\n  - uses: ./missing\n", encoding="utf-8")
            with self.assertRaisesRegex(AssertionError, "目标不存在"):
                _collect_action_files(root)

            empty_action = root / "empty-action"
            empty_action.mkdir()
            workflow.write_text("steps:\n  - uses: ./empty-action\n", encoding="utf-8")
            with self.assertRaisesRegex(AssertionError, "缺少 action.yml/action.yaml"):
                _collect_action_files(root)

            workflow.write_text("steps:\n  - uses: ./../outside\n", encoding="utf-8")
            with self.assertRaisesRegex(AssertionError, "不能逃逸仓库根目录"):
                _collect_action_files(root)

    def test_referenced_local_manifest_is_included_in_external_pin_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = root / ".github" / "workflows" / "ci.yml"
            action = root / "custom" / "action.yml"
            workflow.parent.mkdir(parents=True)
            action.parent.mkdir(parents=True)
            workflow.write_text("steps:\n  - uses: ./custom\n", encoding="utf-8")
            action.write_text("steps:\n  - uses: actions/checkout@v6 # v6\n", encoding="utf-8")
            with self.assertRaisesRegex(AssertionError, "外部 Action 必须锁定 40 位小写 SHA"):
                _validate_action_files(root)

    def test_all_uses_lines_are_parseable_and_external_actions_are_sha_pinned(self) -> None:
        scanned = _validate_action_files(ROOT)
        self.assertTrue(scanned, "至少应扫描到一个 workflow 或 action manifest")

    def test_known_actions_require_the_exact_sha_and_version_on_every_occurrence(self) -> None:
        for action_name, (sha, comment) in KNOWN_ACTION_PINS.items():
            with self.subTest(action=action_name, case="valid"):
                _assert_external_action_pin(f"{action_name}@{sha}", comment)
            with self.subTest(action=action_name, case="wrong-sha"):
                with self.assertRaisesRegex(AssertionError, "已知 Action pin 不匹配"):
                    _assert_external_action_pin(f"{action_name}@{'a' * 40}", comment)
            with self.subTest(action=action_name, case="wrong-comment"):
                with self.assertRaisesRegex(AssertionError, "已知 Action pin 不匹配"):
                    _assert_external_action_pin(f"{action_name}@{sha}", "# v0")

    def test_every_checkout_disables_persisted_credentials(self) -> None:
        for path in _action_files():
            lines = _read(path).splitlines()
            for index, line in enumerate(lines):
                parsed = _parse_uses_line(line)
                if parsed is None or parsed[0].rsplit("@", 1)[0] != "actions/checkout":
                    continue
                step = _uses_step_block(lines, index)
                with self.subTest(path=path.relative_to(ROOT), line=index + 1):
                    self.assertRegex(step, r"(?m)^\s+persist-credentials:\s*false\s*$")

    def test_direct_uses_checkout_step_cannot_borrow_credentials_setting_from_later_step(self) -> None:
        lines = [
            f"      - uses: actions/checkout@{CHECKOUT_SHA} # v6",
            "      - name: Later checkout",
            f"        uses: actions/checkout@{CHECKOUT_SHA} # v6",
            "        with:",
            "          persist-credentials: false",
        ]
        first_step = _uses_step_block(lines, 0)
        self.assertNotIn("persist-credentials", first_step)


if __name__ == "__main__":
    unittest.main()
