"""Generate a complete .pur from scratch; no app, template, or image input needed."""
from pathlib import Path
import struct
import sys
import zlib
from pureref2 import Scene

def png(width,height,pixel):
    def chunk(kind,data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data))
    pixels=b''.join(b'\0'+b''.join(bytes(pixel(x,y)) for x in range(width)) for y in range(height))
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))
            +chunk(b'IDAT',zlib.compress(pixels))+chunk(b'IEND',b''))

def build(output):
    scene=Scene()
    group=scene.group(name='Created without PureRef',background='#ff203040')
    scene.note('PureRef written from scratch',x=0,y=-120,parent=group,font_size=24)
    scene.note('Images · transforms · crops · notes · groups · curves',
               x=0,y=-80,parent=group,font_size=13)
    gradient=png(160,100,lambda x,y:(40+x,70+y,220-y))
    stripes=png(100,140,lambda x,y:(240,100,40) if (x//10+y//10)%2 else (40,160,180))
    scene.image_data(gradient,160,100,x=-140,y=20,parent=group,name='Gradient',rotation=-12)
    scene.image_data(stripes,100,140,x=40,y=20,parent=group,name='Checks',rotation=8)
    scene.image_data(gradient,160,100,x=180,y=20,parent=group,name='Cropped duplicate',clip=(40,0,80,100))
    scene.drawing([[(0,-215,115),(2,-70,170),(3,70,70),(3,235,115)]],
                  parent=group,rgba=(250,165,50,255),width=4)
    scene.write(output)
    print(Path(output).resolve())

if __name__=='__main__':
    build(sys.argv[1] if len(sys.argv)>1 else 'standalone-demo.pur')
