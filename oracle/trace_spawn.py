"""装载期/逐帧 ANM VM 池插入追踪（render_oracle 的诊断子类，不改基类）。

用法（容器内 /work）：python3 -m oracle.trace_spawn [帧数=30]
输出到 stderr：每行 phase tag + 池插入（0x46f4a0 主链 / 0x46f5f0 副链）的
VM 脚本号（+0x4aa）/层（+0x24）/精灵（+0x34）/位置（+0x44/48），以及
插入点回扫栈上返回地址映射出的调用链（functions.json 符号化）。
"""
import json
import os
import struct
import sys

from unicorn.x86_const import (UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_EBP,
                               UC_X86_REG_ECX, UC_X86_REG_EDI, UC_X86_REG_EDX,
                               UC_X86_REG_ESI, UC_X86_REG_ESP)

from .render_oracle import RenderOracle

INSERT_MAIN = 0x0046F4A0   # 主池插入（EDI=VM）
INSERT_FORE = 0x0046F5F0   # 副池插入
BIND_SCRIPT = 0x00461BF0   # 绑定（EAX=VM、EBX=脚本号、EDI=ANM file）
SPAWN_CHILD = 0x0046F990   # EAX=mgr、ESI=父 VM
SPAWN_EFFECT = 0x0046F820  # EAX=mgr、EDI=模板?（load 尾部路径）

ADDR_TAGS = {
    0x0046E510: "load_tick", 0x0046F170: "step_main", 0x0046F240: "step_fore",
    0x0040D920: "bullet_mgr", 0x0046DF40: "anm_open", 0x00418990: "mgr_create",
}


