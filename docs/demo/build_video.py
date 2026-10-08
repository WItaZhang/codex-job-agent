"""Build the captioned demo videos from real browser footage of the local demo.

    uv run --locked python docs/demo/build_video.py capture --config configs/demo_video.yaml
    uv run --locked python docs/demo/build_video.py render --config configs/demo_video.yaml logs/<run_id>

``capture`` runs the synthetic loopback demo once with Playwright video recording
and ``slow_mo`` enabled, and stores each browser session's footage and timing under
``logs/<run_id>/footage``. Nothing is re-enacted: the executor, browser policy and
mock employer are the ones the demo always uses.

``render`` composes that footage with the run's own events, summary and employer
ledger into one MP4 per configured language, frame by frame, and needs ``ffmpeg``
on PATH. The run directory is task data; every other parameter is in the YAML.
"""

import argparse
import inspect
import json
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel

STORYBOARD = Path(__file__).parent / "storyboard.yaml"


class CaptureSettings(BaseModel):
    slow_mo_ms: int = 300
    width: int = 1280
    height: int = 720


class RenderSettings(BaseModel):
    width: int = 1280
    height: int = 720
    fps: int = 25
    crf: int = 23
    languages: list[str] = ["zh", "en"]


class VideoConfig(BaseModel):
    demo_config: Path
    output_dir: Path
    capture: CaptureSettings = CaptureSettings()
    render: RenderSettings = RenderSettings()


def load_config(path: Path) -> VideoConfig:
    path = path.resolve()
    config = VideoConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    config.demo_config = (path.parent / config.demo_config).resolve()
    config.output_dir = (path.parent / config.output_dir).resolve()
    return config


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _duration(path: Path) -> float:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(probe.stdout.strip())


