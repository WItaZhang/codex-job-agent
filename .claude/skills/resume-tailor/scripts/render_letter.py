"""Usage: uv run python <this script> letter.txt letter.pdf 'Applicant Name'."""

import argparse
from pathlib import Path

from intent_job_agent.apply_bridge import letter_date, render_letter_pdf

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("text", type=Path)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("name")
    args = parser.parse_args()
    render_letter_pdf(args.text.read_text(encoding="utf-8"), args.pdf, args.name, letter_date())
