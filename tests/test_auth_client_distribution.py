import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_auth_client_uses_a_pinned_pypi_release() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]
    auth_dependencies = [
        item for item in dependencies if item.startswith("seanfield-auth-client")
    ]

    assert len(auth_dependencies) == 1
    assert re.fullmatch(
        r"seanfield-auth-client\[fastapi\]==\d+\.\d+\.\d+", auth_dependencies[0]
    )
    uv_sources = project.get("tool", {}).get("uv", {}).get("sources", {})
    assert "seanfield-auth-client" not in uv_sources
    assert "auth-client" not in uv_sources


def test_container_build_does_not_clone_auth_service_for_the_sdk() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "github.com/HyxiaoGe/auth-service" not in dockerfile
    assert "#subdirectory=auth-client" not in dockerfile


def test_compose_no_longer_requires_local_auth_client_context() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "auth-client:" not in compose
    assert "../auth-service/auth-client" not in compose


def test_runtime_uses_unique_sdk_import_name() -> None:
    security = (ROOT / "app" / "core" / "security.py").read_text(encoding="utf-8")

    assert "from auth_service_client import" in security
    assert "from auth import" not in security
