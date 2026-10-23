"""Decision 26: synthetic attempts only; numeric thresholds are not full B acceptance."""

import datetime

import pytest

from intent_job_agent.apply_fill import week_stats
from intent_job_agent.tools import Toolbox

from .conftest import settings

WEEK = datetime.date(2026, 10, 12)


def attempts(success, failed=0, reason="expired", week=WEEK):
    return [
        {
            "job_id": f"synthetic-{n}",
            "at": f"{week.isoformat()}T12:00:00+00:00",
            "via": "applypilot",
            "status": "applied" if n < success else "failed",
            "reason": None if n < success else reason,
        }
        for n in range(success + failed)
    ]


@pytest.mark.parametrize(
    "success,failed,reason,met,quantity,rate,clean,status",
    [
        (200, 40, "program_error", True, True, True, False, "not_met"),
        (200, 50, "expired", True, True, True, True, "unverified"),
        (199, 0, None, False, False, True, True, "not_met"),
        (0, 0, None, False, False, None, True, "not_met"),
        (200, 51, "expired", False, True, False, True, "not_met"),
        (200, 1, None, True, True, True, False, "not_met"),
    ],
)
def test_acceptance_distinguishes_numeric_failures_and_missing_evidence(
    success, failed, reason, met, quantity, rate, clean, status
):
    result = week_stats(attempts(success, failed, reason), WEEK, datetime.UTC, settings().apply.done)
    assert result["met"] is met
    assert result["assessment"] == {
        "quantity_met": quantity,
        "success_rate_met": rate,
        "no_to_fix_failures": clean,
        "b_acceptance": status,
        "missing_evidence": ["daily_user_time", "two_consecutive_weeks"],
    }
    line = result["line"]
    assert "完整 B 验收" in line and "未采集" in line and "连续两周" in line
    assert "。达标。" not in line
    if not quantity:
        assert "数量门槛未达到" in line
    if not clean:
        assert "待修复失败" in line and "条件未满足" in line
    if status == "unverified":
        assert "数量/成功率门槛达到" in line and "未证实" in line


def test_thresholds_still_use_configuration():
    done = settings().apply.done.model_copy(update={"weekly_target": 10, "success_rate": 0.5})
    result = week_stats(attempts(10, 10), WEEK, datetime.UTC, done)
    assert result["met"] and result["assessment"]["quantity_met"]
    assert result["assessment"]["b_acceptance"] == "unverified"


def test_two_numeric_weeks_do_not_prove_time_or_full_acceptance(monkeypatch):
    from intent_job_agent import tools

    class Today(datetime.date):
        @classmethod
        def today(cls):
            return WEEK + datetime.timedelta(days=7)

    monkeypatch.setattr(tools, "date", Today)

    class Box:
        settings = settings()

        class store:
            @staticmethod
            def apply_attempts():
                return attempts(200) + attempts(200, week=WEEK + datetime.timedelta(days=7))

    result = Toolbox.apply_stats(Box())
    assert len(result["weeks"]) == 2
    assert all(w["met"] for w in result["weeks"])
    assert all(w["assessment"]["b_acceptance"] == "unverified" for w in result["weeks"])
    assert all("daily_user_time" in w["assessment"]["missing_evidence"] for w in result["weeks"])
