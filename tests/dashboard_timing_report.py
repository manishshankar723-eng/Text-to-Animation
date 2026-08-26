"""What the sign-in / library work actually costs, in milliseconds, against the
real database. A REPORT, not a check — it prints numbers and always exits 0.

⚠ IT IS NOT A PASS/FAIL TEST AND MUST NOT BECOME ONE. Every number here moves
with the network between this machine and Atlas; a threshold would fail on a
train and pass in an office, and a suite that cries wolf gets ignored. What it
is for is answering "did that change do anything, and how much" with a
measurement instead of an argument — and for spotting the shape of a regression
(a per-row cost that should be a per-page one) which IS stable across networks.

Each figure is the median of several runs, because a single timing against a
remote database is mostly noise.

⚠ READ-ONLY. It only ever lists and reads; it writes nothing.

    python tests/dashboard_timing_report.py
"""

import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import logging  # noqa: E402

logging.disable(logging.WARNING)

from server.schemas import JobKind  # noqa: E402

RUNS = 5
PAGE = 100


def timed(fn, runs=RUNS):
    """Median wall-clock milliseconds, and whatever the last run returned."""
    times, out = [], None
    for _ in range(runs):
        t0 = time.perf_counter()
        out = fn()
        times.append((time.perf_counter() - t0) * 1000)
    return statistics.median(times), out


def line(label, ms, note=""):
    print(f"    {label:<46}{ms:>8.0f} ms   {note}")


def delta(before, after):
    if not before:
        return ""
    faster = before / after if after else float("inf")
    return f"{faster:.1f}× faster  (−{before - after:.0f} ms)"


try:
    from server import animatics, main
    from server.jobs import get_store
except Exception as e:  # noqa: BLE001
    print(f"Could not load the server package ({e}).")
    sys.exit(0)

store = get_store()
if type(store).__name__ != "MongoJobStore":
    print(f"Job store is {type(store).__name__}, not Mongo — these numbers would")
    print("be measuring a Python dict. Nothing to report.")
    sys.exit(0)


# ---------------------------------------------------------------------------
# Pick the busiest real account — the one the report was about
# ---------------------------------------------------------------------------
col = store._col  # noqa: SLF001 — a report may look inside; nothing else may
busiest = list(col.aggregate([
    {"$match": {"kind": {"$in": [JobKind.ANIMATIC.value, JobKind.STORYBOARD.value]}}},
    {"$group": {"_id": "$owner", "n": {"$sum": 1}}},
    {"$sort": {"n": -1}},
    {"$limit": 1},
]))
if not busiest or not busiest[0]["_id"]:
    print("No account has any work — nothing to measure.")
    sys.exit(0)

owner = busiest[0]["_id"]
masked = owner[:2] + "…" + owner[owner.find("@"):] if "@" in owner else "…"
print(f"\nBusiest account: {masked}  ({busiest[0]['n']} boards + animatics)")
print(f"Median of {RUNS} runs, page size {PAGE}.\n")

# ---------------------------------------------------------------------------
# 1. The store read
# ---------------------------------------------------------------------------
print("  the animatic library's database read")
full_ms, full_rows = timed(
    lambda: store.list(limit=PAGE, owner=owner, kinds=[JobKind.ANIMATIC])
)
lean_ms, lean_rows = timed(
    lambda: store.list(limit=PAGE, owner=owner, kinds=[JobKind.ANIMATIC],
                       drop=animatics.SUMMARY_DROP)
)
line("whole documents (before)", full_ms, f"{len(full_rows)} rows")
line("without the fields no card reads (now)", lean_ms, delta(full_ms, lean_ms))

print("\n  the storyboard library's database read")
b_full_ms, b_full_rows = timed(
    lambda: store.list(limit=PAGE, owner=owner, kinds=[JobKind.STORYBOARD])
)
b_lean_ms, b_lean_rows = timed(
    lambda: store.list(limit=PAGE, owner=owner, kinds=[JobKind.STORYBOARD],
                       drop=main.BOARD_SUMMARY_DROP)
)
line("whole documents (before)", b_full_ms, f"{len(b_full_rows)} rows")
line("without the fields no card reads (now)", b_lean_ms, delta(b_full_ms, b_lean_ms))

# ---------------------------------------------------------------------------
# 2. Building the cards — the N+1 that was the real cost
# ---------------------------------------------------------------------------
print("\n  building the animatic cards from those rows")
print("    (this is where one board read PER CARD used to happen)")


def summarise_old():
    # ⚠ EXACTLY WHAT THE OLD CODE DID: no shared cache, so `_frame_version`
    # starts a fresh one per card and re-reads the same boards over and over.
    return [animatics._summarise(j) for j in lean_rows]  # noqa: SLF001


