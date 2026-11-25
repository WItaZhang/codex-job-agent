"""tectonic compiles a version; poppler counts pages, draws the first page and extracts text (specs/resume-tailor.md
section 4, decision 8). Build files stay in a temporary folder."""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..config import ResumeTailor
from ..domain import InvariantError

COMPILE_SECONDS = 180
TOOL_SECONDS = 60


class Programs:
    def __init__(self, settings: ResumeTailor, run=subprocess.run):
        self.settings = settings
        self.run = run

    def path(self, name: str) -> str | None:
        configured = getattr(self.settings, name)
        if configured:
            return configured if Path(configured).expanduser().is_file() else None
        return shutil.which(name) or next(
            (str(p) for p in (Path("/opt/homebrew/bin") / name, Path("/usr/local/bin") / name) if p.is_file()), None
        )

    def available(self) -> dict[str, str | None]:
        return {name: self.path(name) for name in ("tectonic", "pdfinfo", "pdftoppm", "pdftotext")}

    def _bin(self, name: str) -> str:
        found = self.path(name)
        if not found:
            hint = "brew install tectonic" if name == "tectonic" else "brew install poppler"
            raise InvariantError(f"{name} is not installed ({hint}); resume tailoring needs it")
        return found

    def _call(self, argv: list[str], seconds: float) -> subprocess.CompletedProcess:
        try:
            return self.run(argv, capture_output=True, text=True, timeout=seconds)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise InvariantError(f"{Path(argv[0]).name} failed: {error}") from error

    def compile(self, tex: str, pdf: Path) -> None:
        with tempfile.TemporaryDirectory(prefix="resume-tailor-build-") as tmp:
            source = Path(tmp) / "resume.tex"
            source.write_text(tex, encoding="utf-8")
            out = self._call(
                [self._bin("tectonic"), "--chatter", "minimal", "--outdir", tmp, str(source)], COMPILE_SECONDS
            )
            built = Path(tmp) / "resume.pdf"
            if out.returncode != 0 or not built.is_file():
                tail = (out.stderr or out.stdout or "").strip().splitlines()[-8:]
                raise InvariantError("compile failed: " + " | ".join(tail))
            shutil.copyfile(built, pdf)

    def pages(self, pdf: Path) -> int:
        out = self._call([self._bin("pdfinfo"), str(pdf)], TOOL_SECONDS)
        m = re.search(r"^Pages:\s+(\d+)", out.stdout or "", re.M)
        if out.returncode != 0 or not m:
            raise InvariantError(f"pdfinfo could not read {pdf.name}")
        return int(m.group(1))

    def first_page_png(self, pdf: Path, png: Path) -> None:
        prefix = png.with_suffix("")
        out = self._call(
            [self._bin("pdftoppm"), "-png", "-r", "150", "-f", "1", "-l", "1", "-singlefile", str(pdf), str(prefix)],
            TOOL_SECONDS,
        )
        if out.returncode != 0 or not png.is_file():
            raise InvariantError(f"pdftoppm could not draw {pdf.name}")

    def text(self, pdf: Path) -> str:
        out = self._call([self._bin("pdftotext"), "-layout", str(pdf), "-"], TOOL_SECONDS)
        if out.returncode != 0:
            raise InvariantError(f"pdftotext could not read {pdf.name}")
        return out.stdout
