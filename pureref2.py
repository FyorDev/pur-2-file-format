"""Independent PureRef 2.1 reader/writer. Python standard library only.

Tested against PureRef 2.1.3; see FORMAT.md for limitations and evidence.
No PureRef installation or template file is required for writing.
"""
from __future__ import annotations
import argparse
import hashlib
import html
import json
import math
from pathlib import Path
import sqlite3
import struct

SQLITE_MAGIC = b'SQLite format 3\0'
NOTE_STYLES = {'comfortable': 0, 'compact': 1}
# Envelope layouts: 2.0 has no thumbnail field, 2.1 ends with a thumbnail QByteArray.
ENVELOPE_VERSIONS = ('2.0', '2.1')
# images.source_type
SOURCE_EMBEDDED, SOURCE_LINKED = 1, 2
# items_images.flags (GraphicsImageItem::RenderFlag)
RENDER_SMOOTH, RENDER_GRAYSCALE = 1, 2
# items_images.playback_state
PLAYBACK_STATIC, PLAYBACK_STOPPED, PLAYBACK_PAUSED, PLAYBACK_PLAYING = 0, 1, 2, 3
# items_groups.lock_mode
LOCK_OPEN, LOCK_CLOSED = 0, 1
# The trailing int of a serialized stroke, GraphicsDrawItem::Stroke's style.
STROKE_ROUND, STROKE_DASHED, STROKE_FLAT = 0, 1, 2
# A stroke starts with a signed-char version. From 100 on, the style int follows
# the stroke's point; below that the byte is the start of the QColor instead,
# which is how strokes looked before the version existed.
STROKE_VERSION, STROKE_LEGACY_LIMIT = 100, 99
SCHEMA = '''
CREATE TABLE images (id INTEGER PRIMARY KEY,source_type INTEGER,origin TEXT,source TEXT,format TEXT,checksum TEXT,data BLOB,width INTEGER,height INTEGER);
CREATE TABLE metadata (id INTEGER PRIMARY KEY,scene_rect TEXT,application_version TEXT,view_transform TEXT,thumbnail BLOB,horizontal_scroll INTEGER,vertical_scroll INTEGER,last_save_path TEXT,last_load_path TEXT,last_load_checksum TEXT,saved INTEGER);
CREATE TABLE items (parent INTEGER,id INTEGER PRIMARY KEY,name TEXT,transform BLOB,sort_order BLOB,z REAL,opacity REAL,locked INTEGER,comment INTEGER);
CREATE TABLE items_images (image INTEGER,playback_speed REAL,id INTEGER PRIMARY KEY,playback_state INTEGER,image_transform BLOB,image_bounds BLOB,playback_frame INTEGER,flags INTEGER);
CREATE TABLE items_drawings (id INTEGER PRIMARY KEY,strokes BLOB);
CREATE TABLE items_notes (text_color TEXT,id INTEGER PRIMARY KEY,fixed_size TEXT,background_color TEXT,text TEXT,style INTEGER);
CREATE TABLE items_groups (id INTEGER PRIMARY KEY,background_color TEXT,lock_mode INTEGER);
'''

class FormatError(ValueError):
    pass

class Reader:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def take(self, n):
        if n < 0 or self.pos+n > len(self.data):
            raise FormatError('Truncated field')
        value = self.data[self.pos:self.pos+n]
        self.pos += n
        return value

    def unpack(self, fmt):
        return struct.unpack('>'+fmt, self.take(struct.calcsize('>'+fmt)))

    def u32(self):
        return self.unpack('I')[0]

    def bytearray(self):
        n = self.u32()
        return None if n == 0xffffffff else self.take(n)

    def string(self):
        value = self.bytearray()
        if value is None:
            return None
        if len(value) % 2:
            raise FormatError('Odd UTF-16 length')
        try:
            return value.decode('utf-16-be')
        except UnicodeDecodeError as e:
            raise FormatError('Invalid UTF-16 string') from e

def qbytes(value):
    return struct.pack('>I', 0xffffffff) if value is None else struct.pack('>I', len(value)) + value

def qstring(value):
    return qbytes(None if value is None else value.encode('utf-16-be'))