class SpawnTracer(RenderOracle):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.tag = "init"
        names = {}
        path = "artifacts/decomp/jp/functions.json"
        if os.path.isfile(path):
            for fn in json.load(open(path))["functions"]:
                names[int(fn["address"], 16)] = fn["name"]
        self.func_names = names

    def drive(self, address, args=(), regs=None, timeout_ms=120_000):
        self.tag = ADDR_TAGS.get(address, "drive_%08x" % address)
        return super().drive(address, args, regs, timeout_ms)

    def call_chain(self, machine, depth=24):
        """从 ESP 向上扫 depth 个 dword，落在代码段的即调用链返回地址。"""
        esp = machine.uc.reg_read(UC_X86_REG_ESP)
        chain = []
        raw = bytes(self.uc.mem_read(esp, 4 * depth))
        for i in range(depth):
            value = struct.unpack_from("<I", raw, i * 4)[0]
            if 0x401000 <= value < 0x480000:
                chain.append(self.func_names.get(value, "sub_%08x" % value))
            if len(chain) >= 6:
                break
        return "<-" .join(chain)

    def f32at(self, addr):
        return struct.unpack("<f", bytes(self.uc.mem_read(addr, 4)))[0]

    def dump_vm_inputs(self, vm):
        """dump 世界矩阵组合输入（0x4692d0 族读的字段）。"""
        import sys
        log = sys.stderr.write
        raw = bytes(self.uc.mem_read(vm, 0x90))
        dwords = struct.unpack("<36I", raw)
        floats = struct.unpack("<36f", raw)
        more = bytes(self.uc.mem_read(vm + 0x528, 0x24))
        td = struct.unpack("<9I", more)
        base = bytes(self.uc.mem_read(vm + 0x3e0, 64))
        bd = struct.unpack("<16I", base)
        bf = struct.unpack("<16f", base)
        log("   vm=%08x flags18=%08x flags1c=%08x sprite34=%d\n" % (
            vm, dwords[6], dwords[7], dwords[13]))
        log("   pos3c=%g,%g,%g rot48=%08x,%08x,%08x (%g,%g,%g)\n" % (
            floats[15], floats[16], floats[17],
            dwords[18], dwords[19], dwords[20],
            floats[18], floats[19], floats[20]))
        log("   scale60/64=%g,%g w80/h84=%08x,%08x uv78/7c=%08x,%08x color528=%08x\n" % (
            floats[24], floats[25],
            dwords[0x80 // 4], dwords[0x84 // 4],
            dwords[30], dwords[31], td[0]))
        log("   trio538=%08x,%08x,%08x p544=%08x f548=%08x\n" % (
            td[4], td[5], td[6], td[7], td[8]))
        log("   base3e0=%s\n" % ",".join("%08x" % v for v in bd))
        rec = self.rd32(vm + 0x2c)
        if rec:
            rraw = bytes(self.uc.mem_read(rec, 0x48))
            rd_ = struct.unpack("<18I", rraw)
            rf = struct.unpack("<18f", rraw)
            log("   spriteRec=%08x w=%08x(%g) h=%08x(%g) su3c=%08x sv40=%08x\n" % (
                rec, rd_[0xe], rf[0xe], rd_[0xd], rf[0xd], rd_[0xf], rd_[0x10]))
        texbase = bytes(self.uc.mem_read(vm + 0x460, 64))
        log("   texbase460=%s\n" % ",".join(
            "%08x" % v for v in struct.unpack("<16I", texbase)))

    def vm_desc(self, vm):
        script = struct.unpack(
            "<H", bytes(self.uc.mem_read(vm + 0x4AA, 2)))[0]
        layer = self.rd32(vm + 0x24)
        sprite = self.rd32(vm + 0x34)
        x, y = self.f32at(vm + 0x44), self.f32at(vm + 0x48)
        return ("script=%d layer=%d sprite=%d pos=(%.2f,%.2f)"
                % (script, layer, sprite, x, y))

    def install_hooks(self):
        super().install_hooks()
        log = sys.stderr.write

        # ---- rand() 消费计数：按 (frame, tag) 聚合，装载期 tag 细分 ----
        self.rand_counts = {}

        def on_rand(machine):
            key = (self.frame, self.tag)
            self.rand_counts[key] = self.rand_counts.get(key, 0) + 1
        self.m.watch(0x0047BBA2, on_rand)

        # ---- ANM RNG（0x472c90/0x472c20）：状态位置 + 调用计数 ----
        self.anm_rng_calls = 0
        self.anm_rng_per_frame = {}

        def on_anm_rng32(machine):
            self.anm_rng_calls += 1
            key = self.frame
            self.anm_rng_per_frame[key] = \
                self.anm_rng_per_frame.get(key, 0) + 1
            if self.anm_rng_calls <= 4:
                esi = machine.uc.reg_read(UC_X86_REG_ESI)
                state = struct.unpack(
                    "<4H", self.uc.mem_read(esi, 8))
                extra = ""
                if self.anm_rng_calls <= 2:
                    edi = machine.uc.reg_read(UC_X86_REG_EDI)
                    ebx = machine.uc.reg_read(UC_X86_REG_EBX)
                    extra = " (edi=%08x ebx=%08x)" % (edi, ebx)
                    if (0x00200000 <= edi < 0x06000000
                            and 0x00800000 <= ebx < 0x06000000):
                        ins = struct.unpack("<hHhH", self.uc.mem_read(edi, 8))
                        script = struct.unpack(
                            "<H", self.uc.mem_read(ebx + 0x4AA, 2))[0]
                        extra += (" vm script=%d timer53c=%d ins(op=%d "
                                  "len=%d time=%d mask=%04x)" % (
                                      script,
                                      struct.unpack("<i", self.uc.mem_read(
                                          ebx + 0x53C, 4))[0],
                                      ins[0], ins[1], ins[2], ins[3]))
                log("[f%d %s] anm_rng32#%d esi=%08x state=%04x,%04x "
                    "counter=%d via %s%s\n" % (
                        self.frame, self.tag, self.anm_rng_calls, esi,
                        state[0], state[1],
                        self.rd32(esi + 4),
                        self.call_chain(machine, 16), extra))
        self.m.watch(0x00472C90, on_anm_rng32)

        def on_anm_rng16(machine):
            esi = machine.uc.reg_read(UC_X86_REG_ESI)
            if not hasattr(self, "_rng16_esi"):
                self._rng16_esi = esi
                log("[f%d %s] anm_rng16 esi=%08x state=%04x\n" % (
                    self.frame, self.tag, esi,
                    struct.unpack("<H", self.uc.mem_read(esi, 2))[0]))
        self.m.watch(0x00472C20, on_anm_rng16)

        def on_insert_main(machine):
            import unicorn as _u
            vm = machine.uc.reg_read(UC_X86_REG_EDI)
            log("[f%d %s] MAIN+ %s  via %s\n" % (
                self.frame, self.tag, self.vm_desc(vm),
                self.call_chain(machine)))
            if self.frame in (61, 62) and not getattr(self, "_e61", False):
                script = struct.unpack(
                    "<H", self.uc.mem_read(vm + 0x4AA, 2))[0]
                if script == 5:
                    self._e61 = True
                    parent = self.rd32(vm + 0x570)
                    log("    [enemy61 vm=%08x] +0x570(parent)=%08x "
                        "ofs574/578/57c=%g,%g,%g flags18=%08x\n" % (
                            vm, parent,
                            self.f32at(vm + 0x574), self.f32at(vm + 0x578),
                            self.f32at(vm + 0x57C),
                            self.rd32(vm + 0x18)))
                    if parent:
                        log("      parent p3c/40/44=%g,%g,%g flags1c=%08x "
                            "p570=%08x\n" % (
                                self.f32at(parent + 0x3C),
                                self.f32at(parent + 0x40),
                                self.f32at(parent + 0x44),
                                self.rd32(parent + 0x1C),
                                self.rd32(parent + 0x570)))
            if self.frame == 0 and not getattr(self, "_sprite_watch", False):
                self._sprite_watch = True
                # 回看模板状态：file+0x10c + script*0x5a4
                script = struct.unpack(
                    "<H", self.uc.mem_read(vm + 0x4AA, 2))[0]
                file_ptr = self.rd32(vm + 0x30)
                template = self.rd32(file_ptr + 0x10C) + script * 0x5A4
                log("    [template%d=%08x] +0x34=%08x +0x80=%08x +0x84=%08x "
                    "base3e0[0]=%08x\n" % (
                        script, template,
                        self.rd32(template + 0x34),
                        self.rd32(template + 0x80),
                        self.rd32(template + 0x84),
                        self.rd32(template + 0x3E0)))
                watch_self = self

                def on_write(uc, access, address, size, value, user):
                    eip = uc.reg_read(_u.x86_const.UC_X86_REG_EIP)
                    sys.stderr.write(
                        "    [write vm+%x] = %08x @ eip=%08x(%s)\n" % (
                            address - vm, value, eip,
                            watch_self.func_names.get(eip, "?")))
                machine.uc.hook_add(_u.UC_HOOK_MEM_WRITE, on_write,
                                    begin=vm + 0x30, end=vm + 0x40)

        def on_insert_fore(machine):
            vm = machine.uc.reg_read(UC_X86_REG_EDI)
            log("[f%d %s] FORE+ %s  via %s\n" % (
                self.frame, self.tag, self.vm_desc(vm),
                self.call_chain(machine)))

        def on_bind(machine):
            vm = machine.uc.reg_read(UC_X86_REG_EAX)
            script = machine.uc.reg_read(UC_X86_REG_EBX) & 0xFFFF
            log("[f%d %s] bind %s -> script=%d\n" % (
                self.frame, self.tag, "%08x" % vm, script))

        def on_spawn_child(machine):
            esi = machine.uc.reg_read(UC_X86_REG_ESI)
            log("[f%d %s] spawn_child parent=%08x %s\n" % (
                self.frame, self.tag, esi,
                self.vm_desc(esi) if esi else "?"))

        self.m.watch(INSERT_MAIN, on_insert_main)
        self.m.watch(INSERT_FORE, on_insert_fore)
        self.m.watch(BIND_SCRIPT, on_bind)
        self.m.watch(SPAWN_CHILD, on_spawn_child)

        def on_quad_submit(machine):
            # 0x4692d0 入口：dump 全部寄存器（帧 0 只 dump 前 2 次）
            if self.frame > 0 or self._quad_dumps >= 2:
                return
            self._quad_dumps += 1
            regs = {}
            for name in ("eax", "ebx", "ecx", "edx", "esi", "edi"):
                regs[name] = machine.uc.reg_read(getattr(
                    __import__("unicorn.x86_const", fromlist=["x"]),
                    "UC_X86_REG_" + name.upper()))
            log("[f0] 0x4692d0 regs=%s\n" % " ".join(
                "%s=%08x" % kv for kv in regs.items()))
            for name, val in regs.items():
                if 0x00800000 <= val < 0x06000000:
                    log("   -- candidate via %s:\n" % name)
                    self.dump_vm_inputs(val)
            esp = machine.uc.reg_read(UC_X86_REG_ESP)
            args = struct.unpack("<6I", self.uc.mem_read(esp + 4, 24))
            log("   stack args=%s\n" % " ".join("%08x" % a for a in args))
            for a in args:
                if 0x00800000 <= a < 0x06000000:
                    log("   -- candidate via stack:\n")
                    self.dump_vm_inputs(a)
        self.m.watch(0x004692D0, on_quad_submit)

        def on_sprite_select(machine):
            # 0x461a60(ins_300)：EAX=VM；精灵号/file 在寄存器或栈（标注存疑双记）
            if self.frame > 0:
                return
            vm = machine.uc.reg_read(UC_X86_REG_EAX)
            esp = machine.uc.reg_read(UC_X86_REG_ESP)
            stack = struct.unpack("<3I", self.uc.mem_read(esp + 4, 12))
            ret = struct.unpack("<I", self.uc.mem_read(esp, 4))[0]
            file = machine.uc.reg_read(UC_X86_REG_EDX)
            slot = struct.unpack("<H", self.uc.mem_read(file, 2))[0]
            if slot == 8:
                log("[f0] eff ins_300 vm=%08x script4aa=%d ecx=%d\n" % (
                    vm,
                    struct.unpack("<H", self.uc.mem_read(vm + 0x4AA, 2))[0],
                    machine.uc.reg_read(UC_X86_REG_ECX)))
        self.m.watch(0x00461A60, on_sprite_select)

        def on_record_expand(machine):
            # 0x46e770：EAX=记录号、EBX=源 def（scratch ebp-0x8c）
            idx = machine.uc.reg_read(UC_X86_REG_EAX)
            if idx != 30 or self._rec_dumps >= 4:
                return
            self._rec_dumps += 1
            src = machine.uc.reg_read(UC_X86_REG_EBX)
            ebp = machine.uc.reg_read(UC_X86_REG_EBP)
            entry = struct.unpack(
                "<I", self.uc.mem_read(ebp - 0x38, 4))[0]
            kx, ky = struct.unpack("<2f", self.uc.mem_read(ebp - 0x50, 8))
            hdr = struct.unpack("<8H", self.uc.mem_read(entry, 16)) if entry else [0]*8
            log("[f0] rec30 def=%08x entry=%08x(ver=%d ns=%d nc=%d +%x w=%d h=%d) "
                "kx=%g ky=%g scratch3c/40=%08x,%08x\n" % (
                    src, entry, hdr[0], hdr[1], hdr[2], hdr[5], hdr[6], hdr[7],
                    kx, ky,
                    struct.unpack("<I", self.uc.mem_read(src + 0x3C, 4))[0],
                    struct.unpack("<I", self.uc.mem_read(src + 0x40, 4))[0]))
        self._rec_dumps = 0
        self.m.watch(0x0046E770, on_record_expand)

        def on_uv_div(machine):
            # 0x46e419 fdivp（X 路）：[ebp-0x10]=被除、[ebp-0x2c]=除数
            ebp = machine.uc.reg_read(UC_X86_REG_EBP)
            if abs(self._div_log) >= 8:
                return
            self._div_log -= 1
            desc = bytes(self.uc.mem_read(ebp - 0x28, 0x24))
            dd = struct.unpack("<9I", desc)
            ebx = machine.uc.reg_read(UC_X86_REG_EBX)
            log("[f0] uvdiv Xnum=%d Ynum=%d den=%d entry+14=%d desc=%s\n" % (
                struct.unpack("<i", self.uc.mem_read(ebp - 0x10, 4))[0],
                struct.unpack("<i", self.uc.mem_read(ebp - 0xC, 4))[0],
                struct.unpack("<i", self.uc.mem_read(ebp - 0x2C, 4))[0],
                struct.unpack("<h", self.uc.mem_read(ebx + 0x14, 2))[0],
                ["%08x" % v for v in dd]))
        self._div_log = 0
        self.m.watch(0x0046E419, on_uv_div)

        for ins_addr, ins_name in ((0x0046F3F0, "f3f0"), (0x0046F540, "f540"),
                                   (0x0046F5F0, "f5f0")):
            def make_insert(addr, nm):
                def on_alt_insert(machine):
                    vm = machine.uc.reg_read(UC_X86_REG_EDI)
                    if not (0x00800000 <= vm < 0x06000000):
                        return
                    if 60 <= self.frame <= 64:
                        log("[f%d %s] %s+ script=%d layer=%d via %s\n" % (
                            self.frame, self.tag, nm,
                            struct.unpack("<H", self.uc.mem_read(vm + 0x4AA, 2))[0],
                            self.rd32(vm + 0x24),
                            self.call_chain(machine, 12)))
                return on_alt_insert
            self.m.watch(ins_addr, make_insert(ins_addr, ins_name))

        def on_create(machine):
            if self.frame > 64:
                return
            eax = machine.uc.reg_read(UC_X86_REG_EAX)
            esp = machine.uc.reg_read(UC_X86_REG_ESP)
            ret = struct.unpack("<I", self.uc.mem_read(esp, 4))[0]
            self._creates = getattr(self, "_creates", 0) + 1
            log("[f%d %s] 0x4243e0#%d script=%d file=%08x via %s\n" % (
                self.frame, self.tag, self._creates, eax,
                struct.unpack("<I", self.uc.mem_read(esp + 4, 4))[0],
                self.func_names.get(ret, "?")))
        self.m.watch(0x004243E0, on_create)

        gld_slot = self.rd32(self.d3d.tex_vtable + 0x44)

        def on_gld(machine):
            if self._gld_log >= 3:
                return
            self._gld_log += 1
            # override 已弹栈（ret+3 参 = 16）：真实 this/level/pdesc 在 esp-12/-8/-4
            esp = machine.uc.reg_read(UC_X86_REG_ESP)
            this = struct.unpack("<I", self.uc.mem_read(esp - 12, 4))[0]
            level = struct.unpack("<I", self.uc.mem_read(esp - 8, 4))[0]
            pdesc = struct.unpack("<I", self.uc.mem_read(esp - 4, 4))[0]
            desc = struct.unpack("<9I", self.uc.mem_read(pdesc, 36))
            log("[f0] GetLevelDesc this=%08x(in_textures=%s) pdesc=%08x "
                "after_write=%s\n" % (
                    this, this in self.d3d.textures, pdesc,
                ["%08x" % v for v in desc]))
        self._gld_log = 0
        self.m.watch(gld_slot, on_gld)

        self._spawn_count = 0
        self._attach_count = 0
        self._up_count = 0

        def on_up_flush(machine):
            if not (91 <= self.frame <= 92):
                return
            esp = machine.uc.reg_read(UC_X86_REG_ESP)
            self._flush_seq = getattr(self, "_flush_seq", 0) + 1
            log("[f%d] 0x4679a0 flush #%d\n" % (self.frame, self._flush_seq))
        self.m.watch(0x004679A0, on_up_flush)

        # DrawPrimitiveUP COM 槽：打参数（栈已弹：ret@esp-20，args 在 esp-16..）
        def on_dpup(machine):
            if not (91 <= self.frame <= 92):
                return
            esp = machine.uc.reg_read(UC_X86_REG_ESP)
            args = struct.unpack("<4I", self.uc.mem_read(esp - 16, 16))
            log("[f%d] DrawPrimitiveUP prim=%d count=%d stride=%d\n" % (
                self.frame, args[1], args[2], args[3]))
        self.m.watch(self.d3d.dev_slots[0x14C], on_dpup)

        def on_spawn_call(machine):
            self._spawn_count += 1
        self.m.watch(0x004183F0, on_spawn_call)

        def on_attach(machine):
            self._attach_count += 1
            if 60 <= self.frame <= 64:
                esi = machine.uc.reg_read(UC_X86_REG_ESI)
                esp = machine.uc.reg_read(UC_X86_REG_ESP)
                ret = struct.unpack("<I", self.uc.mem_read(esp, 4))[0]
                log("[f%d %s] 0x461cb0 vm=%08x script=%d via %s\n" % (
                    self.frame, self.tag, esi,
                    struct.unpack("<i", self.uc.mem_read(esp + 4, 4))[0],
                    self.func_names.get(ret, "?")))
        self.m.watch(0x00461CB0, on_attach)

        orig_up = self.d3d._dev_draw_primitive_up
        ro_self = self

        def counted_up(mm):
            ro_self._up_count += 1
            return orig_up(mm)
        self.d3d.stats["ups"] = 0
        # DrawPrimitiveUP 走 dev vtable 槽：直接 watch 计数
        self.m.watch(self.d3d.dev_slots[0x14C],
                     lambda machine: setattr(self, "_up_count",
                                             self._up_count + 1))

        def report_at_end():
            pass
        self._report_at_end = report_at_end

        def on_reattach(machine):
            # 0x419764：翻转重挂读脚本基（EDI=敌机）——f92 附近 dump 全字段
            edi = machine.uc.reg_read(UC_X86_REG_EDI)
            if 0x00800000 <= edi < 0x06000000 and 61 <= self.frame <= 95:
                log("[f%d] reattach enemy=%08x +278=%d +26c=%d +270=%d "
                    "+78=%.4g +120=%08x\n" % (
                    self.frame, edi, self.rd32(edi + 0x278),
                    self.rd32(edi + 0x26C), self.rd32(edi + 0x270),
                    self.f32at(edi + 0x78), self.rd32(edi + 0x120)))
        self.m.watch(0x00419764, on_reattach)

        def on_mgr_tick(machine):
            # 0x41a8a0 敌机逐拍：dump +0x278/+0x270
            eax = machine.uc.reg_read(UC_X86_REG_EAX)
            if not (0x00800000 <= eax < 0x06000000):
                return
            if 91 <= self.frame <= 93 and getattr(self, "_mgr278", 0) < 8:
                self._mgr278 = getattr(self, "_mgr278", 0) + 1
                log("[f%d] enemy=%08x +278=%d +270=%d +78=%g\n" % (
                    self.frame, eax, self.rd32(eax + 0x278),
                    self.rd32(eax + 0x270), self.f32at(eax + 0x78)))
            # 敌机基址 = EAX；只看 GirlRedA01（rout 名在敌机+0x40? 用脚本名试探：
            # 直接按帧窗口 + 敌机 alive 段（0x11EC 内嵌 VM=034ecf08 ⟹ 基址 034ebd1c）
            cb = self.rd32(eax + 0x2C4)
            if 61 <= self.frame <= 64 and self._mgr_log < 6:
                self._mgr_log += 1
                log("[f%d] 0x41a8a0 enemy=%08x +2c4=%08x(%s) +40e8=%08x\n" % (
                    self.frame, eax, cb,
                    self.func_names.get(cb, "?"), self.rd32(eax + 0x40E8)))
        self._mgr_log = 0
        self.m.watch(0x0041A8A0, on_mgr_tick)

        def on_dispatch(machine):
            # 0x46a700 逐精灵分发：L8 时 dump 判定输入
            if self.d3d.cur_layer != 8:
                return
            if 92 <= self.frame <= 95:
                vm = machine.uc.reg_read(UC_X86_REG_ESI)
                if 0x00800000 <= vm < 0x06000000:
                    pos574 = self.f32at(vm + 0x574)
                    log("[f%d L8 disp] vm=%08x t=%d sprite=%d s4aa=%d "
                        "vis52b=%d flags18=%08x p574=%.2f\n" % (
                        self.frame, vm,
                        struct.unpack("<i", self.uc.mem_read(vm + 0x53C, 4))[0],
                        struct.unpack("<H", self.uc.mem_read(vm + 0x34, 2))[0],
                        struct.unpack("<H", self.uc.mem_read(vm + 0x4AA, 2))[0],
                        self.uc.mem_read(vm + 0x52B, 1)[0],
                        self.rd32(vm + 0x18), pos574))
            vm = machine.uc.reg_read(UC_X86_REG_ESI)
            if 0x00800000 <= vm < 0x06000000 and 61 <= self.frame <= 70:
                file_ptr = self.rd32(vm + 0x30)
                data_base = self.rd32(file_ptr + 0x108) if file_ptr else 0
                log("[f%d L8] vm=%08x t=%d sprite=%d s4aa=%d anmid=%d "
                    "pc_off=%x file=%08x\n" % (
                    self.frame, vm,
                    struct.unpack("<i", self.uc.mem_read(vm + 0x53C, 4))[0],
                    struct.unpack("<H", self.uc.mem_read(vm + 0x34, 2))[0],
                    struct.unpack("<H", self.uc.mem_read(vm + 0x4AA, 2))[0],
                    struct.unpack("<H", self.uc.mem_read(vm + 0x36, 2))[0],
                    self.rd32(vm + 0x28) - data_base if data_base else 0,
                    file_ptr))
            if self._disp_log >= 6:
                return
            self._disp_log += 1
            regs = {}
            for name in ("eax", "ebx", "ecx", "edx", "esi", "edi"):
                regs[name] = machine.uc.reg_read(getattr(
                    __import__("unicorn.x86_const", fromlist=["x"]),
                    "UC_X86_REG_" + name.upper()))
            cand = [v for v in regs.values() if 0x00800000 <= v < 0x06000000]
            log("[f%d L8 dispatch] regs=%s\n" % (
                self.frame, " ".join("%s=%08x" % kv for kv in regs.items())))
            if cand:
                vm = cand[0]
                log("   vm=%08x flags1c=%08x anchor90/94=%g,%g "
                    "ofs4e0/4e4/4e8=%g,%g,%g p3c/40/44=%g,%g,%g "
                    "dims80/84=%g,%g scale60/64=%g,%g\n" % (
                        vm, self.rd32(vm + 0x1C),
                        self.f32at(vm + 0x90), self.f32at(vm + 0x94),
                        self.f32at(vm + 0x4E0), self.f32at(vm + 0x4E4),
                        self.f32at(vm + 0x4E8),
                        self.f32at(vm + 0x3C), self.f32at(vm + 0x40),
                        self.f32at(vm + 0x44),
                        self.f32at(vm + 0x80), self.f32at(vm + 0x84),
                        self.f32at(vm + 0x60), self.f32at(vm + 0x64)))
            for v in cand[:2]:
                f18 = self.rd32(v + 0x18)
                log("   cand %08x: flags18=%08x mode=%d color528=%08x "
                    "b52b=%02x b52f=%02x sprite34=%d\n" % (
                        v, f18, (f18 >> 0x19) & 0x1f,
                        self.rd32(v + 0x528),
                        self.uc.mem_read(v + 0x52B, 1)[0],
                        self.uc.mem_read(v + 0x52F, 1)[0],
                        struct.unpack("<H", self.uc.mem_read(v + 0x34, 2))[0]))
        self._disp_log = 0
        self.m.watch(0x0046A700, on_dispatch)

        def on_pos_fn(machine):
            # 0x466a80 thiscall：this=ECX
            if self.frame != 61:
                return
            this_ = machine.uc.reg_read(UC_X86_REG_ECX)
            log("[f61] 0x466a80 this=%08x esi=%08x p3c/40/44=%g,%g,%g "
                "flags1c=%08x\n" % (
                    this_, machine.uc.reg_read(UC_X86_REG_ESI),
                    self.f32at(this_ + 0x3C) if this_ else 0,
                    self.f32at(this_ + 0x40) if this_ else 0,
                    self.f32at(this_ + 0x44) if this_ else 0,
                    self.rd32(this_ + 0x1C) if this_ else 0))
        self.m.watch(0x00466A80, on_pos_fn)

        def on_corner_build(machine):
            # 0x467b50 建角入口：ESI=VM
            if not (61 <= self.frame <= 61) or self.d3d.cur_layer != 8:
                return
            vm = machine.uc.reg_read(UC_X86_REG_ESI)
            if not (0x00800000 <= vm < 0x06000000):
                return
            parent = self.rd32(vm + 0x570)
            blob = bytes(self.uc.mem_read(vm, 0x5A4))
            import struct as _st
            found = []
            for off in range(0, 0x5A4 - 4, 4):
                v = _st.unpack_from("<f", blob, off)[0]
                if abs(v + 163.783264) < 0.01 or abs(v - 156.2167) < 0.01:
                    found.append("+%x=%g" % (off, v))
            log("[f61 L8] vm=%08x ECL值命中: %s\n" % (vm, found or "无"))
            log("[f61 L8] 0x467b50 vm=%08x p3c/40=%g,%g anchor90/94=%g,%g "
                "flags1c=%08x ofs4e0/4e4=%g,%g scale60/64=%g,%g "
                "parent570=%08x\n" % (
                    vm, self.f32at(vm + 0x3C), self.f32at(vm + 0x40),
                    self.f32at(vm + 0x90), self.f32at(vm + 0x94),
                    self.rd32(vm + 0x1C),
                    self.f32at(vm + 0x4E0), self.f32at(vm + 0x4E4),
                    self.f32at(vm + 0x60), self.f32at(vm + 0x64), parent))
            if parent:
                log("      parent=%08x p3c/40/44=%g,%g,%g flags1c=%08x "
                    "gp570=%08x\n" % (
                        parent, self.f32at(parent + 0x3C),
                        self.f32at(parent + 0x40), self.f32at(parent + 0x44),
                        self.rd32(parent + 0x1C),
                        self.rd32(parent + 0x570)))
        self.m.watch(0x00467B50, on_corner_build)

        def on_up_path(machine):
            # 0x467350 UP 路径核心：只记 f61-63 的 L8（敌机）
            if not (61 <= self.frame <= 63) or self.d3d.cur_layer != 8:
                return
            if self._up_log >= 4:
                return
            self._up_log += 1
            mgr = machine.uc.reg_read(UC_X86_REG_ECX)
            vm = machine.uc.reg_read(UC_X86_REG_EAX)
            corners = struct.unpack(
                "<16f", self.uc.mem_read(0x004E47D8, 64))
            vp = struct.unpack(
                "<6i", self.uc.mem_read(self.rd32(0x004DCC18) + 0xD8, 24))
            log("[f%d L8] 0x467350 mgr+d0/d4=%g,%g vm+3c..44=%g,%g,%g "
                "corners=%s vp=%s\n" % (
                    self.frame, self.f32at(mgr + 0xD0), self.f32at(mgr + 0xD4),
                    self.f32at(vm + 0x3C), self.f32at(vm + 0x40),
                    self.f32at(vm + 0x44),
                    ",".join("%g" % c for c in corners[:8]), list(vp)))
        self._up_log = 0
        self.m.watch(0x00467350, on_up_path)

        def on_enqueue(machine):
            if not (91 <= self.frame <= 94):
                return
            if self._enq_log >= 3:
                return
            self._enq_log += 1
            corners = struct.unpack(
                "<16f", self.uc.mem_read(0x004E47D8, 64))
            mgr = self.rd32(0x004DC688)
            log("[f%d L%s] 0x467a80 enqueue corners=%s mgr_d0/d4=%g,%g\n" % (
                self.frame, self.d3d.cur_layer,
                ",".join("%g" % c for c in corners[:8]),
                self.f32at(mgr + 0xD0), self.f32at(mgr + 0xD4)))
        self._enq_log = 0
        self.m.watch(0x00467A80, on_enqueue)

        self._quad_dumps = 0
        # watch DrawPrimitive 槽（override 已弹栈：返回地址在 esp-4-ret_pop）。
        def on_draw_primitive(machine):
            if self.frame > 0 or self._quad_dumps >= 2:
                return
            self._quad_dumps += 1
            esp = machine.uc.reg_read(UC_X86_REG_ESP)
            ret = struct.unpack("<I", self.uc.mem_read(esp - 20, 4))[0]
            log("[f0] DrawPrimitive caller=%08x(%s)\n" % (
                ret, self.func_names.get(ret, "?")))
            for name in ("eax", "ebx", "ecx", "edx", "esi", "edi"):
                val = machine.uc.reg_read(getattr(
                    __import__("unicorn.x86_const", fromlist=["x"]),
                    "UC_X86_REG_" + name.upper()))
                if 0x04000000 <= val < 0x06000000:
                    log("   reg %s=%08x\n" % (name, val))
                    self.dump_vm_inputs(val)
        self.m.watch(self.d3d.dev_slots[0x144], on_draw_primitive)

        # ---- f92 层内序取证：主链遍历序（0x46f170 逐节点）+ 层桶序 ----
        # 0x46f170 遍历 f48208 链逐 VM tick；ESI=VM 表头（+0x54c 自指）。
        # 只在 f91/f92 记录遍历序（s4aa + 地址），并记 0x46f3f0/f4a0 实际归表。
        self._walk_log = []

        # 0x46f1d7：遍历体 tick 点（ESI=VM，链头起）——记录层桶追加序。
        def on_pool_walk(machine):
            if self.frame not in (91, 92):
                return
            vm = machine.uc.reg_read(UC_X86_REG_ESI)
            if not (0x00800000 <= vm < 0x06000000):
                return
            script = struct.unpack(
                "<H", self.uc.mem_read(vm + 0x4AA, 2))[0]
            layer = self.rd32(vm + 0x24)
            self._walk_log.append(
                (self.frame, vm, script, layer,
                 self.rd32(vm + 0x18) & 0xffffffff))
        self.m.watch(0x0046F1D7, on_pool_walk)

        def report_walk():
            for (fr, vm, script, layer, flags) in self._walk_log:
                sys.stderr.write(
                    "[walk f%d] vm=%08x s4aa=%d L%d flags=%08x\n"
                    % (fr, vm, script, layer, flags))
        self._report_at_end = report_walk

        # 0x46f4a0（头插）与 0x46f3f0（尾插）在 f91/f92 的真实命中：
        # EDI=f4a0 表 / EBX=f3f0 表（unaff_EDI/unaff_EBX 反编译已证）。
        def on_head_insert(machine):
            if self.frame in (91, 92):
                vm = machine.uc.reg_read(UC_X86_REG_EDI)
                log("[f%d] F4A0-head vm=%08x s4aa=%d\n" % (
                    self.frame, vm,
                    struct.unpack("<H", self.uc.mem_read(vm + 0x4AA, 2))[0]))
        self.m.watch(0x0046F4A0, on_head_insert)

        def on_tail_insert(machine):
            if self.frame in (91, 92):
                vm = machine.uc.reg_read(UC_X86_REG_EBX)
                log("[f%d] F3F0-tail vm=%08x s4aa=%d\n" % (
                    self.frame, vm,
                    struct.unpack("<H", self.uc.mem_read(vm + 0x4AA, 2))[0]))
        self.m.watch(0x0046F3F0, on_tail_insert)

        # ---- f159 world[12] ULP 分歧取证：0x4692d0 LAB_004696d2 的平移输入 ----
        # world[12] = (+0x574 + +0x3c + +0x4e0) - (+0x90 * +0x60 * +0x68)。
        # dump 每次进入 0x4692d0 的灵 VM（param_2）输入值与运算次序。
        def on_world_quad(machine):
            if self.frame not in (158, 159):
                return
            esp = machine.uc.reg_read(UC_X86_REG_ESP)
            edx = machine.uc.reg_read(UC_X86_REG_EDX)
            eax = machine.uc.reg_read(UC_X86_REG_EAX)
            for name, vm in (("edx", edx), ("eax", eax)):
                if 0x00800000 <= vm < 0x06000000:
                    raw = bytes(self.uc.mem_read(vm + 0x3C, 4))
                    bits_ = struct.unpack("<I", raw)[0]
                    f = lambda off: struct.unpack(
                        "<f", self.uc.mem_read(vm + off, 4))[0]
                    b = lambda off: struct.unpack(
                        "<I", self.uc.mem_read(vm + off, 4))[0]
                    # 插值通道0（位置）：duration 在 vm+0xEC（0x4652c4 cmpl），
                    # 0x402420 通道基址偏移 +0x50 ⟹ ch = vm+0x9C；
                    # start=ch+0/4/8、end=ch+0xc/10/14、elapsed=ch+0x44、
                    # mode=ch+0x54、current=ch+0x30/34/38。
                    ch = vm + 0x9C
                    log("[f%d 0x4692d0 %s] vm=%08x s4aa=%d x3c=%g bits=%08x "
                        "y40=%g 60=%g 64=%g | ch start=%08x,%08x,%08x "
                        "end=%08x,%08x,%08x elapsed=%08x dur=%08x mode=%08x\n" % (
                        self.frame, name, vm,
                        struct.unpack("<H", self.uc.mem_read(vm + 0x4AA, 2))[0],
                        struct.unpack("<f", raw)[0], bits_,
                        f(0x40), f(0x60), f(0x64),
                        struct.unpack("<I", self.uc.mem_read(ch, 4))[0],
                        struct.unpack("<I", self.uc.mem_read(ch + 4, 4))[0],
                        struct.unpack("<I", self.uc.mem_read(ch + 8, 4))[0],
                        struct.unpack("<I", self.uc.mem_read(ch + 0xC, 4))[0],
                        struct.unpack("<I", self.uc.mem_read(ch + 0x10, 4))[0],
                        struct.unpack("<I", self.uc.mem_read(ch + 0x14, 4))[0],
                        struct.unpack("<I", self.uc.mem_read(ch + 0x44, 4))[0],
                        struct.unpack("<I", self.uc.mem_read(ch + 0x50, 4))[0],
                        struct.unpack("<I", self.uc.mem_read(ch + 0x54, 4))[0]))
                    break
        self.m.watch(0x004692D0, on_world_quad)

        # 0x473a40 mode9：jp（t2<1）→ 0x473b98 走 t2²*0.5；t2≥1 fallthrough
        # 0x473b7a 走 (2-(2-t2)²)*0.5，落位 fstps @0x473b8a。watch 其后的
        # flds（0x473b8e）读 esp+4 = 权重 f32 位——f159 采样对照。
        def on_w9_else(machine):
            if self.frame != 159:
                return
            esp = machine.uc.reg_read(UC_X86_REG_ESP)
            raw = struct.unpack("<I", self.uc.mem_read(esp + 4, 4))[0]
            log("[f%d] W9-t2ge1 w bits=%08x (%.9g)\n" % (
                self.frame, raw,
                struct.unpack("<f", self.uc.mem_read(esp + 4, 4))[0]))
        self.m.watch(0x00473B8E, on_w9_else)


def main(argv):
    frames = int(argv[1]) if len(argv) > 1 else 30
    tracer = SpawnTracer("games/th13/th13.exe",
                         "artifacts/reference/raw", "/tmp/trace-draws.jsonl")
    sys.stderr.write("=== trace_spawn frames=%d ===\n" % frames)
    tracer.run(frames=frames)
    if getattr(tracer, "_report_at_end", None):
        tracer._report_at_end()
    sys.stderr.write("SUMMARY spawns=%d attaches=%d ups=%d\n" % (
        tracer._spawn_count, tracer._attach_count, tracer._up_count))
    total = 0
    lines = []
    for (frame, tag), n in sorted(tracer.rand_counts.items(),
                                  key=lambda kv: (kv[0][0], kv[0][1])):
        total += n
        lines.append("f%d %s: %d" % (frame, tag, n))
    sys.stderr.write("RAND total=%d\n%s\n" % (total, "\n".join(lines[:40])))
    sys.stderr.write("ANM_RNG total=%d per_frame=%s\n" % (
        tracer.anm_rng_calls,
        " ".join("%d:%d" % kv for kv in
                 sorted(tracer.anm_rng_per_frame.items())[:40])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
