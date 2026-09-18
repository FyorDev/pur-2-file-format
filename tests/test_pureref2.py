import hashlib
import sqlite3
import struct
import unittest
from pathlib import Path
from pureref2 import *

ROOT = Path(__file__).resolve().parents[1] / 'investigation'

class FormatTests(unittest.TestCase):
    def test_all_authentic_fixtures(self):
        for name in ['00-empty','01-image','03-two','04-duplicate','20-note-only','21-group','22-drawing','23-line']:
            with self.subTest(name=name):
                data = (ROOT/(name+'.pur')).read_bytes()
                header,db = unwrap(data)
                self.assertTrue(header['checksum_valid'])
                self.assertEqual(wrap(db,thumbnail=header['thumbnail']),data)

    def test_truncation_and_invalid_offset(self):
        data = (ROOT/'01-image.pur').read_bytes()
        for truncated in [b'',data[:4],data[:40],data[:-1]]:
            with self.assertRaises(FormatError):
                unwrap(truncated)
        bad = data[:14]+struct.pack('>Q',2**63)+data[22:]
        with self.assertRaises(FormatError):
            unwrap(bad)

    def test_checksum_corruption_is_reported(self):
        data = bytearray((ROOT/'01-image.pur').read_bytes())
        data[5000] ^= 1
        self.assertFalse(unwrap(bytes(data))[0]['checksum_valid'])

    def test_known_qt_payloads(self):
        f=PurFile.read(ROOT/'01-image.pur')
        self.assertEqual(decode_variant(f.rows('items')[0]['transform'])['value'],
                         [1,0,0,0,1,0,100,200,1])
        self.assertEqual(decode_variant(f.rows('items_images')[0]['image_bounds'])['elements'][0],
                         [0,-32,-16])
        self.assertEqual(binary(rational(1)),binary(f.rows('items')[0]['sort_order']))
        f.close()

    def test_authentic_stroke(self):
        f=PurFile.read(ROOT/'22-drawing.pur')
        decoded=decode_variant(f.rows('items_drawings')[0]['strokes'])['strokes'][0]
        self.assertEqual(decoded['rgba16'],[46*257,132*257,170*257,200*257])
        self.assertEqual(decoded['width'],5)
        self.assertEqual(decoded['path']['elements'][1][0],2)
        f.close()

    def test_fresh_database_without_template(self):
        s=Scene(); group=s.group(name='group')
        s.image(ROOT/'red.png',parent=group); s.image(ROOT/'red.png',x=100,parent=group)
        s.note('\u03a9',parent=group)
        s.drawing([[(0,0,0),(1,10,20)]],parent=group)
        f=PurFile(s.to_bytes())
        self.assertTrue(f.header['checksum_valid'])
        self.assertEqual(len(f.rows('images')),1)
        self.assertEqual(len(f.rows('items')),5)
        self.assertEqual(f.inspect()['integrity_check'],['ok'])
        f.close()

    def test_image_dimensions(self):
        self.assertEqual(image_info((ROOT/'red.png').read_bytes()),('PNG',64,32))
        self.assertEqual(image_info((ROOT/'red.jpg').read_bytes()),('JPG',64,32))
        with self.assertRaises(ValueError):
            image_info(b'not an image')

    def test_bounds_checked_path(self):
        malicious=variant(1024,struct.pack('>I',0x7fffffff),'QPainterPath')
        with self.assertRaises(FormatError):
            decode_variant(malicious)

    def test_compact_note_matches_app_fixture(self):
        fixture=PurFile.read(ROOT/'24-compact-note.pur')
        s=Scene(); s.note('Compact mode probe',style='compact')
        generated=PurFile(s.to_bytes())
        self.assertEqual(generated.rows('items_notes'),fixture.rows('items_notes'))
        self.assertEqual(decode_variant(generated.rows('items')[0]['transform']),
                         decode_variant(fixture.rows('items')[0]['transform']))
        fixture.close(); generated.close()

    def test_note_mode_changes_only_style(self):
        a=Scene(); a.note('<b>Padding</b>',x=23,y=-45,width=180,height=90,
                         rich_text=True)
        b=Scene(); b.note('<b>Padding</b>',x=23,y=-45,width=180,height=90,
                         rich_text=True,style='compact')
        default=PurFile(a.to_bytes()); compact=PurFile(b.to_bytes())
        self.assertEqual(default.rows('items'),compact.rows('items'))
        note=default.rows('items_notes')[0]
        self.assertEqual(note['style'],0)
        note['style']=1
        self.assertEqual(note,compact.rows('items_notes')[0])
        default.close(); compact.close()

    def test_invalid_note_mode_does_not_add_item(self):
        s=Scene()
        with self.assertRaises(ValueError):
            s.note('test',style='unknown')
        self.assertEqual(s.next_id,0)
        self.assertEqual(s.connection.execute('SELECT count(*) FROM items').fetchone()[0],0)

    def test_comments_on_all_item_types(self):
        comments=['Image comment: \u03a9\nsecond line','Note comment','Group comment','Drawing comment']
        s=Scene()
        s.image(ROOT/'red.png',comment=comments[0])
        s.note('note',comment=comments[1])
        s.group(comment=comments[2])
        s.drawing([[(0,0,0),(1,10,20)]],comment=comments[3])
        f=PurFile(s.to_bytes())
        self.assertEqual([row['comment'] for row in f.rows('items')],comments)
        self.assertEqual({row[0] for row in f.connection.execute(
            'SELECT typeof(comment) FROM items WHERE comment IS NOT NULL')},{'text'})
        f.close()

    def test_comment_must_be_text_or_none(self):
        s=Scene()
        with self.assertRaises(TypeError):
            s.note('test',comment=123)
        self.assertEqual(s.next_id,0)
        with self.assertRaises(TypeError):
            s.image(ROOT/'red.png',comment=123)
        self.assertEqual(s.resources,{})

