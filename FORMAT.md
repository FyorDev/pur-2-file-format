# PureRef 2.x `.pur` format

Reverse engineered from **PureRef 2.1.3 for Windows** and **PureRef 2.0.3 for Linux**, using
synthetic canvases, controlled CLI/UI saves, binary inspection, and independent files loaded back
into the app. Fields that are invisible in a file but visible on screen were resolved by writing
crafted values, rendering them with `exportScene`/`exportImages`, and re-saving. No personal
canvases were opened or used as input. Research dates: 2026-09-17.

This describes enough of the format to implement a writer **from scratch** for
embedded images, affine transforms, opacity, rectangular crops, HTML notes,
nested groups, and solid vector drawings. `pureref2.py` is an independent Python
standard-library implementation. It does not need PureRef, Qt, or a template to
create files. PureRef is used only for the integration tests.

Scope: envelope versions `2.0` and `2.1`, database schema version `200101`, as emitted by
applications `2.1.3` and `2.0.3`. Those two write the *same* format: for one scene their files
differ only in the application-version string, the checksum over it, and the copy of that string
in `metadata`. 2.1.3 adds no table, column or serialized value — the only SQL it gained is
`PRAGMA application_id;` and `SELECT thumbnail FROM metadata;`, which is the Windows Explorer
thumbnail provider reading a preview. What 2.1 does add is user-facing: image auto-optimize and
convert rules on import, and a per-image switch between embedded and linked storage, both of
which change what lands in `images` without changing its shape. Its new
`ImageManagement` class registers exactly those operations —
`LinkMode { None, Relink, Embed }` and `DownscaleMode { None, Downscale, Unscale }` — along with
`DrawToolbar`'s tools and shapes; `investigation/metaobjects-2.0.3.txt` and
`investigation/metaobjects-2.1.3.txt` list every registered enum, method and property in both. The envelope version is a *format* version, not the
application version: PureRef **2.0.3 writes `2.1` envelopes**, and its files are read by this
implementation unchanged (`investigation/30-app-2.0.3.pur`). The thumbnail-less `2.0` envelope
layout in section 2.1 was reconstructed and verified against 2.0.3. The unrelated 1.x binary
format is not handled; see FyorDev/PureRef-format for that.

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

### 2.1 The `2.0` envelope has no thumbnail field

Files whose format version is `2.0` end the header at the checksum:

| Field | Encoding |
|---|---|
| format version | QString `2.0` |
| reserved | uint32 `0` |
| displaced-prefix offset / DB length N | uint64 |
| application version | QString |
| file checksum | QString, 32 md5 hex digits |

Verified by loading four candidate layouts in PureRef 2.0.3: this one loads with no warnings and
an accepted checksum (`investigation/31-envelope-2.0.pur`), while the same header plus an empty
thumbnail `QByteArray` fails with `Open failed … no such table: metadata`, and dropping the
reserved word yields "corrupt file". Everything else — displacement, checksum coverage, schema —
is identical to `2.1`. Writers choose the layout with `wrap(..., format_version='2.0')`.

Envelope acceptance in 2.0.3: `2.0`, `2.1` and `2.2` load (the latter two share this layout);
`3.0` is refused as "from a newer version of PureRef"; a major version below 2 is handed to the
legacy 1.x parser; an empty string is reported as corrupt.

The `2.0` layout therefore belongs to the first 2.x releases: 2.0.3 (September 2024) already
writes `2.1` envelopes with a thumbnail, and PureRef's changelog only adds a *setting* to turn
thumbnail generation off in 2.1.0 (January 2026), which 2.0.3 does not have. On Windows the
preview is what the shell thumbnail provider reads, so supplying one gives `.pur` files a
preview in Explorer.

### 2.2 `PRAGMA user_version` is the compatibility gate

A file whose `user_version` is below the application's (`0`, `100000`) loads and is silently
migrated, and is re-saved as `200101`; `999999` is refused with "This PureRef file is from a
newer version of PureRef", quoting `metadata.application_version` as the version. So do not
advertise a future application version in metadata.

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

The serializer is name-based and migrating (`PRAGMA table_info`, `ALTER TABLE … ADD/DROP COLUMN`),
which has three practical consequences, all probe-verified:

* **Column order is irrelevant.** It varies by build rather than by version: the 2.1.3-on-Windows
  fixtures here declare every table in a different order than 2.0.3 and 2.1.3 on Linux, which
  agree with each other exactly. Any order loads in any build.
* **Unknown columns and tables load with a warning and are dropped on save**
  ("Encountered unknown column '%0' in table '%1' it will be dropped on save").
