"""Thin JSON command interface for Codex skills and local operators."""

import json
from collections import Counter
from functools import wraps
from pathlib import Path
from typing import Annotated

import typer

from .config import Settings, load_settings
from .models import Assessment, Job, Packet, Profile
from .serialization import canonical, digest
from .service import AgentService

app = typer.Typer(no_args_is_help=True, help="Evidence-driven local tools for the Codex job agent.")


def emit(value):
    typer.echo(canonical(value))


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def guarded(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (ValueError, TypeError, OSError, RuntimeError) as exc:
            typer.echo(canonical({"error": type(exc).__name__, "message": str(exc)}), err=True)
            raise typer.Exit(1) from exc

    return wrapper


@app.callback()
@guarded
def main(
    ctx: typer.Context,
    config: Annotated[Path, typer.Option(help="All paths/settings come from this YAML.")] = Path("configs/agent.yaml"),
):
    ctx.obj = AgentService(load_settings(config))


@app.command()
def schema(kind: str):
    """Print a JSON Schema before writing an input file."""
    models = {"profile": Profile, "job": Job, "assessment": Assessment, "packet": Packet, "config": Settings}
    if kind == "quality-report":
        from .quality import QualityReport

        emit(QualityReport.model_json_schema())
    elif kind in {"improvement", "improvement-validation"}:
        from .improvement_models import ImprovementProposal, ImprovementValidation

        emit((ImprovementProposal if kind == "improvement" else ImprovementValidation).model_json_schema())
    elif kind == "browser":
        from .browser import BrowserPlan

        emit(BrowserPlan.model_json_schema())
    elif kind in models:
        emit(models[kind].model_json_schema())
    else:
        raise typer.BadParameter(
            "Use profile, job, assessment, packet, browser, config, quality-report, improvement or improvement-validation"
        )


@app.command("profile-set")
@guarded
def profile_set(ctx: typer.Context, path: Path):
    """Save confirmed facts and preferences from onboarding; never infer confirmation."""
    emit(ctx.obj.save_profile(Profile.model_validate(read_json(path))))


@app.command()
@guarded
def profile(ctx: typer.Context):
    value = ctx.obj.profile()
    emit({"profile": value.model_dump(), "profile_hash": digest(value)})


@app.command("import-jobs")
@guarded
def import_jobs(ctx: typer.Context, path: Path):
    """Import a JSON array of canonical jobs, including manually researched postings."""
    from .discovery import normalize_job

    values = read_json(path)
    if not isinstance(values, list):
        raise TypeError("Job import must be a JSON array")
    emit(ctx.obj.import_jobs([Job.model_validate(normalize_job(value)) for value in values]))


@app.command()
@guarded
def discover(ctx: typer.Context, provider: str, board: str):
    """Read a public Greenhouse, Lever or Ashby board and persist deduplicated jobs."""
    from .discovery import fetch_board

    jobs = fetch_board(provider, board, timeout_seconds=ctx.obj.settings.request_timeout_seconds)
    emit(ctx.obj.import_jobs([Job.model_validate(value) for value in jobs]))


@app.command()
def jobs(ctx: typer.Context):
    emit(ctx.obj.list_jobs())


@app.command()
@guarded
def context(ctx: typer.Context, job_id: str):
    """Get facts, job, versions, policy and current application together."""
    emit(ctx.obj.context(job_id))


@app.command()
@guarded
def assess(ctx: typer.Context, job_id: str, path: Annotated[Path | None, typer.Option()] = None):
    """Persist a Codex assessment, or run the transparent lexical baseline."""
    value = Assessment.model_validate(read_json(path)) if path else None
    emit(ctx.obj.assess(job_id, value))


@app.command()
@guarded
def plan(ctx: typer.Context, limit: Annotated[int | None, typer.Option(min=1)] = None):
    """Return bounded work with reserved high-fit capacity and oldest-first remainder."""
    emit(ctx.obj.plan(limit))


@app.command("packet-set")
@guarded
def packet_set(ctx: typer.Context, path: Path):
    emit(ctx.obj.save_packet(Packet.model_validate(read_json(path))))


@app.command()
@guarded
def render(
    ctx: typer.Context,
    job_id: str,
    fact: Annotated[list[str], typer.Option(help="Repeat for each selected fact ID.")],
    pdf: bool = True,
):
    """Render selected confirmed facts into an isolated resume artifact."""
    emit(ctx.obj.render(job_id, fact, pdf=pdf))


@app.command()
def inbox(ctx: typer.Context):
    """One queue for review, missing information and uncertain outcomes."""
    emit(ctx.obj.inbox())


@app.command()
@guarded
def approve(ctx: typer.Context, job_id: str, packet_hash: str, note: Annotated[str, typer.Option()]):
    """Record actual user approval of this exact packet; Codex may not invent it."""
    emit(ctx.obj.approve(job_id, packet_hash, note))


@app.command()
@guarded
def inspect(ctx: typer.Context, job_id: str):
    """Observe the actual form using an isolated browser."""
    from .execution import Executor

    emit(Executor(ctx.obj).inspect(job_id))


@app.command()
@guarded
def execute(ctx: typer.Context, job_id: str, submit: Annotated[bool, typer.Option()] = False):
    """Prepare by default. --submit enforces authorization and records submission intent."""
    from .execution import Executor

    emit(Executor(ctx.obj).execute(job_id, dry_run=not submit))


@app.command()
@guarded
def recover(ctx: typer.Context):
    """Move interrupted submission intents to unknown; never automatically retry them."""
    from .execution import Executor

    emit(Executor(ctx.obj).recover())


@app.command()
@guarded
def reconcile(ctx: typer.Context, job_id: str, evidence: Path, outcome: str, note: Annotated[str, typer.Option()]):
    """Record the user's externally verified result for an unknown application."""
    from .execution import Executor

    emit(Executor(ctx.obj).reconcile(job_id, evidence, outcome, note))


@app.command()
def events(ctx: typer.Context, job_id: Annotated[str | None, typer.Option()] = None):
    emit(ctx.obj.store.events(job_id))


@app.command()
@guarded
def feedback(
    ctx: typer.Context,
    job_id: str,
    interested: str,
    reason: Annotated[str, typer.Option()],
    high_priority: Annotated[bool, typer.Option()] = False,
):
    """Record explicit user feedback, never a generated label or inferred non-click."""
    emit(ctx.obj.record_feedback(job_id, interested, reason, high_priority))


@app.command()
def status(ctx: typer.Context):
    from .quality import QualityService

    items = ctx.obj.list_jobs()
    emit(
        {
            "jobs": len(items),
            "states": dict(Counter(item["application"]["state"] for item in items)),
            "database": str(ctx.obj.store.path),
            "inbox": len(ctx.obj.inbox()),
            "quality": QualityService(ctx.obj).summary(),
        }
    )


@app.command()
@guarded
def dashboard(ctx: typer.Context):
    """Generate a readable local review dashboard without changing authorization."""
    from .dashboard import write_dashboard

    emit({"path": str(write_dashboard(ctx.obj, ctx.obj.settings.data_dir / "dashboard.html"))})


@app.command("export-observations")
@guarded
def export_observed(ctx: typer.Context, mapping: Path, output: Path):
    """Export evaluation predictions from persisted events using an external task/job mapping."""
    from .observations import export_observations

    emit(export_observations(ctx.obj, read_json(mapping), output))


@app.command("quality-sync")
@guarded
def quality_sync(ctx: typer.Context):
    """Recover confirmed submission batches without changing application outcomes."""
    from .quality import QualityService

    emit(QualityService(ctx.obj).sync())


@app.command("quality-inbox")
@guarded
def quality_inbox(ctx: typer.Context):
    """Show sampled audit work and evidence-linked alerts for the active session."""
    from .quality import QualityService

    quality = QualityService(ctx.obj)
    emit({"summary": quality.summary(), "audits": quality.queue(), "alerts": quality.alerts()})


@app.command("quality-prepare")
@guarded
def quality_prepare(ctx: typer.Context, ticket_id: str):
    """Export frozen, neutral review inputs; separate reviewers need fresh contexts."""
    from .quality import QualityService

    emit(QualityService(ctx.obj).prepare(ticket_id))


@app.command("quality-record")
@guarded
def quality_record(ctx: typer.Context, path: Path):
    """Record independent model-proxy reviews; never actual employer feedback."""
    from .quality import QualityService

    emit(QualityService(ctx.obj).record_report(read_json(path)))


@app.command("quality-report")
@guarded
def quality_report(ctx: typer.Context, ticket_id: str):
    """Read the complete sampled review, cited findings and alert state."""
    from .quality import QualityService

    emit(QualityService(ctx.obj).detail(ticket_id))


@app.command("quality-ack")
@guarded
def quality_ack(ctx: typer.Context, alert_id: str):
    """Mark an alert presented to the user; does not declare its concern resolved."""
    from .quality import QualityService

    emit(QualityService(ctx.obj).acknowledge_alert(alert_id))


@app.command("improvement-propose")
@guarded
def improvement_propose(ctx: typer.Context, path: Path):
    """Record a root-cause hypothesis and regression plan for an actual audit alert."""
    from .improvement import ImprovementService
    from .quality import QualityService

    value = read_json(path)
    alerts = QualityService(ctx.obj).alerts()
    alert = next(
        (item for item in alerts if item.get("id", item.get("alert_id")) == value.get("source_alert_id")), None
    )
    if alert is None:
        raise ValueError("Unknown quality alert; retain the original evidence before proposing an improvement")
    emit(ImprovementService(ctx.obj).propose(value, alert))


@app.command("improvement-validate")
@guarded
def improvement_validate(ctx: typer.Context, path: Path):
    """Archive regression/comparison evidence and independent review; never deploy."""
    from .improvement import ImprovementService

    emit(ImprovementService(ctx.obj).validate(read_json(path)))


@app.command("improvements")
@guarded
def improvements(ctx: typer.Context):
    """List root-cause proposals and their separate development validation records."""
    from .improvement import ImprovementService

    emit(ImprovementService(ctx.obj).records())


if __name__ == "__main__":
    app()
