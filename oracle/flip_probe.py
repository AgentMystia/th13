"""翻转窗 ULP 取证探针（轮4 蓝图 §8.9 后续）：逐帧 dump script97 的
插值通道输入（rotation/scale/position）与合成字段。

用法（容器内 /work）：
    python3 -m oracle.flip_probe [首帧=280] [末帧=320]
输出 stderr：每帧一行 `FLIP f<N> <全局帧> rot=<f32位型> sx= sy= px= py=`
（%.9g + f32 十六进制位型双写，可直接与 port 侧同帧值按位对照）。

证据源：TitleOracle 窗A 帧序原样复用（不导出 draws、不截窗）——每帧
update walk 完成后（插值已推进、draw 未跑），从 ANM mgr 层池扫描
source_script==97 的 VM（vm+0x4aa u16，title_oracle 快照同式）。

字段偏移（trace_spawn dump_vm_inputs 实证）：
    position +0x3c/0x40/0x44、rotation +0x48/0x4c/0x50、
    scale +0x60/0x64、插值通道位置 mgr 池对象内（本探针只读合成结果，
    通道 raw 与合成差 = 下一层取证，先锁输入量）。
"""
import struct
import sys

from .title_oracle import TITLE_OBJ

# VM 字段偏移（trace_spawn 实证）
VM_SCRIPT = 0x4AA          # u16 源脚本号
VM_POS = 0x3C              # f32 x3
VM_ROT = 0x48              # f32 x3
VM_SCALE = 0x60            # f32 x2
VM_COLOR = 0x528           # u32 ARGB


def f32(bits):
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def g9(x):
    return "%.9g" % x


def build():
    """延迟导入构建（title_oracle 顶层无重依赖，直接子类化）。"""
    from .title_oracle import TitleOracle

    class FlipProbe(TitleOracle):
        probe_first = 280
        probe_last = 320
        flip_vm = 0

        def install_hooks(self):
            super().install_hooks()
            from unicorn.x86_const import UC_X86_REG_EBX

            def on_tail_insert(machine):
                vm = machine.uc.reg_read(UC_X86_REG_EBX)
                if not vm:
                    return
                try:
                    script = struct.unpack(
                        "<H", bytes(machine.uc.mem_read(vm + VM_SCRIPT, 2)))[0]
                except Exception:
                    return
                if script == 97 and not getattr(self, "flip_vm", 0):
                    self.flip_vm = vm
                    sys.stderr.write("FLIP-CAPTURE vm=%08x at g%d\n" % (
                        vm, self.frame))
                if 13 <= script <= 22:
                    key = "w%d" % script
                    if not hasattr(self, key):
                        setattr(self, key, vm)

                if 94 <= script <= 100:
                    key = "logo%d" % script
                    if not hasattr(self, key):
                        setattr(self, key, vm)
            self.m.watch(0x0046F3F0, on_tail_insert)

        def title_frame(self, out):
            super().title_frame(out)
            if not (self.probe_first <= self.frame <= self.probe_last):
                return
            head = ("FLIP f%d g%d" % (self.frame - 1, self.frame))
            if 355 <= self.frame - 1 <= 380:
                parts = []
                for w in range(13, 23):
                    vm = getattr(self, "w%d" % w, 0)
                    if vm:
                        parts.append("w%d=%08x" % (w, self.rd32(vm + 0x528)))
                sys.stderr.write("%s WHITE %s\n" % (head, " ".join(parts)))
            for script in range(94, 101):
                vm = getattr(self, "logo%d" % script, 0)
                if not vm:
                    continue
                raw = bytes(self.uc.mem_read(vm + VM_POS, 0x1c))
                d = struct.unpack("<7I", raw)
                # pos@0x3c→d[0..2]；rot@0x48→d[3..5]；scale@0x60 相对 0x3c=0x24
                sr = bytes(self.uc.mem_read(vm + VM_SCALE, 8))
                sd = struct.unpack("<2I", sr)
                layer = struct.unpack(
                    "<H", bytes(self.uc.mem_read(vm + 0x24, 2)))[0] & 0xFFFF
                sys.stderr.write(
                    "%s s%d L%d px=%s(%08x) py=%s(%08x) rz=%s(%08x) "
                    "sx=%s(%08x) alpha528=%08x\n" % (
                        head, script, layer,
                        g9(f32(d[0])), d[0], g9(f32(d[1])), d[1],
                        g9(f32(d[5])), d[5], g9(f32(sd[0])), sd[0],
                        self.rd32(vm + 0x528)))
                head = "     "

    return FlipProbe


def main():
    first = int(sys.argv[1]) if len(sys.argv) > 1 else 280
    last = int(sys.argv[2]) if len(sys.argv) > 2 else 320
    exe = "games/th13/th13.exe"
    raw = "artifacts/reference/raw"
    out = "artifacts/oracle-draws/flip-probe-draws.jsonl"
    cls = build()
    probe = cls(exe, raw, out)
    probe.probe_first = first
    probe.probe_last = last
    probe.run(frames=last + 20)


if __name__ == "__main__":
    main()
