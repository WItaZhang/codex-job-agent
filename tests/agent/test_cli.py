"""Exercise the real CLI boundary used by skills, not just Python service calls."""

import json

import yaml
from typer.testing import CliRunner

from applypilot_agent.cli import app


def test_profile_and_local_job_import_through_cli(tmp_path):
    config = tmp_path / "agent.yaml"
    config.write_text(yaml.safe_dump({"data_dir": "state", "logs_dir": "logs"}), encoding="utf-8")
    profile = tmp_path / "candidate.json"
    profile.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "personal": {
                    "id": "contact",
                    "full_name": "Synthetic Candidate",
                    "evidence": {"source": "Synthetic CLI fixture", "confirmed": True},
                },
                "preferences": {"preferred_terms": ["Python"]},
            }
        ),
        encoding="utf-8",
    )
    jobs = tmp_path / "jobs.json"
    jobs.write_text(
        json.dumps(
            [
                {
                    "source": "manual",
                    "source_id": "local-case",
                    "url": "http://127.0.0.1:8099/job",
                    "apply_url": "http://127.0.0.1:8099/apply",
                    "title": "Python Engineer",
                    "company": "Synthetic",
                    "description": "Python work",
                }
            ]
        ),
        encoding="utf-8",
    )
    runner = CliRunner()
    prefix = ["--config", str(config)]
    assert runner.invoke(app, [*prefix, "profile-set", str(profile)]).exit_code == 0
    imported = runner.invoke(app, [*prefix, "import-jobs", str(jobs)])
    assert imported.exit_code == 0, imported.output
    assert json.loads(imported.output)["imported"] == 1
    status = runner.invoke(app, [*prefix, "status"])
    assert json.loads(status.output)["states"] == {"discovered": 1}
    dashboard = runner.invoke(app, [*prefix, "dashboard"])
    assert dashboard.exit_code == 0, dashboard.output
    assert (tmp_path / "state" / "dashboard.html").is_file()


def test_invalid_json_shape_reports_structured_error_without_importing(tmp_path):
    config = tmp_path / "agent.yaml"
    config.write_text(yaml.safe_dump({"data_dir": "state", "logs_dir": "logs"}), encoding="utf-8")
    jobs = tmp_path / "jobs.json"
    jobs.write_text("{}", encoding="utf-8")
    result = CliRunner().invoke(app, ["--config", str(config), "import-jobs", str(jobs)])
    assert result.exit_code == 1
    assert "JSON array" in result.output
    assert "Traceback" not in result.output
