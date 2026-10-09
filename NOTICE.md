# Project origin and acknowledgements

This project is a job-discovery agent focused on maintaining the user's job-search intent from daily
feedback. It is developed and maintained by WItaZhang in the
[codex-job-agent](https://github.com/WItaZhang/codex-job-agent) repository, on a branch with its own
history.

Parts of `src/intent_job_agent/discovery.py` (public Greenhouse, Lever and Ashby board fetching and
parsing) are adapted from `src/applypilot_agent/discovery.py` on that repository's `main` branch
(Codex Job Agent). Codex Job Agent in turn began in a checkout of
[Pickle-Pixel/ApplyPilot](https://github.com/Pickle-Pixel/ApplyPilot) (upstream revision `4a8d521`),
which we acknowledge as the starting point of that application domain.

Job discovery and full-text enrichment are reproduced from ApplyPilot itself at upstream revision
`4a8d521`: `src/intent_job_agent/vendor/applypilot/` contains ApplyPilot's `discovery/jobspy.py`,
`discovery/workday.py`, `discovery/smartextract.py`, `enrichment/detail.py`, `database.py` and `llm.py`
with only their imports changed, plus its bundled `sites.yaml`, `employers.yaml` and
`searches.example.yaml`. The `config.py` beside them is a replacement written for this project. We thank
the ApplyPilot authors for this work; job search through JobSpy follows their design.

Accordingly this project is licensed under the GNU Affero General Public License v3.0 only
(`AGPL-3.0-only`); the full text is in [LICENSE](LICENSE). Nothing here claims affiliation with the
upstream maintainers.
