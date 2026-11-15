"""20+ SYNTHETIC jobs through old host tools. External collector, pages and agents are explicit fakes."""

import json
import socket
from pathlib import Path

import httpx
import yaml

from . import test_gate_handoff as gates
from .test_slice6 import write_letters

box = gates.box


def test_four_skill_tool_journey_without_external_business(box, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("external network forbidden in synthetic journey")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    for key in ("GEMINI_API_KEY", "OPENAI_API_KEY", "LLM_URL", "INTENT_AGENT_PROXY"):
        monkeypatch.delenv(key, raising=False)
    box.settings = box.settings.model_copy(update={"apply": box.settings.apply.model_copy(update={"review_first": 0})})
    boards = [
        {
            "provider": "greenhouse",
            "board": f"synthetic{n}",
            "company": "Stripe, Inc." if n == 1 else f"Synthetic Co {n}",
        }
        for n in range(24)
    ]
    (box.data_dir / box.settings.discovery.boards_file).write_text(yaml.safe_dump({"boards": boards}))
    calls = []

    def collector(request):
        calls.append(request)
        token = request.url.path.split("/")[3]
        return httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": token,
                        "absolute_url": f"https://jobs.example.test/{token}",
                        "title": f"Synthetic ML role {token}",
                        "content": "Synthetic machine learning position",
                        "location": {"name": "Seattle, US"},
                    }
                ]
            },
        )

    box.http_client = httpx.Client(transport=httpx.MockTransport(collector))
    result = box.fetch_boards(wait=True)
    assert sum(board["new"] for board in result["boards"]) == 24
    assert len(calls) == 24
    assert len(box.network_history()["sites"]) == 24
    all_jobs = box.store.jobs()
    ordinary = [job for job in all_jobs if job.company != "Stripe, Inc."]
    protected = next(job for job in all_jobs if job.company == "Stripe, Inc.")
    jobs = [ordinary[0], protected, *ordinary[1:]]
    for n, job in enumerate(jobs):
        tags = ["location.us.seattle", "role.ml_engineering" if n < 18 else "role.data_science"]
        if n == 0:
            tags.append("specialty.ai_infra")
        box.submit_job_tags(job.id, tags)
    day = "2026-10-23"
    today = box.select_today(day)["jobs"]
    assert [j["slot"] for j in today].count("recommended") == 7
    assert [j["slot"] for j in today].count("exploration") == 3
    target = next(j for j in today if j["job_id"] == jobs[0].id)
    # This is an explicit synthetic user message, not a model-inferred label.
    response = box.record_labels(
        day,
        [
            {
                "job_id": target["job_id"],
                "value": "reject",
                "reason_text": "Synthetic user: no more AI infrastructure",
                "understanding": {"keys": ["specialty.ai_infra"]},
            }
        ],
    )
    analysis = box.get_analysis(response["analyses"][0]["id"])
    option = next(o for o in analysis["options"] if "proposal_id" in o)
    changed = box.decide(analysis["id"], option["n"], option["summary"])
    assert changed["version"] == 2
    listing = box.build_apply_list(day)
    assert listing["jobs"]
    assert json.loads(Path(listing["files"]["json"]).read_text())
    selected = [jobs[n].id for n in (1, 2, 3)]
    write_letters(box, selected)
    box.apply_runner = gates.GateRunner({selected[2]: ("held", "login_issue", ["Synthetic login gate"])})
    batch = box.prepare_apply_batch(day, only=selected)
    box.start_applying(batch["batch_id"], batch["summary"])
    status = box.apply_status()
    assert status["status"] == "done"
    applications = box.store.applications()
    assert applications[selected[0]]["status"] == "waiting"
    assert applications[selected[1]]["status"] == "applied"
    assert applications[selected[2]]["status"] == "held"
    assert all(spec.mode != "submit" or spec.company != "Stripe, Inc." for spec in box.apply_runner.calls)
