#!/usr/bin/env python3
"""0x41a8a0 槽位 VM 同步趟取证探针（f61 敌机行走位差机制层战役）。

在原版 0x41a9d1（求和已入栈、写 VM+0x574 前）设被动观察点，逐槽 dump：
敌机位 / +0x160 偏移表值 / +0x220 attach 槽号 / 最终写入 VM 的 xyz /
VM 当前 +0x3c 位。另以首个发现的敌机基址挂 MEM_WRITE 钩，跟踪
enemy+0x160..0x20C 偏移表区间写入（定位写入者）。

用法（容器内 /work）：
  python3 -m oracle.anm_sync_probe [帧数=70] [起始输出帧=55]
输出：SYNC 行（每敌机每槽每帧）+ OFFW 行（偏移表写入事件）。
"""
import struct
import sys

from unicorn import UC_HOOK_MEM_WRITE
from unicorn.x86_const import (UC_X86_REG_EAX, UC_X86_REG_EBX,
                               UC_X86_REG_EDX, UC_X86_REG_ESI,
                               UC_X86_REG_EDI, UC_X86_REG_ESP)

from .render_oracle import RenderOracle
from . import render_oracle as _RO
from unicorn.x86_const import UC_X86_REG_ESP as _REG_ESP

SYNC_AT = 0x41A9D1  # test byte [edi+0x1c],0x40 —— 写 VM+0x574 前


def _install_up_tracer(frame_getter, lo, hi):
    """类级包裹 DrawPrimitiveUP 桩：enemy 贴图 up op 打印原版调用者返回地址。"""
    orig = _RO.FakeD3D._dev_draw_primitive_up

    def traced(self, mm):
        ret = struct.unpack(
            "<I", self.uc.mem_read(self.uc.reg_read(_REG_ESP), 4))[0]
        orig(self, mm)
        op = self.frame_ops[-1] if self.frame_ops else None
        if op and op.get("op") == "up":
            v = op["verts"][0][:2] if op["verts"] else ("?", "?")
            print("UPCALL f=%s layer=%s TL=(%s,%s) tex=%s caller=%#x"
                  % (frame_getter(), op["layer"], v[0], v[1],
                     op.get("tex"), ret))

    _RO.FakeD3D._dev_draw_primitive_up = traced


def _install_tfactor_vm_dump(frame_getter, lo, hi, state):
    """watch 0x4698d7（0x4692d0 内 TFACTOR SetRenderState 返回点）：dump
    esi/edi 所指对象的 +0x30..0x120 区（VM 色/插值字段所在），帧间对比
    还原 α 插值状态机（f0 tfactor 残留建模取证）。"""
    import binascii
    from unicorn.x86_const import UC_X86_REG_ESI as _ESI
    from unicorn.x86_const import UC_X86_REG_EDI as _EDI

    def on_tf(machine):
        f = frame_getter()
        if not (lo <= f <= hi):
            return
        uc = machine.uc
        from unicorn.x86_const import UC_X86_REG_EBX as _EBX
        vm = uc.reg_read(_EBX)
        if not (0x1000000 < vm < 0x8000000):
            return
        dumps = state.setdefault("tf_dump", {})
        blob = bytes(uc.mem_read(vm, 0x5A0))
        prev = dumps.get(vm)
        if prev != blob:
            diff = ""
            if prev is not None:
                for i in range(0, len(blob), 4):
                    if blob[i:i + 4] != prev[i:i + 4]:
                        import struct as _st
                        diff += " +%03x:%s->%s" % (
                            i, prev[i:i+4].hex(),
                            _st.unpack("<f", blob[i:i+4])[0]
                            if blob[i:i+4] != b"\0\0\0\0" or True else "",
                        )
            print("TFVM f=%d vm=%#x full=%s%s"
                  % (f, vm, "" if prev is not None else binascii.hexlify(
                      blob).decode()[:200], diff[:1200]))
            dumps[vm] = blob

    return on_tf


def f32(b):
    return struct.unpack("<f", b)[0]


