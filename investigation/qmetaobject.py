"""Dump the Q_ENUMs out of a stripped Qt application.

A class that says `Q_OBJECT` gets a `staticMetaObject` whose address is exported
even when the binary is stripped, and everything moc knew about the class hangs
off it: the class name, its superclass, its properties, and the name and value of
every key of every `Q_ENUM` and `Q_FLAG`. That is exactly the information you
otherwise have to guess at by writing values into a file and looking at what the
application draws.

Three pieces have to be walked:

    staticMetaObject -> { superdata, stringdata, data, ... }   three pointers
    data             -> uint32 array: counts and offsets, then the enum table
    stringdata       -> the strings, laid out differently in Qt 5 and Qt 6

Qt 5 stores strings as an array of 24-byte `QByteArrayData` headers, each holding
a size and an offset relative to itself. Qt 6 stores one array of (offset, size)
uint32 pairs, offsets relative to the array. Which one a binary uses is detected
by decoding the class name both ways and keeping whichever matches the symbol.

Usage:

    python qmetaobject.py <binary> [<binary> ...] [--json] [--only PATTERN]
"""
from __future__ import annotations

import json
import re
import struct
import sys
from dataclasses import dataclass, field

# Offsets into the uint32 array that `QMetaObject::d.data` points at. Stable
# across every moc revision this handles (7 through 12).
REVISION, CLASSNAME = 0, 1
ENUM_COUNT, ENUM_OFFSET = 8, 9
HEADER_WORDS = 14

QBYTEARRAYDATA_SIZE = 24        # Qt 5: ref, size, alloc bits, padding, offset
ENUM_IS_FLAG, ENUM_IS_SCOPED = 0x1, 0x2


class ElfError(ValueError):
    pass


@dataclass
class Elf:
    """Just enough ELF to read virtual addresses and the dynamic symbols."""

    data: bytes
    segments: list[tuple[int, int, int]] = field(default_factory=list)  # va, size, offset
    symbols: dict[str, tuple[int, int]] = field(default_factory=dict)   # name -> va, size

    @classmethod
    def load(cls, path: str) -> 'Elf':
        data = open(path, 'rb').read()
        if data[:4] != b'\x7fELF':
            raise ElfError(f'{path} is not an ELF file')
        if data[4] != 2 or data[5] != 1:
            raise ElfError('Only 64-bit little-endian ELF is supported')
        elf = cls(data)
        section_offset = struct.unpack_from('<Q', data, 0x28)[0]
        section_size, section_count, string_index = struct.unpack_from('<3H', data, 0x3A)
        sections = []
        for index in range(section_count):
            base = section_offset + index * section_size
            name, kind, flags, va, offset, size, link, _, _, entry = \
                struct.unpack_from('<IIQQQQIIQQ', data, base)
            sections.append(dict(name=name, kind=kind, flags=flags, va=va,
                                 offset=offset, size=size, link=link, entry=entry))
        names_offset = sections[string_index]['offset']

        def name_of(section) -> str:
            start = names_offset + section['name']
            return data[start:data.index(b'\0', start)].decode()

        for section in sections:
            if section['flags'] & 0x2 and section['kind'] != 8:   # ALLOC, not NOBITS
                elf.segments.append((section['va'], section['size'], section['offset']))
            if section['kind'] == 11:                              # DYNSYM
                elf._read_symbols(section, sections)
        return elf

    def _read_symbols(self, section, sections) -> None:
        strings = sections[section['link']]
        count = section['size'] // (section['entry'] or 24)
        for index in range(count):
            base = section['offset'] + index * section['entry']
            name, _, _, _, value, size = struct.unpack_from('<IBBHQQ', self.data, base)
            if not value:
                continue
            start = strings['offset'] + name
            end = self.data.index(b'\0', start)
            self.symbols[self.data[start:end].decode('utf-8', 'replace')] = (value, size)

    def offset_of(self, va: int) -> int:
        for start, size, offset in self.segments:
            if start <= va < start + size:
                return offset + (va - start)
        raise ElfError(f'Address 0x{va:x} is not in any loaded section')

    def read(self, va: int, count: int) -> bytes:
        offset = self.offset_of(va)
        return self.data[offset:offset + count]

    def u32(self, va: int) -> int:
        return struct.unpack('<I', self.read(va, 4))[0]

    def u64(self, va: int) -> int:
        return struct.unpack('<Q', self.read(va, 8))[0]

    def i64(self, va: int) -> int:
        return struct.unpack('<q', self.read(va, 8))[0]

    def string(self, va: int, size: int) -> str:
        return self.read(va, size).decode('utf-8', 'replace')


def demangle_class(symbol: str) -> str | None:
    """`_ZN17GraphicsImageItem16staticMetaObjectE` -> GraphicsImageItem.

    Only the nested-name form moc generates is handled, which is all that is
    needed to recognise the symbols and label them.
    """
    if not symbol.startswith('_ZN') or not symbol.endswith('staticMetaObjectE'):
        return None
    rest, parts = symbol[3:], []
    while rest:
        digits = re.match(r'\d+', rest)
        if not digits:
            break
        length = int(digits.group())
        start = digits.end()
        parts.append(rest[start:start + length])
        rest = rest[start + length:]
    if parts and parts[-1] == 'staticMetaObject':
        parts.pop()
    return '::'.join(parts) or None


