"""Masking by column (specs/form-fill.md 5.6, second way): a field whose question goes to a contact.* entry of the
answer sheet keeps only placeholders and place names in its value and options, whatever masking by value left;
an old address or a personal site that profile.json does not hold is hidden too. Applied to every read before it
is placed on a page or written, so the recording, state.json, the screenshots and the saved HTML never hold it."""

from dataclasses import replace

from .compare import contact_shown
from .matching import match_question
from .models import AnswerEntry
from .recorder import Snapshot
from .redact import Redactor


def is_contact(question: str, sheet: dict[str, AnswerEntry]) -> bool:
    """Whether a question goes to a contact.* entry (or ties with one), as the fact check would match it."""
    if not sheet:
        return False
    found = match_question(question, sheet)
    return bool(found.key and found.key.startswith("contact.")) or any(
        key.startswith("contact.") for key in found.ambiguous
    )


def mask_snapshot(snap: Snapshot, redactor: Redactor, sheet: dict[str, AnswerEntry]):
    """(the read masked by value and by column, the (frame, path) of each contact field to hide in screenshots and
    saved HTML, whether masking by column hid any text)."""
    fields, hide, hidden = [], [], False
    for i, f in enumerate(snap.fields):
        question = redactor.mask(f.question)
        value = redactor.mask(f.value)
        options = tuple(redactor.mask(o) for o in f.options)
        if is_contact(question, sheet):
            shown, shown_options = contact_shown(value), tuple(contact_shown(o) for o in options)
            hidden = hidden or shown != value or shown_options != options
            value, options = shown, shown_options
            if i < len(snap.paths) and snap.paths[i]:
                hide.append((snap.frames[i] if i < len(snap.frames) else 0, snap.paths[i]))
        fields.append(
            f.model_copy(
                update={"question": question, "value": value, "options": options, "section": redactor.mask(f.section)}
            )
        )
    masked = replace(
        snap, url=redactor.mask(snap.url), marker=tuple(redactor.mask(m) for m in snap.marker), fields=tuple(fields)
    )
    return masked, tuple(hide), hidden
