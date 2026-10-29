"""Build real VBA projects for tests, without Office.

`vba_project_bin` writes a Microsoft Compound File (MS-CFB) that holds a VBA
project laid out as MS-OVBA describes: a `PROJECT` stream, a `VBA` storage with
`_VBA_PROJECT`, `dir` and one module stream. `olevba` parses it like any other
`vbaProject.bin`, so tests can exercise the real macro detection. The module
source is stored in uncompressed MS-OVBA chunks, which the format allows.
Nothing in this file is copied from a real document.
"""

import io
import struct
import zipfile

_END = 0xFFFFFFFE
_FREE = 0xFFFFFFFF
_FATSECT = 0xFFFFFFFD
_NONE = 0xFFFFFFFF
_SECTOR = 512
_MINI_SECTOR = 64
_MINI_CUTOFF = 4096
_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _record(record_id, payload=b""):
    return struct.pack("<HL", record_id, len(payload)) + payload


def _ovba_container(data):
    """MS-OVBA compressed container made of uncompressed 4096-byte chunks."""
    out = bytearray(b"\x01")
    for start in range(0, len(data), 4096):
        chunk = data[start : start + 4096].ljust(4096, b"\x00")
        out += struct.pack("<H", 0x3000 | (len(chunk) + 2 - 3)) + chunk
    return bytes(out)


def _dir_stream(module_name):
    name = module_name.encode("ascii")
    name_utf16 = module_name.encode("utf-16-le")
    project = (
        _record(0x0001, struct.pack("<L", 1))  # PROJECTSYSKIND: 32-bit Windows
        + _record(0x0002, struct.pack("<L", 0x409))
        + _record(0x0014, struct.pack("<L", 0x409))
        + _record(0x0003, struct.pack("<H", 1252))
        + _record(0x0004, b"VBAProject")
        + _record(0x0005)
        + _record(0x0040)
        + _record(0x0006)
        + _record(0x003D)
        + _record(0x0007, struct.pack("<L", 0))
        + _record(0x0008, struct.pack("<L", 0))
        + struct.pack("<HLLH", 0x0009, 4, 1, 0)
        + _record(0x000C)
        + _record(0x003C)
        + struct.pack("<HLH", 0x000F, 2, 1)  # PROJECTMODULES: one module
        + _record(0x0013, struct.pack("<H", 0xFFFF))
    )
    module = (
        _record(0x0019, name)
        + _record(0x0047, name_utf16)
        + _record(0x001A, name)
        + _record(0x0032, name_utf16)
        + _record(0x001C)
        + _record(0x0048)
        + _record(0x0031, struct.pack("<L", 0))  # source starts at offset 0
        + _record(0x001E, struct.pack("<L", 0))
        + _record(0x002C, struct.pack("<H", 0xFFFF))
        + _record(0x0021)  # procedural module
        + _record(0x002B)
    )
    return _ovba_container(project + module + _record(0x0010))


def _directory_entry(name, kind, left, right, child, start, size):
    encoded = name.encode("utf-16-le") + b"\x00\x00" if name else b""
    return struct.pack(
        "<64sHBBLLL16sL8s8sLLL",
        encoded,
        len(encoded),
        kind,
        1 if kind else 0,
        left,
        right,
        child,
        b"\x00" * 16,
        0,
        b"\x00" * 8,
        b"\x00" * 8,
        start,
        size,
        0,
    )