* **Missing columns are fatal**, not migrated in: a database without `metadata.saved` fails with
  `Table metadata has no column named saved`. A wrong declared type only warns
  ("Wrong data type of column 'z'. The database has it set to 'TEXT', expected 'REAL'").

`items.z` and `items.sort_order` are renormalised to `1..n` on every application save:

```sql
UPDATE items SET sort_order = toBigRational(new_order) FROM(SELECT id AS id2,
  row_number() OVER(PARTITION by parent ORDER BY sort_order COLLATE collateBigRational)
  AS new_order FROM items) WHERE id2 == id
```

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

These numbers are plain `QMetaType` ids, which is worth knowing because it makes them
predictable rather than magic: 20 is `QRectF`, 22 is `QSizeF`, 80 is `QTransform`, and 1024 is
`QMetaType::User` — the id Qt writes for any type outside its builtin set, followed by the
registered type name. `investigation/qmetaobject.py` prints the same table when it names method
argument types.

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

It is two big integers, numerator then denominator, each serialized as:

```text
uint32 sign          1 = positive, 0 = zero, 0xffffffff = negative
uint64 block_count
uint32 blocks[block_count]     least significant block first
```

The previously documented `1, 0, 1, n, 1, 0, 1, d` word pattern is this encoding with one block
each. (`Error: BigUnsigned::underflow` in the executable identifies the classic C++ BigInteger
library, whose sign is `{negative, zero, positive}` and whose magnitude is a block vector.)

Verified by ordering: six images given crafted orders `-3`, `0`, `3`, `7/2`, `5` and `2^32`
(blocks `[0, 1]`) were exported by `exportImages … %2-%0` in exactly that ascending order
(`investigation/32-rational-probe.pur`), so signs, zero, fractions and multi-block magnitudes all
decode as above and blocks are least-significant-first. `exportImages` sequence numbers follow
`items.sort_order`, which is how sibling ordering becomes observable from the command line.

Because the application renormalises orders to `1..n` per parent on save, files written by PureRef
itself contain small positive integers; a writer only needs those.

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
| `comment` | Nullable plain-text comment, set through the application's comment dialog and shown in the item's tooltip (`GraphicsItem::setComment(const QString&)`). Unicode and line breaks are preserved. Despite the schema's `INTEGER` declaration, non-null comments have SQLite storage class `text`; a comment that looks like a number is stored as one, because of the column's integer affinity |

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
| `playback_speed` | 1.0 for static images; a **float** internally, per the
  `playbackSpeedChanged(float newSpeed)` signal, so expect single-precision values in the
  `REAL` column |
| `playback_state` | 0 for static images |
| `playback_frame` | 0 for static images |
| `flags` | `GraphicsImageItem::RenderFlag` bitmask, 1 for ordinary static images |

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

### Render flags

Rendering one 4x4 image at 32x with `flags` 0-3 isolates two bits:

| bit | meaning |
|---|---|
| `0x1` | bilinear/smooth sampling (application default; 0 renders nearest-neighbour) |
| `0x2` | grayscale filter |

No other bit is used. `GraphicsImageItem::setRenderFlags` stores the word as-is and only
reacts to `0x2` (it forwards grayscale to `Movie::setGrayscale` for animations), and every
other read of the member masks either `& 0x1` — passed straight to
`QPainter::setRenderHint(SmoothPixmapTransform, ...)` — or `>> 1 & 0x1`, passed to
`ImageCache::getMipHandle` as an image option. Bits `0x4` upward, up to `0x7fffffff`, change
nothing on screen and are preserved verbatim across an application save. The UI calls the two
that matter "Toggle bilinear sampling" and "Toggle grayscale" (`FilterCommand`).

### Linked resources

With `General_Settings/Embed Local Files=false` (`PureRef -S "Embed Local Files=false"`) the
application stores resources by reference: `source_type = 2`, `data` and `checksum` NULL, with
`format`, `width`, `height`, `origin` and `source` still set
(`investigation/35-linked-2.0.pur`).

The column only ever holds these two values. `SceneSerializerSqlite::storeImage` branches on the
in-memory `ImageData` source type: zero means embed, and it deduplicates with
`SELECT id FROM images where checksum=? AND source_type=?` binding **1**; anything else means
link, and it deduplicates with `where source=? AND source_type=?` binding **2**. There is no
third value to find, and none for web images in particular: an image dropped from a browser is
downloaded and embedded with the URL left in `origin`/`source`. The command line cannot load a
URL at all (`load;http://...` fails with "File does not exist").

