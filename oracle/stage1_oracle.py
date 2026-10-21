"""stage1 原版行为 oracle：Unicorn 装载 th13.exe，按 event-contract.md 输出事件流。

方法论（oracle-entry.md §五 + event-contract.md）：
  - 装载：oracle.machine（PE 映射 + 导入桩 + 范围挂钩）。
  - 资源缝：override 0x4724c0（按名打开）→ artifacts/reference/raw 的 dump 直送。
  - 建管：直调 0x418d30("st01.ecl")（敌机管理器 + EclResourceInf 全链）。
  - root：直调 0x4183f0("main", {0,0,...})（契约 §2：main 由 root 敌机承载，serial 0，
    不产生事件）。mgr+0xBC 序号清 0 以对齐契约 §3.1（原版 0x418d30 自带 +1 偏移）。
  - 逐帧：0x418e00（EDI=ctl）3600 趟；第 0 帧即 root 构造内首趟。
  - 事件：三个观察点——
      enemy_spawn : 0x4183f0 入口（param1=名字, param2={x,y,z,life,...}）
      enemy_move  : 0x41b260 入口停机，探参 0x478e60/0x478ee0（RNG 快照/回滚，
                    探参后恢复寄存器续跑原处理器）
      bullet_spawn: 0x40e840 入口换返回地址，节点落值（+0xb70..+0xb8c）在返回点读回
  - 移植侧 no-op 指令族（StageCommands.cpp return 0 集）以改写分发跳表（0x41f760）
    对齐——数据改写，不动代码；518 定向到 525 处理块（共享清场尾块）。

用法（容器内 /work）：
    python3 -m oracle.stage1_oracle [exe] [raw_dir] [out_jsonl]
默认：games/th13/th13.exe  artifacts/reference/raw
      eagler-touhou/ports/th13/web/artifacts/wasi/stage1-oracle.jsonl
"""
import json
import os
import struct
import sys
import time

from unicorn import UcError, UC_HOOK_BLOCK, UC_HOOK_MEM_UNMAPPED
from unicorn.x86_const import (UC_X86_REG_EAX, UC_X86_REG_EBP, UC_X86_REG_ECX,
                               UC_X86_REG_EDI, UC_X86_REG_EDX, UC_X86_REG_EFLAGS,
                               UC_X86_REG_EIP, UC_X86_REG_ESI, UC_X86_REG_ESP,
                               UC_X86_REG_EBX)

from .machine import CALLEE_SCRATCH, Machine, RET_MAGIC

# ---- 锚点（oracle-entry.md / ecl-vm.md §5；均经本仓导出或直读验证）----
MGR_CREATE      = 0x418D30   # 敌机管理器创建（cdecl [esp+4]=ECL 名）
SPAWN           = 0x4183F0   # 建敌（cdecl p1=名字 p2=参数块；返回敌机指针）
MGR_UPDATE      = 0x418E00   # 逐帧全量更新（EDI=ctl）
DISPATCH        = 0x41B260   # ECL 游戏指令分发（ECX=vm=enemy+0x11EC）
RES_INT         = 0x478E60   # 整型取参（EDX=thread，[esp+4]=index；EAX=值）
RES_FLOAT       = 0x478EE0   # 浮点取参（EAX=thread，ECX=index；ST0=值）
ET_ON_BATCH     = 0x40FFE0   # etOn 批发射（EAX=et 槽状态）
BULLET_SPAWN    = 0x40E840   # 逐弹出生（cdecl root/et/idx/way + ST0 aim）
NOOP_BLOCK      = 0x41F738   # 分发默认 no-op 块
KILLALL_BLOCK   = 0x41E3FD   # 525 enmKillAll 处理块（518 共享尾块）
BYTE_TABLE      = 0x41F9D8   # opcode-300 → 密集 case
JUMP_TABLE      = 0x41F760   # 密集 case → 处理块
VT_DISPATCH     = 0x41B250

MALLOC          = 0x47A0ED
FREE            = 0x47A181
GETPTD          = 0x47FE5A
OPEN_BY_NAME    = 0x4724C0
SOUND_PLAY      = 0x460C50
DEATH_RING      = 0x46F820

