"""Freeze and verify exact pre-submit bytes; no database or model operations."""

import hashlib
import json
import re
from pathlib import Path

from ..profile_evidence import confirmed_evidence
from ..profile_models import Profile
from ..serialization import canonical, digest, utc_now
from .rubric import RUBRIC


def bytes_hash(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()


def write_json(path: Path, value: object) -> str:
    contents = canonical(value).encode("utf-8")
    path.write_bytes(contents)
    return bytes_hash(contents)


def within(directory: Path, relative: str) -> Path:
    path = (directory / relative).resolve()
    if not path.is_relative_to(directory.resolve()):
        raise ValueError("Quality artifact path escapes its archive")
    return path


def freeze(directory: Path, attempt_id: str, packet, profile, job) -> dict:
    """Called before durable submit intent. Failure prevents any submit click."""
    output = directory / "submissions" / attempt_id
    output.mkdir(parents=True, exist_ok=False)
    attachments = []
    for index, attachment in enumerate(packet.attachments, 1):
        contents = Path(attachment.path).read_bytes()
        if bytes_hash(contents) != attachment.sha256:
            raise ValueError("Attachment changed while freezing submission")
        suffix = Path(attachment.path).suffix.lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
            suffix = ".bin"
        name = f"attachment-{index}{suffix}"
        (output / name).write_bytes(contents)
        attachments.append({"source_id": f"attachment-{index}", "path": name, "sha256": attachment.sha256})
    manifest = {
        "schema_version": 1,
        "attempt_id": attempt_id,
        "job_id": job.id,
        "packet_hash": digest(packet),
        "created_at": utc_now(),
        "packet": packet.model_dump(mode="json"),
        "profile": profile.model_dump(mode="json"),
        "job": job.model_dump(mode="json"),
        "attachments": attachments,
    }
    manifest_hash = write_json(output / "manifest.json", manifest)
    return {
        "attempt_id": attempt_id,
        "job_id": job.id,
        "packet_hash": digest(packet),
        "manifest_path": str((output / "manifest.json").resolve()),
        "manifest_hash": manifest_hash,
        "created_at": manifest["created_at"],
    }


def verified_snapshot(directory: Path, record: dict) -> tuple[dict, Path]:
    path = within(directory, record["manifest_path"])
    contents = path.read_bytes()
    if bytes_hash(contents) != record["manifest_hash"]:
        raise ValueError("Submission archive manifest hash mismatch")
    manifest = json.loads(contents)
    if any(manifest[key] != record[key] for key in ("attempt_id", "job_id", "packet_hash")):
        raise ValueError("Submission archive identity mismatch")
    if digest(manifest["packet"]) != record["packet_hash"]:
        raise ValueError("Submission packet hash mismatch")
    for item in manifest["attachments"]:
        if bytes_hash(within(path.parent, item["path"]).read_bytes()) != item["sha256"]:
            raise ValueError("Submission attachment hash mismatch")
    return manifest, path.parent


def archived_evidence(profile: dict, job_id: str) -> list[dict]:
    # Read-only compatibility for immutable pre-v2 submission archives.
    if "facts" in profile and "schema_version" not in profile:
        return [
            fact
            for fact in profile["facts"]
            if fact["confirmed"] and (not fact["scope_job_ids"] or job_id in fact["scope_job_ids"])
        ]
    return [item.model_dump() for item in confirmed_evidence(Profile.model_validate(profile), job_id).values()]


def export_bundle(output: Path, ticket: dict, manifest: dict, source_dir: Path) -> dict:
    """Neutral copied files: no original filenames, producer grades or browser plan."""
    output.mkdir(parents=True, exist_ok=False)
    packet, job = manifest["packet"], manifest["job"]
    fields = packet["browser_plan"].get("fields", [])
    submitted_selectors = {item["selector"] for item in fields if item["kind"] != "file"}
    uploaded_paths = {str(Path(item["value"]).resolve()) for item in fields if item["kind"] == "file"}
    submitted_answers = {key: value for key, value in packet["answers"].items() if key in submitted_selectors}
    uploaded = [
        item
        for item, original in zip(manifest["attachments"], packet["attachments"], strict=True)
        if str(Path(original["path"]).resolve()) in uploaded_paths
    ]
    files = []
    for attachment in uploaded:
        contents = within(source_dir, attachment["path"]).read_bytes()
        if bytes_hash(contents) != attachment["sha256"]:
            raise ValueError("Submission attachment changed during export")
        (output / attachment["path"]).write_bytes(contents)
        files.append({**attachment, "kind": "attachment"})
    hiring = {
        "rubric": {key: RUBRIC[key] for key in ("version", "provenance", "hiring", "shared")},
        "job": {key: job[key] for key in ("title", "company", "location", "description")},
        "answers": [
            {"id": f"answer-{index}", "field": key, "value": answer["value"]}
            for index, (key, answer) in enumerate(submitted_answers.items(), 1)
        ],
        "attachments": uploaded,
    }
    factual = {
        "rubric": {key: RUBRIC[key] for key in ("version", "provenance", "factual", "shared")},
        "facts": archived_evidence(manifest["profile"], manifest["job_id"]),
        "answer_links": [
            {"id": f"answer-{index}", "fact_ids": answer["fact_ids"]}
            for index, answer in enumerate(submitted_answers.values(), 1)
        ],
        "attachment_links": [
            {"id": f"attachment-{index}", "fact_ids": item["fact_ids"]}
            for index, item in enumerate(packet["attachments"], 1)
            if str(Path(item["path"]).resolve()) in uploaded_paths
        ],
    }
    for source_id, content in (("hiring", hiring), ("factual", factual)):
        name = f"{source_id}.json"
        files.append(
            {"source_id": source_id, "path": name, "sha256": write_json(output / name, content), "kind": "json"}
        )
    bundle = {"schema_version": 1, "ticket_id": ticket["id"], "rubric_version": RUBRIC["version"], "files": files}
    bundle_hash = write_json(output / "bundle.json", bundle)
    return {"bundle_path": str((output / "bundle.json").resolve()), "bundle_hash": bundle_hash}


def verified_bundle(directory: Path, ticket: dict) -> tuple[dict, Path]:
    path = within(directory, ticket["bundle_path"])
    contents = path.read_bytes()
    if bytes_hash(contents) != ticket["bundle_hash"]:
        raise ValueError("Review bundle manifest hash mismatch")
    bundle = json.loads(contents)
    if bundle["ticket_id"] != ticket["id"] or bundle["rubric_version"] != RUBRIC["version"]:
        raise ValueError("Review bundle identity mismatch")
    for item in bundle["files"]:
        if bytes_hash(within(path.parent, item["path"]).read_bytes()) != item["sha256"]:
            raise ValueError("Review bundle file hash mismatch")
    return bundle, path.parent


def validate_citations(report, bundle: dict, directory: Path) -> None:
    sources = {item["source_id"]: item for item in bundle["files"]}
    for issue in report.issues:
        for citation in issue.citations:
            if citation.source_id not in sources or (issue.view == "hiring" and citation.source_id == "factual"):
                raise ValueError("Citation source is absent or outside the reviewer's view")
            item = sources[citation.source_id]
            path = within(directory, item["path"])
            if item["kind"] == "json":
                value = json.loads(path.read_bytes())
                if not citation.locator.startswith("/"):
                    raise ValueError("JSON citation requires a JSON pointer")
                try:
                    for part in citation.locator[1:].split("/"):
                        key = part.replace("~1", "/").replace("~0", "~")
                        if isinstance(value, list) and not re.fullmatch(r"0|[1-9][0-9]*", key):
                            raise ValueError("Invalid JSON pointer array index")
                        value = value[int(key)] if isinstance(value, list) else value[key]
                except (ValueError, KeyError, IndexError, TypeError) as exc:
                    raise ValueError("Citation JSON pointer does not exist") from exc
                if citation.quote not in (value if isinstance(value, str) else canonical(value)):
                    raise ValueError("Citation quote does not match its JSON source")
            elif not re.fullmatch(r"(?:page|line|paragraph):[1-9][0-9]*", citation.locator):
                raise ValueError("Attachment citation requires page:N, line:N or paragraph:N")
            # Binary document quotations require independent semantic inspection. File hashes
            # bind the target; they cannot authenticate whether a model quoted it faithfully.
