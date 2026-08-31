from pathlib import Path
import re

import pytest

from scripts.verify_environment_example import (
    INTERNAL_ENVIRONMENT_NAMES,
    EnvironmentExampleEntry,
    _unsafe_example_reason,
    discover_compose_environment_names,
    discover_environment_accesses,
    discover_settings_environment_names,
    parse_environment_example,
    verify_environment_contract,
)

ROOT = Path(__file__).resolve().parents[1]


def _write_contract(tmp_path: Path, source: str, example: str) -> tuple[Path, Path]:
    source_root = tmp_path / "src"
    source_root.mkdir()
    (source_root / "settings.py").write_text(source)
    example_path = tmp_path / ".env.example"
    example_path.write_text(example)
    return source_root, example_path


def test_discovery_handles_optional_nested_and_environ_accesses(tmp_path: Path):
    source_root, _ = _write_contract(
        tmp_path,
        """
import os
from pydantic import Field

OPTIONAL_ENVIRONMENT = "OPTIONAL_SETTING"
ordinary = os.getenv("ORDINARY_SETTING", "default")
optional = os.getenv(OPTIONAL_ENVIRONMENT)
nested = Field(default_factory=lambda: int(os.getenv("NESTED_SETTING", "3")))
mapping_get = os.environ.get("MAPPING_SETTING")
mapping_item = os.environ["REQUIRED_SETTING"]
""",
        "",
    )

    names, dynamic = discover_environment_accesses(source_root)

    assert names == {
        "MAPPING_SETTING",
        "NESTED_SETTING",
        "OPTIONAL_SETTING",
        "ORDINARY_SETTING",
        "REQUIRED_SETTING",
    }
    assert dynamic == ()


def test_commented_empty_values_are_documented_once(tmp_path: Path):
    _, example_path = _write_contract(
        tmp_path,
        "",
        "# OPTIONAL_SETTING=\nACTIVE_SETTING=value\n",
    )

    assert parse_environment_example(example_path) == (
        EnvironmentExampleEntry("OPTIONAL_SETTING", "", True, 1),
        EnvironmentExampleEntry("ACTIVE_SETTING", "value", False, 2),
    )


def test_settings_fields_include_aliases_without_instantiation(tmp_path: Path):
    source_root, _ = _write_contract(
        tmp_path,
        """
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    optional_value: str | None = None
    canonical_value: str = Field(default="", validation_alias="CANONICAL_ALIAS")
    compatible_value: str = Field(
        default="", validation_alias=AliasChoices("PRIMARY_ALIAS", "LEGACY_ALIAS")
    )
""",
        "",
    )

    assert discover_settings_environment_names(source_root) == {
        "CANONICAL_ALIAS",
        "LEGACY_ALIAS",
        "OPTIONAL_VALUE",
        "PRIMARY_ALIAS",
    }


def test_nested_settings_honor_prefix_and_delimiter(tmp_path: Path):
    source_root, _ = _write_contract(
        tmp_path,
        """
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

class DatabaseSettings(BaseModel):
    host: str
    port: int | None = None

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", env_nested_delimiter="__")
    database: DatabaseSettings
    mode: str = "development"
""",
        "",
    )

    assert discover_settings_environment_names(source_root) == {
        "APP_DATABASE__HOST",
        "APP_DATABASE__PORT",
        "APP_MODE",
    }


def test_deliberate_internal_setting_is_explicitly_classified(tmp_path: Path):
    internal_name = next(iter(INTERNAL_ENVIRONMENT_NAMES))
    source_root, example_path = _write_contract(
        tmp_path,
        f'import os\nvalue = os.getenv("{internal_name}")\n',
        "",
    )

    assert verify_environment_contract(source_root, example_path) == []


def test_template_only_setting_must_be_consumed_by_compose(tmp_path: Path):
    source_root, example_path = _write_contract(
        tmp_path,
        "",
        "POSTGRES_DB=aelira\nPOSTGRES_PASSWORD=change-me\nPOSTGRES_USER=aelira\n",
    )
    (tmp_path / "docker-compose.prod.yml").write_text(
        "database: ${POSTGRES_DB}\nuser: ${POSTGRES_USER}\n"
    )

    assert discover_compose_environment_names(tmp_path) == {
        "POSTGRES_DB",
        "POSTGRES_USER",
    }
    assert verify_environment_contract(source_root, example_path) == [
        "template-only classification is not consumed by Compose: POSTGRES_PASSWORD"
    ]


@pytest.mark.parametrize(
    ("source", "example", "expected"),
    [
        (
            'import os\nvalue = os.getenv("ADDED_SETTING")\n',
            "",
            "missing from .env.example: ADDED_SETTING",
        ),
        (
            "",
            "REMOVED_SETTING=value\n",
            "not read by runtime or classified template-only: REMOVED_SETTING",
        ),
        (
            'import os\nvalue = os.getenv("RENAMED_SETTING")\n',
            "OLD_SETTING=value\n",
            "missing from .env.example: RENAMED_SETTING",
        ),
    ],
)
def test_unclassified_additions_removals_and_renames_fail(
    tmp_path: Path,
    source: str,
    example: str,
    expected: str,
):
    source_root, example_path = _write_contract(tmp_path, source, example)

    assert expected in verify_environment_contract(source_root, example_path)


def test_legacy_alias_requires_its_canonical_name(tmp_path: Path):
    source_root, example_path = _write_contract(
        tmp_path,
        'import os\nvalue = os.getenv("API_BASE_URL")\n',
        "API_BASE_URL=\n",
    )

    assert verify_environment_contract(source_root, example_path) == [
        "legacy alias API_BASE_URL targets absent canonical name PUBLIC_API_URL",
        "documented alias API_BASE_URL lacks canonical example PUBLIC_API_URL",
    ]


@pytest.mark.parametrize(
    ("entry", "reason"),
    [
        (
            EnvironmentExampleEntry("SERVICE_URL", "http://192.168.1.20", False, 1),
            "example contains a private network address",
        ),
        (
            EnvironmentExampleEntry(
                "PUBLIC_API_URL",
                "https://" + ".".join(("api", "aelira", "ai")),
                False,
                1,
            ),
            "example assumes Aelira's hosted production domain",
        ),
        (
            EnvironmentExampleEntry("API_KEY", "live-credential-value", False, 1),
            "secret-like setting has a non-placeholder example",
        ),
    ],
)
def test_unsafe_example_values_are_rejected(
    entry: EnvironmentExampleEntry, reason: str
):
    assert _unsafe_example_reason(entry) == reason


def test_repository_environment_contract_is_in_parity():
    assert verify_environment_contract(ROOT / "src", ROOT / ".env.example") == []


def test_deployment_documentation_uses_canonical_environment_names():
    documentation = (ROOT / "docs/deployment/self-hosting.md").read_text()
    required_environment = documentation.split("## Required environment", 1)[1].split(
        "\n## ", 1
    )[0]
    documented_names = set(re.findall(r"`([A-Z][A-Z0-9_]*)`", required_environment))
    example_names = {
        entry.name for entry in parse_environment_example(ROOT / ".env.example")
    }

    assert "[`.env.example`](../../.env.example)" in required_environment
    assert (
        "[`src/config/settings.py`](../../src/config/settings.py)"
        in required_environment
    )
    assert documented_names <= example_names
