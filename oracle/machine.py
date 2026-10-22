"""Unicorn x86-32 无头机器：装载 th13.exe，桩掉 Win32 导入，支持按地址直调函数。

用法（RNG 探针等行为对照）：
    from oracle.machine import Machine
    m = Machine("games/th13/th13.exe")
    eax = m.call(0x0044b9e0, args=[0x12345678])
"""
import os
import struct

from unicorn import *
from unicorn.x86_const import *

from .pe import PE

PAGE = 0x1000

STACK_TOP = 0x00300000
STACK_SIZE = 0x00100000
HEAP_BASE = 0x01000000
# 128MB：原 64MB 在开局装载链（0x42b990 一串多 MB 对象，如 0x42e270
# 一次 0x79dd04≈7.9MB）下实测溢出（bump 越过 0x05000000 读崩——窗C
# 攻坚实证）。真机 Windows 堆远大于此，扩容即恢复语义。
HEAP_SIZE = 0x08000000
TEB_ADDR = 0x7FFDE000
STUB_BASE = 0xF0000000      # 每个导入一个 16 字节桩槽
RET_MAGIC = 0xF1000000      # call() 的哨兵返回地址
GDT_ADDR = 0xF2000000
CALLEE_SCRATCH = 0xF2800000  # 直调辅助代码洞（如 x87 ST0→EAX 转换壳）

