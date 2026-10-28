"""Smart Backlog Assistant CLI.

    python app.py

Reads every .txt/.md/.pdf in samples/ as notes and samples/backlog.json as the backlog.
"""
import argparse
import json
import logging
import sys
from pathlib import Path

from backlog_assistant.analyzer import AnalysisError, analyze
from backlog_assistant.formatter import to_markdown
from backlog_assistant.parsers import InputError, load_backlog, load_document

SAMPLES_DIR = Path(__file__).parent / "samples"
BACKLOG_FILE = SAMPLES_DIR / "backlog.json"
NOTE_SUFFIXES = {".txt", ".md", ".pdf"}


def load_samples() -> tuple[str | None, list[dict] | None]:
    """Load all notes/requirements docs and the backlog from the samples folder."""
    if not SAMPLES_DIR.is_dir():
        raise InputError(f"Samples folder not found: {SAMPLES_DIR}")
    note_files = sorted(f for f in SAMPLES_DIR.iterdir() if f.suffix.lower() in NOTE_SUFFIXES)
    notes = "\n\n".join(f"=== {f.name} ===\n{load_document(str(f))}" for f in note_files) or None
    backlog = load_backlog(str(BACKLOG_FILE)) if BACKLOG_FILE.is_file() else None
    if not notes and not backlog:
        raise InputError(f"No notes or backlog.json found in {SAMPLES_DIR}")
    return notes, backlog


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Turn notes/requirements/backlog in samples/ into structured user stories.")
    p.add_argument("--out", help="Write JSON result here (default: print to stdout)")
    p.add_argument("--markdown", help="Also write a readable Markdown report here")
    p.add_argument("--model", help="OpenAI model ID (default: $BACKLOG_MODEL or gpt-4o)")
    p.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    try:
        notes, backlog = load_samples()
        result = analyze(notes, backlog, model=args.model)
    except (InputError, AnalysisError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    payload = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
        logging.info("Wrote %s", args.out)
    else:
        print(payload)
    if args.markdown:
        Path(args.markdown).write_text(to_markdown(result), encoding="utf-8")
        logging.info("Wrote %s", args.markdown)
    logging.info("Done: %d user stories", len(result["user_stories"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