When `data` is NULL the image is loaded from `source` regardless of `source_type`. If that path
is gone, PureRef retries it under the `.pur`'s own folder, dropping leading components one at a
time - for `/tmp/gone/wanted.png` beside `board.pur` it tries `<folder>/tmp/gone/wanted.png`,
then `<folder>/gone/wanted.png`, then `<folder>/wanted.png`. On a hit it rewrites both `source`
and `origin` to the file it found and keeps `source_type = 2`; otherwise the item renders as a
missing-image placeholder. A copy in an unrelated subfolder is not found, so the search is not a
recursive scan.

`format` is not normalised: it is the lowercase file extension when the image came from a path
(`png`, `gif`) and the uppercase detected format otherwise.

### Downscaling on load

`General_Settings/AutoDownscale`, with `AutoDownscaleMaxWidth`/`AutoDownscaleMaxHeight`, reduces
images as they are imported, and the file then holds only the reduced image: a 3000x2000 PNG
imported with a 512 limit is stored as 512x342 re-encoded PNG bytes, with `checksum` over those
bytes and `source`/`origin` still pointing at the original file. Nothing marks the row as
downscaled. Mip levels are a runtime cache (`OnDiskImageStore`, `ImageCache::MipId`) kept in the
temporary directory, not in the `.pur`.

### Animation

Note that `playback_state` is **not** the `Movie::State` enum the binary registers
(`NotRunning = 0`, `Paused = 1`, `Running = 2`): the stored values sit one higher, so the
column is a separate, unregistered enum where 0 means "not an animation at all".

