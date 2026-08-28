import hashlib
import os
import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from app.models.channel import AppConfig

ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ConfigLoadError(ValueError):
    pass


def _expand_environment(data: object) -> object:
    missing: set[str] = set()

    def replace_string(value: str) -> str:
        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            environment_value = os.environ.get(name)
            if environment_value is None:
                missing.add(name)
                return match.group(0)
            return environment_value

        return ENV_PATTERN.sub(replace, value)

    def visit(value: object) -> object:
        if isinstance(value, str):
            return replace_string(value)
        if isinstance(value, list):
            return [visit(item) for item in value]
        if isinstance(value, dict):
            return {key: visit(item) for key, item in value.items()}
        return value

    expanded = visit(data)
    if missing:
        names = ", ".join(sorted(missing))
        raise ConfigLoadError(f"missing environment variables: {names}")
    return expanded


def load_config(path: Path) -> tuple[AppConfig, str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigLoadError(f"cannot read config {path}: {exc}") from exc

    revision = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    try:
        data = yaml.safe_load(raw)
    except yaml.MarkedYAMLError as exc:
        location = ""
        if exc.problem_mark is not None:
            location = f" at line {exc.problem_mark.line + 1}, column {exc.problem_mark.column + 1}"
        raise ConfigLoadError(f"invalid YAML{location}: {exc.problem or str(exc)}") from exc

    if data is None:
        raise ConfigLoadError("configuration file is empty")
    expanded = _expand_environment(data)
    try:
        config = AppConfig.model_validate(expanded)
    except ValidationError as exc:
        raise ConfigLoadError(str(exc)) from exc
    return config, revision