class Version20Tests(unittest.TestCase):
    """PureRef 2.0.3 writes 2.1 envelopes; early 2.0.x wrote a thumbnail-less one."""

    def test_app_2_0_3_file_reads(self):
        f = PurFile.read(ROOT/'30-app-2.0.3.pur')
        self.assertTrue(f.header['checksum_valid'])
        self.assertEqual(f.header['format_version'],'2.1')
        self.assertEqual(f.header['application_version'],'2.0.3')
        self.assertEqual(decode_variant(f.rows('items')[0]['transform'])['value'][6:8],[100,200])
        f.close()

    def test_envelope_2_0_has_no_thumbnail_field(self):
        data = (ROOT/'31-envelope-2.0.pur').read_bytes()
        header,db = unwrap(data)
        self.assertEqual(header['format_version'],'2.0')
        self.assertTrue(header['checksum_valid'])
        self.assertEqual(header['thumbnail'],b'')
        self.assertEqual(header['header_size'],header['checksum_start']+0)
        self.assertEqual(wrap(db,application_version=header['application_version'],
                              format_version='2.0'),data)

    def test_2_0_envelope_rejects_thumbnail(self):
        db = (ROOT/'31-envelope-2.0.pur').read_bytes()
        with self.assertRaises(ValueError):
            wrap(unwrap(db)[1],thumbnail=b'\xff\xd8ignored',format_version='2.0')

class BigRationalTests(unittest.TestCase):
    def test_round_trip_signs_and_limbs(self):
        for numerator,denominator in [(1,1),(0,1),(-3,1),(7,2),(2**32,1),(-(2**70)-5,3)]:
            with self.subTest(value=(numerator,denominator)):
                decoded = decode_variant(rational(numerator,denominator))
                self.assertEqual(decoded['type_name'],'BigRational')
                self.assertEqual((decoded['numerator'],decoded['denominator']),
                                 (numerator,denominator))

    def test_app_ordered_probe_values(self):
        """PureRef sorted these six crafted orders exactly as decoded here."""
        f = PurFile.read(ROOT/'32-rational-probe.pur')
        orders = [(decode_variant(r['sort_order'])['numerator'],
                   decode_variant(r['sort_order'])['denominator']) for r in f.rows('items')]
        self.assertEqual(orders,[(-3,1),(0,1),(3,1),(7,2),(5,1),(2**32,1)])
        f.close()

    def test_denominator_must_be_positive(self):
        with self.assertRaises(ValueError):
            rational(1,0)