# Win32 导入几乎全是 stdcall：桩模拟 ret 必须连同参数一并弹出，否则调用方
# 栈永久错位。按名给出参数字节数；未列出的按 cdecl（ret，不弹参）。
WIN32_STDCALL_ARGS = {
    "sleep": 4,
    "entercriticalsection": 4, "leavecriticalsection": 4,
    "initializecriticalsection": 4, "initializecriticalsectionandspincount": 8,
    "deletecriticalsection": 4, "tryentercriticalsection": 4,
    "tlsalloc": 0, "tlsfree": 4, "tlsgetvalue": 4, "tlssetvalue": 8,
    "flsalloc": 0, "flsfree": 4, "flsgetvalue": 4, "flssetvalue": 8,
    "getlasterror": 0, "setlasterror": 4,
    "getcurrentthreadid": 0, "getcurrentprocessid": 0,
    "getmodulehandlea": 4, "getmodulehandlew": 4,
    "getprocaddress": 8, "loadlibrarya": 4, "loadlibraryw": 4, "freelibrary": 4,
    "queryperformancecounter": 4, "queryperformancefrequency": 4,
    "gettickcount": 0, "timegettime": 0,
    "getsystemtime": 4, "getlocaltime": 4, "getsystemtimeasfiletime": 4,
    "heapalloc": 12, "heapfree": 12, "heapsize": 12, "heaprealloc": 16,
    "heapcreate": 12, "heapdestroy": 4, "heapsetinformation": 16,
    "createfilea": 28, "createfilew": 28, "readfile": 20, "writefile": 20,
    "closehandle": 4, "setfilepointer": 16, "getfilesize": 8, "getfileattributesa": 4,
    "findfirstfilea": 8, "findfirstfilew": 8, "findnextfilea": 8, "findnextfilew": 8,
    "findclose": 4,
    "waitforsingleobject": 8, "resumethread": 4, "suspendthread": 4,
    "createthread": 24, "terminatethread": 8, "exitthread": 4,
    "createeventa": 20, "createeventw": 20, "setevent": 4, "resetevent": 4,
    "exitprocess": 4, "terminateprocess": 8,
    "getstartupinfoa": 4, "getstartupinfow": 4,
    "getmodulefilenamea": 12, "getmodulefilenamew": 12,
    "multibytetowidechar": 24, "widechartomultibyte": 32,
    "lstrlena": 4, "lstrlenw": 4, "lstrcpya": 8, "lstrcmpia": 8, "lstrcmpi": 8,
    "encodepointer": 4, "decodepointer": 4,
    "outputdebugstringa": 4, "isdebuggerpresent": 0,
    "setpriorityclass": 8, "getdesktopwindow": 0,
    "interlockedincrement": 4, "interlockeddecrement": 4,
    "interlockedexchange": 8, "interlockedcompareexchange": 12,
    "globalalloc": 8, "globalfree": 4, "globallock": 4, "globalunlock": 4,
    "localalloc": 8, "localfree": 4,
    "virtualalloc": 16, "virtualfree": 12, "virtualprotect": 16, "virtualquery": 12,
    "getcurrentdirectorya": 8, "setcurrentdirectorya": 4,
    "getprivateprofileinta": 16, "getprivateprofilestringa": 24,
    "showcursor": 4, "getcursorpos": 4, "setcursorpos": 8,
    "getkeyboardstate": 4, "joygetposex": 8, "setkeyboardstate": 4,
    # 标题窗 boot 链（0x43a440/0x45ac90 等）触达的 GDI32/User32/COM stdcall
    # 字节数（缺省会 ret 不弹参 → 调用方栈逐次泄漏，0x45ac90 七连
    # CreateFontA 后 ret 到脏栈实测崩）。仅为默认桩 ret N 用的兜底。
    "cocreateinstance": 24, "coinitialize": 4, "couninitialize": 0,
    "comparestringw": 24,
    "createcompatibledc": 4, "createdibsection": 28, "createdirectorya": 12,
    "createfonta": 56, "createmutexa": 12, "createprocessa": 40,
    "createwindowexa": 48,
    "defwindowproca": 16, "deletedc": 4, "deletefilea": 4, "deleteobject": 4,
    "destroywindow": 4, "dialogboxparama": 24, "dispatchmessagea": 4,
    "drawtexta": 20, "enddialog": 8, "enumsystemlocalesa": 8,
    "fatalappexita": 12, "findresourcea": 12, "findwindowa": 8,
    "flushfilebuffers": 4, "freeresource": 4,
    "getacp": 0, "getcommandlinea": 0, "getconsolecp": 0, "getconsolemode": 8,
    "getconsoletitlea": 8, "getcpinfo": 8, "getcurrentprocess": 0,
    "getcurrentthread": 0, "getdlgitem": 8, "getenvironmentstringsw": 0,
    "getenvironmentvariablea": 16, "getexitcodeprocess": 8, "getfiletype": 4,
    "getlocaleinfoa": 16, "getlocaleinfow": 16, "getoemcp": 0,
    "getstdhandle": 4, "getstockobject": 4, "getstringtypew": 16,
    "getsystemmetrics": 4, "gettimezoneinformation": 4,
    "getuserdefaultlcid": 0, "getversionexa": 4, "getwindowrect": 8,
    "isdlgbuttonchecked": 8, "isprocessorfeaturepresent": 4,
    "isvalidcodepage": 4, "isvalidlocale": 4,
    "joygetdevcapsa": 12, "killtimer": 8, "lcmapstringw": 28,
    "loadcursora": 8, "loadresource": 8, "lockresource": 4,
    "movewindow": 24, "msgwaitformultipleobjects": 20,
    "peekmessagea": 20, "postthreadmessagea": 16, "raiseexception": 20,
    "registerclassa": 4, "rtlunwind": 16, "selectobject": 8,
    "sendmessagea": 16, "setbkmode": 8, "setconsolectrlhandler": 8,
    "setcursor": 4, "setenvironmentvariablea": 12, "setforegroundwindow": 4,
    "sethandlecount": 4, "setstdhandle": 8, "settextcolor": 8,
    "settimer": 16, "setunhandledexceptionfilter": 4, "setwindowlonga": 12,
    "setwindowpos": 28, "showwindow": 8, "sizeofresource": 8,
    "systemparametersinfoa": 16, "textouta": 20,
    "unhandledexceptionfilter": 4, "winnlsenableime": 8, "writeconsolew": 20,
    "messageboxw": 16, "messageboxa": 16,
    "widechartomultibyte": 32,
}


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
        self._overrides = {}
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
        self.uc.mem_map(CALLEE_SCRATCH, PAGE)
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
    def stub(self, name, fn, args_pop=0):
        """注册导入桩实现 fn(machine) -> eax；默认桩返回 0 并记录调用。
        args_pop：stdcall 参数字节数（模拟 ret N；cdecl 保持 0）。"""
        self._stub_impl[name.lower()] = (fn, args_pop)

    def override_call(self, address, fn, ret_pop=0):
        """内部地址覆写：EIP 到达 address 时以 fn(machine) 的返回值作为 EAX 并模拟 ret。
        ret_pop：stdcall 系列需要额外弹出的字节数（如 ___fls_getvalue@4 为 4）。
        用于绕开依赖完整 Win32 线程环境的 CRT 内部函数（如 _getptd）。"""
        self._overrides[address] = (fn, ret_pop)
        self.watch(address, self._on_override)

    def watch(self, address, fn):
        """被动观察 address：EIP 到达时回调 fn(machine)，不改动任何寄存器。"""
        watchers = self._watchers.setdefault(address, [])
        watchers.append(fn)
        if len(watchers) == 1:
            def cb(uc, addr, size, user_data):
                for f in list(watchers):
                    f(self)
            self.uc.hook_add(UC_HOOK_CODE, cb, begin=address, end=address)

    def _hook(self):
        self._watchers = {}
        # 哨兵停机（范围挂钩：仅在该地址触发，逐指令 Python 回调不出现在热路径）
        self.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, u: uc.emu_stop(),
                         begin=RET_MAGIC, end=RET_MAGIC)
        # 导入桩区间：一个范围挂钩盖住全部桩槽
        self.uc.hook_add(UC_HOOK_CODE, self._on_stub_range,
                         begin=STUB_BASE, end=self.next_stub_slot - 1)

    def _on_stub_range(self, uc, address, size, user_data):
        imp = self.import_stubs.get(address)
        if imp is None:
            return
        entry = self._stub_impl.get(imp.name.lower())
        if entry is not None:
            fn, args_pop = entry
            eax = fn(self)
            uc.reg_write(UC_X86_REG_EAX, eax & 0xFFFFFFFF)
        else:
            if self.log_stubs:
                count = self.stub_calls.setdefault(imp.name, 0)
                self.stub_calls[imp.name] = count + 1
            args_pop = WIN32_STDCALL_ARGS.get(imp.name.lower(), 0)
            uc.reg_write(UC_X86_REG_EAX, 0)
        # 模拟 ret / ret N：跳过桩体直接返回
        esp = uc.reg_read(UC_X86_REG_ESP)
        retaddr = struct.unpack("<I", uc.mem_read(esp, 4))[0]
        uc.reg_write(UC_X86_REG_ESP, esp + 4 + args_pop)
        uc.reg_write(UC_X86_REG_EIP, retaddr)

    def _on_override(self, machine):
        uc = machine.uc
        address = uc.reg_read(UC_X86_REG_EIP)
        entry = machine._overrides.get(address)
        if entry is None:
            return
        fn, ret_pop = entry
        eax = fn(machine)
        uc.reg_write(UC_X86_REG_EAX, eax & 0xFFFFFFFF)
        esp = uc.reg_read(UC_X86_REG_ESP)
        retaddr = struct.unpack("<I", uc.mem_read(esp, 4))[0]
        uc.reg_write(UC_X86_REG_ESP, esp + 4 + ret_pop)
        uc.reg_write(UC_X86_REG_EIP, retaddr)

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
    def call(self, address, args=(), regs=None, esp_extra=0x1000, timeout_ms=10_000,
             stop_at=None, probe_stack_top=None):
        """cdecl 直调目标函数，返回 EAX。

        栈布局：[esp]=RET_MAGIC、[esp+4..]=args（带栈参目标的正确 cdecl 形状；
        0 参目标与旧布局等价）。regs：额外寄存器初值（如 {'edi': ctl, 'eax': vm}），
        键为寄存器名。probe_stack_top：可选的探针栈基（默认 STACK_TOP-0x1000-esp_extra），
        嵌套直调时传更低的地址避免踩活栈帧。
        """
        uc = self.uc
        esp = (probe_stack_top or (STACK_TOP - 0x1000 - esp_extra)) - esp_extra
        esp &= ~0xF
        uc.mem_write(esp, struct.pack("<I", RET_MAGIC))
        for i, a in enumerate(args):
            uc.mem_write(esp + 4 + i * 4, struct.pack("<I", a & 0xFFFFFFFF))
        uc.reg_write(UC_X86_REG_ESP, esp)
        uc.reg_write(UC_X86_REG_EBP, STACK_TOP - 0x200)
        uc.reg_write(UC_X86_REG_EAX, 0)
        uc.reg_write(UC_X86_REG_ECX, 0)
        uc.reg_write(UC_X86_REG_EDX, 0)
        if regs:
            for name, value in regs.items():
                uc.reg_write(globals()[f"UC_X86_REG_{name.upper()}"], value & 0xFFFFFFFF)
        self._stop_at = stop_at
        try:
            uc.emu_start(address, RET_MAGIC, timeout=timeout_ms * 1000)
        except UcError as exc:
            if os.environ.get("TH13_UC_DEBUG"):
                import sys
                sys.stderr.write("emu_start(0x%08x) failed: %s EIP=0x%08x ESP=0x%08x\n"
                                 % (address, exc, uc.reg_read(UC_X86_REG_EIP),
                                    uc.reg_read(UC_X86_REG_ESP)))
            raise
        self._stop_at = None
        return uc.reg_read(UC_X86_REG_EAX)
