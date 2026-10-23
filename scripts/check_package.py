"""Internal CI only: actual wheel + sdist roundtrip in disposable synthetic workspaces.

No upload, publish, browser, employer, or personal-data access. Dependencies may be fetched during installation.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

PROBE = r"""
import asyncio, json, os, subprocess, sys
from pathlib import Path
import yaml
import intent_job_agent
from intent_job_agent.resources import default_config, letter_skill
from intent_job_agent.workspace import initialize, load_workspace
from intent_job_agent.store import Store
from intent_job_agent.tools import Toolbox
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

base = Path.cwd()
assert Path(intent_job_agent.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
assert default_config().is_file()
assert (letter_skill() / 'references/structure.md').is_file()
a, b = base / 'workspace-a', base / 'workspace-b'
console_dir = Path(sys.prefix) / ('Scripts' if os.name == 'nt' else 'bin')
init_cli = console_dir / ('intent-job-agent.exe' if os.name == 'nt' else 'intent-job-agent')
server_cli = console_dir / ('intent-job-agent-mcp.exe' if os.name == 'nt' else 'intent-job-agent-mcp')
for target in (a,b):
    result = subprocess.run([str(init_cli),'init','--workspace',str(target)], capture_output=True,text=True)
    assert result.returncode == 0 and 'local-only' in result.stdout, result.stderr
missing = subprocess.run([str(server_cli),'--workspace',str(base / 'missing')],capture_output=True,text=True)
assert missing.returncode == 2 and 'intent-job-agent init' in missing.stderr
assert 'Traceback' not in missing.stderr and not (base / 'missing').exists()
settings = load_workspace(a)
# Only SYNTHETIC materials, no real profile needed for listing skill files.
app = a / 'applicant'; app.mkdir()
(app / 'synthetic.json').write_text(json.dumps({'personal':{'full_name':'Synthetic Example'}}))
(app / 'resume.md').write_text('Synthetic applicant')
(app / 'resume.pdf').write_bytes(b'%PDF synthetic')
(app / 'applicant.yaml').write_text(yaml.safe_dump({
    'profile':'synthetic.json', 'careful_companies':[],
    'resumes':[{'id':'synthetic','pdf':'resume.pdf','text':'resume.md','use_for':'synthetic'}]}))
store = Store(settings.storage.db_path, settings)
materials = Toolbox(settings, store).get_cover_letter_materials()
assert {f['id'] for f in materials['skill']['files']} == {'skill/SKILL.md','skill/references/structure.md'}
store.db.close()
assert settings.storage.db_path != load_workspace(b).storage.db_path
for name in ('cwd-a','cwd-b'):
    cwd = base / name; cwd.mkdir()
    os.chdir(cwd)
    assert load_workspace(a).storage.db_path == settings.storage.db_path
    assert not (cwd / 'data').exists()
os.chdir(base)
assert not (b / 'agent.sqlite').exists()
async def handshake(profile):
    params = StdioServerParameters(command=sys.executable, args=['-I','-m','intent_job_agent.mcp_server',
        '--workspace',str(a),'--profile',profile])
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            assert 'discover_jobs' in names
            if profile == 'full':
                assert len(names) == 47
            # Refusal path only: no website is visited, also for scheduled profile.
            for tool,args in [('discover_jobs',{}),('fetch_boards',{}),
                              ('check_board',{'provider':'greenhouse','board':'synthetic'})]:
                response = await session.call_tool(tool,args)
                assert response.is_error
                assert 'local-only' in str(response.content)
for profile in ('full','scheduled'):
    asyncio.run(handshake(profile))
# Exercise the second installed entrypoint, not just --help.
report = subprocess.run([sys.executable,'-I','-m','intent_job_agent.form_fill.cli','report','synthetic',
                         '--workspace',str(a)],capture_output=True,text=True)
assert report.returncode == 2 and 'no run synthetic' in report.stderr
assert 'Traceback' not in report.stderr
(app / 'answers.yaml').write_text(yaml.safe_dump({'version':1,'entries':{
    'name.first':{'answer':'Synthetic','kind':'text'},'name.last':{'answer':'Example','kind':'text'}}}))
job = a / 'form-fill/runs/synthetic/synthetic-job'; job.mkdir(parents=True)
(job / 'meta.json').write_text(json.dumps({'job_id':'synthetic-job','company':'Synthetic',
    'title':'Synthetic role','status':'filled','redaction':2,'checks':{'not_submitted':True}}))
(job / 'readback.json').write_text('[]')
report = subprocess.run([sys.executable,'-I','-m','intent_job_agent.form_fill.cli','report','synthetic',
                         '--workspace',str(a)],capture_output=True,text=True)
assert report.returncode == 0, report.stderr
assert (job.parent / 'summary.md').exists()
assert not (b / 'form-fill').exists()
print(json.dumps({'installed':True,'tools':47,'profiles':['full','scheduled'],
    'resource_closure':True,'cwd_independent':True,'form_fill_missing_run':True}))
"""


def run(args, cwd, env=None):
    subprocess.run([str(a) for a in args], cwd=cwd, env=env, check=True)


def resources(wheel):
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert not any(name.startswith((".claude/", ".codex/", "data/local/")) for name in names)
        return {
            name: hashlib.sha256(archive.read(name)).hexdigest()
            for name in names
            if name.startswith("intent_job_agent_data/")
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    source = args.source.resolve()
    env = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}}
    with tempfile.TemporaryDirectory(prefix="r1-synthetic-package-") as folder:
        root = Path(folder)
        artifacts = root / "direct"
        run(["uv", "build", "--wheel", "--out-dir", artifacts], source, env)
        run(["uv", "build", "--sdist", "--out-dir", artifacts], source, env)
        direct = next(artifacts.glob("*.whl"))
        sdist = next(artifacts.glob("*.tar.gz"))
        extracted = root / "extracted"
        extracted.mkdir()
        with tarfile.open(sdist) as archive:
            archive.extractall(extracted, filter="data")
        unpacked = next(extracted.iterdir())
        rebuilt = root / "rebuilt"
        run(["uv", "build", "--wheel", "--out-dir", rebuilt], unpacked, env)
        second = next(rebuilt.glob("*.whl"))
        expected = {"intent_job_agent_data/default.yaml": source / "configs/default.yaml"}
        for file in (source / ".claude/skills/cover-letter").rglob("*.md"):
            name = file.relative_to(source / ".claude/skills/cover-letter")
            expected[f"intent_job_agent_data/cover-letter/{name}"] = file
        hashes = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in expected.items()}
        assert resources(direct) == resources(second) == hashes
        for n, wheel in enumerate((direct, second)):
            venv = root / f"venv-{n}"
            run(["uv", "venv", "--python", sys.executable, venv], root, env)
            python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            run(["uv", "pip", "install", "--python", python, wheel], root, env)
            cwd = root / f"probe-{n}"
            cwd.mkdir()
            run([python, "-I", "-c", PROBE], cwd, env)
        print(
            json.dumps(
                {"internal_prerelease": True, "direct_wheel": "passed", "sdist_roundtrip": "passed", "uploaded": False}
            )
        )


if __name__ == "__main__":
    main()
