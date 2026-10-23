"""Disposable-source uv editable refresh probe; never edits the maintained skill."""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

CHECK = """
from pathlib import Path
from intent_job_agent.resources import default_config, letter_skill
source = Path.cwd()
assert default_config().read_bytes() == (source / 'configs/default.yaml').read_bytes()
a = source / '.claude/skills/cover-letter'
b = letter_skill()
def contents(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
assert contents(a) == contents(b), 'editable resource copy is stale'
"""


def main():
    source = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="intent-editable-probe-") as temp:
        root = Path(temp)
        for name in (
            "src",
            "configs/default.yaml",
            ".claude/skills/cover-letter",
            "pyproject.toml",
            "uv.lock",
            "LICENSE",
            "NOTICE.md",
            "README.md",
        ):
            original, target = source / name, root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if original.is_dir():
                shutil.copytree(original, target, ignore=shutil.ignore_patterns("__pycache__"))
            else:
                shutil.copy2(original, target)
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "PYTHONPATH", "PYTHONHOME")
        }

        def check(sync=False):
            if sync:
                subprocess.run(["uv", "sync", "--locked"], cwd=root, env=env, check=True)
            subprocess.run(["uv", "run", "--locked", "python", "-c", CHECK], cwd=root, env=env, check=True)

        check()
        config = root / "configs/default.yaml"
        config.write_text(config.read_text() + "\n# synthetic changed resource\n")
        check()
        ref = root / ".claude/skills/cover-letter/references/structure.md"
        ref.write_text(ref.read_text() + "\nSynthetic editable probe.\n")
        check(sync=True)
        added = ref.parent / "synthetic-added.txt"
        added.write_text("synthetic new resource")
        check()
        added.unlink()
        check()
        print(
            json.dumps(
                {
                    "editable_resource_changes": [
                        "config modified",
                        "reference modified",
                        "reference added",
                        "reference deleted",
                    ],
                    "uv_run_and_sync": "passed",
                }
            )
        )


if __name__ == "__main__":
    main()
