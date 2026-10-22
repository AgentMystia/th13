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

        def on_spawn_call(machine):
            self._spawn_count += 1
        self.m.watch(0x004183F0, on_spawn_call)

        def on_attach(machine):
            self._attach_count += 1
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

        def on_dispatch(machine):
            # 0x46a700 逐精灵分发：cur_layer==8 时 dump 判定输入
            if self.d3d.cur_layer != 8 or self._disp_log >= 6:
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


def main(argv):
    frames = int(argv[1]) if len(argv) > 1 else 30
    tracer = SpawnTracer("games/th13/th13.exe",
                         "artifacts/reference/raw", "/tmp/trace-draws.jsonl")
    sys.stderr.write("=== trace_spawn frames=%d ===\n" % frames)
    tracer.run(frames=frames)
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