def unwrap(data):
    """Return (header, reconstructed SQLite bytes), without touching disk."""
    r = Reader(data)
    version = r.string()
    if version not in ENVELOPE_VERSIONS:
        raise FormatError(f'Unsupported envelope version {version!r}; verified: {ENVELOPE_VERSIONS}')
    reserved = r.u32()
    offset = r.unpack('Q')[0]
    app_version, token = r.string(), r.string()
    checksum_start = r.pos
    thumbnail = r.bytearray() if version != '2.0' else b''
    size = r.pos
    if offset < size or offset+size != len(data):
        raise FormatError('Invalid displacement offset/header length')
    db = data[offset:] + data[size:offset]
    if not db.startswith(SQLITE_MAGIC):
        raise FormatError('Reconstructed data lacks SQLite header')
    page_size = struct.unpack('>H', db[16:18])[0]
    page_size = 65536 if page_size == 1 else page_size
    if page_size < 512 or page_size & (page_size-1) or len(db) % page_size:
        raise FormatError('Invalid SQLite page size or database length')
    return dict(format_version=version, reserved=reserved, database_size=offset,
                application_version=app_version, save_token=token,
                checksum_valid=hashlib.md5(data[checksum_start:]).hexdigest()==token,
                checksum_start=checksum_start, thumbnail=thumbnail, header_size=size), db

def wrap(db, *, application_version='2.1.3', save_token=None, thumbnail=b'', reserved=0,
         format_version='2.1'):
    if not db.startswith(SQLITE_MAGIC):
        raise FormatError('Expected SQLite database bytes')
    if format_version not in ENVELOPE_VERSIONS:
        raise ValueError(f'Unsupported envelope version {format_version!r}')
    if save_token is not None and (len(save_token)!=32 or any(c not in '0123456789abcdef' for c in save_token)):
        raise ValueError('Checksum must be 32 lowercase hexadecimal characters')
    if format_version == '2.0':
        if thumbnail:
            raise ValueError('The 2.0 envelope has no thumbnail field')
        preview = b''
    else:
        preview = qbytes(thumbnail)
    header = (qstring(format_version) + struct.pack('>IQ', reserved, len(db))
              + qstring(application_version) + qstring('0'*32) + preview)
    if len(header) > len(db):
        raise FormatError('Thumbnail/header exceeds database size')
    tail = preview + db[len(header):] + db[:len(header)]
    checksum = save_token or hashlib.md5(tail).hexdigest()
    return (qstring(format_version) + struct.pack('>IQ',reserved,len(db))
            + qstring(application_version) + qstring(checksum) + tail)

def binary(value):
    """Qt writes serialized bytes as Latin-1 QStrings in SQLite TEXT cells.

    SQLite enforces no column type, so a damaged file can hold a number where a
    payload belongs; that is a format error, not a crash in the caller.
    """
    if isinstance(value, str):
        return value.encode('latin1', errors='replace')
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    raise FormatError(f'A serialized cell holds {type(value).__name__}, not bytes')

def variant(type_id, payload, name=None):
    return (struct.pack('>IB',type_id,0) + (qbytes(name.encode()+b'\0') if name else b'') + payload).decode('latin1')

def transform(x=0, y=0, scale_x=1, scale_y=1, rotation=0):
    a = math.radians(rotation)
    c,s = math.cos(a),math.sin(a)
    return variant(80,struct.pack('>9d',scale_x*c,scale_x*s,0,-scale_y*s,scale_y*c,0,x,y,1))

def rect(x,y,w,h):
    return variant(20,struct.pack('>4d',x,y,w,h))

def size(w,h):
    return variant(22,struct.pack('>2d',w,h))

def big_integer(value):
    """sign word, 64-bit block count, then 32-bit blocks, least significant first."""
    magnitude, blocks = abs(value), []
    while magnitude:
        blocks.append(magnitude & 0xffffffff)
        magnitude >>= 32
    sign = 0 if value == 0 else (1 if value > 0 else 0xffffffff)
    return struct.pack('>IQ',sign,len(blocks)) + b''.join(struct.pack('>I',b) for b in blocks)

def rational(numerator, denominator=1):
    """Encode the sibling-ordering rational; any integer numerator, positive denominator."""
    if denominator <= 0:
        raise ValueError('Rational denominator must be positive')
    return variant(1024,big_integer(numerator)+big_integer(denominator),'BigRational')

