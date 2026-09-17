"""Integration tests against the installed PureRef, using only this directory.

Each run writes to a fresh validation-* directory. No personal files are used.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from .make_fixtures import ROOT, run, png
from pureref2 import Scene, PurFile, decode_variant, binary, unwrap, wrap

def validate():
    out = Path(tempfile.mkdtemp(prefix='validation-',dir=ROOT))
    results = {}
    # Confirm envelope reversal and exact repacking of all authentic fixtures.
    for name in ['00-empty','01-image','02-origin','03-two','04-duplicate','05-resave',
                 '20-note-only','21-group','22-drawing','23-line']:
        data = (ROOT/(name+'.pur')).read_bytes()
        h,db = unwrap(data)
        assert h['checksum_valid'],name
        assert wrap(db,application_version=h['application_version'],thumbnail=h['thumbnail'],reserved=h['reserved']) == data,name
        f = PurFile(data)
        assert f.inspect()['integrity_check'] == ['ok'],name
        f.close()
    results['authentic_fixtures_exact_repack'] = 10

    def app_render(name,path):
        render = out/(name+'.png')
        saved = out/(name+'-resaved.pur')
        saved.touch()  # PureRef 2.1.3 CLI save-as requires an existing destination.
        log = run([f'load;{path}',f'exportScene;{render};512;512;false;false',f'save;{saved}','exit'])
        (out/(name+'.log')).write_text(log)
        assert '[Warning]' not in log and '[Critical]' not in log,log
        assert render.stat().st_size > 0
        parsed = PurFile.read(saved)
        assert parsed.header['checksum_valid']
        assert parsed.inspect()['integrity_check'] == ['ok']
        return render,parsed

    # Independent image placement must render identically to app-generated file.
    s = Scene()
    s.image(ROOT/'red.png',x=100,y=200)
    s.image(ROOT/'blue.png',x=-50,y=75)
    s.write(out/'images.pur')
    actual,parsed = app_render('images',out/'images.pur')
    expected,reference = app_render('reference',ROOT/'03-two.pur')
    assert actual.read_bytes() == expected.read_bytes()
    results['two_image_render_identical'] = True
    for row in parsed.rows('images'):
        assert hashlib.md5(row['data']).hexdigest() == row['checksum']

    # Fresh schema and independently chosen values for every item class.
    s = Scene()
    comments = ['Group comment','Image comment: \u03a9 \u4e2d\nsecond line','Note comment','Drawing comment']
    g = s.group(name='Generated group',x=20,y=-15,background='#80305070',comment=comments[0])
    s.image(ROOT/'red.png',x=-75,y=30,parent=g,rotation=30,scale_x=1.5,scale_y=.75,opacity=.65,
            comment=comments[1])
    s.image(ROOT/'blue.png',x=65,y=30,parent=g)
    s.image(ROOT/'red.png',x=140,y=30,parent=g)  # deduplication
    s.note('Written from scratch: \u03a9 \u4e2d\nsecond line',x=0,y=-75,parent=g,background='#a0203040',
           comment=comments[2])
    s.drawing([[(0,-90,90),(1,90,90)],[(0,-90,110),(2,-40,150),(3,40,70),(3,90,110)]],
              parent=g,rgba=(240,100,35,255),width=3,comment=comments[3])
    s.write(out/'mixed.pur')
    _,mixed = app_render('mixed',out/'mixed.pur')
    assert len(mixed.rows('images')) == 2
    assert len(mixed.rows('items_images')) == 3
    assert len(mixed.rows('items_notes')) == 1
    assert len(mixed.rows('items_groups')) == 1
    assert len(mixed.rows('items_drawings')) == 1
    assert '\u03a9' in mixed.rows('items_notes')[0]['text']
    drawing = decode_variant(mixed.rows('items_drawings')[0]['strokes'])
    assert len(drawing['strokes']) == 2
    assert drawing['strokes'][0]['width'] == 3
    assert drawing['strokes'][0]['rgba16'] == [61680,25700,8995,65535]
    assert [row['comment'] for row in mixed.rows('items') if row['comment'] is not None] == comments
    assert all(row[0] == 'text' for row in mixed.connection.execute(
        'SELECT typeof(comment) FROM items WHERE comment IS NOT NULL'))
    results['mixed_canvas_all_item_classes'] = True
    results['comments_round_trip'] = True
    results['mixed_inspection'] = mixed.inspect()

    # Crop top-left quarter, preserving the original image coordinate system.
    s = Scene()
    s.image(ROOT/'red.png',x=100,y=200,clip=(0,0,32,16))
    s.image(ROOT/'blue.png',x=-50,y=75)
    s.write(out/'crop.pur')
    crop,_ = app_render('crop',out/'crop.pur')
    png('red-quarter.png',32,16,(220,50,30))
    s = Scene()
    s.image(ROOT/'red-quarter.png',x=84,y=192)
    s.image(ROOT/'blue.png',x=-50,y=75)
    s.write(out/'crop-reference.pur')
    crop_ref,_ = app_render('crop-reference',out/'crop-reference.pur')
    assert crop.read_bytes() == crop_ref.read_bytes()
    results['crop_render_matches_separate_image'] = True

    s = Scene()
    parent = s.group(x=100,y=50)
    child = s.group(x=-30,y=20,parent=parent)
    s.image(ROOT/'red.jpg',x=15,y=25,parent=child)
    s.write(out/'nested-jpeg.pur')
    _,nested = app_render('nested-jpeg',out/'nested-jpeg.pur')
    assert len(nested.rows('items_groups')) == 2
    assert nested.rows('images')[0]['data'] == (ROOT/'red.jpg').read_bytes()
    assert nested.rows('items')[2]['parent'] == child
    results['nested_groups_and_jpeg'] = True

    # The app's actual Compact selector changes only items_notes.style to 1.
    # Match view metadata so the comparison tests note rendering, not framing.
    compact_ref = PurFile.read(ROOT/'24-compact-note.pur')
    s = Scene()
    s.note('Compact mode probe',style='compact')
    s.to_bytes()
    metadata = compact_ref.rows('metadata')[0]
    framing = ('scene_rect','view_transform','horizontal_scroll','vertical_scroll')
    s.connection.execute('UPDATE metadata SET '+','.join(k+'=?' for k in framing)+' WHERE id=0',
                         [metadata[k] for k in framing])
    s.connection.commit()
    (out/'compact.pur').write_bytes(wrap(s.connection.serialize()))
    compact_png,compact_saved = app_render('compact',out/'compact.pur')
    reference_png,_ = app_render('compact-reference',ROOT/'24-compact-note.pur')
    assert compact_png.read_bytes() == reference_png.read_bytes()
    assert compact_saved.rows('items_notes')[0]['style'] == 1
    results['compact_note_render_matches_app_fixture'] = True
    (out/'results.json').write_text(json.dumps(results,indent=2))
    print(json.dumps({'directory':str(out),**{k:v for k,v in results.items() if k!='mixed_inspection'}},indent=2))
    return out

if __name__ == '__main__':
    validate()
