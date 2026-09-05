# FriBiDi — the other half of complex-script text

`libfribidi-0.dll` is here so that a Hindi, Punjabi, Urdu, Arabic, Tamil,
Bengali or Thai caption comes out of the exporter looking the way it looks in
the Program monitor. Read `text_shaping.py` for the full story; the short
version is:

- Turning characters into glyphs for those writing systems is called SHAPING.
  `हिन्दी` is six characters and four glyphs, in a different order.
- Pillow can shape, through **libraqm**, and its wheel already carries libraqm
  and HarfBuzz. It does **not** carry FriBiDi. libraqm looks FriBiDi up by name
  when Pillow's font module is first imported, and if it is not there libraqm
  switches itself off silently.
- So without this file `draw.text` drew Hindi in typed order, unjoined —
  `हिन्दी` came out as `हनि्दी` — while the browser preview showed it correctly.
  Every Hindi caption this app exported was wrong, and the preview hid it.

`text_shaping.py` loads this file into the process before Pillow's font module
is imported. Nothing else references it.

## Other platforms

Only Windows is vendored, because that is the one platform with no package for
it. Elsewhere FriBiDi is a normal system library and `text_shaping.py` finds it:

| Platform | Install |
| --- | --- |
| Debian / Ubuntu (and most Docker base images) | `apt-get install -y libfribidi0` |
| Alpine | `apk add fribidi` |
| RHEL / Fedora | `dnf install fribidi` |
| macOS | `brew install fribidi` |

⚠ **A server that cannot load FriBiDi cannot export those languages.** It is not
a soft degrade — `tests/captions_check.py` fails on it, and the editor locks the
complex-script fonts rather than letting someone make a video that is wrong.
If you containerise this app, that `apt-get` line is not optional.

## Licence and provenance

FriBiDi is **LGPL-2.1-or-later**. The full text is in `COPYING` next to this
file. Nothing about it has been modified, it is loaded dynamically at runtime,
and it can be replaced by dropping a different build in over the top — which is
what the licence asks for.

| | |
| --- | --- |
| Version | 1.0.16 |
| File | `libfribidi-0.dll` (x86-64) |
| SHA-256 | `84930fd86a3b46b62794a3bbaff21fbbaeaade5ff7cdc70be7f717ad29af87ed` |
| Binary from | `https://repo.msys2.org/mingw/mingw64/mingw-w64-x86_64-fribidi-1.0.16-1-any.pkg.tar.zst` |
| Upstream source | `https://github.com/fribidi/fribidi/releases/tag/v1.0.16` |
| Copyright | © 2004 Sharif FarsiWeb, Inc.; © 2001, 2002, 2004 Behdad Esfahbod; © 1999, 2000 Dov Grobgeld |

To replace it, download the MSYS2 package above, take `mingw64/bin/libfribidi-0.dll`
out of it, drop it here, and update the version and hash in this table.
