# PureRef 2.1 `.pur` format

Reverse engineered from **PureRef 2.1.3 for Windows**, using synthetic canvases,
controlled CLI/UI saves, binary inspection, and independent files loaded back into
the app. No personal canvases were opened or used as input. Research date:
2026-09-17.

This describes enough of the format to implement a writer **from scratch** for
embedded images, affine transforms, opacity, rectangular crops, HTML notes,
nested groups, and solid vector drawings. `pureref2.py` is an independent Python
standard-library implementation. It does not need PureRef, Qt, or a template to
create files. PureRef is used only for the integration tests.

Scope: envelope version `2.1`, database schema version `200101`, as emitted by
application `2.1.3`. Other 2.x releases have not been tested. In particular, do not
assume this reader accepts 2.0 files or the unrelated 1.x binary format.

## 1. Container: displaced SQLite prefix

A `.pur` is an ordinary SQLite database whose beginning has been replaced by a
PureRef header. The displaced database bytes are appended to the end of the file.
It is neither a ZIP archive nor an encrypted database.

Let `D` be the original database, `N = len(D)`, and `H` be the length of the
PureRef header. The physical file is:

```text
offset 0        PureRef header                         H bytes
offset H        D[H:N]                                 N-H bytes
offset N        D[0:H]                                 H bytes
EOF             N+H
```

Reconstruct the database with:

```python
database = file[N:] + file[H:N]
```

**Do not just carve from the `SQLite format 3` signature to EOF.** That returns
only the displaced prefix, not the whole database. `H` need not be a page size,
and can span multiple pages when the thumbnail is large.

The exact reversal and re-encoding are byte-identical for all ten authentic
fixtures in `investigation/validate.py`; each reconstructed database passes SQLite
`PRAGMA integrity_check`.

## 2. Header

All integers in the header are big-endian. The layout is sequential:

| Field | Encoding | Observed/writer value |
|---|---|---|
| format version | Qt QString | `2.1` |
| unidentified/reserved | uint32 | `0` |
| displaced-prefix offset / DB byte length N | uint64 | e.g. `36864` |
| application version | Qt QString | `2.1.3` |
| file checksum | Qt QString | 32 lowercase MD5 hex digits |
| thumbnail | Qt QByteArray | JPEG bytes; an empty array also works |

The end of the thumbnail is the end of the header (`H`). No alignment or padding
is inserted. With these exact version strings and a normal checksum, the prefix
before the thumbnail length is 104 bytes and `H = 108 + thumbnail_length`.

For a typical `2.1.3` file:

| Offset | Bytes | Meaning |
|---:|---:|---|
| 0 | 4 | `00 00 00 06`, format string byte length |
| 4 | 6 | UTF-16BE `2.1` |
| 10 | 4 | zero reserved field |
| 14 | 8 | N |
| 22 | 4 | app string byte length, 10 |
| 26 | 10 | UTF-16BE `2.1.3` |
| 36 | 4 | checksum string byte length, 64 |
| 40 | 64 | UTF-16BE checksum hex |
| 104 | 4 | thumbnail byte length |
| 108 | variable | thumbnail bytes |

Do not hard-code offset 104 when supporting different application-version strings.

### Qt strings and arrays

`QString`: uint32 **byte count**, then UTF-16BE bytes. This is not a count of
characters or UTF-16 code units. `0xffffffff` denotes null.

`QByteArray`: uint32 byte count, then raw bytes. Zero means empty;
`0xffffffff` means null. The implementation deliberately does not support Qt's
extended length encoding for huge arrays; no such fixture was observed.

### Checksum

The checksum is:

```python
md5(file[immediately_after_checksum_qstring:]).hexdigest()
```

Thus it covers the **thumbnail length**, thumbnail data, database body in its
physical order, and displaced database prefix. It excludes all preceding header
fields and the checksum itself. It is not the MD5 of the reconstructed database.

Writing procedure:

1. Create and serialize SQLite database D.
2. Determine header length H, reserving 64 UTF-16 bytes for checksum digits.
3. Construct `tail = QByteArray(thumbnail) + D[H:] + D[:H]`.
4. Compute `MD5(tail)` and encode its lowercase hex as a QString.
5. Write the header fields through checksum, followed by tail.

An early random-checksum experiment loaded through the CLI but produced a
`Checksum mismatch` warning. Correctly computed files produce no warnings.
The parser reports `checksum_valid`; it retains access to structurally readable
files with a bad checksum, which is useful for diagnostics.

