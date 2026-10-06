# Project origin and acknowledgements

Codex Job Agent is developed and maintained by WItaZhang. It is a personal
job-search agent designed to run inside Codex, delivered as repository skills
and a Python tool runtime.

Development began in a checkout of
[Pickle-Pixel/ApplyPilot](https://github.com/Pickle-Pixel/ApplyPilot), with
upstream revision `4a8d521` as the starting reference. We acknowledge that
project as the starting point for the application domain and development
workspace.

This independent repository exports the Codex-oriented `src/applypilot_agent`,
its skills, tests, evaluation fixtures, configuration and documentation. The
original `src/applypilot` pipeline is not included. The Python module,
`applypilot-agent` command and `applypilot*` skill names are retained so existing
usage instructions remain compatible. They are not claims of affiliation with
the upstream maintainer.

The project retains the GNU Affero General Public License v3.0 only
(`AGPL-3.0-only`); the full license text is in [LICENSE](LICENSE). Creating an
independent repository does not remove the project's source attribution or
license notices.
