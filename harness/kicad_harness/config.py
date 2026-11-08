"""Paths and settings for the harness. See DESIGN.md, "Config"."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

HARNESS_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = HARNESS_DIR.parent


def _bifrost_key() -> str | None:
    """The gateway key, from BIFROST_API_KEY."""
    env = os.environ.get("BIFROST_API_KEY")
    return env.strip() if env else None


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else default


# No hardcoded routes: a model is offered only when its provider is configured (see
# models.catalog), so a dead key cannot leave an unusable entry in the picker.
DEFAULT_MODELS: list[str] = []


@dataclass
class Config:
    home: Path = field(default_factory=lambda: _env_path("HARNESS_HOME", HARNESS_DIR / "var"))
    kicad_app: Path = field(
        default_factory=lambda: _env_path("KICAD_APP", Path("/Applications/KiCad/KiCad.app"))
    )
    kicad_python: Path | None = None
    kicad_cli: Path | None = None
    footprint_dirs: list[Path] | None = None
    java: Path | None = None
    freerouting_jar: Path | None = None
    model: str = field(default_factory=lambda: os.environ.get("HARNESS_MODEL", "anthropic:claude-sonnet-5-5"))
    # A separate model for the adversarial judge; empty means use the working model.
    judge_model: str = field(default_factory=lambda: os.environ.get("HARNESS_JUDGE_MODEL", ""))
    # Extra models the UI offers, comma separated in HARNESS_MODELS. The configured model is
    # always offered as well; DEFAULTS below are the routes verified with this project.
    models: list[str] = field(
        default_factory=lambda: [m.strip() for m in os.environ.get("HARNESS_MODELS", "").split(",") if m.strip()]
    )
    # The user's Bifrost gateway (OpenAI-compatible). Models are selected as "bifrost:<id>".
    bifrost_base_url: str = field(
        default_factory=lambda: os.environ.get("BIFROST_BASE_URL", "")
    )
    bifrost_api_key: str | None = field(default_factory=_bifrost_key)
    # Optional curated list; otherwise the gateway's own chat-model list is offered.
    bifrost_models: list[str] = field(
        default_factory=lambda: [m.strip() for m in os.environ.get("BIFROST_MODELS", "").split(",") if m.strip()]
    )

    def __post_init__(self) -> None:
        self.home = Path(self.home)
        app = Path(self.kicad_app)
        if self.kicad_python is None:
            self.kicad_python = _env_path(
                "KICAD_PYTHON", app / "Contents/Frameworks/Python.framework/Versions/Current/bin/python3"
            )
        if self.kicad_cli is None:
            self.kicad_cli = _env_path("KICAD_CLI", app / "Contents/MacOS/kicad-cli")
        if self.footprint_dirs is None:
            env = os.environ.get("HARNESS_FOOTPRINT_DIRS")
            if env:
                self.footprint_dirs = [Path(p).expanduser() for p in env.split(os.pathsep) if p]
            else:
                self.footprint_dirs = [
                    REPO_DIR / "demo" / "libs" / "footprints",
                    app / "Contents" / "SharedSupport" / "footprints",
                ]
        if self.java is None:
            self.java = _env_path("HARNESS_JAVA", HARNESS_DIR / ".vendor" / "jre" / "Contents" / "Home" / "bin" / "java")
        if self.freerouting_jar is None:
            self.freerouting_jar = _env_path("HARNESS_FREEROUTING_JAR", HARNESS_DIR / ".vendor" / "freerouting.jar")

    @property
    def workspaces(self) -> Path:
        return self.home / "workspaces"

    @property
    def cache(self) -> Path:
        return self.home / "cache"

    @property
    def renders(self) -> Path:
        return self.home / "renders"

    @property
    def copilot_db(self) -> Path:
        """The web runtime's thread store (its replay history lives here)."""
        env = os.environ.get("COPILOT_DB")
        return Path(env).expanduser() if env else REPO_DIR / "harness" / "web" / ".data" / "copilot-threads.db"

    @property
    def db_path(self) -> Path:
        return self.home / "harness.db"

    def ensure_dirs(self) -> "Config":
        for path in (self.home, self.workspaces, self.cache, self.renders):
            path.mkdir(parents=True, exist_ok=True)
        return self


_config: Config | None = None


def get() -> Config:
    """Process-wide config, created from the environment on first use."""
    global _config
    if _config is None:
        _config = Config().ensure_dirs()
    return _config


def set_config(config: Config) -> Config:
    global _config
    _config = config.ensure_dirs()
    return _config