def summarise_new():
    boards: dict = {}
    return [animatics._summarise(j, boards) for j in lean_rows]  # noqa: SLF001


old_ms, _ = timed(summarise_old, runs=3)
new_ms, _ = timed(summarise_new, runs=3)
line("a fresh board cache per card (before)", old_ms, f"{len(lean_rows)} cards")
line("one board cache for the page (now)", new_ms, delta(old_ms, new_ms))

# ---------------------------------------------------------------------------
# 3. The whole sign-in, as the browser experiences it
# ---------------------------------------------------------------------------
print("\n  what a sign-in asks the database for")
count_ms, counts = timed(lambda: store.count_by_kind(owner=owner))
line("the new `counts` hint on the login answer", count_ms,
     f"{sum(counts.values())} records, one aggregate")

print("\n  the dashboard's five lists, as they are actually requested now")


def dashboard_now():
    total = 0
    total += len(store.list(limit=8, owner=owner, kinds=[JobKind.ANIMATIC],
                            drop=animatics.SUMMARY_DROP))
    total += len(store.list(limit=8, owner=owner, kinds=[JobKind.STORYBOARD],
                            drop=main.BOARD_SUMMARY_DROP))
    total += len(store.list(limit=8, owner=owner, kinds=[JobKind.FINAL_VIDEO]))
    total += len(store.list(limit=8, owner=owner, kinds=[JobKind.PLAN]))
    total += len(store.list(limit=8, owner=owner,
                            kinds=[JobKind.GENERATE, JobKind.MESHY],
                            drop=("params",)))
    return total


def dashboard_before():
    # 100 rows, whole documents, and a fresh board cache per card.
    total = 0
    rows = store.list(limit=100, owner=owner, kinds=[JobKind.ANIMATIC])
    total += len([animatics._summarise(j) for j in rows])  # noqa: SLF001
    total += len(store.list(limit=100, owner=owner, kinds=[JobKind.STORYBOARD]))
    total += len(store.list(limit=100, owner=owner, kinds=[JobKind.FINAL_VIDEO]))
    total += len(store.list(limit=100, owner=owner, kinds=[JobKind.PLAN]))
    total += len(store.list(limit=50, owner=owner,
                            kinds=[JobKind.GENERATE, JobKind.MESHY]))
    return total


d_before, n_before = timed(dashboard_before, runs=3)
d_now, n_now = timed(dashboard_now, runs=3)
line("before (100 rows each, N+1 covers)", d_before, f"{n_before} records")
line("now (8 rows each, one cover cache)", d_now, delta(d_before, d_now))

# ---------------------------------------------------------------------------
# 4. How the N+1 SCALES — the number that matters as accounts grow
# ---------------------------------------------------------------------------
# ⚠ THE BUSIEST ACCOUNT TODAY IS SMALL, so the section above under-states the
# fix: a per-card cost and a per-page cost look much alike at seven cards. The
# shape only shows at size. This times the newest N animatics in the WHOLE
# collection — not one account's — because the question here is "what does this
# do as a library grows", and that answer should not have to wait for a customer
# to hit it. Cross-owner rows summarise to a "0" cover version, which changes
# nothing about the reads being counted.
print("\n  how it scales (newest animatics across the collection)")
for _n in (25, 100):
    _rows = store.list(limit=_n, owner=None, kinds=[JobKind.ANIMATIC],
                       drop=animatics.SUMMARY_DROP)
    if len(_rows) < _n:
        print(f"    (only {len(_rows)} animatics exist; skipping the {_n}-row case)")
        continue

    def _old(rows=_rows):
        return [animatics._summarise(j) for j in rows]  # noqa: SLF001

    def _new(rows=_rows):
        boards: dict = {}
        return [animatics._summarise(j, boards) for j in rows]  # noqa: SLF001

    _o, _ = timed(_old, runs=3)
    _w, _ = timed(_new, runs=3)
    line(f"{_n} cards — a board read per card (before)", _o)
    line(f"{_n} cards — one cache for the page (now)", _w, delta(_o, _w))

print("\n  ⚠ AND THE PART NO STOPWATCH HERE CAN SHOW: before this change, none of")
print("    the work above STARTED until React had mounted and painted the empty")
print("    dashboard. It now starts inside Login, before the page exists, and the")
print("    answers are kept for the session — so the second visit to Home costs")
print("    nothing at all. That is the larger half of what was fixed.")

print("\n(read-only; nothing was written)")
sys.exit(0)