def capture(config: VideoConfig) -> Path:
    """Run the demo once with recording on; return the run's log directory."""
    from playwright.sync_api import Browser, BrowserType

    from applypilot_agent.demo import run_demo
    from applypilot_agent.execution import Executor

    size = {"width": config.capture.width, "height": config.capture.height}
    calls: list[dict] = []
    sessions: list[dict] = []
    current: dict = {}
    staging = Path(tempfile.mkdtemp(prefix="demo-footage-"))

    original_launch = BrowserType.launch
    original_context = Browser.new_context
    original_close = Browser.close
    original_execute = Executor.execute
    original_inspect = Executor.inspect

    def launch(self, *args, **kwargs):
        kwargs["slow_mo"] = config.capture.slow_mo_ms
        return original_launch(self, *args, **kwargs)

    def new_context(self, *args, **kwargs):
        kwargs.update(record_video_dir=str(staging), record_video_size=size, viewport=size)
        record = {"call": len(calls) - 1, **current, "started_at": _now()}
        context = original_context(self, *args, **kwargs)
        # The video path must be read while Playwright is still running.
        context.on("page", lambda page: record.setdefault("video", page.video.path()))
        context.on("close", lambda _: record.setdefault("ended_at", _now()))
        sessions.append(record)
        return context

    def close(self, *args, **kwargs):
        # Playwright finishes writing a video only when its context closes.
        for context in list(self.contexts):
            context.close()
        return original_close(self, *args, **kwargs)

    def traced(step_of, original):
        signature = inspect.signature(original)

        def wrapper(self, *args, **kwargs):
            bound = signature.bind(self, *args, **kwargs)
            bound.apply_defaults()
            job_id, step = bound.arguments["job_id"], step_of(bound.arguments)
            current.update(job_id=job_id, step=step)
            call = {"job_id": job_id, "step": step, "started_at": _now()}
            calls.append(call)
            try:
                result = original(self, *args, **kwargs)
            except Exception as exc:
                call.update(ended_at=_now(), refused=str(exc))
                raise
            call.update(ended_at=_now(), state=result.get("state") if isinstance(result, dict) else None)
            return result

        return wrapper

    BrowserType.launch = launch
    Browser.new_context = new_context
    Browser.close = close
    Executor.execute = traced(lambda a: "dry_run" if a["dry_run"] else "submit", original_execute)
    Executor.inspect = traced(lambda a: "inspect", original_inspect)
    try:
        summary = run_demo(config.demo_config)
    finally:
        BrowserType.launch = original_launch
        Browser.new_context = original_context
        Browser.close = original_close
        Executor.execute = original_execute
        Executor.inspect = original_inspect

    run_dir = Path(summary["logs_dir"])
    footage = run_dir / "footage"
    footage.mkdir()
    clips = []
    for index, record in enumerate(sessions):
        video = record.pop("video", None)
        if video is None:
            continue
        name = f"{index:02d}-{record['job_id']}-{record['step']}.webm"
        shutil.move(video, footage / name)
        clips.append({**record, "file": name, "duration": _duration(footage / name)})
    shutil.rmtree(staging, ignore_errors=True)
    manifest = {
        "run_id": summary["run_id"],
        "slow_mo_ms": config.capture.slow_mo_ms,
        "size": size,
        "calls": calls,
        "clips": clips,
    }
    (footage / "footage.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return run_dir


def render(config: VideoConfig, run_dir: Path) -> list[Path]:
    """Render one MP4 (and a poster frame) per configured language."""
    from playwright.sync_api import sync_playwright
    from video_stage import Run, stage_html

    from applypilot_agent.demo_support import load_demo_config

    storyboard = yaml.safe_load(STORYBOARD.read_text(encoding="utf-8"))
    run = Run(run_dir.resolve())
    executable = load_demo_config(config.demo_config).browser.executable_path
    settings = config.render
    size = {"width": settings.width, "height": settings.height}
    config.output_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    with sync_playwright() as playwright, tempfile.TemporaryDirectory() as scratch:
        browser = playwright.chromium.launch(executable_path=str(executable) if executable else None)
        try:
            for lang in settings.languages:
                html, duration = stage_html(storyboard, run, lang)
                stage = Path(scratch) / f"stage.{lang}.html"
                stage.write_text(html, encoding="utf-8")
                page = browser.new_page(viewport=size)
                page.goto(stage.as_uri())
                page.evaluate("window.ready")
                target = config.output_dir / f"demo.{lang}.mp4"
                encoder = subprocess.Popen(
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-y",
                        "-f",
                        "image2pipe",
                        "-framerate",
                        str(settings.fps),
                        "-i",
                        "-",
                        "-c:v",
                        "libx264",
                        "-preset",
                        "slow",
                        "-crf",
                        str(settings.crf),
                        "-pix_fmt",
                        "yuv420p",
                        "-movflags",
                        "+faststart",
                        str(target),
                    ],
                    stdin=subprocess.PIPE,
                )
                previous, frame = None, b""
                poster_at = storyboard.get("poster_at", 0)
                for index in range(round(duration * settings.fps)):
                    t = index / settings.fps
                    signature = page.evaluate("t => window.renderAt(t)", t)
                    if signature != previous:
                        frame, previous = page.screenshot(type="png"), signature
                    encoder.stdin.write(frame)
                    if index == round(poster_at * settings.fps):
                        poster = config.output_dir / f"poster.{lang}.png"
                        poster.write_bytes(frame)
                        outputs.append(poster)
                encoder.stdin.close()
                if encoder.wait() != 0:
                    raise SystemExit(f"ffmpeg failed for {target}")
                page.close()
                outputs.append(target)
        finally:
            browser.close()
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["capture", "render"])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("run_dir", type=Path, nargs="?", help="logs/<run_id> written by capture (render only)")
    args = parser.parse_intermixed_args()
    config = load_config(args.config)
    if args.command == "capture":
        print(capture(config))
        return
    if args.run_dir is None:
        parser.error("render needs the run directory written by capture")
    for path in render(config, args.run_dir):
        print(path)


if __name__ == "__main__":
    main()
