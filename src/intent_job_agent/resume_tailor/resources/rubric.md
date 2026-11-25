# Resume rubric (general template)

The target role and its requirement files are named in the caller's message (files under `roles/`; when `jd.md` is
present, the job description decides the role focus). An applicant may replace this file with their own rubric.

## Six dimensions (1-5 each)

| key | dimension | what to look at |
|---|---|---|
| `role_focus` | Role focus | Identify the two most important deliverables and the seniority implied by the JD. Does the first screen show relevant evidence of ownership at that level? Distinguish required tools, preferred tools and interchangeable examples. |
| `transferability` | Transferability | Would an engineer in another domain recognize the transferable mechanism? Give equivalent implementations credit when the JD accepts alternatives. |
| `tech_purpose` | Technology and purpose | What work does the component do and who benefits? Judge a design by its interfaces, constraints and trade-offs, a prototype by what was tested, and a deployment by its observed behavior. Keep each measurement tied to the implementation on which it was measured. |
| `language` | Language | Clear, technical, concise; no filler or clichés; standard terms of the field instead of casual wording; bullets in one entry share a pattern (verb + what + with what + why) without repeating the opening verb |
| `appeal` | Appeal | After reading, the person seems capable, promising, creative and able to deliver |
| `format` | Format | Easy to scan, clear hierarchy, key points visible, polished (alignment, white space, font levels, no cramped lines or widows, especially one-word wrap lines and excessive empty space at the foot of the page); name, titles, employers, dates and schools are easy to find |

## Score anchors
- 5: nothing worth changing; among the best resumes of its kind.
- 4: solid overall, only small polish left.
- 3: readable but with clear weaknesses a recruiter may hesitate over.
- 2: problems that hurt understanding or judgment; the part needs rewriting.
- 1: essentially not done.

## Severity
- `major`: makes a recruiter misunderstand, lose the thread, or want an interview less.
- `minor`: polish; better if fixed, not fatal if not.

## Output (only this JSON)
```json
{
  "scores": {
    "role_focus":      {"score": 4, "reason": "one sentence"},
    "transferability": {"score": 4, "reason": "..."},
    "tech_purpose":    {"score": 4, "reason": "..."},
    "language":        {"score": 4, "reason": "..."},
    "appeal":          {"score": 4, "reason": "..."},
    "format":          {"score": 4, "reason": "..."}
  },
  "issues": [
    {"id": "I1", "location": "Experience, entry 1, bullet 2", "severity": "major",
     "problem": "what is wrong", "suggestion": "how to change it"}
  ]
}
```
- All six keys, no others; scores are integers 1-5.
- Issue ids run I1, I2, ... without gaps; an empty list when there is nothing to fix.
