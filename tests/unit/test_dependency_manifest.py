from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).parents[2]


def _requirements(name: str) -> list[str]:
    direct = []
    for raw in (ROOT / name).read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line and not line.startswith("-r "):
            direct.append(line)
    return sorted(direct)


def _manifest() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        return tomllib.load(stream)


def test_pyproject_matches_compatibility_requirements() -> None:
    """Les anciens manifests pip ne doivent pas diverger de la source uv."""
    manifest = _manifest()

    assert sorted(manifest["project"]["dependencies"]) == _requirements("requirements.txt")
    assert sorted(manifest["dependency-groups"]["dev"]) == _requirements(
        "requirements-dev.txt"
    )
    assert sorted(manifest["dependency-groups"]["diar"]) == _requirements(
        "requirements-diar.txt"
    )


def test_toolchain_targets_the_validated_python() -> None:
    manifest = _manifest()

    assert manifest["project"]["requires-python"] == "==3.11.*"
    assert manifest["tool"]["ruff"]["target-version"] == "py311"
