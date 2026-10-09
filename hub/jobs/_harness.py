"""Entry-point wrapper shared by all jobs.

A job module ends with:

    if __name__ == "__main__":
        harness.main(run)

`run(cfg)` returns a summary dict. The harness prints it on the last stdout line
prefixed with RESULT_MARKER so the scheduler can parse it; any exception exits 1
with the traceback on stderr.
"""

import json
import sys
import traceback

from hub import config, paths

RESULT_MARKER = "@@RESULT "


def main(run):
    paths.ensure_dirs()
    try:
        cfg = config.load()
        summary = run(cfg) or {}
    except Exception:
        traceback.print_exc()
        sys.exit(1)
    print(RESULT_MARKER + json.dumps(summary, default=str), flush=True)
    if summary.get("ok") is False:
        sys.exit(2)