class FeatureTests(unittest.TestCase):
    def test_dashed_stroke_matches_app_resave(self):
        f = PurFile.read(ROOT/'33-dashed-app-2.0.3.pur')
        flags = [decode_variant(r['strokes'])['strokes'][0]['dashed'] for r in f.rows('items_drawings')]
        self.assertEqual(sorted(flags),[False,True])
        f.close()
        s = Scene()
        s.drawing([[(0,0,0),(1,240,0)]])
        s.drawing([[(0,0,0),(1,240,0)]],dashed=True)
        s.drawing([[(0,0,0),(1,240,0)]],style=STROKE_FLAT)
        generated = PurFile(s.to_bytes())
        decoded = [decode_variant(r['strokes'])['strokes'][0]
                   for r in generated.rows('items_drawings')]
        self.assertEqual([stroke['style'] for stroke in decoded],
                         [STROKE_ROUND,STROKE_DASHED,STROKE_FLAT])
        self.assertEqual([stroke['point'] for stroke in decoded],[[0.0,0.0]]*3)
        self.assertEqual({stroke['version'] for stroke in decoded},{STROKE_VERSION})
        generated.close()

    def test_strokes_written_before_the_version_byte_still_read(self):
        """A first byte below 100 is the QColor, not a version."""
        color = struct.pack('>b5H',1,255*257,240*257,200*257,60*257,0)
        path = binary(painter_path([(0,0,0),(1,200,0)]))
        reader = Reader(path); reader.unpack('IB'); reader.bytearray()
        payload = (struct.pack('>I',1) + color + struct.pack('>d',16.0)
                   + path[reader.pos:] + struct.pack('>2d',0.0,0.0))
        decoded = decode_variant(variant(1024,payload,'QList<GraphicsDrawItem::Stroke>'))
        stroke, = decoded['strokes']
        self.assertEqual(stroke['rgba16'],[240*257,200*257,60*257,255*257])
        self.assertEqual(stroke['width'],16.0)
        self.assertEqual(stroke['style'],STROKE_ROUND)

    def test_render_flags(self):
        s = Scene()
        s.image(ROOT/'red.png',grayscale=True)
        s.image(ROOT/'red.png',x=100,flags=0)
        f = PurFile(s.to_bytes())
        self.assertEqual([r['flags'] for r in f.rows('items_images')],
                         [RENDER_SMOOTH|RENDER_GRAYSCALE,0])
        f.close()

    def test_linked_resource(self):
        s = Scene(); s.image_link(ROOT/'red.png')
        f = PurFile(s.to_bytes())
        row, = f.rows('images')
        self.assertEqual((row['source_type'],row['data'],row['checksum']),(SOURCE_LINKED,None,None))
        self.assertTrue(row['source'].endswith('red.png'))
        self.assertEqual((row['width'],row['height']),(64,32))
        f.close()

    def test_app_linked_fixture(self):
        f = PurFile.read(ROOT/'35-linked-2.0.3.pur')
        row, = f.rows('images')
        self.assertEqual((row['source_type'],row['data'],row['checksum']),(SOURCE_LINKED,None,None))
        f.close()

    def test_animation_fixture_and_gif_dimensions(self):
        self.assertEqual(image_info((ROOT/'anim.gif').read_bytes()),('GIF',16,16))
        f = PurFile.read(ROOT/'34-animation-2.0.3.pur')
        row, = f.rows('items_images')
        self.assertEqual(row['playback_state'],PLAYBACK_PLAYING)
        self.assertEqual(f.rows('images')[0]['format'],'gif')
        f.close()

    def test_note_text_color_and_group_lock_mode(self):
        s = Scene()
        s.note('colored',text_color='#ff00ff')
        s.group(lock_mode=LOCK_OPEN)
        f = PurFile(s.to_bytes())
        self.assertEqual(f.rows('items_notes')[0]['text_color'],'#ff00ff')
        self.assertEqual(f.rows('items_groups')[0]['lock_mode'],LOCK_OPEN)
        f.close()

