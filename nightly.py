"""The unattended nightly job: refresh prices, check them, rebuild the pages.

Usage:
  python nightly.py                # refresh, validate, build — but do not publish
  python nightly.py --publish      # also commit and push to GitHub Pages
  python nightly.py --skip-refresh # validate and build from data already on disk
  python nightly.py --force        # run even if today already had a successful run

Publishing is off by default on purpose: run it for a week, read the summaries in
data/runs/, and turn --publish on once the numbers look right.

Every run writes data/runs/<timestamp>.json and appends one line to data/runs/log.txt.
A failed or blocked run shows a macOS notification and exits non-zero.
"""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import validate

ROOT = Path(__file__).parent
RUNS = ROOT / "data" / "runs"
PYTHON = ROOT / ".venv" / "bin" / "python"


def run(cmd, timeout=None):
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def notify(title, message):
    """A macOS banner, so a failure at 03:00 is visible in the morning."""
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification {json.dumps(message)} with title {json.dumps(title)}'],
            capture_output=True, timeout=10,
        )
    except Exception:
        pass


def finish(record, ok_message, fail_message=None):
    RUNS.mkdir(parents=True, exist_ok=True)
    stamp = record["started"].replace(":", "-")
    (RUNS / f"{stamp}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    line = (f"{record['started']} · {record['result']} · "
            f"{record['stats'].get('with_data', '?')}/{record['stats'].get('items', '?')} with data · "
            f"{record['stats'].get('price_changes', 0)} price change(s) · "
            f"{len(record['blocking'])} blocking · {len(record['warnings'])} note(s)")
    with (RUNS / "log.txt").open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line)
    for w in record["warnings"]:
        print(f"  note: {w}")
    for b in record["blocking"]:
        print(f"  BLOCKED: {b}")
    if fail_message:
        notify("Cairo Sales QR — needs a look", fail_message)
        sys.exit(1)
    print(ok_message)


def ran_successfully_today():
    log = RUNS / "log.txt"
    if not log.exists():
        return False
    today = datetime.now().date().isoformat()
    return any(line.startswith(today) and " · blocked" not in line and " failed" not in line
               for line in log.read_text(encoding="utf-8").splitlines())


def main(args):
    publish = "--publish" in args
    if "--force" not in args and ran_successfully_today():
        print("already completed a run today — nothing to do")
        return
    record = {
        "started": datetime.now().isoformat(timespec="seconds"),
        "published": False, "result": "", "stats": {}, "blocking": [], "warnings": [],
    }

    if "--skip-refresh" not in args:
        code, out = run([str(PYTHON), "scrape_site.py", "--prices"], timeout=7200)
        record["refresh_tail"] = out.strip().splitlines()[-3:]
        if code != 0:
            record["result"] = "refresh failed"
            record["stats"], record["blocking"], record["warnings"] = validate.run()
            finish(record, "", "Price refresh failed — see data/runs/")

    record["stats"], record["blocking"], record["warnings"] = validate.run()

    if record["blocking"]:
        record["result"] = "blocked"
        finish(record, "", f"{len(record['blocking'])} check(s) failed — nothing was published")

    code, out = run([str(PYTHON), "build_pages.py"], timeout=900)
    if code != 0:
        record["result"] = "build failed"
        record["build_output"] = out.strip()[-500:]
        finish(record, "", "Page build failed — see data/runs/")

    # The database only ever sees data the gate has already approved.
    if (ROOT / ".env").exists():
        code, out = run([str(PYTHON), "push_supabase.py"], timeout=900)
        record["supabase"] = out.strip().splitlines()[-1:] or ["no output"]
        if code != 0:
            record["result"] = "supabase push failed"
            finish(record, "", "Could not update Supabase — see data/runs/")

    if publish:
        run(["git", "add", "-A"])
        code, out = run(["git", "commit", "-m", f"Nightly price refresh {record['started'][:10]}"])
        if code == 0:
            code, out = run(["git", "push", "origin", "main"], timeout=300)
            record["published"] = code == 0
            if code != 0:
                record["result"] = "push failed"
                record["push_output"] = out.strip()[-300:]
                finish(record, "", "Could not push to GitHub — see data/runs/")
        else:
            record["result"] = "nothing changed"

    validate.accept()  # today's data becomes tomorrow's comparison point
    record["result"] = record["result"] or ("published" if publish else "built, not published")
    finish(record, "ok")


if __name__ == "__main__":
    main(sys.argv[1:])
