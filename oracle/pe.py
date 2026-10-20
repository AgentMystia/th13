"""极简 PE32 解析：节表、导入表。仅用于把 th13.exe 装载进 Unicorn。"""
import struct

IMAGE_BASE_DEFAULT = 0x400000


class Section:
    def __init__(self, name, va, vsize, raw_offset, raw_size, characteristics):
        self.name = name
        self.va = va
        self.vsize = vsize
        self.raw_offset = raw_offset
        self.raw_size = raw_size
        self.characteristics = characteristics

    def __repr__(self):
        return f"<Section {self.name!r} va=0x{self.va:08x} vsize=0x{self.vsize:x}>"


class Import:
    def __init__(self, dll, name, iat_va):
        self.dll = dll
        self.name = name
        self.iat_va = iat_va


class PE:
    def __init__(self, data):
        self.data = data
        if data[:2] != b"MZ":
            raise ValueError("not an MZ executable")
        pe_off = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe_off:pe_off + 4] != b"PE\x00\x00":
            raise ValueError("not a PE executable")
        coff = pe_off + 4
        machine, num_sections, timestamp, _, _, opt_size, _ = struct.unpack_from(
            "<HHIIIHH", data, coff)
        if machine != 0x14C:
            raise ValueError(f"not 32-bit x86 (machine=0x{machine:04x})")
        self.timestamp = timestamp
        opt = coff + 20
        magic = struct.unpack_from("<H", data, opt)[0]
        if magic != 0x10B:
            raise ValueError(f"not PE32 (magic=0x{magic:04x})")
        self.entry_rva = struct.unpack_from("<I", data, opt + 16)[0]
        self.image_base = struct.unpack_from("<I", data, opt + 28)[0] or IMAGE_BASE_DEFAULT
        self.size_of_image = struct.unpack_from("<I", data, opt + 56)[0]
        self.size_of_headers = struct.unpack_from("<I", data, opt + 60)[0]
        self.section_alignment = struct.unpack_from("<I", data, opt + 32)[0]
        self.file_alignment = struct.unpack_from("<I", data, opt + 36)[0]

        sec_off = opt + opt_size
        self.sections = []
        for i in range(num_sections):
            off = sec_off + i * 40
            name = data[off:off + 8].rstrip(b"\x00").decode("ascii", "replace")
            vsize, va, rsize, roff = struct.unpack_from("<IIII", data, off + 8)
            chars = struct.unpack_from("<I", data, off + 36)[0]
            self.sections.append(Section(name, va, vsize, roff, rsize, chars))

        self.imports = []
        self._parse_imports(opt)

    def rva_to_offset(self, rva):
        for s in self.sections:
            if s.va <= rva < s.va + max(s.vsize, s.raw_size):
                delta = rva - s.va
                if delta < s.raw_size:
                    return s.raw_offset + delta
                return None  # 虚拟填充区（BSS）
        if rva < self.size_of_headers:
            return rva
        return None

    def _parse_imports(self, opt):
        import_rva, = struct.unpack_from("<I", self.data, opt + 96 + 8)  # 第二数据目录=导入表
        if not import_rva:
            return
        off = self.rva_to_offset(import_rva)
        if off is None:
            return
        while True:
            ilt_rva, ts, fc, name_rva, iat_rva = struct.unpack_from("<IIIII", self.data, off)
            if ilt_rva == 0 and name_rva == 0:
                break
            dll_off = self.rva_to_offset(name_rva)
            dll = self.data[dll_off:self.data.index(b"\x00", dll_off)].decode("ascii", "replace") if dll_off else "?"
            thunk_rva = ilt_rva or iat_rva
            thunk_off = self.rva_to_offset(thunk_rva)
            idx = 0
            while True:
                entry, = struct.unpack_from("<I", self.data, thunk_off + idx * 4)
                if entry == 0:
                    break
                if entry & 0x80000000:
                    name = f"ordinal#{entry & 0xFFFF}"
                else:
                    hint_off = self.rva_to_offset(entry & 0x7FFFFFFF)
                    name = self.data[hint_off + 2:self.data.index(b"\x00", hint_off + 2)].decode("ascii", "replace")
                self.imports.append(Import(dll, name, iat_rva + idx * 4))
                idx += 1
            off += 20
