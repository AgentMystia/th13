#!/usr/bin/env python3
"""音频外部设备契约驱动（独立 ignored 驱动，不改 battle_oracle/产品）。

契约（反编译 + 反汇编实证）：
  - DSOUND.dll import 名是 "ordinal#11"（PE 导入表），不是 ordinal_11；
    DirectSoundCreate8(pcGuid, ppDS8, pUnkOuter) stdcall 12 字节
    （0045fea0: `Ordinal_11(0,piVar4,0)`）。旧驱动按 ordinal_11 注册，
    实际从未命中（设备指针保持 NULL）。
  - DAT_004df1a0 = IDirectSound8*；vt+0x0c CreateSoundBuffer(desc,out,unk)
    pop16（0045fea0:0x45fff5 / 00461490:0x46167c）；
    vt+0x14 DuplicateSoundBuffer(src,out) pop12（00461490:0x461594）；
    vt+0x18 SetCooperativeLevel(hwnd,2) pop12（0045fea0:0x45ff5e 两个
    显式参数 + this）；
    vt+0x08 Release pop4。
  - 0x4e0c28 族 sound slot（stride 0x18，0x4e0ef8=槽30 / 0x4e1150=槽55）
    存 IDirectSoundBuffer8*。IDirectSoundBuffer8 槽位（this+显式参数）：
      0x08 Release pop4；0x0c GetCaps pop8；0x10 GetCurrentPosition pop12；
      0x14 GetFormat pop16；0x18/0x1c/0x20 GetVolume/GetPan/GetFrequency
      pop8；0x24 GetStatus pop8（00460d40:0x460d??）；0x28 Initialize pop12；
      0x2c Lock pop32（7 显式参数 + this，0045fea0:0x460020 /
      00461490:0x46169f）；0x30 Play pop16（0045fea0:0x460068 /
      004613c0:0x4613e3）；0x34 SetCurrentPosition pop8；0x38 SetFormat pop8
      （00475200）；0x3c SetVolume pop8；0x40 SetPan pop8
      （004460d0:148/152）；0x44 SetFrequency pop8；0x48 Stop pop4
      （00460250:0x4602e2 / 004613c0:0x4613d7 / 00460dd0:250）；
      0x4c Unlock pop20（0045fea0:0x460058 / 00461490:0x46170?）；
      0x50 Restore pop4；0x54 SetFX pop16；0x58 AcquireResources pop20；
      0x5c GetObjectInPath pop20。
  注意：审计代理曾把 Lock 的 7 个显式参数误算成 28 字节；stdcall 的
  `ret N` 必须连同 this 一起弹出，所以 Lock=32 是正确的，不得按 28 改。

实现要点（相对旧驱动的修复）：
  1. 虚表与桩代码分离：每个槽的 `ret N` 桩在 code 页，vtable 在独立页，
     vtable[slot] = 桩地址；对象 [0] = vtable。旧实现 vtable 直接指向
     机器码页，`[vt+slot]` 读到的是桩字节而非函数指针。
  2. 钩子只记录 (this, 显式参数)，显式参数个数 = pop/4 - 1；旧实现多读
     一个 dword 且把 this 后的第一个参数当 data，Lock 指针参数整体错位。
  3. DSOUND 导入名改为 "ordinal#11"，并模拟 stdcall ret 12。
  4. 初始化驱动不再从 UC_HOOK_CODE 回调里嵌套 emu_start（Unicorn 不支持，
     旧实现硬崩）；改在 BattleOracle.thread_bodies（Python 帧间隙）驱动。

真实音频输出（线程/混音）headless 不可达：BGM 线程未驱动，登记为边界。
本驱动只提供可录音的设备契约；不写回放/游戏字段。
"""
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unicorn import UcError, UC_HOOK_CODE, x86_const as ux  # noqa: E402
from unicorn.x86_const import (UC_X86_REG_EAX, UC_X86_REG_EBP,  # noqa: E402
                               UC_X86_REG_EBX, UC_X86_REG_ECX,
                               UC_X86_REG_EDI, UC_X86_REG_EDX,
                               UC_X86_REG_EFLAGS, UC_X86_REG_EIP,
                               UC_X86_REG_ESI, UC_X86_REG_ESP)