def painter_path(elements, cstart=0, fill_rule=0):
    """elements: (Qt element type, x, y); 0 move, 1 line, 2 curve, 3 curve data."""
    payload = struct.pack('>I',len(elements))
    for kind,x,y in elements:
        payload += struct.pack('>idd',kind,x,y)
    if elements:
        payload += struct.pack('>ii',cstart,fill_rule)
    return variant(1024,payload,'QPainterPath')

def bounds(w,h):
    return painter_path([(0,-w/2,-h/2),(1,w/2,-h/2),(1,w/2,h/2),
                         (1,-w/2,h/2),(1,-w/2,-h/2)])

def webp_size(data):
    chunk = data[12:16]
    if chunk == b'VP8X' and len(data)>=30:
        w,h = int.from_bytes(data[24:27],'little'),int.from_bytes(data[27:30],'little')
        return w+1,h+1
    if chunk == b'VP8 ' and len(data)>=30 and data[23:26] == b'\x9d\x01\x2a':
        w,h = struct.unpack('<HH',data[26:30])
        return w & 0x3fff,h & 0x3fff
    if chunk == b'VP8L' and len(data)>=25:
        bits = int.from_bytes(data[21:25],'little')
        return (bits & 0x3fff)+1,((bits>>14) & 0x3fff)+1
    raise ValueError('Unsupported WebP variant; pass dimensions to image_data')

def tiff_size(data):
    order = '<' if data[:2] == b'II' else '>'
    offset = struct.unpack(order+'I',data[4:8])[0]
    if offset+2 > len(data):
        raise ValueError('Truncated TIFF directory')
    size = {}
    for i in range(struct.unpack(order+'H',data[offset:offset+2])[0]):
        entry = offset+2+12*i
        if entry+12 > len(data):
            break
        tag,kind = struct.unpack(order+'HH',data[entry:entry+4])
        if tag in (256,257):
            fmt = order+('H' if kind == 3 else 'I')
            size[tag] = struct.unpack(fmt,data[entry+8:entry+8+struct.calcsize(fmt)])[0]
    if 256 not in size or 257 not in size:
        raise ValueError('TIFF lacks width/height tags; pass dimensions to image_data')
    return size[256],size[257]

