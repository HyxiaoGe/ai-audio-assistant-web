"""确保公开的环境变量样例不会携带任何凭证值。"""

from pathlib import Path

ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"
SENSITIVE_SUFFIXES = (
    "_PASSWORD",
    "_SECRET",
    "_SECRET_ID",
    "_SECRET_KEY",
    "_ACCESS_TOKEN",
    "_API_KEY",
    "_PRIVATE_KEY",
    "_ENCRYPTION_KEY",
    "_MASTER_KEY",
    "_ACCESS_KEY",
    "_ACCESS_KEY_ID",
    "_ACCESS_KEY_SECRET",
    "_APP_KEY",
)


def _parse_env_example() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip()
    return values


def test_env_example_keeps_credentials_empty() -> None:
    """凭证键即使使用占位符也必须留空，避免示例值被误当成可用配置。"""
    values = _parse_env_example()
    populated = sorted(
        name
        for name, value in values.items()
        if name.endswith(SENSITIVE_SUFFIXES) and value
    )

    assert not populated, "以下凭证字段在 .env.example 中必须保持空值: " + ", ".join(populated)