from oracle.battle_oracle import BattleOracle  # noqa: E402
from oracle.machine import RET_MAGIC           # noqa: E402
from oracle.replayfmt import ReplayFile        # noqa: E402

OUT_DIR = os.environ.get("TH13_AUDIO_OUT",
                         "artifacts/youmu-lunatic-20261029/bomb")
PAGE = 0x1000

# 设备/缓冲代码页、虚表页、对象页、Lock 数据区（heap 高段，避开游戏区）。
DEV_CODE = 0x0F000000
DEV_VT = 0x0F001000
BUF_CODE = 0x0F002000
BUF_VT = 0x0F003000
DEV_OBJ = 0x0F004000
BUF_OBJ_BASE = 0x0F005000
BUF_OBJ_SIZE = 0x80000
DATA_BASE = 0x0F100000
DATA_BYTES = 0x400000

# 设备虚表：偏移 → (名, stdcall 弹参字节数 = this + 显式参数)。
# 槽位顺序 = DirectSound8 公共 ABI；本文件头列出的零售调用点均已逐一核对。
DEV_SLOTS = {
    0x00: ("QueryInterface", 12), 0x04: ("AddRef", 4), 0x08: ("Release", 4),
    0x0C: ("CreateSoundBuffer", 16), 0x10: ("GetCaps", 8),
    0x14: ("DuplicateSoundBuffer", 12),
    0x18: ("SetCooperativeLevel", 12), 0x1C: ("Compact", 4),
    0x20: ("GetSpeakerConfig", 8), 0x24: ("SetSpeakerConfig", 8),
    0x28: ("Initialize", 8), 0x2C: ("VerifyCertification", 8),
}
BUF_SLOTS = {
    0x00: ("QueryInterface", 12), 0x04: ("AddRef", 4), 0x08: ("Release", 4),
    0x0C: ("GetCaps", 8), 0x10: ("GetCurrentPosition", 12),
    0x14: ("GetFormat", 16), 0x18: ("GetVolume", 8), 0x1C: ("GetPan", 8),
    0x20: ("GetFrequency", 8), 0x24: ("GetStatus", 8),
    0x28: ("Initialize", 12), 0x2C: ("Lock", 32), 0x30: ("Play", 16),
    0x34: ("SetCurrentPosition", 8), 0x38: ("SetFormat", 8),
    0x3C: ("SetVolume", 8), 0x40: ("SetPan", 8), 0x44: ("SetFrequency", 8),
    0x48: ("Stop", 4), 0x4C: ("Unlock", 20), 0x50: ("Restore", 4),
    0x54: ("SetFX", 16), 0x58: ("AcquireResources", 20),
    0x5C: ("GetObjectInPath", 20),
}

REGS = (UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX, UC_X86_REG_EDX,
        UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP, UC_X86_REG_ESP,
        UC_X86_REG_EFLAGS)


def rd_u32(uc, addr):
    return struct.unpack("<I", bytes(uc.mem_read(addr, 4)))[0]