def _compound_file(entries):
    """Write entries as (name, kind, left, right, child, data) to an MS-CFB file.

    Entry 0 is the root. Streams under 4096 bytes go in the mini stream.
    """
    mini = bytearray()
    mini_fat = []
    starts = {}
    for index, (_, kind, _, _, _, data) in enumerate(entries):
        if kind == 2 and len(data) < _MINI_CUTOFF:
            first = len(mini) // _MINI_SECTOR
            count = -(-len(data) // _MINI_SECTOR)
            mini_fat += list(range(first + 1, first + count)) + [_END]
            mini += data.ljust(count * _MINI_SECTOR, b"\x00")
            starts[index] = first

    fat = [_FATSECT]  # sector 0 holds the single FAT sector

    def allocate(byte_count):
        first = len(fat)
        count = max(1, -(-byte_count // _SECTOR))
        fat.extend(list(range(first + 1, first + count)) + [_END])
        return first

    mini_fat_sector = allocate(len(mini_fat) * 4) if mini_fat else _END
    dir_sector = allocate(len(entries) * 128)
    mini_stream_sector = allocate(len(mini)) if mini else _END
    big_streams = {}
    for index, (_, kind, _, _, _, data) in enumerate(entries):
        if kind == 2 and len(data) >= _MINI_CUTOFF:
            starts[index] = big_streams[index] = allocate(len(data))

    body = bytearray(len(fat) * _SECTOR)
    fat_bytes = struct.pack("<128L", *(fat + [_FREE] * (128 - len(fat))))
    body[0:_SECTOR] = fat_bytes

    def put(sector, data):
        body[sector * _SECTOR : sector * _SECTOR + len(data)] = data

    if mini_fat:
        put(mini_fat_sector, struct.pack(f"<{len(mini_fat)}L", *mini_fat))
    directory = b""
    for index, (name, kind, left, right, child, data) in enumerate(entries):
        if kind == 5:
            directory += _directory_entry(
                name, kind, left, right, child, mini_stream_sector, len(mini)
            )
        elif kind == 2:
            directory += _directory_entry(
                name, kind, left, right, child, starts[index], len(data)
            )
        else:
            directory += _directory_entry(name, kind, left, right, child, 0, 0)
    while len(directory) % _SECTOR:
        directory += _directory_entry("", 0, _NONE, _NONE, _NONE, 0, 0)
    put(dir_sector, directory)
    if mini:
        put(mini_stream_sector, bytes(mini))
    for index, sector in big_streams.items():
        put(sector, entries[index][5])

    header = struct.pack(
        "<8s16sHHHHH6sLLLLLLLLL",
        _MAGIC,
        b"\x00" * 16,
        0x003E,
        3,
        0xFFFE,
        9,
        6,
        b"\x00" * 6,
        0,
        1,
        dir_sector,
        0,
        _MINI_CUTOFF,
        mini_fat_sector,
        -(-len(mini_fat) * 4 // _SECTOR),
        _END,
        0,
    ) + struct.pack("<109L", 0, *([_FREE] * 108))
    return header + bytes(body)


def vba_project_bin(source, module_name="Module1"):
    """A vbaProject.bin whose single module holds the given VBA source."""
    code = f'Attribute VB_Name = "{module_name}"\r\n{source}\r\n'.encode("cp1252")
    project = (
        b'ID="{00000000-0000-0000-0000-000000000000}"\r\n'
        + f"Module={module_name}\r\n".encode("ascii")
        + b'Name="VBAProject"\r\n'
    )
    none = _NONE
    return _compound_file(
        [
            ("Root Entry", 5, none, none, 1, b""),
            ("VBA", 1, none, 5, 2, b""),
            ("_VBA_PROJECT", 2, none, 3, none, b"\xcc\x61\xff\xff\x00\x00\x00"),
            ("dir", 2, none, 4, none, _dir_stream(module_name)),
            (module_name, 2, none, none, none, _ovba_container(code)),
            ("PROJECT", 2, none, none, none, project),
        ]
    )


def ooxml_with_vba(source, body_text="ordinary text", prefix="word", extra_parts=None):
    """A minimal OOXML package that carries a real vbaProject.bin."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
            'package/2006/content-types"></Types>',
        )
        zf.writestr(
            f"{prefix}/document.xml",
            '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats'
            f'.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{body_text}'
            "</w:t></w:r></w:p></w:body></w:document>",
        )
        zf.writestr(f"{prefix}/vbaProject.bin", vba_project_bin(source))
        for name, data in (extra_parts or {}).items():
            zf.writestr(name, data)
    return buffer.getvalue()