if __name__ == '__main__':
    unittest.main()

class RobustnessTests(unittest.TestCase):
    """A damaged file may be refused, but it may not crash the reader.

    Every fixture is truncated and bit-flipped, and each result has to come back
    as a report or as a FormatError. Anything else -- a sqlite3 error escaping, a
    cell that is not UTF-8 raising from inside a cursor, a number where a payload
    belongs -- is a bug here, because the input is somebody else's file.
    """

    def attempt(self, data):
        try:
            f = PurFile(data)
        except (FormatError, ValueError):
            return None
        try:
            return f.inspect()
        except (FormatError, ValueError):
            return None
        finally:
            f.close()

    def test_truncation(self):
        for name in ['01-image.pur', '20-note.pur', '22-drawing.pur', '30-app-2.0.3.pur']:
            data = (ROOT/name).read_bytes()
            for length in [0, 1, 16, 100, 108, len(data)//3, len(data)//2, len(data)-1]:
                with self.subTest(name=name, length=length):
                    self.attempt(data[:length])

    def test_bit_flips(self):
        for name in ['01-image.pur', '21-group.pur', '23-line.pur', '31-envelope-2.0.pur']:
            data = bytearray((ROOT/name).read_bytes())
            step = max(1, len(data)//128)
            for offset in range(0, len(data), step):
                for bit in (0x01, 0x80):
                    damaged = bytearray(data)
                    damaged[offset] ^= bit
                    with self.subTest(name=name, offset=offset, bit=bit):
                        self.attempt(bytes(damaged))

    def test_inputs_that_are_not_pur_files(self):
        for data in [b'', b'\0'*8, b'SQLite format 3\0', b'not a pur file', bytes(range(256))*4]:
            with self.subTest(head=data[:8]):
                with self.assertRaises(FormatError):
                    PurFile(data)

    def test_a_number_where_a_payload_belongs_is_reported(self):
        self.assertEqual(PurFile.opaque(42), {'not_a_payload': '42'})
        with self.assertRaises(FormatError):
            binary(42)


class SchemaReportTests(unittest.TestCase):
    def setUp(self):
        self.file = PurFile.read(ROOT/'30-app-2.0.3.pur')
        self.addCleanup(self.file.close)

    def test_an_app_file_matches_the_schema_this_package_writes(self):
        self.assertEqual(self.file.schema_report(),
                         {'unknown_tables': [], 'unknown_columns': {}, 'missing_columns': {}})

    def test_the_pragmas_are_the_ones_pureref_gates_on(self):
        pragmas = self.file.pragmas()
        self.assertEqual(pragmas['user_version'], 200101)
        self.assertEqual(pragmas['application_id'], 940753918)
        self.assertEqual(pragmas['encoding'], 'UTF-8')

    def test_a_column_pureref_added_later_is_still_readable(self):
        scene = Scene()
        scene.image_data(b'x'*8, 4, 4, format='PNG', source='/tmp/x.png')
        scene.connection.execute('ALTER TABLE items ADD COLUMN mood TEXT')
        scene.connection.execute("UPDATE items SET mood='new'")
        f = PurFile(scene.to_bytes())
        self.addCleanup(f.close)
        self.assertEqual(f.schema_report()['unknown_columns'], {'items': ['mood']})
        self.assertEqual(f.rows('items')[0]['mood'], 'new')

    def test_a_missing_column_is_named(self):
        # PureRef refuses such a file outright: "Table metadata has no column
        # named saved". Reporting which column is gone is the point of the check.
        _, database = unwrap(Scene().to_bytes())
        edited = sqlite3.connect(':memory:')
        edited.deserialize(database)
        edited.execute('ALTER TABLE metadata DROP COLUMN saved')
        f = PurFile(wrap(edited.serialize()))
        self.addCleanup(f.close)
        self.assertEqual(f.schema_report()['missing_columns'], {'metadata': ['saved']})
