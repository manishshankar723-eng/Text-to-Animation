"""The export PREVIEW must show the same columns the export actually writes.

`PlanExportPreview.jsx` mirrors `plan_export.COLUMNS` in JavaScript, because the
preview is rendered in the browser from data it already has (instant, no round
trip). Two hand-kept lists drift — and a preview that disagrees with the file is
worse than no preview, since the user checks it and is then surprised.

So the drift is made loud here instead: this parses the JS list out of the
component and asserts it matches the Python one exactly, in order.

    python tests/plan_export_columns_check.py
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from plan_export import COLUMNS

JSX = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "client", "src", "components", "PlanExportPreview.jsx",
)

with open(JSX, encoding="utf-8") as f:
    source = f.read()

block = re.search(r"EXPORT_COLUMNS\s*=\s*\[(.*?)\n\];", source, re.S)
if not block:
    print("FAIL: couldn't find EXPORT_COLUMNS in PlanExportPreview.jsx")
    sys.exit(1)

js_columns = [
    (k, label)
    for k, label in re.findall(r'\[\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\]', block.group(1))
]

print(f"python plan_export.COLUMNS : {len(COLUMNS)} columns")
print(f"js EXPORT_COLUMNS          : {len(js_columns)} columns\n")

ok = True
if js_columns == list(COLUMNS):
    for key, label in COLUMNS:
        print(f"  ok   {key:<10} {label}")
else:
    ok = False
    width = max(len(COLUMNS), len(js_columns))
    for i in range(width):
        py = COLUMNS[i] if i < len(COLUMNS) else None
        js = js_columns[i] if i < len(js_columns) else None
        mark = "ok  " if py == js else "FAIL"
        print(f"  {mark} [{i}] python={py!r}  js={js!r}")

print()
if not ok:
    print("FAILED: the export preview would show different columns than the file.")
    print("Fix EXPORT_COLUMNS in client/src/components/PlanExportPreview.jsx")
    sys.exit(1)
print("Preview columns match the exporter exactly.")
