"""Generate a complete .pur from scratch; no app, template, or image input needed."""
from pathlib import Path
import struct
import sys
import zlib
from pureref2 import Scene, bounds, rational, rect, variant, wrap

def png(width,height,pixel):
    def chunk(kind,data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data))
    pixels=b''.join(b'\0'+b''.join(bytes(pixel(x,y)) for x in range(width)) for y in range(height))
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))
            +chunk(b'IDAT',zlib.compress(pixels))+chunk(b'IEND',b''))

def shared_tile_png():
    """A muted two-part gradient used by every image in the shared grid."""
    warm=((226,84,145),(242,126,135))
    cool=((242,150,65),(170,205,70))

    def pixel(x,y):
        start,end=warm if x<16 else cool
        return tuple(round(a+(b-a)*y/31) for a,b in zip(start,end))

    return png(32,32,pixel)

# Rich-text formatting authored in the editor.
SUBTITLE_HTML = '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0//EN" "http://www.w3.org/TR/REC-html40/strict.dtd">\n<html><head><meta name="qrichtext" content="1" /><meta charset="utf-8" /><style type="text/css">\np, li { white-space: pre-wrap; }\nli.unchecked::marker { content: "\\2610"; }\nli.checked::marker { content: "\\2612"; }\n</style></head><body style=" font-family:\'Open Sans\'; font-size:22px; font-weight:400; font-style:normal;">\n<p style=" margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px;"><span style=" font-size:19px; color:#eaeaea;">Images · transforms · crops · notes · groups · curves</span></p></body></html>'

CHECKER_DENSITY = 8

def build(output):
    scene=Scene()
    group=scene.group(name='Created without PureRef',background='#ff203040',
                      comment='Generated entirely by pureref2.py.\nUnicode comment check: Ω 中')
    title=scene.note('.pur encoded from scratch, rendered by PureRef',parent=group,font_size=24)
    subtitle=scene.note(SUBTITLE_HTML,parent=group,rich_text=True,style='compact')
    gradient=png(160,100,lambda x,y:(40+x,70+y,220-y))
    # More source pixels, with the same 10-by-14 grid and logical canvas size.
    density=CHECKER_DENSITY
    stripes=png(100*density,140*density,
                lambda x,y:(240,100,40) if (x//(10*density)+y//(10*density))%2 else (40,160,180))
    gradient_item=scene.image_data(gradient,160,100,parent=group,name='Gradient')
    checker=scene.image_data(stripes,100*density,140*density,parent=group,name='Checks')
    pixel_to_canvas=variant(80,struct.pack('>9d',1/density,0,0,0,1/density,0,-50,-70,1))
    scene.connection.execute(
        'UPDATE items_images SET image_transform=?, image_bounds=? WHERE id=?',
        (pixel_to_canvas,bounds(100,140),checker))
    pattern=scene.group(name='Shared image pattern',parent=group,background='#00000000',
                        comment='234 independently transformed items sharing one embedded image resource.')
    tile=shared_tile_png()
    columns,rows,tile_size,gap=13,18,5.2,.55
    step=tile_size+gap
    for row in range(rows):
        for column in range(columns):
            i=row*columns+column
            scene.image_data(tile,32,32,parent=pattern,
                             name=f'Shared tile {i+1}',
                             x=(column-(columns-1)/2)*step,
                             y=(row-(rows-1)/2)*step,
                             scale_x=tile_size/32,scale_y=tile_size/32,
                             rotation=(i%4)*90)
    drawing=scene.drawing([[(0,-215,115),(2,-70,170),(3,70,70),(3,235,115)]],
                          parent=group,rgba=(250,165,50,255),width=4)
    # Full saved editor layout, including recentering performed during canvas resize.
    # Each entry is (item_id, [m11, m12, m21, m22, x, y], z, sibling_order).
    layout = [
        (group, [1.0, 0.0, 0.0, 1.0, 32.807692307692065, -44.96153846153845], 5.0, 1),
        (title, [1.0, 0.0, 0.0, 1.0, 0.0, -102.13205015053313], 7.0, 1),
        (subtitle, [1.0, 0.0, 0.0, 1.0, 0.0, -45.13205015053313], 6.0, 2),
        (gradient_item, [0.9781476007338057, -0.20791169081775937, 0.20791169081775937, 0.9781476007338057, -140.99999999999997, 66.86794984946687], 1.0, 3),
        (checker, [0.9641302408632731, 0.1354996688127422, -0.1354996688127422, 0.9641302408632731, 32.0, 58.86794984946687], 3.0, 4),
        (pattern, [1.1817693036146495, -0.2083778132003164,
                   0.2083778132003164, 1.1817693036146495,
                   179.0, 66.86794984946687], 4.0, 5),
        (drawing, [1.0, 0.0, 0.0, 1.0, -1.0, 46.86794984946686], 2.0, 6),
    ]
    for item_id, (a, b, c, d, x, y), z, order in layout:
        matrix = variant(80, struct.pack('>9d', a, b, 0, c, d, 0, x, y, 1))
        scene.connection.execute(
            'UPDATE items SET transform=?, z=?, sort_order=? WHERE id=?',
            (matrix, z, rational(order), item_id))
    # Retain the authored scene framing as well as the item layout.
    scene.to_bytes()  # Initialize the standard metadata row.
    scene.connection.execute(
        'UPDATE metadata SET scene_rect=?, view_transform=?, horizontal_scroll=?, vertical_scroll=? WHERE id=0',
        (rect(*[-277.30769230769226, -226.24743476591746, 619.038461538461, 374.8878232275099]),
         variant(80, struct.pack('>9d', *[0.8387096774193603, 0.0, 0.0, 0.0, 0.8387096774193603, 0.0, 0.0, 0.0, 1.0])),
         -291, -249))
    scene.connection.commit()
    with Path(output).open('xb') as stream:
        stream.write(wrap(scene.connection.serialize()))
    print(Path(output).resolve())

if __name__=='__main__':
    build(sys.argv[1] if len(sys.argv)>1 else 'standalone-demo.pur')