An animated GIF is stored as ordinary embedded data (`format='gif'`) with
`playback_state = 3`, `playback_frame = 0`, `playback_speed = 1.0`
(`investigation/34-animation-2.0.3.pur`). Probing states 0-3 with `playback_frame = 1`: only
state **2** renders the requested frame, so 2 is "paused at `playback_frame`", 3 is "playing"
(the application's default), 0 is the static value written for still images. All values survive a
re-save. `image_info` recognises PNG, JPEG, GIF, BMP, WebP and TIFF headers; `image_data` still
accepts arbitrary encoded bytes with explicit dimensions.

## 7. Notes

`items_notes.id` references the common item. `text` contains ordinary Unicode
HTML suitable for Qt rich text; it is **not** a QVariant or UTF-16 byte string.
The app emits a full HTML document with Qt-specific metadata, but the writer's
small `<html><body><p>...</p></body></html>` document also works.

| Column | Tested representation |
|---|---|
| `text` | HTML including font/style and Unicode content |
| `text_color` | Default text colour, used when the HTML carries no colour and overridden by an inline HTML colour; NULL in the app-generated test note |
| `fixed_size` | QVariant QSizeF; `(-1,-1)` means automatic sizing |
| `background_color` | Empty string for default; `#AARRGGBB` accepted |
| `style` | 0 = Comfortable (default), 1 = Compact |

The note writer preserves Unicode, line breaks, and HTML escaping. A generated
note containing Greek and Chinese characters was rendered and saved successfully.
Switching the actual PureRef note toolbar from Comfortable to Compact changed
only `items_notes.style` from 0 to 1. The common item transform, HTML, background
color, and `fixed_size` were unchanged. Values 2 and 3 render like Compact and are preserved
unchanged, so the field is not validated. Compact uses the app's smaller note
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

`lock_mode` is `GraphicsGroupItem::LockMode`, and because that enum is registered with
`Q_ENUM` its keys survive in the binary's meta object: **`Open = 0`, `Closed = 1`**, read out
with `investigation/qmetaobject.py`. There is no third mode.

App-generated groups have `background_color=NULL`, `lock_mode=1`. Explicit
`#AARRGGBB` backgrounds are accepted and preserved by the app, with the alpha byte honoured
(`#80ff0000` renders translucent). Two levels of nested groups are integration-tested.
Values outside 0 and 1 load and round-trip with no render difference, since locking only affects
interaction.

## 9. Drawings

`items_drawings.strokes` is a special text cell containing a QVariant with type
ID 1024 and registered name `QList<GraphicsDrawItem::Stroke>\0` (32 bytes).

The application exports its own stream operators for this type
(`operator<<(QDataStream&, GraphicsDrawItem::Stroke const&)` and its `>>`
counterpart), and disassembling them gives the struct exactly:

```text
uint32 stroke_count
repeat stroke_count:
    int8   version                 -- 100; see below for lower values
    int8   QColor_spec             -- 1 = RGB
    uint16 alpha                   -- 0..65535
    uint16 red
    uint16 green
    uint16 blue
    uint16 QColor_padding          -- 0
    double width
    QPainterPath payload           -- no QVariant wrapper here
    double point_x                 -- QPointF, (0,0) in every saved file
    double point_y
    int32  style                   -- only written when version > 99
```

So what earlier looked like 20 opaque option bytes is a `QPointF` followed by an
`int`. Convert ordinary 8-bit color channels using `channel * 257`. The app's
default test stroke was RGBA `(46,132,170,200)`, width 5. Independently generated
orange strokes with width 3 and alpha 255 render correctly. Multiple strokes,
straight lines, and cubic Béziers were loaded and saved successfully.

### style

| Value | Rendering |
|---:|---|
| 0 | solid with rounded ends — what PureRef writes for freehand and straight strokes alike |
| 1 | dashed |
| 2 | solid with flat, square ends |
| anything else | drawn like 0 |

Renders of the same stroke under each value differ only in these ways, with an
anchor stroke in the scene to keep the framing identical. Style 2 also widens the
item's bounding rectangle: `GraphicsDrawItem::strokeStyleExtraBounds` returns an
empty rectangle unless the style is exactly 2, in which case it expands the
path's end points by the stroke width — which is what a square cap needs.

An application re-save keeps a style the file already had
(`investigation/33-dashed-app-2.0.3.pur` came back with 0 and 1), but 2.0.3 and
2.1.3 only ever *write* 0: even a straight line drawn with the toolbar
(`investigation/23-line.pur`) is style 0.

The style is also **not** the drawing tool. 2.1 registers
`DrawToolbar::DrawTool { None, Pen, Shape, Eraser }` and
`DrawToolbar::Shape { Line, Ellipse, Rectangle }`, so 2.1 can draw ellipses and
rectangles — but the schema and this struct are unchanged from 2.0.3, and the
three style values render as round, dashed and flat-capped strokes in 2.1.3 just
as they do in 2.0.3. Shapes therefore have to be stored as ordinary
`QPainterPath` geometry inside a normal stroke.

### version, and strokes written before it existed

The deserializer reads the leading `int8` and, when it is **99 or lower**, seeks
one byte back and treats that byte as the start of the QColor, skipping the
trailing `style` int. That is the pre-versioned stroke layout, and it is why
values like 0, 1, 2 or 200 in that position produce garbage: a QColor spec of 200
is not RGB. A stroke written the old way (QColor first, no style) loads and
renders correctly in 2.0.3 and is re-serialized with version 100 and style 0.

`point` is transient state the application keeps while a stroke is being drawn
(`DrawToolbar::appendSmoothedPoint`); every saved file carries (0, 0), and
putting a real coordinate or a NaN there changes nothing on screen.

There is no arrowhead in 2.0.3 stroke data: the `Arrow`, `arrowWidth` and
`arrowHeight` strings in the executable belong to the `PopupArrow` stylesheet,
and no drawing code reads them.

## 10. Metadata and thumbnails

One metadata row with `id=0` was observed. Relevant columns:

- `scene_rect`: QVariant QRectF of the canvas scene rectangle. It can include the
  origin and is not necessarily the tight object bounding box.
- `view_transform`: QVariant QTransform for the view, distinct from item transforms.
- `horizontal_scroll`, `vertical_scroll`: view scroll positions.
- `application_version`: ordinary text.
- `thumbnail`: true BLOB. It matches the header's thumbnail in observed saves. Application
  thumbnails are 256x256 RGB JPEG scene renders; a PNG thumbnail is also accepted and loads
  without warnings, so supplying a preview does not require a JPEG encoder.
- `last_save_path`: the directory of the last save, which seeds the save dialog.
- `last_load_path`: the path the scene is associated with; after a save it is that file's own
  path.
- `last_load_checksum`: the header checksum of the file the scene was *loaded* from, so PureRef
  can tell whether the file on disk has changed since. Following a chain of saves, each file
  carries its predecessor's checksum, and a scene built from imported images carries NULL.
- `saved`: 0 when the scene had never been associated with a `.pur` before this save (an import
  that is being saved for the first time), 1 when it was loaded from one.
- `scene_rect` is the scene's bounding rectangle, including the origin: for a single 64x32 image
  centred at (100, 200) it is `(0, 0, 132, 216)`. A file converted from 1.x keeps the old 1.x
  canvas corner in it, which is how a converted scene ends up with a `(-10000, -10000, ...)`
  rectangle. Leaving the column NULL makes PureRef compute the framing itself.

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

## 12. Neighbouring formats, migration and repair

Three things the binary describes that are not the file format itself but decide
what happens to a file.

### The clipboard: `pureref/binary`

Copying items puts a `QMimeData` on the clipboard carrying the rendered image,
the plain text and HTML of any note, and — under the mime type
**`pureref/binary`**, present in every release from 1.10.4 to 2.1.3 — a
`QDataStream` payload:

```text
QString  sending instance key     (LocalServer::getKey())
int      item count
QRectF   bounds of the selection
item records                      (SaveFileLoader::writeItemMetadata)
```

Those records are the **1.x item stream**, not the SQLite schema: the paste path
hands the stream to `SaveFileLoader::loadItemMetadata`, the same reader the 1.x
file loader uses. Image pixels are not in the payload. The receiver takes the key
from the payload and asks the sending instance for the data over a local socket
(`LocalServer::sendCommandAsync`), which is why pasting between two different
PureRef versions is refused and why "A pasted image item is missing its image
data" exists as an error.

### Migration is reconciliation, not a version ladder

`migrateDb` sets `PRAGMA auto_vacuum = 1` and `PRAGMA encoding = 'UTF-8'`, drops
a leftover `lost_and_found` table, then walks the expected schema comparing
`PRAGMA table_info(%0)` against it, issuing `CREATE TABLE %0 (%1)` and
`ALTER TABLE %0 ADD %1 %2` / `DROP COLUMN %1` as needed. There is no table of
version-to-version steps.

That reconciliation applies to the database PureRef works in, not to the file
being opened: a file missing `metadata.saved` fails to load with
`table metadata has no column named saved` at `user_version` 100000 exactly as it
does at 200101. So `user_version` gates nothing but "too new", and a writer must
still emit every column.

### Recovery

`SceneSerializerSqlite::recoverFromFile` tries three things in order, logging as
it goes:

1. hand the file to SQLite's recovery extension, which rebuilds what it can and
   parks unattributable rows in `lost_and_found`;
2. "trying to find header" — search the file for the `SQLite format 3` signature
   and reassemble from there, which is the displaced-prefix layout of section 1
   used as a repair heuristic;
3. "trying with dummy header" — prepend a synthetic SQLite header so the
   recovery extension can read the body at all.

Afterwards it reads `lost_and_found`, patches `items`, `items_images` and
`items_notes` back together, continues ids from `SELECT MAX(items.id) FROM
items`, and looks for images no item references with
`SELECT id, source FROM images WHERE images.id NOT IN (SELECT items_images.image
FROM items_images)` before dropping the table again. The `-b/--brute-force`
command-line flag reaches this path, but it runs asynchronously: a `-c save`
issued in the same invocation writes an empty scene before recovery finishes, so
it cannot be measured from the command line alone.

## 13. Reading a file that is damaged

SQLite reports corruption whenever the damaged page is touched, and it does so
in its own exception types; a TEXT cell that is not valid UTF-8 raises from
inside the cursor before any of this code sees it; and because SQLite enforces
no column type, a damaged file can hold a number where a serialized payload
belongs. Truncating and bit-flipping every fixture in `investigation/` turns up
all three. `pureref2` maps them onto `FormatError`, or onto a cell reported as
hex, so inspecting a file you suspect is broken is safe -- which is the case
where inspection matters most.

`PurFile.schema_report()` names what a rejected file is missing. PureRef reads
its schema by column name and refuses a database that lacks one ("Table metadata
has no column named saved"), while an extra table or column only produces a
warning and is dropped on the next save.

## 14. Looking at the bytes

[pur2.hexpat](pur2.hexpat) is an [ImHex](https://imhex.werwolv.net) pattern for
this container: it decodes the header, previews the thumbnail, reassembles the
displaced database into an ImHex section, maps its b-tree pages, and registers
the whole database as a `scene.sqlite` virtual file, which is the only way to
see the scene -- carving from the `SQLite format 3` signature recovers just the
displaced prefix. It also carries a `Variant` type for the serialized cells,
to be placed by hand on a payload recovered through `binary()`.

## 15. External context

The container and application-specific encodings above come from local experiments.
Qt's [serialization overview](https://doc.qt.io/qt-6/datastreamformat.html),
[QTransform documentation](https://doc.qt.io/qt-6/qtransform.html), and upstream
[QPainterPath](https://github.com/qt/qtbase/blob/6.8/src/gui/painting/qpainterpath.cpp)
and [QColor](https://github.com/qt/qtbase/blob/6.8/src/gui/painting/qcolor.cpp)
sources provide context for the Qt value types.

Existing [FyorDev/PureRef-format](https://github.com/FyorDev/PureRef-format) targets
1.10/1.11.1 and was not used as a source-code implementation for this writer.
This work does not claim a complete mapping of every 2.x feature or version.