## 3. SQLite layout and schema

Observed database properties:

```sql
PRAGMA page_size = 4096;
PRAGMA auto_vacuum = FULL; -- value 1
PRAGMA application_id = 940753918; -- 0x3812c3fe
PRAGMA user_version = 200101;
-- encoding: UTF-8
```

The independent writer uses the same properties. The exact observed schema is
in `schema.sql`, also embedded in `pureref2.py` as `SCHEMA`. All primary keys are
INTEGER PRIMARY KEY; the observed schema declares no foreign-key constraints.

| Table | Role |
|---|---|
| `images` | Image resources, including encoded image bytes |
| `items` | Common properties for every object on the canvas |
| `items_images` | Image instances and their crop/playback attributes |
| `items_notes` | HTML note content and presentation |
| `items_groups` | Group background and group locking mode |
| `items_drawings` | Lists of vector strokes |
| `metadata` | Scene view, thumbnail, version, and save/load bookkeeping |

An item's type is determined by membership in a subtype table with the same
`id`, not by a type column in `items`.

### Critical SQLite storage detail

SQLite declared column types are misleading here. `items.transform`, for example,
is declared `BLOB` but the app stores it with SQLite storage class **TEXT**.
The serialized bytes have been mapped byte-for-byte to Unicode code points
U+0000..U+00FF, then stored as UTF-8 text by SQLite.

After SQLite decodes a cell to a Python `str`, recover the bytes with
`value.encode('latin1')`. To write them, bind `raw_bytes.decode('latin1')` as a
string. Do not UTF-8-encode that string and interpret the result as the original
binary representation. Embedded NULs are significant and must be retained.

True binary image data and thumbnails are SQLite **BLOB** values. Plain names,
paths, HTML, and color strings are ordinary Unicode text, not Latin-1 payloads.

## 4. Serialized values

The special text cells contain QDataStream-style QVariant records:

```text
uint32 type_id
uint8  is_null         -- 0 in all non-null observed values
[for type_id 1024: QByteArray registered_type_name_including_NUL]
type-specific payload
```

All numeric payload fields below are big-endian; reals are IEEE-754 binary64.
These numeric type IDs match the observed serialized stream, not necessarily
the running Qt installation's native QMetaType IDs.

| Type ID | Meaning | Payload |
|---:|---|---|
| 20 | QRectF | doubles x, y, width, height |
| 22 | QSizeF | doubles width, height |
| 80 | QTransform | nine doubles |
| 1024 | registered/custom type | NUL-terminated type name, then payload |

Unknown variants can be retained as raw bytes. The parser exposes their payload
hex instead of inventing semantics.

### QTransform

Nine doubles are serialized in order:

```text
m11 m12 m13 m21 m22 m23 m31 m32 m33
```

The usual affine transform has `m13=m23=0`, `m33=1`:

```text
x' = m11*x + m21*y + m31
y' = m12*x + m22*y + m32
```

Identity plus translation to `(100,200)` is
`[1,0,0,0,1,0,100,200,1]`. Positive rotations appear clockwise in a screen
coordinate system where y increases downward. Nested item transforms operate
relative to the parent. Image pixel coordinates first pass through
`image_transform`, then the item's transform and its ancestors.

### BigRational: sibling ordering

Registered type name: `BigRational\0` (12 bytes including NUL).

Positive 32-bit numerator/denominator values use these eight uint32 words:

```text
1, 0, 1, numerator, 1, 0, 1, denominator
```

Values `1/1`, `2/1`, `3/1`, and `4/1` were observed. The writer uses positive
integer order values with denominator 1. These words likely include arbitrary
precision integer metadata; the full representation for negative values,
zero, or multiple limbs has **not** been established. Preserve such values as
raw data. Do not substitute a decimal string for this field.

### QPainterPath

Registered name: `QPainterPath\0` (13 bytes including NUL).

```text
uint32 element_count
repeat element_count:
    int32 element_type
    double x
    double y
if element_count != 0:
    int32 current_subpath_start_index
    int32 fill_rule
```

Element types: 0 move-to, 1 line-to, 2 cubic first control point,
3 cubic continuation. A cubic consists of one type-2 record and two type-3
records (second control point and endpoint). Tested paths use subpath index 0
and fill rule 0. The path payload is also embedded directly in strokes without
the QVariant/type-name wrapper.

## 5. Common item properties (`items`)