DAT_ROOT        = 0x004C2174   # 根对象指针（+0x9752b4 弹幕池）
DAT_GAMECTX     = 0x004C2180   # 游戏上下文（+0x10 被 0x4187d0 读）
DAT_MGR         = 0x004C2188   # 敌机 ctl
DAT_PLAYER      = 0x004C22C4   # 玩家对象（+0x5b8=x, +0x5bc=y）
DAT_DIFF        = 0x004BE7C4   # 难度（Lunatic=3）
DAT_RANK        = 0x004BE7CC   # RANK 初值 3
DAT_SPELL       = 0x004BE7D4   # SPELL_ID = -1
DAT_ANM_MGR     = 0x004DC688   # ANM mgr（0x418990 ANIM 登记需要非零）
DAT_ANM_FLAGS   = 0x004DCC5C   # & 0x180 → ANM 等待环立即可破
RNG_OBJ         = 0x004DC324   # GameRandom 状态 {u16 seed; u16 pad; u32 count}

ROOT_SIZE       = 0x9752B8
BULLET_NODE     = 0x00001074   # [root+0x74]：弹幕池取弹节点（复用单节点）
ANM_MGR_SIZE    = 0x0F53C48

FRAMES          = 3600

# enemy_move 事件集与整型参数位（event-contract.md §4.2 / StageCommands.cpp emit_move）
MOVE_SIGNATURES = {
    400: (0, 0), 402: (0, 0),
    401: (1, 1, 0, 0), 403: (1, 1, 0, 0),
    404: (0, 0), 406: (0, 0), 428: (0, 0), 430: (0, 0),
    405: (1, 1, 0, 0), 407: (1, 1, 0, 0), 429: (1, 1, 0, 0), 431: (1, 1, 0, 0),
    408: (0, 0, 0, 0), 410: (0, 0, 0, 0),
    409: (1, 1, 0, 0, 0, 0), 411: (1, 1, 0, 0, 0, 0),
    412: (1, 1, 0),
    425: (1, 0, 0, 0, 0, 0, 0),
    432: (1,), 433: (1,),
}

# 移植侧 no-op 指令集（StageCommands.cpp 直接 return 0 的行为空转族）。
NOOP_OPS = (
    [302, 303, 306, 307, 308, 313, 316, 319, 554]
    + [513, 515, 516, 517, 521, 523, 524, 527, 540, 542, 545, 557, 559, 560, 562, 638]
    + [608, 609, 610, 611, 612, 615, 616, 629, 632]
    + list(range(700, 712)) + [713, 714]
    + [1000, 1001, 1002, 1003]
)

SPAWN_RET        = CALLEE_SCRATCH + 0x200   # 0x4183f0 返回哨兵
BULLET_RET       = CALLEE_SCRATCH + 0x300   # 0x40e840 返回哨兵
# 浮点取参转换壳需 call 0x478ee0（rel32），必须落在镜像 ±2GB 内的低地址洞：
CAVE_CODE        = 0x00100000
FLOAT_STUB       = CAVE_CODE + 0x000        # call 0x478ee0; fstp→eax 转换壳

GAME_BUMP = 0x02000000   # 游戏 malloc 的 16 对齐 bump 区（HEAP 中段）


def f32(bits_or_float):
    if isinstance(bits_or_float, (bytes, bytearray)):
        return struct.unpack("<f", bytes(bits_or_float))[0]
    if isinstance(bits_or_float, int):
        return struct.unpack("<f", struct.pack("<I", bits_or_float & 0xFFFFFFFF))[0]
    return struct.unpack("<f", struct.pack("<f", bits_or_float))[0]


def fmt_f(value):
    """契约 §5：binary32 以 %.9g（float32 往返无损）序列化。"""
    return "%.9g" % value


def c_int_arg(value):
    """整型参数出口：RES_INT（0x478e60）返回的已是整数值，直通；
    浮点入参（RES_FLOAT 的 float 值）先舍入 binary32 再向零截断，
    溢出按 x86 cvttss2si 语义回 0x80000000。"""
    if isinstance(value, int):
        return value
    x = f32(value)
    if x != x or x in (float("inf"), float("-inf")) or x >= 2 ** 31 or x < -(2 ** 31):
        return -0x80000000
    return int(x)


