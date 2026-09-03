# `test_shots/` — where the browser tests leave their screenshots

Every Playwright suite in `tests/` writes its screenshots here, and nowhere
else. When a probe fails it saves what the page looked like at that moment, so
you can open the PNG and *see* the bug instead of reading a stack trace.

**Nothing in here is committed.** `.gitignore` drops the whole folder except
this README. The pictures are a debugging trail from one afternoon's test run —
they are not part of the app, and they should never turn up in `git status` or
in a commit. (They used to: the suites wrote straight into the repo root, and
`row_routing_failed.png` got committed that way.)

Delete anything in here whenever you like — the next test run makes it again.

Tests get their path from `tests/_shots.py`:

```python
from _shots import shot

page.screenshot(path=shot("bin_probe_failed.png"))
page.screenshot(path=shot("bands.png", "restack"))   # a subfolder per suite
```