| Column | Interpretation |
|---|---|
| `id` | Unique object ID, separate namespace from resource IDs |
| `parent` | Parent item ID; `-1` for root |
| `name` | Display name, nullable |
| `transform` | QVariant QTransform, relative to parent |
| `sort_order` | QVariant BigRational, hierarchy/sibling ordering |
| `z` | Real-valued stacking coordinate |
| `opacity` | Real alpha multiplier, tested 0.65 and 1.0 |
| `locked` | Integer lock flag; ordinary fixtures use 0 |
| `comment` | Nullable plain-text comment. Unicode and line breaks are preserved. Despite the schema's `INTEGER` declaration, non-null comments have SQLite storage class `text` |

IDs start at 0 in the synthetic saves. They need not match resource IDs, and a
parent can have a larger ID than its children. A group operation changed child
translations to preserve their world positions.

## 6. Images

`images` has `id, source_type, origin, source, format, checksum, data, width,
height`. For tested embedded resources:

- `source_type = 1`.
- `origin` and `source` contain the source path, using forward slashes in the
  app-generated cases. With embedded data, the original image file is not needed
  to render the canvas.
- `format` is `PNG` or `JPG` in tested writer output.
- `checksum` is lowercase MD5 of the **encoded image bytes**.
- `data` is the original PNG/JPEG bytes, not decompressed pixels.
- `width` and `height` are pixel dimensions.

Two instances of the same imported PNG share one resource row. `items_images`
joins to `items` through `id` and references `images.id` through `image`.

| Instance column | Tested representation |
|---|---|
| `image` | Resource ID |
| `image_transform` | QTransform mapping image pixels to local item coordinates |
| `image_bounds` | QPainterPath clipping boundary in local item coordinates |
| `playback_speed` | 1.0 for static images |
| `playback_state` | 0 for static images |
| `playback_frame` | 0 for static images |
| `flags` | 1 for ordinary static images |

For an unmodified `w × h` image, `image_transform` translates by `(-w/2,-h/2)`.
The image item position is therefore its center. Its bounds are a closed
five-element rectangle from `(-w/2,-h/2)` to `(w/2,h/2)`.

For a crop `(left,top,crop_width,crop_height)` in source pixel coordinates,
retain the image transform and replace the clipping rectangle by:

```text
x0 = left - w/2
y0 = top  - h/2
x1 = x0 + crop_width
y1 = y0 + crop_height
```

This was verified by comparing app renders of a cropped image with a separately
created image containing the same crop at the corresponding position.

Linked/external resources, animation states, optimization variants, and the
other `flags` bits are not yet mapped. `image_data` accepts arbitrary encoded
bytes and explicit dimensions, but only PNG and JPEG are integration-tested.

## 7. Notes

`items_notes.id` references the common item. `text` contains ordinary Unicode
HTML suitable for Qt rich text; it is **not** a QVariant or UTF-16 byte string.
The app emits a full HTML document with Qt-specific metadata, but the writer's
small `<html><body><p>...</p></body></html>` document also works.

| Column | Tested representation |
|---|---|
| `text` | HTML including font/style and Unicode content |
| `text_color` | NULL in app-generated test note |
| `fixed_size` | QVariant QSizeF; `(-1,-1)` means automatic sizing |
| `background_color` | Empty string for default; `#AARRGGBB` accepted |
| `style` | 0 = Comfortable (default), 1 = Compact |

The note writer preserves Unicode, line breaks, and HTML escaping. A generated
note containing Greek and Chinese characters was rendered and saved successfully.
Switching the actual PureRef note toolbar from Comfortable to Compact changed
only `items_notes.style` from 0 to 1. The common item transform, HTML, background
color, and `fixed_size` were unchanged. Compact uses the app's smaller note
background/padding; no HTML or coordinate workaround is needed. The writer accepts
`style="comfortable"` (default) or `style="compact"` and rejects other values.
The parser already exposes the numeric `style` field unchanged, including unknown
values. `investigation/24-compact-note.pur` is the app-created fixture; a freshly
generated compact note renders byte-identically when given the same view framing
and retains style 1 after an app resave. Interaction with `text_color` remains
untested.

## 8. Groups

`items_groups` contains only `id`, `background_color`, and `lock_mode`. The common
item supplies the group name, transform, parent, and opacity. Children reference
the group through `items.parent`. Group geometry is derived from its contents.