def image_info(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n') and len(data)>=33:
        return 'PNG',*struct.unpack('>II',data[16:24])
    if data[:6] in (b'GIF87a',b'GIF89a') and len(data)>=10:
        return 'GIF',*struct.unpack('<HH',data[6:10])
    if data.startswith(b'BM') and len(data)>=26:
        return 'BMP',*(abs(v) for v in struct.unpack('<ii',data[18:26]))
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'WEBP',*webp_size(data)
    if data[:4] in (b'II*\0',b'MM\0*'):
        return 'TIFF',*tiff_size(data)
    if data.startswith(b'\xff\xd8'):
        pos = 2
        sof = {0xc0,0xc1,0xc2,0xc3,0xc5,0xc6,0xc7,0xc9,0xca,0xcb,0xcd,0xce,0xcf}
        while pos < len(data):
            if data[pos]!=255:
                raise ValueError('Malformed JPEG marker')
            while pos < len(data) and data[pos]==255:
                pos += 1
            if pos >= len(data):
                break
            marker = data[pos]; pos += 1
            if marker in (0xda,0xd9):
                break
            if marker == 1 or 0xd0 <= marker <= 0xd8:
                continue
            if pos+2>len(data):
                break
            length = int.from_bytes(data[pos:pos+2],'big')
            if length<2 or pos+length>len(data):
                break
            if marker in sof and length>=8:
                h,w = struct.unpack('>HH',data[pos+3:pos+7])
                return 'JPG',w,h
            pos += length
        raise ValueError('JPEG lacks a supported size header')
    raise ValueError('Unrecognized image header; use image_data with explicit dimensions')

def read_big_integer(r):
    sign, count = r.unpack('IQ')
    if sign not in (0,1,0xffffffff) or count > (len(r.data)-r.pos)//4:
        raise FormatError('Invalid BigInteger sign or block count')
    value = 0
    for shift in range(count):
        value |= r.u32() << (32*shift)
    return -value if sign == 0xffffffff else value

def read_path(r):
    count = r.u32()
    if count > (len(r.data)-r.pos)//20:
        raise FormatError('Invalid path element count')
    result = {'elements':[list(r.unpack('idd')) for _ in range(count)]}
    if count:
        result['cstart'],result['fill_rule'] = r.unpack('ii')
    return result

def strokes(paths, rgba=(46,132,170,200), width=5, style=STROKE_ROUND, *, dashed=False,
            point=(0.0,0.0)):
    """Strokes. Each path is a sequence of (element type, x, y).

    style is STROKE_ROUND (rounded ends, what PureRef writes), STROKE_DASHED or
    STROKE_FLAT (square ends). `point` is transient state the application uses
    while a stroke is being drawn; saved files always carry (0, 0).
    """
    red,green,blue,alpha = rgba
    if any(not 0 <= c <= 255 for c in rgba) or width <= 0:
        raise ValueError('Invalid RGBA or stroke width')
    if dashed:
        style = STROKE_DASHED
    payload = struct.pack('>I',len(paths))
    for elements in paths:
        # version, then QColor as RGB with 16-bit channels
        payload += struct.pack('>2b5Hd',STROKE_VERSION,1,alpha*257,red*257,green*257,blue*257,0,width)
        p = binary(painter_path(elements))
        r = Reader(p); r.unpack('IB'); r.bytearray()
        payload += p[r.pos:] + struct.pack('>2di',*point,style)
    return variant(1024,payload,'QList<GraphicsDrawItem::Stroke>')

def decode_variant(value):
    if value is None:
        return None
    raw = binary(value)
    r = Reader(raw)
    type_id, is_null = r.unpack('IB')
    result = dict(type_id=type_id, is_null=bool(is_null))
    if type_id == 1024:
        name = r.bytearray()
        result['type_name'] = (name or b'').rstrip(b'\0').decode('ascii',errors='replace')
    if type_id in (80,20,22):
        count = {80:9,20:4,22:2}[type_id]
        result['value'] = list(r.unpack(str(count)+'d'))
    elif result.get('type_name') == 'QPainterPath':
        result.update(read_path(r))
    elif result.get('type_name') == 'QList<GraphicsDrawItem::Stroke>':
        count = r.u32()
        if count > len(raw)//43:
            raise FormatError('Invalid stroke count')
        result['strokes'] = []
        for _ in range(count):
            version = r.unpack('b')[0]
            if version <= STROKE_LEGACY_LIMIT:
                r.pos -= 1          # not a version: the QColor starts at this byte
            color_spec = r.unpack('b')[0]
            if color_spec != 1:
                raise FormatError('Stroke color is not stored as RGB')
            alpha,red,green,blue,pad = r.unpack('5H')
            width = r.unpack('d')[0]
            stroke = dict(version=version,color_spec=color_spec,
                          rgba16=[red,green,blue,alpha],color_pad=pad,width=width)
            stroke['path'] = read_path(r)
            stroke['point'] = list(r.unpack('2d'))
            stroke['style'] = r.unpack('i')[0] if version > STROKE_LEGACY_LIMIT else STROKE_ROUND
            stroke['dashed'] = stroke['style'] == STROKE_DASHED
            result['strokes'].append(stroke)
    elif result.get('type_name') == 'BigRational':
        try:
            result['numerator'],result['denominator'] = read_big_integer(r),read_big_integer(r)
        except FormatError:
            result['payload_hex'] = raw[5:].hex()
    else:
        result['payload_hex'] = r.take(len(raw)-r.pos).hex()
    if r.pos < len(raw):
        result['tail_hex'] = raw[r.pos:].hex()
    return result

def text_or_bytes(raw):
    """A TEXT cell as text, or as raw bytes when it is not valid UTF-8.

    Payloads are stored as Latin-1 mapped strings, so a healthy file decodes
    cleanly. A damaged one can hold a sequence that is not UTF-8 at all, which
    sqlite3's default factory raises on from inside the cursor.
    """
    try:
        return raw.decode()
    except UnicodeDecodeError:
        return raw

class PurFile:
    """A .pur opened for reading. Every failure is a FormatError."""

    TABLES = ('images','metadata','items','items_images','items_notes','items_groups','items_drawings')

    def __init__(self, data):
        self.header,self.database = unwrap(data)
        self.connection = sqlite3.connect(':memory:')
        try:
            self.connection.deserialize(self.database)
        except sqlite3.Error as error:
            raise FormatError(f'The reconstructed database will not open: {error}') from error
        self.connection.execute('PRAGMA query_only=ON')
        self.connection.row_factory = sqlite3.Row
        self.connection.text_factory = text_or_bytes

    @classmethod
    def read(cls,path):
        return cls(Path(path).read_bytes())

    def close(self):
        self.connection.close()

    def query(self, sql, *args):
        """Run one statement. A damaged database reports itself through SQLite,
        including column names that are not UTF-8, which sqlite3 decodes itself."""
        try:
            return list(self.connection.execute(sql, args))
        except (sqlite3.Error, UnicodeDecodeError) as error:
            raise FormatError(f'{sql.split()[0].lower()} failed: {error}') from error

    def tables(self):
        names = [row[0] for row in
                 self.query("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        if not all(isinstance(name, str) for name in names):
            raise FormatError('The schema holds a table name that is not text')
        return names

    def columns(self, table):
        return [row[1] for row in self.query(f'PRAGMA table_info("{table.replace(chr(34), chr(34)*2)}")')]

    def rows(self, table):
        """Every row of `table`, values exactly as stored. Tables this package
        does not know about are readable too: PureRef may add one."""
        if table not in self.tables():
            raise ValueError('Unknown table')
        return [dict(row) for row in self.query(f'SELECT * FROM "{table.replace(chr(34), chr(34)*2)}"')]

    def pragmas(self):
        """The values PureRef gates a file on, plus the page geometry."""
        names = ('user_version','application_id','page_size','auto_vacuum','encoding')
        return {name: self.query(f'PRAGMA {name}')[0][0] for name in names}

    def schema_report(self):
        """What this schema has that the package does not know, and the other
        way round: a missing column makes PureRef refuse the whole file."""
        present = self.tables()
        declared = {table: [line.split('(',1)[1].rstrip(');').split(',') for line in SCHEMA.splitlines()
                            if line.startswith(f'CREATE TABLE {table} ')][0]
                    for table in self.TABLES}
        missing = {}
        unknown_columns = {}
        for table, entries in declared.items():
            wanted = [entry.split()[0] for entry in entries]
            if table not in present:
                missing[table] = wanted
                continue
            have = self.columns(table)
            absent = [name for name in wanted if name not in have]
            extra = [name for name in have if name not in wanted]
            if absent:
                missing[table] = absent
            if extra:
                unknown_columns[table] = extra
        return dict(unknown_tables=[name for name in present if name not in self.TABLES],
                    unknown_columns=unknown_columns, missing_columns=missing)

    VARIANT_COLUMNS = {'transform','image_transform','image_bounds','sort_order',
                       'scene_rect','view_transform','fixed_size','strokes'}

    def inspect(self):
        """Everything in the file, decoded as far as it can be.

        A cell that cannot be decoded is reported as hex rather than raised over,
        because inspecting a file you suspect is damaged is the point of this.
        """
        out = {'header':{k:v for k,v in self.header.items() if k!='thumbnail'}}
        out['header']['thumbnail_bytes'] = len(self.header['thumbnail'] or b'')
        out['pragmas'] = self.pragmas()
        out['integrity_check'] = [r[0] for r in self.query('PRAGMA integrity_check')]
        out['schema'] = self.schema_report()
        for table in list(self.TABLES) + out['schema']['unknown_tables']:
            if table not in self.tables():
                continue
            rows = self.rows(table)
            for row in rows:
                for key,value in list(row.items()):
                    if key in self.VARIANT_COLUMNS and value is not None:
                        try:
                            row[key] = decode_variant(value)
                        except (ValueError,struct.error):
                            row[key] = self.opaque(value)
                    elif isinstance(value,(bytes,bytearray)):
                        row[key] = {'bytes':len(value),'md5':hashlib.md5(value).hexdigest()}
                    elif isinstance(value,str) and '\0' in value:
                        row[key] = self.opaque(value)
            out[table] = rows
        return out

    @staticmethod
    def opaque(value):
        """A cell shown as bytes because it could not be read as what it claims."""
        try:
            return {'raw_hex':binary(value).hex()}
        except FormatError:
            return {'not_a_payload':repr(value)[:80]}

class Scene:
    """Construct a fresh schema; no template, installed Qt, or PureRef needed."""
    def __init__(self):
        self.connection = sqlite3.connect(':memory:')
        self.connection.execute('PRAGMA page_size=4096')
        self.connection.execute('PRAGMA auto_vacuum=FULL')
        self.connection.execute('PRAGMA application_id=940753918')
        self.connection.execute('PRAGMA user_version=200101')
        self.connection.executescript(SCHEMA)
        self.next_id = 0
        self.resources = {}

    def _insert(self,table,**values):
        keys = ','.join(values)
        self.connection.execute(f'INSERT INTO {table} ({keys}) VALUES ({",".join("?" for _ in values)})',list(values.values()))

    @staticmethod
    def _validate_comment(comment):
        if comment is not None and not isinstance(comment,str):
            raise TypeError('Comment must be a string or None')

    def _item(self,name,x,y,parent=-1,scale_x=1,scale_y=1,rotation=0,opacity=1,locked=False,
              comment=None):
        self._validate_comment(comment)
        i = self.next_id
        self.next_id += 1
        self._insert('items',id=i,parent=parent,name=name,transform=transform(x,y,scale_x,scale_y,rotation),
                     sort_order=rational(i+1),z=float(i+1),opacity=float(opacity),locked=int(locked),comment=comment)
        return i

    def image(self,path,**options):
        path = Path(path)
        data = path.read_bytes()
        fmt,w,h = image_info(data)
        options.setdefault('name',path.stem)
        return self.image_data(data,w,h,format=fmt,source=str(path.resolve()).replace('\\','/'),**options)

    def image_link(self,path,**options):
        """Reference an image on disk instead of embedding it (source_type 2).

        PureRef resolves the path at load time, falling back to the .pur's own
        folder and subfolders, so linked canvases stay small but need their files.
        """
        path = Path(path)
        fmt,w,h = image_info(path.read_bytes())
        options.setdefault('name',path.stem)
        return self.image_data(None,w,h,format=fmt,source=str(path.resolve()).replace('\\','/'),**options)

    def image_data(self,data,w,h,*,format='PNG',source='',x=0,y=0,name=None,parent=-1,
                   scale_x=1,scale_y=1,rotation=0,opacity=1,clip=None,comment=None,
                   flags=RENDER_SMOOTH,grayscale=False,playback_state=PLAYBACK_STATIC,
                   playback_frame=0,playback_speed=1.0):
        """Embed encoded image bytes; clip=(left,top,width,height) in original pixels.

        data=None stores a linked resource that PureRef loads from `source`.
        flags is a RenderFlag bitmask: RENDER_SMOOTH (bilinear) | RENDER_GRAYSCALE.
        """
        self._validate_comment(comment)
        if w<=0 or h<=0:
            raise ValueError('Image dimensions must be positive')
        if data is None and not source:
            raise ValueError('A linked image needs a source path')
        if grayscale:
            flags |= RENDER_GRAYSCALE
        checksum = hashlib.md5(data).hexdigest() if data is not None else None
        key = checksum or ('linked',source)
        if key not in self.resources:
            rid = len(self.resources)
            self.resources[key] = rid
            self._insert('images',id=rid,
                         source_type=SOURCE_EMBEDDED if data is not None else SOURCE_LINKED,
                         origin=source,source=source,format=format,checksum=checksum,
                         data=data,width=w,height=h)
        i = self._item(name,x,y,parent,scale_x,scale_y,rotation,opacity,comment=comment)
        image_bounds = bounds(w,h)
        if clip is not None:
            left,top,cw,ch = clip
            if cw<=0 or ch<=0:
                raise ValueError('Crop dimensions must be positive')
            left -= w/2; top -= h/2
            image_bounds = painter_path([(0,left,top),(1,left+cw,top),(1,left+cw,top+ch),
                                         (1,left,top+ch),(1,left,top)])
        self._insert('items_images',id=i,image=self.resources[key],playback_speed=float(playback_speed),
                     playback_state=playback_state,image_transform=transform(-w/2,-h/2),
                     image_bounds=image_bounds,playback_frame=playback_frame,flags=flags)
        return i

    def note(self,text,*,x=0,y=0,parent=-1,name=None,font='Open Sans',font_size=22,
             color='#eaeaea',background=None,width=-1,height=-1,rich_text=False,
             style='comfortable',text_color=None,comment=None):
        """Create a note with PureRef's 'comfortable' or 'compact' background mode.

        Pass Qt-compatible HTML with rich_text=True. Style changes padding only;
        the requested transform, fixed size, and HTML are stored unchanged.
        """
        if style not in NOTE_STYLES:
            raise ValueError("Note style must be 'comfortable' or 'compact'")
        if not rich_text:
            text = (f'<html><body style="font-family:{html.escape(font,quote=True)};font-size:{float(font_size)}px;'
                    f'color:{html.escape(color,quote=True)};">'
                    f'<p style="white-space:pre-wrap;margin:0">{html.escape(text)}</p></body></html>')
        i = self._item(name,x,y,parent,comment=comment)
        self._insert('items_notes',id=i,text_color=text_color,fixed_size=size(width,height),
                     background_color=background or '',text=text,style=NOTE_STYLES[style])
        return i

    def group(self,*,name=None,x=0,y=0,parent=-1,locked=True,background=None,lock_mode=None,
              comment=None):
        """lock_mode overrides `locked`: LOCK_OPEN or LOCK_CLOSED (the app default)."""
        i = self._item(name,x,y,parent,comment=comment)
        self._insert('items_groups',id=i,background_color=background,
                     lock_mode=int(locked) if lock_mode is None else int(lock_mode))
        return i

    def drawing(self,paths,*,x=0,y=0,parent=-1,name=None,rgba=(46,132,170,200),width=5,
                style=STROKE_ROUND,dashed=False,comment=None):
        i = self._item(name,x,y,parent,comment=comment)
        self._insert('items_drawings',id=i,strokes=strokes(paths,rgba,width,style,dashed=dashed))
        return i

    def to_bytes(self,thumbnail=b'',format_version='2.1',application_version='2.1.3'):
        """Serialize the scene. format_version='2.0' omits the header thumbnail field."""
        self.connection.execute('DELETE FROM metadata')
        self._insert('metadata',id=0,application_version=application_version,view_transform=transform(),
                     horizontal_scroll=0,vertical_scroll=0,thumbnail=thumbnail,saved=1)
        self.connection.commit()
        return wrap(self.connection.serialize(),thumbnail=b'' if format_version=='2.0' else thumbnail,
                    format_version=format_version,application_version=application_version)

    def write(self,path,thumbnail=b'',**options):
        """Refuse overwrites by default, including user canvases."""
        with Path(path).open('xb') as f:
            f.write(self.to_bytes(thumbnail,**options))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command',required=True)
    p = sub.add_parser('inspect'); p.add_argument('file')
    p = sub.add_parser('unpack'); p.add_argument('file'); p.add_argument('output')
    p = sub.add_parser('pack'); p.add_argument('database'); p.add_argument('output')
    p = sub.add_parser('extract'); p.add_argument('file'); p.add_argument('directory')
    p = sub.add_parser('create'); p.add_argument('output'); p.add_argument('images',nargs='+')
    a = parser.parse_args()
    if a.command == 'pack':
        data = wrap(Path(a.database).read_bytes())
        with Path(a.output).open('xb') as out:
            out.write(data)
        return
    if a.command == 'create':
        s = Scene()
        for i,path in enumerate(a.images):
            s.image(path,x=i*150,y=0)
        s.write(a.output)
        return
    f = PurFile.read(a.file)
    try:
        if a.command == 'inspect':
            print(json.dumps(f.inspect(),indent=2,ensure_ascii=True))
        elif a.command == 'unpack':
            with Path(a.output).open('xb') as out:
                out.write(f.database)
        else:
            directory = Path(a.directory)
            directory.mkdir(parents=True,exist_ok=True)
            for row in f.rows('images'):
                if row['data']:
                    extension = str(row['format']).lower()
                    if not extension.isalnum():
                        extension = 'bin'
                    with (directory/f'image-{int(row["id"])}.{extension}').open('xb') as out:
                        out.write(row['data'])
    finally:
        f.close()

if __name__ == '__main__':
    main()