class ComStubs:
    """一类 COM 接口的桩：代码页放 `ret N`，虚表页放函数指针。

    vtable[slot] = code_base + i*8；对象 [0] = vtable 页基。
    钩子只覆盖代码页；`_on` 按 stdcall 形状读取 this 与显式参数。
    """

    def __init__(self, device, code_base, vt_base, slots, kind):
        self.device = device
        self.uc = device.uc
        self.kind = kind
        self.code_base = code_base
        self.vt_base = vt_base
        self.table = {}
        self.uc.mem_map(code_base, PAGE)
        self.uc.mem_map(vt_base, PAGE)
        self.uc.mem_write(vt_base, b"\x00" * PAGE)
        for index, (off, (name, pop)) in enumerate(sorted(slots.items())):
            if pop % 4 or pop < 4:
                raise ValueError("stdcall pop must include this: %s" % name)
            stub = code_base + index * 8
            self.uc.mem_write(stub, b"\xC2" + struct.pack("<H", pop))
            self.uc.mem_write(vt_base + off, struct.pack("<I", stub))
            self.table[stub] = (off, name, pop)
        self.vt = vt_base
        self.uc.hook_add(UC_HOOK_CODE, self._on, begin=code_base,
                         end=code_base + len(slots) * 8 - 1)

    def bind(self, obj_addr, vtable_addr=None):
        self.uc.mem_write(obj_addr,
                          struct.pack("<I", vtable_addr or self.vt))

    def _on(self, uc, addr, size, ud):
        ent = self.table.get(addr)
        if ent is None:
            return
        off, name, pop = ent
        esp = uc.reg_read(UC_X86_REG_ESP)
        this = rd_u32(uc, esp + 4)
        argc = pop // 4 - 1          # 显式参数个数（pop 含 this）
        args = (list(struct.unpack("<%dI" % argc,
                                   bytes(uc.mem_read(esp + 8, argc * 4))))
                if argc else [])
        record = {"kind": self.kind, "slot": hex(off), "name": name,
                  "this": hex(this), "args": [hex(a) for a in args],
                  "argc": argc, "pop": pop}
        self.device.trace.append(record)
        try:
            self._side_effect(uc, name, args, record)
        finally:
            ret = rd_u32(uc, esp)
            uc.reg_write(UC_X86_REG_ESP, esp + 4 + pop)
            uc.reg_write(UC_X86_REG_EIP, ret)
            uc.reg_write(UC_X86_REG_EAX, 0)   # S_OK

    def _side_effect(self, uc, name, args, record):
        if self.kind == "dev" and name in ("CreateSoundBuffer",
                                           "DuplicateSoundBuffer"):
            buf = self.device.make_buffer()
            out = args[1]
            uc.mem_write(out, struct.pack("<I", buf))
            record["created_buffer"] = hex(buf)
        elif self.kind == "buf" and name == "Lock":
            # Lock(this, off, bytes, &p1, &n1, &p2, &n2, flags)
            offset, nbytes = args[0], args[1]
            if offset >= DATA_BYTES:
                nbytes = 0
            else:
                nbytes = min(nbytes, DATA_BYTES - offset)
            uc.mem_write(args[2], struct.pack("<I", DATA_BASE + offset))
            uc.mem_write(args[3], struct.pack("<I", nbytes))
            uc.mem_write(args[4], struct.pack("<I", 0))
            uc.mem_write(args[5], struct.pack("<I", 0))
            record["locked"] = [hex(offset), hex(nbytes)]
        elif self.kind == "buf" and name == "GetStatus":
            uc.mem_write(args[0], struct.pack("<I", 0))
        elif self.kind == "buf" and name == "GetCurrentPosition":
            uc.mem_write(args[0], struct.pack("<I", 0))
            uc.mem_write(args[1], struct.pack("<I", 0))
        elif self.kind == "buf" and name == "GetFormat":
            uc.mem_write(args[2], struct.pack("<I", 0))
        elif self.kind == "buf" and name in ("GetCaps", "GetVolume", "GetPan",
                                             "GetFrequency"):
            uc.mem_write(args[0], b"\x00" * 4)