class EventSink:
    """JSONL 落盘，键序与 StageEvent.cpp 逐字节一致。"""

    def __init__(self, path):
        self.out = open(path, "wb")
        self.frame = 0
        self.seq = 0
        self.count = 0
        self.spawns = 0
        self.moves = 0
        self.bullets = 0

    def set_frame(self, frame):
        self.frame = frame
        self.seq = 0

    def _head(self, event_type):
        self.count += 1
        return b'{"frame":%d,"seq":%d,"type":"%s"' % (
            self.frame, self.seq, event_type.encode("ascii"))

    def _bump_seq(self):
        self.seq += 1

    def enemy_spawn(self, enemy, routine, x, y, z, life, parent):
        line = (self._head("enemy_spawn")
                + b',"enemy":%d,"routine":"%s","x":%s,"y":%s,"z":%s,"life":%s,"parent":%d}'
                % (enemy, routine.encode("ascii"), fmt_f(x).encode(), fmt_f(y).encode(),
                   fmt_f(z).encode(), fmt_f(life).encode(), parent))
        self._bump_seq()
        self.spawns += 1
        self.out.write(line + b"\n")

    def enemy_move(self, enemy, op, args):
        body = b",".join(
            b"%d" % c_int_arg(v) if integral else fmt_f(f32(v)).encode()
            for v, integral in args)
        line = (self._head("enemy_move")
                + b',"enemy":%d,"op":%d,"args":[%s]}' % (enemy, op, body))
        self._bump_seq()
        self.moves += 1
        self.out.write(line + b"\n")

    def bullet_spawn(self, enemy, slot, way, idx, x, y, z, angle, speed, sprite,
                     subtype):
        line = (self._head("bullet_spawn")
                + b',"enemy":%d,"slot":%d,"way":%d,"idx":%d,"x":%s,"y":%s,"z":%s,'
                   b'"angle":%s,"speed":%s,"sprite":%d,"subtype":%d}'
                % (enemy, slot, way, idx, fmt_f(x).encode(), fmt_f(y).encode(),
                   fmt_f(z).encode(), fmt_f(angle).encode(), fmt_f(speed).encode(),
                   sprite, subtype))
        self._bump_seq()
        self.bullets += 1
        self.out.write(line + b"\n")

    def close(self):
        self.out.close()


