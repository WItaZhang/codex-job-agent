"""Daily flow: record labels, find mismatches, run the two-step analysis, apply user choices."""

from .analysis import (
    Candidate,
    Context,
    build_proposals,
    candidate_refs,
    cause_for,
    classify,
    command_candidates,
    common_groups,
    enumerate_causes,
    exception_candidates,
    multi_ref_candidates,
    range_candidates,
    salary_proposal,
    single_ref_candidates,
    supporting_refs,
    target_resolved,
)
from .changes import SetLevel, parse_change
from .config import Settings
from .domain import DIMENSIONS, IntentModel, InvariantError, Label, expand, parse_ref
from .llm import LLMClient
from .prompts import CauseRanking, FeedbackChanges, ReasonMapping, feedback_prompt, ranking_prompt, reason_prompt
from .proposals import Analysis, Cause, DayReport, LabelInput, MisreadRequest, Proposal
from .store import Store, new_id

__all__ = ["Engine", "LabelInput"]

DIRECTION = {"false_positive": -1, "false_negative": 1}


class Engine:
    def __init__(self, store: Store, settings: Settings, llm: LLMClient):
        self.store = store
        self.settings = settings
        self.llm = llm

    def _ctx(self) -> Context:
        return Context(self.store.current_intent(), self.store.vocabulary(), self.store, self.settings)

    # --- daily labels ---------------------------------------------------------------------

    def process_day(self, day: str, inputs: list[LabelInput]) -> DayReport:
        ctx = self._ctx()
        labels = [self._record(ctx, day, item) for item in inputs]
        understanding = {label.id: item.understanding for label, item in zip(labels, inputs, strict=True)}
        report = DayReport()
        implicit: dict[str, list[Label]] = {}
        for label in labels:
            if label.value == "misread":
                report.misread.append(MisreadRequest(job_id=label.job_id, label_id=label.id, tags=ctx.tags(label)))
                continue
            kind = classify(ctx, label)
            if kind is None:
                self.store.add_support(label.id, supporting_refs(ctx, label))
                report.consistent.append(label.id)
            elif label.reason_keys or label.reason_text:
                report.analyses.append(self._explained(ctx, day, label, kind, understanding[label.id]))
            else:
                implicit.setdefault(kind, []).append(label)
        for kind, group in implicit.items():
            for members, common in common_groups(ctx, group):
                report.analyses.append(self._unexplained(ctx, day, kind, members, common))
        return report

    def _record(self, ctx: Context, day: str, item: LabelInput) -> Label:
        # Job tags are normalized through the vocabulary so aliases never become parallel keys.
        job = item.job.model_copy(update={"tags": list(dict.fromkeys(ctx.vocab.canonical(t) for t in item.job.tags))})
        self.store.add_job(job)
        tags = expand(list(job.tags))
        reasons = []
        for ref in item.reason_keys:
            ref = ctx.vocab.canonical(ref)
            if ref not in tags:
                raise InvariantError(f"Reason {ref} is not a tag of job {item.job.id}")
            reasons.append(ref)
        label = Label(
            id=item.label_id or new_id("label"),
            job_id=item.job.id,
            day=day,
            slot=item.slot,
            value=item.value,
            reason_keys=reasons,
            reason_text=item.reason_text,
        )
        self.store.add_label(label)
        return label

    def _explained(
        self, ctx: Context, day: str, label: Label, kind: str, mapping: ReasonMapping | None = None
    ) -> Analysis:
        """Step 1 is skipped when the user's reason identifies the cause."""
        direction = label.direction
        causes: list[Cause] = []
        step1 = "skip"
        if label.reason_keys:
            causes = [cause_for(ctx, [ref], direction) for ref in label.reason_keys]
        else:
            # The host agent may pass its reading of the reason; otherwise ask the model client.
            mapping = mapping or self.llm.structured(
                reason_prompt(ctx.intent, label.value, ctx.tags(label), label.reason_text, DIMENSIONS), ReasonMapping
            )
            if mapping.unexpressible:
                self.store.add_dimension_request(mapping.unexpressible, label.id)
            scopes = [(option.label, self._valid_refs(ctx, option.keys)) for option in mapping.scope_options]
            scopes = [(text, refs) for text, refs in scopes if refs]
            keys = self._valid_refs(ctx, mapping.keys)
            if len(scopes) >= 2:
                step1 = "ask_scope"
                causes = [cause_for(ctx, refs, direction).model_copy(update={"label": text}) for text, refs in scopes]
            elif keys:
                causes = [cause_for(ctx, keys, direction)]
            if mapping.mentions_salary:
                causes.append(Cause(id=new_id("cause"), refs=[], type="salary", label="薪资"))
            if not causes:
                step1 = "ask"
                causes = self._rank(ctx, [label], enumerate_causes(ctx, ctx.tags(label), direction))
        analysis = Analysis(
            id=new_id("analysis"),
            day=day,
            kind=kind,
            direction=direction,
            base_version=ctx.intent.version,
            tag_revision=self.store.tag_revision(),
            label_ids=[label.id],
            step1=step1,
            causes=causes,
        )
        if step1 == "skip":
            for cause in causes:
                analysis.proposals.extend(self._proposals(ctx, analysis, cause, [label]))
        self.store.save_analysis(analysis)
        return analysis

    def _unexplained(self, ctx: Context, day: str, kind: str, labels: list[Label], common: list[str]) -> Analysis:
        """Merged by common cause where possible; otherwise the user picks among candidate causes."""
        direction = DIRECTION[kind]
        if len(labels) > 1:
            causes = [cause_for(ctx, [ref], direction) for ref in candidate_refs(ctx, common)]
        else:
            causes = enumerate_causes(ctx, ctx.tags(labels[0]), direction)
        step1 = "skip" if len(causes) == 1 else "ask"
        if step1 == "ask":
            causes = self._rank(ctx, labels, causes)
        analysis = Analysis(
            id=new_id("analysis"),
            day=day,
            kind=kind,
            direction=direction,
            base_version=ctx.intent.version,
            tag_revision=self.store.tag_revision(),
            label_ids=[label.id for label in labels],
            step1=step1,
            causes=causes,
        )
        if step1 == "skip":
            analysis.chosen_cause = causes[0].id
            analysis.proposals = self._proposals(ctx, analysis, causes[0], labels)
        self.store.save_analysis(analysis)
        return analysis

    def _valid_refs(self, ctx: Context, refs: list[str]) -> list[str]:
        """Model output is only accepted as known-dimension references, canonicalized through the vocabulary."""
        valid = []
        for ref in refs:
            try:
                valid.append(ctx.vocab.canonical(parse_ref(ref).ref))
            except InvariantError:
                continue
        return list(dict.fromkeys(valid))

    def _rank(self, ctx: Context, labels: list[Label], causes: list[Cause]) -> list[Cause]:
        if len(causes) > 1:
            prompt = ranking_prompt(
                ctx.intent, labels[0].value, [ctx.tags(label) for label in labels], [(c.id, c.label) for c in causes]
            )
            order = self.llm.structured(prompt, CauseRanking).order
            by_id = {cause.id: cause for cause in causes}
            ranked = [by_id[i] for i in dict.fromkeys(order) if i in by_id]
            causes = ranked + [cause for cause in causes if cause.id not in order]
        return causes[: self.settings.analysis.max_causes]

    def _proposals(self, ctx: Context, analysis: Analysis, cause: Cause, targets: list[Label]) -> list[Proposal]:
        if cause.type == "salary":
            proposal = salary_proposal(ctx, analysis.id, cause.id, targets[0])
            return [proposal] if proposal else []
        direction = analysis.direction
        if len(cause.refs) == 1:
            ref = cause.refs[0]
            candidates = single_ref_candidates(ctx, ref, direction) + exception_candidates(ctx, ref)
            if parse_ref(ref).dimension == "location" and direction < 0:
                candidates += range_candidates(ctx, ref)
        else:
            candidates = multi_ref_candidates(ctx, cause.refs, direction)
        reason_refs = [ref for label in targets for ref in label.reason_keys]
        return build_proposals(ctx, analysis.id, cause.id, candidates, targets, analysis.kind, reason_refs)

    # --- user choices ---------------------------------------------------------------------

    def _open(self, analysis_id: str) -> Analysis:
        analysis = self.store.analysis(analysis_id)
        if analysis.status != "open":
            raise InvariantError(f"Analysis {analysis_id} is already {analysis.status}")
        if analysis.base_version != self.store.current_version():
            raise InvariantError(f"The intent changed since analysis {analysis_id}; refresh it first")
        if analysis.tag_revision != self.store.tag_revision():
            raise InvariantError(f"Job tags were backfilled since analysis {analysis_id}; refresh it first")
        return analysis

    def refresh(self, analysis_id: str) -> Analysis:
        """Re-check an open analysis after another accepted change: close it if already resolved,
        otherwise rebuild its causes and options on the current version."""
        analysis = self.store.analysis(analysis_id)
        ctx = self._ctx()
        revision = self.store.tag_revision()
        if analysis.status != "open" or (
            analysis.base_version == ctx.intent.version and analysis.tag_revision == revision
        ):
            return analysis
        self.store.close_pending(analysis.id)
        if analysis.kind == "command":  # no label to resolve: rebuild the same request on the new version
            analysis.base_version, analysis.tag_revision = ctx.intent.version, revision
            rejected: list[str] = []
            analysis.proposals = self._command_proposals(ctx, analysis, rejected)
            analysis.rejected_suggestions.extend(rejected)
            self.store.save_analysis(analysis)
            return analysis
        targets = [self.store.label(i) for i in analysis.label_ids]
        if all(target_resolved(ctx, analysis.kind, label) for label in targets):
            analysis.status = "resolved"
            analysis.proposals = []
            self.store.save_analysis(analysis)
            return analysis
        if analysis.tag_revision != revision and analysis.step1 == "ask":
            # Backfilled tags can add candidate causes; known causes keep their ids.
            if len(targets) > 1:
                common = sorted(set.intersection(*(set(ctx.tags(label)) for label in targets)))
                fresh_causes = [cause_for(ctx, [ref], analysis.direction) for ref in candidate_refs(ctx, common)]
            else:
                fresh_causes = enumerate_causes(ctx, ctx.tags(targets[0]), analysis.direction)
            known = {tuple(cause.refs): cause.id for cause in analysis.causes}
            causes = [
                cause.model_copy(update={"id": known.get(tuple(cause.refs), cause.id)})
                for cause in self._rank(ctx, targets, fresh_causes)
            ]
            if analysis.chosen_cause not in {cause.id for cause in causes}:
                analysis.chosen_cause = None
        else:
            causes = []
            for cause in analysis.causes:
                if cause.type == "salary":
                    causes.append(cause)
                    continue
                fresh = cause_for(ctx, cause.refs, analysis.direction)
                label = cause.label if cause.type == "scope" else fresh.label
                causes.append(fresh.model_copy(update={"id": cause.id, "label": label}))
        analysis.causes = causes
        analysis.base_version = ctx.intent.version
        analysis.tag_revision = revision
        chosen = [c for c in causes if c.id == analysis.chosen_cause] or (causes if analysis.step1 == "skip" else [])
        analysis.proposals = [p for cause in chosen for p in self._proposals(ctx, analysis, cause, targets)]
        self.store.save_analysis(analysis)
        return analysis

    def choose_cause(self, analysis_id: str, cause_id: str) -> Analysis:
        self.refresh(analysis_id)
        analysis = self._open(analysis_id)
        cause = next((c for c in analysis.causes if c.id == cause_id), None)
        if cause is None:
            raise InvariantError(f"Unknown cause {cause_id}")
        ctx = self._ctx()
        targets = [self.store.label(i) for i in analysis.label_ids]
        analysis.chosen_cause = cause.id
        analysis.proposals = self._proposals(ctx, analysis, cause, targets)
        self.store.save_analysis(analysis)
        return analysis

    def rank_causes(self, analysis_id: str, order: list[str]) -> Analysis:
        """The host agent orders the candidate causes; unknown ids are ignored, none are dropped."""
        analysis = self._open(analysis_id)
        by_id = {cause.id: cause for cause in analysis.causes}
        ranked = [i for i in dict.fromkeys(order) if i in by_id]
        analysis.causes = [by_id[i] for i in ranked] + [c for c in analysis.causes if c.id not in ranked]
        self.store.save_analysis(analysis)
        return analysis

    def feedback(self, analysis_id: str, text: str, changes: list[dict] | None = None) -> Analysis:
        """The user's own words become new options; they still need confirmation.

        `changes` is the host agent's translation of `text`; without it the model client is asked.
        """
        self.refresh(analysis_id)
        analysis = self._open(analysis_id)
        ctx = self._ctx()
        targets = [self.store.label(i) for i in analysis.label_ids]
        if changes is None and not targets:
            raise InvariantError("Feedback on an explicit command needs the changes it asks for")
        if changes is None:
            prompt = feedback_prompt(ctx.intent, targets[0].value, [ctx.tags(label) for label in targets], text)
            changes = self.llm.structured(prompt, FeedbackChanges).changes
        raw_changes, changes, rejected = changes, [], []
        for raw in raw_changes:
            try:
                changes.append(self._suggested_change(ctx.intent, raw))
            except InvariantError as error:
                rejected.append(f"{raw}: {error}")
        self.store.record_decision(analysis.id, "feedback", {"text": text})
        proposals = []
        if changes and not rejected:
            reason_refs = [ref for label in targets for ref in label.reason_keys]
            proposals = build_proposals(
                ctx, analysis.id, None, [Candidate(changes)], targets, analysis.kind, reason_refs, "feedback", rejected
            )
        analysis.proposals = proposals
        analysis.rejected_suggestions.extend(rejected)
        self.store.save_analysis(analysis)
        return analysis

    # --- explicit commands (spec §10 item 27) ------------------------------------------------

    def propose_edit(self, day: str, text: str, changes: list[dict]) -> Analysis:
        """The user's explicit command, read by the agent as changes: options to confirm, nothing written."""
        ctx = self._ctx()
        analysis = Analysis(
            id=new_id("analysis"),
            day=day,
            kind="command",
            direction=0,
            base_version=ctx.intent.version,
            tag_revision=self.store.tag_revision(),
            request=text,
            request_changes=[dict(raw) for raw in changes],
            label_ids=[],
            step1="skip",
            causes=[],
        )
        rejected: list[str] = []
        analysis.proposals = self._command_proposals(ctx, analysis, rejected)
        if not analysis.proposals:
            raise InvariantError("The command cannot be applied: " + ("; ".join(rejected) or "no change given"))
        self.store.save_analysis(analysis)
        self.store.record_decision(analysis.id, "command", {"text": text, "changes": analysis.request_changes})
        return analysis

    def _command_proposals(self, ctx, analysis: Analysis, rejected: list[str]) -> list[Proposal]:
        """The command as asked (it must apply cleanly) and, for a hard level, its soft counterpart."""
        changes = []
        for raw in analysis.request_changes:
            try:
                changes.append(self._suggested_change(ctx.intent, raw))
            except InvariantError as error:
                rejected.append(f"{raw}: {error}")
        if rejected or not changes:
            return []
        candidates = command_candidates(ctx, changes)
        errors: list[str] = []
        proposals = build_proposals(ctx, analysis.id, None, candidates, [], "command", [], "command", errors)
        asked = candidates[0].key
        if not any(frozenset(change.summary() for change in p.changes) == asked for p in proposals):
            rejected.extend(errors or ["the requested change is not valid"])
            return []
        return proposals

    @staticmethod
    def _suggested_change(intent: IntentModel, raw: dict):
        raw = dict(raw)
        if raw.get("op") == "set":
            if "level" in raw and "to_level" not in raw:
                raw["to_level"] = raw.pop("level")
            entry = intent.entry(raw["ref"]) if "ref" in raw else None
            raw.setdefault("from_level", entry.level if entry else None)
        change = parse_change(raw)
        if isinstance(change, SetLevel) and change.from_level is None:
            raise InvariantError(f"{change.ref} has no entry to change")
        return change

    def decide(
        self, analysis_id: str, proposal_id: str, inputs: dict | None = None, approved_via: str = "direct"
    ) -> IntentModel:
        analysis = self._open(analysis_id)
        if proposal_id not in {p.id for p in analysis.proposals}:
            raise InvariantError(f"Proposal {proposal_id} was not offered in analysis {analysis_id}")
        intent = self.store.apply_decision(proposal_id, inputs, approved_via)
        analysis.status = "accepted"
        self.store.save_analysis(analysis)
        return intent

    def decline(self, analysis_id: str) -> None:
        """ "这次不改": recorded, and carries no signal about the proposals."""
        analysis = self.store.analysis(analysis_id)
        if analysis.status != "open":
            raise InvariantError(f"Analysis {analysis_id} is already {analysis.status}")
        self.store.record_decision(analysis.id, "no_change")
        analysis.status = "declined"
        self.store.save_analysis(analysis)

    def correct_tags(self, job_id: str, remove: list[str], add: list[str], label_id: str | None = None) -> list[str]:
        """Misread flow: fix the job's tags. The intent is not touched."""
        vocab = self.store.vocabulary()
        tags = self.store.effective_tags(job_id)
        missing = [ref for ref in remove if ref not in tags]
        if missing:
            raise InvariantError(f"Job {job_id} has no tags {missing}")
        new_tags = [tag for tag in tags if tag not in remove]
        new_tags += [vocab.canonical(parse_ref(ref).ref) for ref in add if ref not in new_tags]
        self.store.set_effective_tags(job_id, new_tags, label_id)
        return new_tags