class AudioDevice:
    """DirectSound8 设备 + 缓冲对象的外部契约模型。"""

    def __init__(self, oracle):
        self.oracle = oracle
        self.uc = oracle.m.uc
        self.trace = []
        self._next_buf = BUF_OBJ_BASE
        self.uc.mem_map(DATA_BASE, DATA_BYTES)
        self.uc.mem_map(DEV_OBJ, PAGE)
        self.uc.mem_map(BUF_OBJ_BASE, BUF_OBJ_SIZE)
        self.uc.mem_write(DEV_OBJ, b"\x00" * PAGE)
        self.dev = ComStubs(self, DEV_CODE, DEV_VT, DEV_SLOTS, "dev")
        self.buf = ComStubs(self, BUF_CODE, BUF_VT, BUF_SLOTS, "buf")
        self.dev.bind(DEV_OBJ)
        oracle.m.stub("ordinal#11", self._dsound_create8, args_pop=12)

    def _dsound_create8(self, machine):
        # DirectSoundCreate8(pcGuid,[esp+8]ppDS8,pUnk) stdcall 12：
        # 0045fea0 正文 Ordinal_11(0,piVar4,0)；*ppDS8 = 设备对象。
        uc = machine.uc
        esp = uc.reg_read(UC_X86_REG_ESP)
        ppds8 = rd_u32(uc, esp + 8)
        uc.mem_write(ppds8, struct.pack("<I", DEV_OBJ))
        self.trace.append({"kind": "import", "name": "DirectSoundCreate8",
                           "ppDS8": hex(ppds8), "device": hex(DEV_OBJ)})
        return 0

    def make_buffer(self):
        buf = self._next_buf
        self._next_buf += PAGE
        if self._next_buf > BUF_OBJ_BASE + BUF_OBJ_SIZE:
            raise RuntimeError("buffer object pool exhausted")
        self.uc.mem_write(buf, struct.pack("<I", self.buf.vt))
        self.trace.append({"kind": "buf-alloc", "obj": hex(buf)})
        return buf

    def counts(self):
        out = {}
        for record in self.trace:
            key = "%s:%s" % (record.get("kind"), record.get("name", "alloc"))
            out[key] = out.get(key, 0) + 1
        return out


def drive_retail(oracle, address, regs=None, args=(), timeout_ms=120_000):
    """在帧间隙直调零售函数（不嵌套 emu_start）。"""
    uc = oracle.uc
    saved = {reg: uc.reg_read(reg) for reg in
             (UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX,
              UC_X86_REG_EDX, UC_X86_REG_ESI, UC_X86_REG_EBP,
              UC_X86_REG_ESP, UC_X86_REG_EFLAGS)}
    esp = (0x002FF000 - 0x8000) & ~0xF
    uc.mem_write(esp, struct.pack("<I", RET_MAGIC))
    for index, value in enumerate(args):
        uc.mem_write(esp + 4 + index * 4,
                     struct.pack("<I", value & 0xFFFFFFFF))
    uc.reg_write(UC_X86_REG_ESP, esp)
    uc.reg_write(UC_X86_REG_EBP, 0x002FF000)
    for name, value in (regs or {}).items():
        uc.reg_write(getattr(ux, "UC_X86_REG_" + name.upper()),
                     value & 0xFFFFFFFF)
    try:
        uc.emu_start(address, RET_MAGIC, timeout=timeout_ms * 1000)
        return {"status": "ok",
                "eax": "%08x" % uc.reg_read(UC_X86_REG_EAX)}
    except UcError as exc:
        return {"status": "fault", "error": str(exc),
                "eip": "%08x" % uc.reg_read(UC_X86_REG_EIP),
                "esp": "%08x" % uc.reg_read(UC_X86_REG_ESP)}
    finally:
        for reg, value in saved.items():
            uc.reg_write(reg, value)


