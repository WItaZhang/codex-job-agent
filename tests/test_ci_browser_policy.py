"""CI policy generation only; tests never install a host security policy."""

import runpy
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/ci_browser_userns.py"
BINARY = "/home/runner/.cache/ms-playwright/chromium-1194/chrome-linux/chrome"


def test_policy_is_exact_and_only_grants_userns():
    api = runpy.run_path(str(SCRIPT))
    name, text = api["policy"](BINARY, "123", "1")
    assert name == "intent-job-agent-ci-chromium-123-1"
    for run_id in ("../other", "123\n", "*", ""):
        with pytest.raises(ValueError):
            api["policy"](BINARY, run_id, "1")
    assert text == (f'abi <abi/4.0>,\nprofile {name} "{BINARY}" flags=(unconfined) {{\n  userns,\n}}\n')


@pytest.mark.parametrize(
    "path",
    [
        BINARY.replace("1194", "*"),
        BINARY + '" { userns, }',
        BINARY + "\n",
        BINARY.replace("1194", "@{HOME}"),
        BINARY.replace("chrome-linux", "../chrome-linux"),
        BINARY.replace("chrome-linux", "chrome linux"),
        "/tmp/workspace/chrome",
        BINARY + "\\*",
    ],
)
def test_nonliteral_or_unexpected_binary_is_rejected(path):
    api = runpy.run_path(str(SCRIPT))
    with pytest.raises(ValueError):
        api["policy"](path, "123", "1")


def test_local_or_self_hosted_environment_cannot_install():
    api = runpy.run_path(str(SCRIPT))
    for env, system in [({}, "Linux"), ({"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "self-hosted"}, "Linux")]:
        with pytest.raises(ValueError):
            api["check_runner"](env, system)
    valid = {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted", "RUNNER_OS": "Linux"}
    api["check_runner"](valid, "Linux")
    with pytest.raises(ValueError):
        api["check_runner"](valid, "Darwin")


def test_symlink_or_nonexecutable_binary_is_refused(tmp_path):
    api = runpy.run_path(str(SCRIPT))
    binary = tmp_path / "chrome"
    binary.write_text("synthetic")
    with pytest.raises(ValueError):
        api["validate_binary"](binary)
    binary.chmod(0o700)
    api["validate_binary"](binary)
    link = tmp_path / "alias"
    link.symlink_to(binary)
    with pytest.raises(ValueError):
        api["validate_binary"](link)


def test_policy_script_not_in_product_sdist_and_no_global_relaxation():
    import tomllib

    root = SCRIPT.parents[1]
    config = tomllib.loads((root / "pyproject.toml").read_text())
    assert "/scripts/ci_browser_userns.py" in config["tool"]["hatch"]["build"]["targets"]["sdist"]["exclude"]
    source = SCRIPT.read_text()
    for forbidden in ("sysctl -w", "--no-sandbox", "--disable-setuid-sandbox", "aa-complain", "service apparmor"):
        assert forbidden not in source
    assert "shell=True" not in source


def test_product_arguments_keep_chromium_sandbox():
    from intent_job_agent.browser_session import launch_arguments

    for headless in (False, True):
        args = launch_arguments(BINARY, "/synthetic/profile", headless=headless)
        assert not {"--no-sandbox", "--disable-setuid-sandbox", "--disable-namespace-sandbox"}.intersection(args)