class Stage1Oracle:
    def __init__(self, exe_path, raw_dir, out_path):
        self.raw_dir = raw_dir
        self.m = Machine(exe_path)
        self.uc = self.m.uc
        self.sink = EventSink(out_path)
        self.out_path = out_path
        self.frame = 0
        self.enemy_serial = {}       # enemy ptr -> serial
        self.spawn_stack = []        # 构造中敌机的 (serial, orig_ret)
        self.cur_enemy = 0           # 最近一次 0x41b260 的敌机指针
        self.cur_thread = 0
        self.cur_slot_of_et = {}     # et 指针 -> (enemy, slot)
        self.bullet_ctx = []         # 0x40e840 待回收 (node, et, way, idx)
        self.probe_request = None    # (thread, q, op) 待探参的 move 指令
        self.probe_resume = False    # 探参后续跑标记（分发观察跳过一次）
        self.resume_addr = 0
        self.bump = GAME_BUMP
        self.ptd = 0
        self.stats = {"mallocs": 0, "malloc_bytes": 0, "seam": 0}
        self.trace = []
        self.trace_on = False

    def enable_trace(self):
        """基本块尾迹（诊断用；正常跑关闭）。"""
        self.trace_on = True
        trace = self.trace

        game_last = []

        def cb(uc, addr, size, ud):
            trace.append(addr)
            if len(trace) > 600:
                del trace[:300]
            if 0x401000 <= addr < 0x490000:
                game_last.append(addr)
                if len(game_last) > 40:
                    del game_last[:20]
        self.uc.hook_add(UC_HOOK_BLOCK, cb)

        def unmapped(uc, access, address, size, value, ud):
            sys.stderr.write("UNMAPPED access=%d address=0x%08x size=%d EIP=0x%08x\n"
                             % (access, address, size, uc.reg_read(UC_X86_REG_EIP)))
            sys.stderr.write("game blocks: %s\n" % " ".join(
                "0x%08x" % a for a in game_last[-24:]))
            return False
        self.uc.hook_add(UC_HOOK_MEM_UNMAPPED, unmapped)

    # ---------- 底层工具 ----------
    def rd32(self, addr):
        return struct.unpack("<I", bytes(self.uc.mem_read(addr, 4)))[0]

    def wr32(self, addr, value):
        self.uc.mem_write(addr, struct.pack("<I", value & 0xFFFFFFFF))

    def rd16(self, addr):
        return struct.unpack("<H", bytes(self.uc.mem_read(addr, 2)))[0]

    def cstr(self, addr, limit=256):
        out = bytearray()
        while len(out) < limit:
            chunk = bytes(self.uc.mem_read(addr + len(out), 32))
            if b"\x00" in chunk:
                out += chunk[:chunk.index(b"\x00")]
                break
            out += chunk
        return out.decode("ascii", "replace")

    def game_malloc(self, size):
        ptr = self.bump
        self.bump = (self.bump + max(size, 1) + 15) & ~15
        self.stats["mallocs"] += 1
        self.stats["malloc_bytes"] += max(size, 1)
        return ptr

    def save_regs(self):
        uc = self.uc
        return {r: uc.reg_read(r) for r in
                (UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX, UC_X86_REG_EDX,
                 UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP, UC_X86_REG_ESP,
                 UC_X86_REG_EFLAGS)}

    def restore_regs(self, saved):
        for reg, value in saved.items():
            self.uc.reg_write(reg, value)

    # ---------- 桩与全局 ----------
    def install_overrides(self):
        m = self.m
        # CRT 线程数据块：见 init_ptd()（run() 里预跑真实 __initptd 后才挂覆写）。
        # ___fls_getvalue@4（0x47fc7f）：TLS 无值的坍缩路径——原版会在 TlsGetValue
        # 返回 0 后 call eax 崩落；返回 0 走"无 FLS"分支。
        m.override_call(0x0047FC7F, lambda machine: 0, ret_pop=4)
        # __mtinitlocknum（0x485e0f）：CRT 锁表懒初始化——真实路径在无内核对象的
        # 环境里自递归不收敛（栈溢出）；锁本体全为导入桩空转，初始化直接报成功。
        m.override_call(0x00485E0F, lambda machine: 1)

        # 堆：CRT 堆未初始化，_malloc 从 bump 区分配；_free 全空转（oracle-entry §三.2）。
        def do_malloc(machine):
            size = self.rd32(machine.uc.reg_read(UC_X86_REG_ESP) + 4)
            return self.game_malloc(size)
        m.override_call(MALLOC, do_malloc)

        m.override_call(FREE, lambda machine: 0)

        # 资源缝：按名返回 raw dump（oracle-entry §四.3）。0x4724c0 为被调方清栈
        # （0x46dcf0 调用后无 add esp——ret 0xC），覆写须带 ret_pop=12。
        def seam(machine):
            esp = machine.uc.reg_read(UC_X86_REG_ESP)
            name = self.cstr(self.rd32(esp + 4))
            size_ptr = self.rd32(esp + 8)
            path = os.path.join(self.raw_dir, name)
            if not os.path.isfile(path):
                sys.stderr.write("seam miss: %s\n" % name)
                return 0
            data = open(path, "rb").read()
            buf = self.game_malloc(len(data))
            machine.uc.mem_write(buf, data)
            if size_ptr:
                self.wr32(size_ptr, len(data))
            self.stats["seam"] += 1
            return buf
        m.override_call(OPEN_BY_NAME, seam, ret_pop=12)

        # 音效/死亡环弹：渲染与音频栈缺席（561 die 的 FUN_00460c50/0x46f820；
        # 移植侧同族均无事件，StageCommands.cpp case 561/516）。0x460c50 为
        # __fastcall(EDI=id, 栈=pan)——全部出口 ret 4。
        m.override_call(SOUND_PLAY, lambda machine: 0, ret_pop=4)
        # 0x46f820 死亡炸裂环弹：0x41b437/0x41aad0 均以 6 参调用（ret 0x18）。
        m.override_call(DEATH_RING, lambda machine: 0, ret_pop=24)
        # 0x419a90 死亡/掉落/结算路径：仅服务被击杀敌机（stage1 玩家被动，无击杀；
        # 移植侧亦无此路径）。它牵一串掉落/符卡管理器，直接空转（ret 4 单栈参）。
        m.override_call(0x00419A90, lambda machine: 0, ret_pop=4)
        # 弹体视觉 ANM 挂接（0x40e840 尾部）：0x461cb0 按弹种建 ANM、0x462450 步进
        # 弹体 ANM——合成 ANM mgr 上无实数据，空转（事件无涉，移植侧无此路径）。
        m.override_call(0x00461CB0, lambda machine: 0)
        m.override_call(0x00462450, lambda machine: 0)

        # EncodePointer/DecodePointer：CRT（0x47fc6d 封装）把结果当函数指针存，
        # 返回 slot+8 处的一个可调用 ret 桩（0x47a1e1 operator new 路径需要）。
        for slot, imp in list(m.import_stubs.items()):
            if imp.name.lower() in ("encodepointer", "decodepointer"):
                self.uc.mem_write(slot + 8, b"\xC3")
                m.stub(imp.name, (lambda s: (lambda machine: s + 8))(slot))

        # HeapAlloc：__malloc_crt（CRT 锁表初始化等启动分配）不经 _malloc 而直呼
        # HeapAlloc；返回 bump 指针使其成功，锁初始化循环才肯收敛。
        # stdcall (hHeap, dwFlags, dwBytes) → [esp+0xC]=size。
        def heap_alloc(machine):
            esp = machine.uc.reg_read(UC_X86_REG_ESP)
            size = self.rd32(esp + 0xC)
            return self.game_malloc(size)
        m.stub("HeapAlloc", heap_alloc, args_pop=12)
        m.stub("HeapFree", lambda machine: 1, args_pop=12)
        # InitializeCriticalSectionAndSpinCount：锁初始化成败开关——返回 0 会让
        # __mtinitlocknum 永久重试（栈溢出）；返回 1 锁表一次收敛。
        m.stub("InitializeCriticalSectionAndSpinCount", lambda machine: 1, args_pop=8)
        m.stub("InitializeCriticalSection", lambda machine: 1, args_pop=4)

    def patch_globals(self):
        # 会话常量（契约 §2）。
        self.wr32(DAT_DIFF, 3)
        self.wr32(DAT_RANK, 3)
        self.wr32(DAT_SPELL, 0xFFFFFFFF)
        # 玩家 (192, 420)：0x443f60 瞄准与 0x40e840 近距剔除都经 DAT_004c22c4。
        player = self.game_malloc(0x1000)
        self.uc.mem_write(player, b"\x00" * 0x1000)
        self.uc.mem_write(player + 0x5B8, struct.pack("<f", 192.0))
        self.uc.mem_write(player + 0x5BC, struct.pack("<f", 420.0))
        self.wr32(DAT_PLAYER, player)
        # 根对象占位：0x4187d0 读 +0x9752b4（弹幕池）；0x40e840 读 +0x74（取弹节点）
        # 与 +0x60（近距剔除半径，0=关，对齐移植侧“剔除未实现”）。
        root = self.game_malloc(ROOT_SIZE)
        self.uc.mem_write(root, b"\x00" * ROOT_SIZE)
        self.wr32(DAT_ROOT, root)
        node = self.game_malloc(0x2000)
        self.uc.mem_write(node, b"\x00" * 0x2000)
        self.wr32(root + 0x74, node)
        # 游戏上下文占位（0x4187d0 读 +0x10）。
        ctx = self.game_malloc(0x100)
        self.uc.mem_write(ctx, b"\x00" * 0x100)
        self.wr32(DAT_GAMECTX, ctx)
        # ANM mgr 合成（0x418990 的 ANIM 登记路径读 DAT_004dc688；全零缓冲 +
        # 0x180 标志立即可破等待环）。mgr 缺席时 0x46fb90 对空槽直接返回 0。
        anm = self.game_malloc(ANM_MGR_SIZE)
        self.uc.mem_write(anm, b"\x00" * ANM_MGR_SIZE)
        self.wr32(DAT_ANM_MGR, anm)
        self.wr32(DAT_ANM_FLAGS, self.rd32(DAT_ANM_FLAGS) | 0x180)
        # GameRandom：契约 §2 种子 0（EXE 镜像初值即 0，此处显式固定）。
        self.uc.mem_write(RNG_OBJ, b"\x00" * 8)
        # CRT 堆句柄占位：__mtinitlocknum/_malloc 的“CRT 未初始化”检查读它，
        # 非零即过；值只流入 Heap* 导入桩（恒返 0，不解引用）。
        self.wr32(0x004C1D40, 1)
        # 任务链调度器占位（DAT_004dc658）：0x4187d0 注册 TaskInf 时经 0x470990/
        # 0x470a40 读写 [obj+0x14]（运行链锚）与 [obj+0x3C]（关闭链锚）；
        # 全零块即空链（挂接分支自洽）。
        scheduler = self.game_malloc(0x60)
        self.uc.mem_write(scheduler, b"\x00" * 0x60)
        self.wr32(0x004DC658, scheduler)
        # 结算/血条显示对象占位（DAT_004c2190）：0x419a90 掉落路径读 +0x198，
        # 527 lifeMarker 写 +0x1BC+id*8——全零块走"无符卡/无显示"分支。
        scoreboard = self.game_malloc(0x400)
        self.uc.mem_write(scoreboard, b"\x00" * 0x400)
        self.wr32(0x004C2190, scoreboard)
        # 分数/结算对象占位（DAT_004c2178，0x419c60 读 +0x7c/+0x78）与
        # 游戏上下文门（DAT_004c2194，0x418ef0 更新回调的门控位，0=关）。
        score_ctx = self.game_malloc(0x200)
        self.uc.mem_write(score_ctx, b"\x00" * 0x200)
        self.wr32(0x004C2178, score_ctx)
        game_ctx = self.game_malloc(0x100)
        self.uc.mem_write(game_ctx, b"\x00" * 0x100)
        self.wr32(0x004C2194, game_ctx)

    def patch_dispatch_table(self):
        """把移植侧 no-op 指令族的跳表项改到默认 no-op 块；518 定向 525 尾块。"""
        patched = 0
        for op in NOOP_OPS:
            dense = bytes(self.uc.mem_read(BYTE_TABLE + (op - 300), 1))[0]
            addr = JUMP_TABLE + dense * 4
            if self.rd32(addr) != NOOP_BLOCK:
                self.wr32(addr, NOOP_BLOCK)
                patched += 1
        dense = bytes(self.uc.mem_read(BYTE_TABLE + (518 - 300), 1))[0]
        self.wr32(JUMP_TABLE + dense * 4, KILLALL_BLOCK)
        return patched

    # ---------- 观察钩子 ----------
    def install_hooks(self):
        uc = self.uc

        # 0x41b260 入口：当前敌机登记 + move 指令停机探参。
        def on_dispatch(machine):
            if self.probe_resume:
                # 探参后续跑同一指令：跳过本次分发观察，避免无限探参环。
                self.probe_resume = False
                vm = uc.reg_read(UC_X86_REG_ECX)
                self.cur_enemy = vm - 0x11EC
                self.cur_thread = self.rd32(self.rd32(vm + 0x40C8) + 4)
                return
            vm = uc.reg_read(UC_X86_REG_ECX)
            enemy = vm - 0x11EC
            self.cur_enemy = enemy
            thread = self.rd32(self.rd32(vm + 0x40C8) + 4)
            self.cur_thread = thread
            q = self.rd32(thread + 4)
            op = self.rd16(q + 4)
            if op in MOVE_SIGNATURES:
                self.probe_request = (thread, q, op)
                uc.emu_stop()
        self.m.watch(DISPATCH, on_dispatch)

        # 0x4183f0 入口/返回：enemy_spawn 事件 + 敌机序号登记。
        def on_spawn(machine):
            esp = uc.reg_read(UC_X86_REG_ESP)
            name_ptr = self.rd32(esp + 4)
            param2 = self.rd32(esp + 8)
            serial = self.rd32(self.rd32(DAT_MGR) + 0xBC)
            parent = self.serial_of(self.cur_enemy)
            if parent is not None and self.cur_enemy:
                x = f32(bytes(uc.mem_read(param2, 4)))
                y = f32(bytes(uc.mem_read(param2 + 4, 4)))
                z = f32(bytes(uc.mem_read(param2 + 8, 4)))
                # life：0x41fcc0 的 FUN_00417500(ctx,3)（postname 第 2 参）落在
                # param2[5]（+0x14；实测 MapleEnemy {0,0,0,1000,0,100,…}）——按
                # int 位模式转 f32。
                life = f32(float(struct.unpack("<i", bytes(uc.mem_read(param2 + 20, 4)))[0]))
                self.sink.enemy_spawn(serial, self.cstr(name_ptr), x, y, z, life,
                                      parent)
            orig_ret = self.rd32(esp)
            self.wr32(esp, SPAWN_RET)
            self.spawn_stack.append((serial, orig_ret))
        self.m.watch(SPAWN, on_spawn)

        def on_spawn_ret(machine):
            enemy = uc.reg_read(UC_X86_REG_EAX)
            if self.spawn_stack:
                serial, orig_ret = self.spawn_stack.pop()
                self.enemy_serial[enemy] = self.rd32(enemy + 0x11E4)
                uc.reg_write(UC_X86_REG_EIP, orig_ret)
        self.m.watch(SPAWN_RET, on_spawn_ret)

        # 0x40ffe0 入口：登记 et 槽（enemy, slot）。
        def on_et_batch(machine):
            et = uc.reg_read(UC_X86_REG_EAX)
            vm = self.cur_enemy + 0x11EC
            offset = et - (vm + 0x15C)
            if 0 <= offset < 8 * 0xE0 and offset % 0xE0 == 0:
                self.cur_slot_of_et[et] = (self.serial_of(self.cur_enemy),
                                           offset // 0xE0)
        self.m.watch(ET_ON_BATCH, on_et_batch)

        # 0x40e840 入口：换返回地址，返回点读弹幕节点落值。
        def on_bullet(machine):
            esp = uc.reg_read(UC_X86_REG_ESP)
            root = self.rd32(esp + 4)
            et = self.rd32(esp + 8)
            idx = self.rd32(esp + 0xC)
            way = self.rd32(esp + 0x10)
            node = self.rd32(root + 0x74)
            if node == 0:
                return  # 弹幕池空：原版即不产弹（契约 §4.3 count/ways 关闭同义）
            self.bullet_ctx.append((node, et, way, idx, self.rd32(esp)))
            self.wr32(esp, BULLET_RET)
        self.m.watch(BULLET_SPAWN, on_bullet)

        def on_bullet_ret(machine):
            node, et, way, idx, orig_ret = self.bullet_ctx.pop()
            def nf(off):
                return f32(bytes(uc.mem_read(node + off, 4)))
            enemy, slot = self.cur_slot_of_et.get(et,
                                                 (self.serial_of(self.cur_enemy), 0))
            self.sink.bullet_spawn(enemy, slot, way, idx, nf(0xB70), nf(0xB74),
                                   nf(0xB78), nf(0xB8C), nf(0xB88),
                                   self.rd32(et), self.rd32(et + 4))
            uc.reg_write(UC_X86_REG_EIP, orig_ret)
        self.m.watch(BULLET_RET, on_bullet_ret)

    def serial_of(self, enemy):
        if enemy in self.enemy_serial:
            return self.enemy_serial[enemy]
        if self.spawn_stack:
            return self.spawn_stack[-1][0]
        return None

    # ---------- 探参 ----------
    def setup_float_stub(self):
        """FLOAT_STUB: call 0x478ee0; sub esp,4; fstp dword [esp]; pop eax; ret。
        把 ST0 结果转成 EAX=f32 位。"""
        self.uc.mem_map(CAVE_CODE, 0x1000)
        rel = RES_FLOAT - (FLOAT_STUB + 5)
        code = (b"\xE8" + struct.pack("<i", rel)     # call RES_FLOAT
                + b"\x83\xEC\x04"                    # sub esp,4
                + b"\xD9\x1C\x24"                    # fstp dword [esp]
                + b"\x58"                            # pop eax
                + b"\xC3")
        self.uc.mem_write(FLOAT_STUB, code)

    def snapshot_rng(self):
        return (self.rd32(self.ptd + 0x14), self.rd32(RNG_OBJ),
                self.rd32(RNG_OBJ + 4))

    def restore_rng(self, snap):
        self.wr32(self.ptd + 0x14, snap[0])
        self.wr32(RNG_OBJ, snap[1])
        self.wr32(RNG_OBJ + 4, snap[2])

    def probe_move(self, thread, q, op):
        """在 0x41b260 入口处以同一状态探参（RNG 快照回滚，探完恢复寄存器）。
        count/mask 直读指令头（§1.2），int/float 划分按契约 §4.2。"""
        sig = MOVE_SIGNATURES[op]
        count = min(bytes(self.uc.mem_read(q + 0x0B, 1))[0], len(sig), 8)
        saved = self.save_regs()
        esp = self.uc.reg_read(UC_X86_REG_ESP)
        rng = self.snapshot_rng()
        args = []
        try:
            for i in range(count):
                if sig[i]:
                    value = self.m.call(RES_INT, args=[i],
                                        regs={"edx": thread, "ecx": op},
                                        probe_stack_top=esp - 0x8000,
                                        timeout_ms=2000)
                    args.append((value, True))
                else:
                    bits = self.m.call(FLOAT_STUB, args=[i],
                                       regs={"eax": thread, "ecx": i},
                                       probe_stack_top=esp - 0x8000,
                                       timeout_ms=2000)
                    args.append((f32(bits), False))
        finally:
            self.restore_rng(rng)
            self.restore_regs(saved)
        return args

    # ---------- 驱动 ----------
    def drive(self, address, args=(), regs=None, timeout_ms=120_000):
        """可续跑直调：emu_stop 由探参请求触发时，处理后续跑同地址。"""
        uc = self.uc
        esp = (0x002FF000 - 0x8000) & ~0xF
        uc.mem_write(esp, struct.pack("<I", RET_MAGIC))
        for i, a in enumerate(args):
            uc.mem_write(esp + 4 + i * 4, struct.pack("<I", a & 0xFFFFFFFF))
        uc.reg_write(UC_X86_REG_ESP, esp)
        uc.reg_write(UC_X86_REG_EBP, 0x002FF000)
        uc.reg_write(UC_X86_REG_EAX, 0)
        uc.reg_write(UC_X86_REG_ECX, 0)
        uc.reg_write(UC_X86_REG_EDX, 0)
        if regs:
            for name, value in regs.items():
                uc.reg_write(globals()[f"UC_X86_REG_{name.upper()}"],
                             value & 0xFFFFFFFF)
        self.resume_addr = address
        while True:
            try:
                uc.emu_start(self.resume_addr, RET_MAGIC,
                             timeout=timeout_ms * 1000)
            except UcError as exc:
                if self.trace_on:
                    sys.stderr.write("last blocks: %s\n" % " ".join(
                        "0x%08x" % a for a in self.trace[-24:]))
                self.fail("emulation error at 0x%08x: %s" % (
                    uc.reg_read(UC_X86_REG_EIP), exc))
            if self.probe_request is None:
                return uc.reg_read(UC_X86_REG_EAX)
            thread, q, op = self.probe_request
            self.probe_request = None
            args_resolved = self.probe_move(thread, q, op)
            enemy = self.serial_of(self.cur_enemy)
            self.sink.enemy_move(enemy, op, args_resolved)
            self.probe_resume = True
            self.resume_addr = DISPATCH  # 恢复寄存器后重入分发器
            # probe_move 内部已恢复 save_regs 的现场（含 ESP/EBP/ECX）。

    def fail(self, message):
        self.sink.close()
        sys.stderr.write("stage1-oracle FAIL frame=%d: %s\n" % (self.frame, message))
        raise SystemExit(1)

    def init_ptd(self):
        """预跑真实 __initptd(ptd, NULL)（0x47fd2d，cdecl (ptd, locale)）得到
        locale/rand/errno 全真的 ptd，再覆写 _getptd 恒返它。"""
        self.ptd = self.game_malloc(0x214)
        self.uc.mem_write(self.ptd, b"\x00" * 0x214)
        # __initptd 可能调用 _getptd 之外的 CRT 内部（calloc 等）——malloc 已桩好。
        self.m.call(0x0047FD2D, args=[self.ptd, 0], timeout_ms=10_000)
        self.m.override_call(GETPTD, lambda machine: self.ptd)

    # ---------- 主流程 ----------
    def run(self, frames=FRAMES):
        self.install_overrides()
        self.init_ptd()
        self.patch_globals()
        self.patch_dispatch_table()
        self.setup_float_stub()
        self.install_hooks()

        stage_name = getattr(self, "stage_name", b"st01.ecl")
        name_buf = self.game_malloc(0x100)
        self.uc.mem_write(name_buf, stage_name + b"\x00")
        ctl = self.m.call(MGR_CREATE, args=[name_buf], timeout_ms=30_000)
        if not ctl or self.rd32(DAT_MGR) != ctl:
            self.fail("enemy manager creation failed (ctl=0x%08x)" % ctl)
        self.ctl = ctl
        # 契约 §3.1：root 敌机 serial 固定 0；原版 0x418d30 将计数器置 1，此处对齐。
        self.wr32(ctl + 0xBC, 0)

        # 第 0 帧：stage 装载 + root（承载 main）构造内首趟（契约 §3.2）。
        self.sink.set_frame(0)
        param2 = self.game_malloc(0x54)
        self.uc.mem_write(param2, b"\x00" * 0x54)
        self.uc.mem_write(name_buf, b"main\x00")
        root = self.drive(SPAWN, args=[name_buf, param2], timeout_ms=60_000)
        if not root:
            self.fail("root enemy creation failed")

        t_frame = time.time()
        for frame in range(1, frames):
            self.sink.set_frame(frame)
            self.frame = frame
            self.drive(MGR_UPDATE, regs={"edi": self.ctl})
            if frame % 300 == 0:
                sys.stderr.write("frame %d events=%d (%.1fs)\n" % (
                    frame, self.sink.count, time.time() - t_frame))
                sys.stderr.flush()
                t_frame = time.time()

        self.sink.close()
        s = self.stats
        print("stage1-oracle frames=%d enemies=%d events=%d "
              "(spawn=%d move=%d bullet=%d) malloc=%d/%dKB seam=%d" % (
                  frames, len(self.enemy_serial), self.sink.count,
                  self.sink.spawns, self.sink.moves, self.sink.bullets,
                  s["mallocs"], s["malloc_bytes"] // 1024, s["seam"]))
        return 0


def main(argv):
    flags = {"--trace": False}
    positional = []
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == "--trace":
            flags["--trace"] = True
        elif arg == "--frames":
            i += 1
            flags["--frames"] = int(argv[i])
        elif arg == "--stage":
            i += 1
            flags["--stage"] = argv[i]
        else:
            positional.append(arg)
        i += 1
    exe = positional[0] if len(positional) > 0 else "games/th13/th13.exe"
    raw_dir = positional[1] if len(positional) > 1 else "artifacts/reference/raw"
    out = (positional[2] if len(positional) > 2 else
           "eagler-touhou/ports/th13/web/artifacts/wasi/stage1-oracle.jsonl")
    frames = flags.get("--frames", FRAMES)
    started = time.time()
    oracle = Stage1Oracle(exe, raw_dir, out)
    if "--stage" in flags:
        oracle.stage_name = flags["--stage"].encode("ascii")
    if flags["--trace"]:
        oracle.enable_trace()
    code = oracle.run(frames=frames)
    print("elapsed %.1fs -> %s" % (time.time() - started, out))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