def main(argv):
    frames = int(argv[1]) if len(argv) > 1 else 70
    start = int(argv[2]) if len(argv) > 2 else 55
    state = {"ro": None, "base": None, "hook": None, "vm_hooks": {}}
    _install_up_tracer(lambda: state["ro"].frame if state["ro"] else 0,
                       start, frames)
    ro = RenderOracle("games/th13/th13.exe", "artifacts/reference/raw",
                      "/tmp/anm-sync-probe-draws.jsonl")
    state["ro"] = ro
    uc = ro.uc
    ro.m.watch(0x4698D7, _install_tfactor_vm_dump(
        lambda: ro.frame, start, frames, state))

    def rd(addr):
        return bytes(uc.mem_read(addr, 4))

    def on_sync(machine):
        if not (start <= ro.frame <= frames):
            return
        # 相机对象 [0x4dcc18] 快照（quad 路径 0x468670 减其 +0、
        # 插值因子 +0x108/-0x10c——对账 up 路径无相机之差）。
        cam = struct.unpack("<I", bytes(uc.mem_read(0x4DCC18, 4)))[0]
        if cam:
            vals = struct.unpack("<12f", bytes(uc.mem_read(cam, 48)))
            hi = struct.unpack("<4f", bytes(uc.mem_read(cam + 0x100, 16)))
            sys.stdout.write(
                "CAM f=%d obj=%#x x=%.9g y=%.9g z=%.9g .. +0x20=(%.9g,%.9g) "
                "+0x100..=(%.9g,%.9g,%.9g,%.9g)\n"
                % (ro.frame, cam, vals[0], vals[1], vals[2], vals[8], vals[9],
                   hi[0], hi[1], hi[2], hi[3]))
        esp = uc.reg_read(UC_X86_REG_ESP)
        ebx = uc.reg_read(UC_X86_REG_EBX)
        esi = uc.reg_read(UC_X86_REG_ESI)
        edi = uc.reg_read(UC_X86_REG_EDI)
        eax = uc.reg_read(UC_X86_REG_EAX)
        slot = (uc.reg_read(UC_X86_REG_EDX) - (ebx + 0x220)) // 4
        sys.stdout.write(
            "SYNC f=%d slot=%d enemy=%#x vm=%#x "
            "enemy_pos=(%.9g,%.9g,%.9g) off=(%.9g,%.9g,%.9g) attach=%d "
            "final=(%.9g,%.9g,%.9g) vm_prev574=(%.9g,%.9g,%.9g) "
            "vm_cur3c=(%.9g,%.9g,%.9g)\n"
            % (ro.frame, slot, ebx, edi,
               f32(rd(ebx + 0x44)), f32(rd(ebx + 0x48)), f32(rd(ebx + 0x4C)),
               f32(rd(esi - 8)), f32(rd(esi - 4)), f32(rd(esi)),
               struct.unpack("<i", struct.pack("<I", eax))[0],
               f32(rd(esp + 0x1C)), f32(rd(esp + 0x20)), f32(rd(esp + 0x24)),
               f32(rd(edi + 0x574)), f32(rd(edi + 0x578)), f32(rd(edi + 0x57C)),
               f32(rd(edi + 0x3C)), f32(rd(edi + 0x40)), f32(rd(edi + 0x44))))
        # 首个敌机基址出现 → 挂偏移表写入钩（敌机 0=MapleEnemy 常驻，
        # 它的 +0x160 表同样值得看写入时机）。
        if state["hook"] is None:
            state["base"] = ebx
            state["hook"] = uc.hook_add(
                UC_HOOK_MEM_WRITE, on_offw, begin=ebx + 0x160,
                end=ebx + 0x20B)
        # 首次见到该敌机的槽 0 VM → 挂 +0x3c / +0x574 写入钩（跟踪
        # 同步趟之外还有谁写位、tick 链如何把 +0x574 变成 +0x3c）。
        if slot == 0 and edi not in state["vm_hooks"]:
            state["vm_hooks"][edi] = True
            for name, field in (("POS3C", 0x3C), ("POS574", 0x574)):
                uc.hook_add(UC_HOOK_MEM_WRITE, on_vm_write,
                            begin=edi + field, end=edi + field + 0xB,
                            user_data=(name, edi))

    def on_vm_write(uc_, access, address, size, value, user_data):
        name, vm = user_data
        if start <= ro.frame <= frames:
            sys.stdout.write(
                "%s f=%d vm=%#x +%#x val=%.9g\n"
                % (name, ro.frame, vm, address - vm,
                   f32(struct.pack("<I", value & 0xFFFFFFFF))))


    def on_offw(uc_, access, address, size, value, user_data):
        base = state["base"]
        if base and base + 0x160 <= address < base + 0x20C:
            sys.stdout.write(
                "OFFW f=%d off+%#x size=%d value=%.9g\n"
                % (ro.frame, address - base, size,
                   f32(struct.pack("<I", value & 0xFFFFFFFF))))

    ro.m.watch(SYNC_AT, on_sync)
    code = ro.run(frames=frames)
    if state["hook"]:
        uc.hook_del(state["hook"])
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
