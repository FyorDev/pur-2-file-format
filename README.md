# pur-2-file-format

An independent parser, format specification, and from-scratch writer for PureRef
2.1 `.pur` files. Verified against **PureRef 2.1.3 on Windows**.

For the PureRef 1.x format, see [FyorDev's PureRef-format](https://github.com/FyorDev/PureRef-format).

![Screenshot of the generated demo canvas in PureRef](examples/standalone-demo.png)

The [demo canvas](examples/standalone-demo.pur) was created entirely in Python,
including its image pixels. Writing files requires no PureRef installation, Qt,
template canvas, or third-party Python dependencies.

## What works

- Embedded PNG/JPEG images with shared resources.
- Position, rotation, scaling, opacity, and rectangular cropping.
- Unicode HTML notes, colored backgrounds, and Comfortable/Compact note modes.
- Multiline Unicode comments on images, notes, groups, and drawings.
- Groups and nested parent relationships.
- Solid line and cubic Bézier drawings.
- File inspection, image extraction, SQLite unpacking, and repacking.

The [format specification](FORMAT.md) documents the displaced SQLite container,
MD5 checksum, exact [SQL schema](schema.sql), serialized Qt values, and evidence
behind each interpretation.

## What doesn't work (WIP)

These features are unsupported or unverified, rather than confirmed working:

- Other PureRef versions, including 2.0 and the unrelated 1.x format.
- Linked/external image resources and animation playback.
- Image filter flags.
- Alternate group locking modes.
- Dashed strokes, arrowheads, and other non-default drawing options.
- Negative or large multi-limb BigRational ordering values.
- Automatic thumbnail generation; new files use an empty preview unless supplied.

The [specification](FORMAT.md) distinguishes tested behavior from fields whose
meaning still needs investigation.

## Install

Python 3.11+ with SQLite serialization support is required.

```sh
git clone https://github.com/Vacyyyy/pur-2-file-format.git
cd pur-2-file-format
python -m pip install .
```

The module can also be used directly as `python pureref2.py` without installation.

## Command line

```sh
pureref2 create new-board.pur photo.jpg drawing.png
pureref2 inspect new-board.pur
pureref2 extract new-board.pur extracted-images
pureref2 unpack new-board.pur new-board.sqlite
pureref2 pack new-board.sqlite repacked-board.pur
python -m examples.demo standalone-demo.pur
```

Output files are created exclusively: existing files are not overwritten. `pack`
computes a new checksum and writes an empty thumbnail by default. For byte-exact
repacking, use `wrap` with the original thumbnail and application version.

## Python API

```python
from pureref2 import Scene, PurFile

scene = Scene()
group = scene.group(name="References", x=100, y=50)
scene.image("photo.jpg", parent=group, x=0, y=0, rotation=15,
            scale_x=0.5, scale_y=0.5, opacity=0.8,
            comment="Primary composition reference")
scene.image("drawing.png", parent=group, x=250, y=0,
            clip=(0, 0, 100, 100))
scene.note("Unicode notes: Ω 中", parent=group, x=0, y=-100)
scene.drawing([[(0, 0, 120), (1, 300, 120)]], parent=group,
              rgba=(255, 100, 20, 255), width=4)
scene.write("generated.pur")

board = PurFile.read("generated.pur")
print(board.inspect())
raw_items = board.rows("items")
sqlite_bytes = board.database
board.close()
```

Use `scene.note("Compact note", style="compact")` for PureRef's actual Compact
background mode. The default is `style="comfortable"`; changing the mode preserves
the supplied position, text, and fixed-size settings.

Image positions locate their original centers; object transforms are relative
to their parent. `image_data` accepts encoded bytes and explicit dimensions for
other formats, but only PNG/JPEG are integration-tested. The reader loads files
into memory. `inspect` returns a readable summary; raw rows/database bytes retain
data that specialized decoders do not interpret.

## Tests

```sh
python -m unittest discover -s tests -v
python -m investigation.validate
```

Unit tests run without PureRef. Integration tests use the installed application;
set `PUREREF_EXE` to override its executable path. They use isolated settings and
synthetic files only.

## Scope

This is an experimental implementation of the tested **2.1.3 subset**, not a
complete mapping of all 2.x features. This project is independent of PureRef.
