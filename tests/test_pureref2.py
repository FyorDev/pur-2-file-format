import hashlib
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

if __name__ == '__main__':
    unittest.main()
