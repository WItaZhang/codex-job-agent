"""Local-only R1 initialization. Layout markers are not user authorization."""

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import yaml
from pydantic import ValidationError

from .config import Settings
from .domain import InvariantError
from .resources import default_config

MARKER = "workspace.json"
VERSION = {"version": 1, "maturity": "internal-prerelease", "execution_mode": "local_only"}


def default_workspace() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library/Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return base / "intent-job-agent" / "default"


def _inside(root: Path, value: str) -> Path:
    path = (root / value).resolve()
    if not path.is_relative_to(root):
        raise InvariantError("Workspace paths must stay inside the selected workspace")
    return path


def validate_paths(root: Path, settings: Settings) -> None:
    data = Path(settings.storage.data_dir)
    db = Path(settings.storage.db_path)
    if not data.is_absolute() or not db.is_absolute():
        raise InvariantError("Workspace storage paths must be absolute")
    if data.resolve() != root or _inside(root, str(db)) != root / "agent.sqlite":
        raise InvariantError("Workspace storage does not match its root")
    for value in (
        settings.discovery.boards_file,
        settings.discovery.applypilot_dir,
        settings.apply_list.dir,
        settings.apply.applicant_dir,
        settings.apply.letters_dir,
        settings.apply.logs_dir,
        settings.form_fill.dir,
    ):
        _inside(root, value)
    for base, values in (
        (_inside(root, settings.form_fill.dir), (settings.form_fill.recordings_dir, settings.form_fill.lists_dir)),
        (
            _inside(root, settings.apply.applicant_dir),
            (settings.form_fill.answers_file, settings.form_fill.checklist_file),
        ),
    ):
        for value in values:
            _inside(base, value)


def load_workspace(path: str | Path | None = None) -> Settings:
    root = Path(path or default_workspace()).expanduser().resolve()
    try:
        if (root / MARKER).is_symlink() or (root / "settings.yaml").is_symlink():
            raise InvariantError("Workspace marker and settings.yaml must not be symlinks")
        marker = json.loads((root / MARKER).read_text(encoding="utf-8"))
        if marker != VERSION:
            raise InvariantError("Unsupported or damaged workspace marker")
        settings = Settings.model_validate(yaml.safe_load((root / "settings.yaml").read_text(encoding="utf-8")))
        if settings.execution_mode != "local_only":
            raise InvariantError("Workspace cannot enable legacy execution")
        validate_paths(root, settings)
        return settings
    except (OSError, ValueError, ValidationError, yaml.YAMLError) as error:
        raise InvariantError(
            f"Cannot load workspace {root}; run intent-job-agent init --workspace PATH or repair it"
        ) from error


def initialize(path: str | Path | None = None) -> Path:
    root = Path(path or default_workspace()).expanduser().resolve()
    if root.exists():
        if (root / MARKER).exists() or (root / MARKER).is_symlink():
            load_workspace(root)
            return root
        if not root.is_dir() or any(root.iterdir()):
            raise InvariantError("Refusing to overwrite a non-workspace directory")
    root.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=".intent-init-", dir=root.parent))
    try:
        raw = yaml.safe_load(default_config().read_text(encoding="utf-8"))
        raw["execution_mode"] = "local_only"
        raw["storage"] = {"data_dir": str(root), "db_path": str(root / "agent.sqlite")}
        settings = Settings.model_validate(raw)
        validate_paths(root, settings)
        (staged / "settings.yaml").write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
        (staged / MARKER).write_text(json.dumps(VERSION) + "\n", encoding="utf-8")
        for file in staged.iterdir():
            file.chmod(0o600)
        # rename only onto an absent/empty target, never replace existing user files.
        staged.rename(root)
        return root
    except OSError as error:
        raise InvariantError(f"Cannot initialize workspace {root}: check directory permissions") from error
    finally:
        if staged.exists():
            shutil.rmtree(staged)


def resolve_settings(*, config: str | None = None, workspace: str | None = None) -> Settings:
    if config and workspace:
        raise InvariantError("Choose --config or --workspace, not both")
    return Settings.load(config) if config else load_workspace(workspace)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Internal prerelease: local workspace initialization only")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--workspace")
    args = parser.parse_args(argv)
    try:
        root = initialize(args.workspace)
    except (InvariantError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(
        f"Workspace: {root}\nInternal prerelease, local-only; "
        "personal data and business permissions are not configured."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())


def validate_applicant(root: Path, applicant) -> None:
    paths = [applicant.profile, *applicant.cover_letter_context, *applicant.apply_agent_read]
    paths += [p for resume in applicant.resumes for p in (resume.text, resume.pdf)]
    paths += [p for p in (applicant.resume_selection, applicant.cover_letter_skill) if p is not None]
    for path in paths:
        _inside(root, str(path))
