"""Unicorn x86-32 无头机器：装载 th13.exe，桩掉 Win32 导入，支持按地址直调函数。

用法（RNG 探针等行为对照）：
    from oracle.machine import Machine
    m = Machine("games/th13/th13.exe")
    eax = m.call(0x0044b9e0, args=[0x12345678])
"""
import struct

from unicorn import *
from unicorn.x86_const import *

from .pe import PE

PAGE = 0x1000

STACK_TOP = 0x00300000
STACK_SIZE = 0x00100000
HEAP_BASE = 0x01000000
HEAP_SIZE = 0x02000000
TEB_ADDR = 0x7FFDE000
STUB_BASE = 0xF0000000      # 每个导入一个 16 字节桩槽
RET_MAGIC = 0xF1000000      # call() 的哨兵返回地址
GDT_ADDR = 0xF2000000


def align_up(x, a=PAGE):
    return (x + a - 1) & ~(a - 1)


def make_gdt_entry(base, limit, access, flags):
    limit_field = (limit & 0xFFFF) | ((limit >> 12) & 0x0F) << 16
    entry = limit & 0xFFFF
    entry |= (base & 0xFFFFFF) << 16
    entry |= (access & 0xFF) << 40
    entry |= ((limit >> 16) & 0x0F) << 48
    entry |= (flags & 0x0F) << 52
    entry |= ((base >> 24) & 0xFF) << 56
    return struct.pack("<Q", entry)


