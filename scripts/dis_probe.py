"""反汇编探针：从 games/th13/th13.exe 按 VA 反汇编区间（capstone，容器内跑）。

用法：python3 scripts/dis_probe.py <start_va> <end_va>
"""
import struct
import sys

from capstone import Cs, CS_ARCH_X86, CS_MODE_32

DATA = open("games/th13/th13.exe", "rb").read()
PE_OFF = struct.unpack_from("<I", DATA, 0x3C)[0]
NSEC = struct.unpack_from("<H", DATA, PE_OFF + 6)[0]
OPT = struct.unpack_from("<H", DATA, PE_OFF + 20)[0]
IMGBASE = struct.unpack_from("<I", DATA, PE_OFF + 24 + 28)[0]
SECTIONS = []
for i in range(NSEC):
    s = PE_OFF + 24 + OPT + i * 40
    name = DATA[s:s + 8].rstrip(b"\0").decode()
    vsz, va, rsz, ro = struct.unpack_from("<IIII", DATA, s + 8)
    SECTIONS.append((name, va, vsz, ro, rsz))


def va_to_offset(va):
    rva = va - IMGBASE
    for _, sva, vsz, ro, rsz in SECTIONS:
        if sva <= rva < sva + max(vsz, rsz):
            return ro + (rva - sva)
    return None


MD = Cs(CS_ARCH_X86, CS_MODE_32)


def dis(start, end):
    off = va_to_offset(start)
    if off is None:
        print("va not in any section")
        return
    for ins in MD.disasm(DATA[off:off + (end - start)], start):
        print("%08x  %-8s %s" % (ins.address, ins.mnemonic, ins.op_str))


if __name__ == "__main__":
    dis(int(sys.argv[1], 16), int(sys.argv[2], 16))
