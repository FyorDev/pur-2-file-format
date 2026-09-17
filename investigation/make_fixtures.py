"""Generate only synthetic test canvases using the installed PureRef CLI."""
import pathlib, struct, zlib, subprocess, json, os

ROOT = pathlib.Path(__file__).resolve().parent
EXE = os.environ.get('PUREREF_EXE', r'C:\Program Files\PureRef\PureRef.exe')

def png(name, w, h, rgb):
    def chunk(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t+d))
    data = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB',w,h,8,2,0,0,0))
    data += chunk(b'IDAT', zlib.compress((b'\0' + bytes(rgb)*w)*h)) + chunk(b'IEND', b'')
    (ROOT/name).write_bytes(data)

def run(commands, timeout=40):
    args = [EXE, '-s', str(ROOT/'settings.ini')]
    for command in commands:
        args += ['-c', command]
    result = subprocess.run(args, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    log = (result.stdout + result.stderr).decode(errors='replace')
    if '[Critical]' in log:
        raise RuntimeError(log)
    return log

if __name__ == '__main__':
    png('red.png',64,32,(220,50,30))
    png('blue.png',30,90,(20,70,230))
    scenarios = {
        '00-empty': [],
        '02-origin': [f'load;{ROOT / "red.png"};0;0'],
        '03-two': [f'load;{ROOT / "red.png"};100;200', f'load;{ROOT / "blue.png"};-50;75'],
        '04-duplicate': [f'load;{ROOT / "red.png"};100;200', f'load;{ROOT / "red.png"};-50;75'],
        '05-resave': [f'load;{ROOT / "01-image.pur"}'],
    }
    logs = {}
    for name, commands in scenarios.items():
        if name == '05-resave':
            (ROOT/(name+'.pur')).touch()
        logs[name] = run(commands + [f'save;{ROOT / (name+".pur")}', 'exit'])
        (ROOT/'fixture-generation-log.json').write_text(json.dumps(logs,indent=2))
        print(name, (ROOT/(name+'.pur')).stat().st_size)
    (ROOT/'fixture-generation-log.json').write_text(json.dumps(logs,indent=2))