class Strings:
    """The two string-table layouts, chosen by whichever decodes the class name."""

    def __init__(self, elf: Elf, base: int, flavour: str):
        self.elf, self.base, self.flavour = elf, base, flavour

    def __getitem__(self, index: int) -> str:
        if self.flavour == 'qt6':
            offset = self.elf.u32(self.base + 8 * index)
            size = self.elf.u32(self.base + 8 * index + 4)
            return self.elf.string(self.base + offset, size)
        header = self.base + QBYTEARRAYDATA_SIZE * index
        size = struct.unpack('<i', self.elf.read(header + 4, 4))[0]
        offset = self.elf.i64(header + 16)
        return self.elf.string(header + offset, size)


@dataclass
class Enum:
    name: str
    alias: str
    is_flag: bool
    is_scoped: bool
    flags: int
    keys: list[tuple[str, int]]


@dataclass
class MetaClass:
    name: str
    revision: int
    superclass: str | None
    enums: list[Enum]


def read_meta_object(elf: Elf, address: int, expected: str) -> MetaClass:
    superdata = elf.u64(address)
    stringdata = elf.u64(address + 8)
    data = elf.u64(address + 16)
    if not stringdata or not data:
        raise ElfError('meta object pointers are not initialised in the image')
    revision = elf.u32(data + 4 * REVISION)
    if not 7 <= revision <= 12:
        raise ElfError(f'unexpected moc revision {revision}')
    name_index = elf.u32(data + 4 * CLASSNAME)

    strings = None
    for flavour in ('qt6', 'qt5'):
        candidate = Strings(elf, stringdata, flavour)
        try:
            if candidate[name_index] == expected:
                strings = candidate
                break
        except (ElfError, UnicodeDecodeError, struct.error):
            continue
    if strings is None:
        raise ElfError(f'neither string layout decodes the name of {expected}')

    superclass = None
    if superdata:
        # The parent's name lives in the parent's own string table, not this one.
        try:
            parent_strings = Strings(elf, elf.u64(superdata + 8), strings.flavour)
            parent_data = elf.u64(superdata + 16)
            superclass = parent_strings[elf.u32(parent_data + 4 * CLASSNAME)]
        except (ElfError, UnicodeDecodeError, struct.error):
            superclass = None

    return MetaClass(name=expected, revision=revision, superclass=superclass,
                     enums=_read_enums(elf, data, strings, revision))


def _read_enums(elf: Elf, data: int, strings: Strings, revision: int) -> list[Enum]:
    count = elf.u32(data + 4 * ENUM_COUNT)
    table = elf.u32(data + 4 * ENUM_OFFSET)
    if not count or table < HEADER_WORDS:
        return []
    words = 5 if revision >= 8 else 4       # the alias was added in revision 8
    enums = []
    for index in range(count):
        entry = data + 4 * (table + index * words)
        name = strings[elf.u32(entry)]
        alias = strings[elf.u32(entry + 4)] if words == 5 else name
        flags = elf.u32(entry + 4 * (words - 3))
        key_count = elf.u32(entry + 4 * (words - 2))
        key_table = elf.u32(entry + 4 * (words - 1))
        keys = []
        for key in range(key_count):
            pair = data + 4 * (key_table + key * 2)
            key_name = strings[elf.u32(pair)]
            value = struct.unpack('<i', elf.read(pair + 4, 4))[0]
            keys.append((key_name, value))
        enums.append(Enum(name=name, alias=alias, is_flag=bool(flags & ENUM_IS_FLAG),
                          is_scoped=bool(flags & ENUM_IS_SCOPED), flags=flags, keys=keys))
    return enums


def scan(path: str, only: str | None = None) -> list[MetaClass]:
    elf = Elf.load(path)
    found, problems = [], []
    for symbol, (address, _size) in sorted(elf.symbols.items()):
        name = demangle_class(symbol)
        if not name or (only and not re.search(only, name)):
            continue
        try:
            found.append(read_meta_object(elf, address, name))
        except (ElfError, UnicodeDecodeError, struct.error) as error:
            problems.append(f'{name}: {error}')
    for problem in problems:
        print(f'  ! {problem}', file=sys.stderr)
    return sorted(found, key=lambda meta: meta.name)


def report(path: str, classes: list[MetaClass]) -> str:
    lines = [f'{path}', f'  {len(classes)} meta objects, '
             f'{sum(len(c.enums) for c in classes)} enums, '
             f'{sum(len(e.keys) for c in classes for e in c.enums)} keys']
    for meta in classes:
        if not meta.enums:
            continue
        parent = f' : {meta.superclass}' if meta.superclass else ''
        lines.append(f'\n  {meta.name}{parent}   (moc revision {meta.revision})')
        for enum in meta.enums:
            kind = 'flag' if enum.is_flag else 'enum'
            scoped = ' scoped' if enum.is_scoped else ''
            alias = f' (alias {enum.alias})' if enum.alias != enum.name else ''
            lines.append(f'    {kind}{scoped} {enum.name}{alias}')
            for key, value in enum.keys:
                lines.append(f'      {value:>11} = {key}')
    return '\n'.join(lines)


def main(argv: list[str]) -> int:
    paths = [argument for argument in argv if not argument.startswith('--')]
    as_json = '--json' in argv
    only = None
    if '--only' in argv:
        only = argv[argv.index('--only') + 1]
        paths = [path for path in paths if path != only]
    if not paths:
        print(__doc__)
        return 2
    everything = {}
    for path in paths:
        classes = scan(path, only)
        everything[path] = classes
        if not as_json:
            print(report(path, classes))
    if as_json:
        print(json.dumps({
            path: [{'class': meta.name, 'superclass': meta.superclass,
                    'revision': meta.revision,
                    'enums': [{'name': enum.name, 'alias': enum.alias,
                               'flag': enum.is_flag, 'scoped': enum.is_scoped,
                               'keys': dict(enum.keys)} for enum in meta.enums]}
                   for meta in classes]
            for path, classes in everything.items()}, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