class Machine:
    def __init__(self, exe_path, log_stubs=False):
        self.pe = PE(open(exe_path, "rb").read())
        self.uc = Uc(UC_ARCH_X86, UC_MODE_32)
        self.stub_calls = {}
        self.log_stubs = log_stubs
        self._stub_impl = {}
        self._load_image()
        self._map_runtime()
        self._setup_gdt_fs()
        self._hook()

    # ---------- 装载 ----------
    def _load_image(self):
        base = self.pe.image_base
        size = align_up(self.pe.size_of_image)
        self.uc.mem_map(base, size)
        self.uc.mem_write(base, self.pe.data[:self.pe.size_of_headers])
        for s in self.pe.sections:
            raw = self.pe.data[s.raw_offset:s.raw_offset + s.raw_size]
            self.uc.mem_write(base + s.va, raw)
        self.image_base = base
        self.image_size = size
        self.entry = base + self.pe.entry_rva

    def _map_runtime(self):
        self.uc.mem_map(STACK_TOP - STACK_SIZE, STACK_SIZE)
        self.uc.mem_map(HEAP_BASE, HEAP_SIZE)
        self.heap_ptr = HEAP_BASE
        self.uc.mem_map(TEB_ADDR & ~(PAGE - 1), PAGE * 2)
        # TEB: self 引用 + 常用字段桩
        self.uc.mem_write(TEB_ADDR, struct.pack("<I", TEB_ADDR))
        self.uc.mem_map(STUB_BASE, PAGE)
        self.uc.mem_map(RET_MAGIC & ~(PAGE - 1), PAGE)
        # 导入桩：IAT 指向桩槽，槽内放 ret
        self.import_stubs = {}
        for i, imp in enumerate(self.pe.imports):
            slot = STUB_BASE + i * 16
            self.uc.mem_write(slot, b"\xC3")
            self.uc.mem_write(self.image_base + imp.iat_va, struct.pack("<I", slot))
            self.import_stubs[slot] = imp
        self.next_stub_slot = STUB_BASE + (len(self.pe.imports) + 1) * 16

    def _setup_gdt_fs(self):
        uc = self.uc
        uc.mem_map(GDT_ADDR, PAGE)
        entries = b"\x00" * 8  # 空选择子
        entries += make_gdt_entry(0, 0xFFFFF, access=0x9A, flags=0xC)   # 0x08 code
        entries += make_gdt_entry(0, 0xFFFFF, access=0x92, flags=0xC)   # 0x10 data
        entries += make_gdt_entry(TEB_ADDR, 0xFFF, access=0x92, flags=0xC)  # 0x18 fs → TEB
        uc.mem_write(GDT_ADDR, entries)
        uc.reg_write(UC_X86_REG_GDTR, (0, GDT_ADDR, len(entries) - 1, 0))
        uc.reg_write(UC_X86_REG_CS, 0x08)
        uc.reg_write(UC_X86_REG_DS, 0x10)
        uc.reg_write(UC_X86_REG_ES, 0x10)
        uc.reg_write(UC_X86_REG_SS, 0x10)
        uc.reg_write(UC_X86_REG_FS, 0x18)

    # ---------- 桩实现 ----------
    def stub(self, name, fn):
        """注册导入桩实现 fn(machine, esp) -> eax；默认桩返回 0 并记录调用。"""
        self._stub_impl[name.lower()] = fn

    def _hook(self):
        def on_code(uc, address, size, user_data):
            if address == RET_MAGIC:
                uc.emu_stop()
                return
            imp = self.import_stubs.get(address)
            if imp is not None:
                impl = self._stub_impl.get(imp.name.lower())
                if impl is not None:
                    eax = impl(self, uc.reg_read(UC_X86_REG_ESP))
                    uc.reg_write(UC_X86_REG_EAX, eax & 0xFFFFFFFF)
                else:
                    if self.log_stubs:
                        count = self.stub_calls.setdefault(imp.name, 0)
                        self.stub_calls[imp.name] = count + 1
                    uc.reg_write(UC_X86_REG_EAX, 0)
                # 模拟 ret：跳过桩体直接返回
                esp = uc.reg_read(UC_X86_REG_ESP)
                retaddr = struct.unpack("<I", uc.mem_read(esp, 4))[0]
                uc.reg_write(UC_X86_REG_ESP, esp + 4)
                uc.reg_write(UC_X86_REG_EIP, retaddr)

        self.uc.hook_add(UC_HOOK_CODE, on_code)

    # ---------- 内存工具 ----------
    def malloc(self, size):
        ptr = self.heap_ptr
        self.heap_ptr = align_up(self.heap_ptr + size)
        return ptr

    def read_u32(self, addr):
        return struct.unpack("<I", self.uc.mem_read(addr, 4))[0]

    def write_u32(self, addr, value):
        self.uc.mem_write(addr, struct.pack("<I", value & 0xFFFFFFFF))

    def read_cstr(self, addr, max_len=256):
        out = b""
        while len(out) < max_len:
            chunk = bytes(self.uc.mem_read(addr + len(out), 16))
            if b"\x00" in chunk:
                out += chunk[:chunk.index(b"\x00")]
                break
            out += chunk
        return out.decode("ascii", "replace")

    # ---------- 直调 ----------
    def call(self, address, args=(), esp_extra=0x1000, timeout_ms=10_000, stop_at=None):
        """cdecl 直调目标函数，返回 EAX。stop_at: 提前停止地址（可选）。"""
        uc = self.uc
        esp = STACK_TOP - 0x1000 - esp_extra
        for i, a in enumerate(args):
            uc.mem_write(esp + i * 4, struct.pack("<I", a & 0xFFFFFFFF))
        uc.mem_write(esp + len(args) * 4, struct.pack("<I", RET_MAGIC))
        uc.reg_write(UC_X86_REG_ESP, esp)
        uc.reg_write(UC_X86_REG_EBP, STACK_TOP - 0x200)
        uc.reg_write(UC_X86_REG_EAX, 0)
        uc.reg_write(UC_X86_REG_ECX, 0)
        uc.reg_write(UC_X86_REG_EDX, 0)
        self._stop_at = stop_at
        uc.emu_start(address, RET_MAGIC, timeout=timeout_ms * 1000)
        self._stop_at = None
        return uc.reg_read(UC_X86_REG_EAX)