def preload_se_wave_data(oracle, raw_dir):
    """把零售异步 loader 会填的 0x4e12d0[file_idx] 指向真实 wav 字节。

    0x461490 在 `(&DAT_004e12d0)[id]` 为空时 Sleep(10) 自旋等待
    （00461490.c:57-62）；headless 没有文件线程，测试按同一张
    0x4bad90/0x4bb320 表把 raw_dir 里的真实 wav 读进 game heap。
    """
    uc = oracle.uc
    loaded, missing = {}, []
    for index in range((0x4BB320 - 0x4BAD90) // 0x14):
        file_idx = oracle.rd32(0x4BAD90 + index * 0x14 + 4)
        if file_idx in loaded or file_idx in missing:
            continue
        name_ptr = oracle.rd32(0x4BB320 + file_idx * 4)
        name = bytes(oracle.cstr(name_ptr)).decode("ascii", "replace")
        path = os.path.join(raw_dir, name)
        if not os.path.isfile(path):
            missing.append(name)
            continue
        data = open(path, "rb").read()
        ptr = oracle.game_malloc(len(data))
        uc.mem_write(ptr, data)
        oracle.wr32(0x4E12D0 + file_idx * 4, ptr)
        loaded[file_idx] = name
    return loaded, missing


class AudioOracle(BattleOracle):
    """BattleOracle + 设备安装/初始化驱动（驱动点=帧间隙 thread_bodies）。"""

    def __init__(self, exe_path, raw_dir, out_path):
        super().__init__(exe_path, raw_dir, out_path)
        self.audio_device = None
        self.audio_init = {"done": False}
        self.audio_preload = {}

    def install_audio(self):
        self.audio_device = AudioDevice(self)
        loaded, missing = preload_se_wave_data(self, self.raw_dir)
        self.audio_preload = {"loaded": len(loaded), "missing": missing}
        if missing:
            sys.stderr.write("AUDIO-PRELOAD missing: %s\n" % missing)
        return self.audio_device

    def thread_bodies(self):
        super().thread_bodies()
        if (self.audio_device is not None
                and not self.audio_init["done"]):
            self.audio_init["done"] = True
            self.audio_init.update(drive_retail(
                self, 0x0045FEA0, regs={"ecx": 0x004DF1A0, "edx": 0}))
            sys.stderr.write("AUDIO-INIT %s\n" % self.audio_init)


def build_synthetic_replay():
    from tools.youmu_bomb_probe import build_synthetic_replay as build
    return build()


def run_replay(argv):
    frames = 120
    replay_path = stage_idx = None
    index = 0
    while index < len(argv):
        if argv[index] == "--replay":
            index += 1
            replay_path = argv[index]
        elif argv[index] == "--stage":
            index += 1
            stage_idx = int(argv[index])
        elif argv[index] == "--frames":
            index += 1
            frames = int(argv[index])
        else:
            frames = int(argv[index])
        index += 1
    os.makedirs(OUT_DIR, exist_ok=True)
    if replay_path:
        replay = ReplayFile().decode(open(replay_path, "rb").read())
        stage = replay.stages[stage_idx or 0]
        tag = "%s-s%d" % (os.path.splitext(
            os.path.basename(replay_path))[0], stage_idx or 0)
        bomb_bf = None
        for frame in range(stage.frame_count):
            if stage.frame(frame)[1] & 0x02:
                bomb_bf = frame
                break
    else:
        replay = build_synthetic_replay()
        stage = replay.stages[0]
        tag, bomb_bf = "synthetic", 140
    if not frames:
        frames = 120

    out_draws = os.path.join(OUT_DIR, "draws-audio-%s.jsonl" % tag)
    oracle = AudioOracle("games/th13/th13.exe", "artifacts/reference/raw",
                         out_draws)
    oracle.replay, oracle.replay_stage = replay, stage
    oracle.replay_stage_index = stage_idx or 0
    oracle._replay_file_name = os.path.basename(replay_path) if replay_path \
        else "youmu_audio_oracle.rpy"
    oracle.difficulty = replay.rank & 3
    oracle.character = replay.character & 3
    oracle.shot_type = replay.shot_type
    oracle.target_frames = frames
    oracle.install_audio()

    oracle.run(frames=oracle.target_frames)
    return finish(oracle, tag, bomb_bf, None)


def selftest(argv):
    """可执行正负控：真实零售函数驱动全部设备虚调用。

    顺序：0x45fea0 音频初始化（CreateSoundBuffer/Lock/Unlock/Play +
    71 个 SE 缓冲）→ 0x4613c0（Stop/SetCurrentPosition/SetPan/SetVolume/
    Play）→ 0x461490 slot1（Release + DuplicateSoundBuffer）→
    0x460250（全槽 Release + 主缓冲 Stop/Release + 设备 Release）。
    """
    os.makedirs(OUT_DIR, exist_ok=True)
    oracle = AudioOracle("games/th13/th13.exe", "artifacts/reference/raw",
                         os.path.join(OUT_DIR, "draws-audio-selftest.jsonl"))
    # 与 run() 相同的环境前序（不跑游戏帧）。
    oracle.install_overrides()
    oracle.init_ptd()
    oracle.patch_globals_title()
    device = oracle.install_audio()
    results = {}
    results["init"] = drive_retail(oracle, 0x0045FEA0,
                                   regs={"ecx": 0x004DF1A0, "edx": 0})
    slot0 = 0x4E0C28
    results["play"] = drive_retail(oracle, 0x004613C0,
                                   regs={"esi": slot0, "eax": 0})
    results["release_duplicate"] = drive_retail(
        oracle, 0x00461490, regs={"esi": slot0 + 0x18},
        args=[oracle.rd32(0x4BB320)])
    # 关闭路径会把所有 slot/主缓冲/设备指针清 0；先快照真实构造结果，
    # 再驱动 0x460250，最后用快照做对象断言、用后态做清理断言。
    uc = oracle.uc
    pre_shutdown = {
        "device_ptr": rd_u32(uc, 0x4DF1A0),
        "primary": rd_u32(uc, 0x4DF1A8),
        "slots": [rd_u32(uc, 0x4E0C28 + index * 0x18)
                  for index in range(71)],
    }
    results["shutdown"] = drive_retail(oracle, 0x00460250)
    counts = device.counts()
    required = ("dev:CreateSoundBuffer", "buf:Lock", "buf:Play", "buf:Stop",
                "buf:Release", "buf:Unlock", "dev:DuplicateSoundBuffer")
    missing = [name for name in required if counts.get(name, 0) < 1]
    # 形状/对象断言：防止“桩自己造结果”被当成真实调用通过。每个调用都
    # 必须由零售代码进入，且 (argc,pop) 与调用点一致；CreateSoundBuffer
    # 必须真的写出缓冲对象；主缓冲/slot30/slot55 的 [0] 必须指向 BUF_VT。
    shapes = {
        "dev:CreateSoundBuffer": (3, 16), "dev:DuplicateSoundBuffer": (2, 12),
        "dev:SetCooperativeLevel": (2, 12), "dev:Release": (0, 4),
        "buf:Lock": (7, 32), "buf:Unlock": (4, 20), "buf:Play": (3, 16),
        "buf:Stop": (0, 4), "buf:SetPan": (1, 8), "buf:SetVolume": (1, 8),
        "buf:SetCurrentPosition": (1, 8), "buf:SetFormat": (1, 8),
    }
    bad_shapes = []
    bad_create = []
    bad_locks = []
    for record in device.trace:
        if record.get("kind") not in ("dev", "buf") or "name" not in record:
            continue
        key = "%s:%s" % (record["kind"], record["name"])
        if key in shapes:
            want = shapes[key]
            if (record.get("argc"), record.get("pop")) != want:
                bad_shapes.append([key, record.get("argc"),
                                   record.get("pop"), list(want)])
        if record["name"] == "CreateSoundBuffer" \
                and "created_buffer" not in record:
            bad_create.append(record)
        if record["name"] == "Lock":
            locked = record.get("locked") or ["0x0", "0x0"]
            if record.get("argc") != 7 or int(locked[1], 16) <= 0:
                bad_locks.append(record)
    uc = oracle.uc
    primary = pre_shutdown["primary"]
    primary_vt = rd_u32(uc, primary) if primary else None
    device_ptr = pre_shutdown["device_ptr"]
    slot_ptrs = pre_shutdown["slots"]
    slot_vts = [rd_u32(uc, ptr) if ptr else None for ptr in slot_ptrs]
    post_slots_clear = all(rd_u32(uc, 0x4E0C28 + index * 0x18) == 0
                           for index in range(71))
    post_primary_clear = rd_u32(uc, 0x4DF1A8) == 0
    post_device_clear = rd_u32(uc, 0x4DF1A0) == 0
    ok = (all(results[key]["status"] == "ok" for key in results)
          and not missing and not oracle.audio_preload["missing"]
          and not bad_shapes and not bad_create and not bad_locks
          and device_ptr == DEV_OBJ and primary_vt == BUF_VT
          and all(ptr for ptr in slot_ptrs)
          and all(vt == BUF_VT for vt in slot_vts)
          and post_slots_clear and post_primary_clear and post_device_clear)
    payload = {"selftest": "ok" if ok else "failed", "results": results,
               "counts": counts, "missing_calls": missing,
               "bad_shapes": bad_shapes, "bad_create": bad_create[:4],
               "bad_locks": bad_locks[:4],
               "device_ptr": hex(device_ptr),
               "primary_vt": hex(primary_vt or 0),
               "slot_buffers": sum(1 for ptr in slot_ptrs if ptr),
               "slot_vts_ok": sum(1 for vt in slot_vts if vt == BUF_VT),
               "post_shutdown_clear": {
                   "slots": post_slots_clear,
                   "primary": post_primary_clear,
                   "device": post_device_clear},
               "preload": oracle.audio_preload,
               "first_trace": device.trace[:40],
               "last_trace": device.trace[-40:]}
    path = os.path.join(OUT_DIR, "audio_selftest.json")
    with open(path, "w") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)
    print("SELFTEST", payload["selftest"], "missing", missing,
          "bad_shapes", len(bad_shapes), "bad_create", len(bad_create),
          "bad_locks", len(bad_locks), "slots",
          payload["slot_buffers"], "/", payload["slot_vts_ok"])
    print("RESULTS", json.dumps(results, ensure_ascii=False))
    print("COUNTS", json.dumps(counts, ensure_ascii=False))
    print("WROTE", path)
    return 0 if ok else 1