App-generated groups have `background_color=NULL`, `lock_mode=1`. Explicit
`#AARRGGBB` backgrounds are accepted and preserved by the app. Two levels of
nested groups are integration-tested. `lock_mode=1` is the default group-locking
behavior; other modes are not experimentally mapped.

## 9. Drawings

`items_drawings.strokes` is a special text cell containing a QVariant with type
ID 1024 and registered name `QList<GraphicsDrawItem::Stroke>\0` (32 bytes).

The tested solid-stroke encoding is:

```text
uint32 stroke_count
repeat stroke_count:
    uint8 tag                      -- 100 in observed strokes
    uint8 QColor_spec              -- 1 = RGB
    uint16 alpha                   -- 0..65535
    uint16 red
    uint16 green
    uint16 blue
    uint16 QColor_padding          -- 0
    double width
    QPainterPath payload           -- no QVariant wrapper here
    byte options[20]               -- all zero for tested solid strokes
```

Convert ordinary 8-bit color channels using `channel * 257`. The app's default
test stroke was RGBA `(46,132,170,200)`, width 5. Independently generated orange
strokes with width 3 and alpha 255 render correctly. Multiple strokes, straight
lines, and cubic Béziers were loaded and saved successfully.

The meaning of tag 100 and the 20 trailing option bytes is not established.
The writer emits the observed solid-style defaults; the parser exposes options
as hex. Dashed styles, arrowheads, alternative drawing tools, and other tag/color
encodings require additional fixtures. The reader retains their raw cell data
even when the specialized decoder cannot interpret them.

## 10. Metadata and thumbnails

One metadata row with `id=0` was observed. Relevant columns:

- `scene_rect`: QVariant QRectF of the canvas scene rectangle. It can include the
  origin and is not necessarily the tight object bounding box.
- `view_transform`: QVariant QTransform for the view, distinct from item transforms.
- `horizontal_scroll`, `vertical_scroll`: view scroll positions.
- `application_version`: ordinary text.
- `thumbnail`: true BLOB. It matches the header's thumbnail in observed saves.
- `last_save_path`: directory in the tested saves.
- `last_load_path`: scene path.
- `last_load_checksum`: previous file checksum after load/resave.
- `saved`: 0 in the initial imported fixture, 1 after load/resave.

The writer supplies identity view transform, zero scroll, app version, an empty
thumbnail, and `saved=1`. It omits scene rectangle and path bookkeeping. This
loads, exports, and re-saves without warnings. PureRef fills in metadata and
generates a thumbnail when re-saving. Empty thumbnails mean no preview image is
provided by our initial file; callers can supply JPEG thumbnail bytes themselves.

## 11. Reproducible evidence

`investigation/validate.py` creates a fresh output directory on every run and checks:

1. Exact container repacking for ten authentic app-generated fixtures.
2. SQLite integrity and checksum validity.
3. Byte-identical 512×512 PNG exports for an independently written two-image
   canvas and its app-created reference.
4. Warning-free app loading and re-saving of a fresh mixed canvas containing all
   four item classes, a rotated/scaled transparent image, Unicode note, colors,
   shared image resources, and two drawing strokes.
5. Crop render equivalence and nested groups with JPEG preservation.
6. Compact note render equivalence to the app's actual Compact mode and style
   preservation after re-saving.

`tests/test_pureref2.py` also exercises malformed/truncated headers, checksum corruption,
known binary fixtures, bounds-checked paths, deduplication, and new databases.

The CLI reports exit code 0 even for some failed commands. The integration test
therefore checks output artifacts and warning/critical log text, not only exit
status. A discovered 2.1.3 CLI quirk: saving an already loaded scene to a *new*
filename fails unless that destination exists; the tests create an empty file
inside their new test directory before `save`.

## 12. External context

The container and application-specific encodings above come from local experiments.
Qt's [serialization overview](https://doc.qt.io/qt-6/datastreamformat.html),
[QTransform documentation](https://doc.qt.io/qt-6/qtransform.html), and upstream
[QPainterPath](https://github.com/qt/qtbase/blob/6.8/src/gui/painting/qpainterpath.cpp)
and [QColor](https://github.com/qt/qtbase/blob/6.8/src/gui/painting/qcolor.cpp)
sources provide context for the Qt value types.

Existing [FyorDev/PureRef-format](https://github.com/FyorDev/PureRef-format) targets
1.10/1.11.1 and was not used as a source-code implementation for this writer.
This work does not claim a complete mapping of every 2.x feature or version.