def finish(oracle, tag, bomb_bf, selftest_payload):
    device = oracle.audio_device
    uc = oracle.uc
    slots = {}
    for address in (0x4DF1A0, 0x4DF1A8, 0x4E0E08, 0x4E0EF8, 0x4E1150):
        value = rd_u32(uc, address)
        slots[hex(address)] = {"ptr": hex(value),
                               "vt": hex(rd_u32(uc, value)) if value else None}
    entries = {}
    for index in (0, 30, 55):
        base = 0x4E0C28 + index * 0x18
        entries[str(index)] = [hex(x) for x in struct.unpack(
            "<6I", bytes(uc.mem_read(base, 0x18)))]
    result = {"tag": tag, "frames_run": oracle.frame,
              "crash_frame": oracle.crash_frame,
              "edge_mismatches": getattr(
                  oracle, "replay_edge_mismatches", [])[:6],
              "audio_init": oracle.audio_init,
              "audio_preload": oracle.audio_preload,
              "drive": oracle.audio_init,
              "trace_n": len(device.trace),
              "trace_counts": device.counts(),
              "trace": device.trace[:300],
              "slots": slots, "entries": entries,
              "bomb_bf_input": bomb_bf}
    if selftest_payload is not None:
        result["selftest"] = selftest_payload
    path = os.path.join(OUT_DIR, "audio_oracle-%s.json" % tag)
    with open(path, "w") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=1)
    print("WROTE", path, "trace:", len(device.trace), "init:",
          oracle.audio_init)
    print("SLOTS", json.dumps(slots))
    print("CRASH", oracle.crash_frame, "EDGE_DIFF",
          len(getattr(oracle, "replay_edge_mismatches", [])))
    return 0


def main(argv):
    if argv and argv[0] == "--selftest":
        return selftest(argv[1:])
    return run_replay(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
