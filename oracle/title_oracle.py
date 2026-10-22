"""标题画面「真机驱动」绘制 oracle：调度器/场景状态机/装载线程体全真机代码。

与对局窗（render_oracle，直调 0x418e00+手工层循环）的区别：本窗按 WinMain
0x45c2f0 帧序驱动**真实调度器**（DAT_004dc658 双链）与**真实场景状态机**
（0x439ee0→0x43c1c0），标题的创建/装载/更新/绘制全部由原版代码自行完成，
oracle 只负责：帧序编排、键态注入、被桩线程（_beginthreadex 返 0）的线程体
补调、以及 FakeD3D 捕获。

真机启动链（全部由本 oracle 的真实调用复现，反编译锚点见各函数注释）：
  WinMain 0x45c110 LAB_0045c3d5:
    调度器构造（19 处字段写，含 +0x14/+0x3c 哨兵自链）
    Direct3DCreate9 → DAT_004dc6a4；0x45dc80 设备
    0x46bc60 ANM mgr → DAT_004dc688（mgr 构造内自注册 31 个调度器任务：
      2 个 update（0x46f330/0x46f360 ANM 池步进）+ 29 个 draw（逐层
      0x46f380 族 + 视口切换 0x46f000/0x46f090/0x46f0e0 + UP 刷 0x43c840））
    0x43a440 场景任务注册：update 0x439ee0(优先级1) + 10 个场景 draw 任务；
      注册时 0x470990 对 task+0xc 槽的一次性回调 = 0x43a040（全局初始化：
      0x43bbc0 gamestate、0x46c950 静态 VB、DAT_004dc85c..868 四个精灵栈）
    帧循环 0x45d2f0
  帧 0 调度器 walk（0x470af0）→ 场景机 case 0 → 0x439bc0 开场对象
    （_beginthreadex(0x439730) 桩返 0）→ oracle 补调线程体 0x439730：
    装 sig.anm(mgr 槽1)、thbgm.fmt、game_main(0x4038a0)、MsgInf(0x449b00)，
    尾部使能开场 update 任务
  帧 1+：开场 update 0x439e10 一次性块：使能 game_main 三任务、
    DAT_004dcc24=4 → 场景机 case 4（current=1 经 0x43c4a0 字节表）→
    0x44c4c0 TitleInf 创建（_beginthreadex(0x44c0d0) 桩返 0）→
    oracle 补调线程体首指令目标 0x44c180：注册标题 update 垫片 0x44cb60 /
    draw 垫片 0x44cb70（均禁止态）、0x46df40(EAX=16,"title.anm"→+0x14)、
    (EAX=17,"title_v.anm"→+0x18)
  开场 draw 0x439ce0 每帧使 开场+0x5e4 计数 +1；到 0xb4(300) 后 oracle 补调
    0x44c0d0 线程尾段（0x44c12e..0x44c176 的原指令序列）：卸载槽1、
    使能标题 update 任务（TitleInf+0xc 任务 |= 2）、DAT_004df120=1
  此后每帧：
    update walk 0x470af0(EBX=调度器)：
      0x439ee0（音乐 0x460dd0 / 输入轮询 0x471620 / ANM 装载推进 0x46e510 /
      场景机 0x43c1c0）→ 标题 update 垫片 → 0x44c570 子状态机
      （TitleInf+0x1c；DAT_004dc670 流程态；1800 帧无操作放 demo）→
      0x44cd10 菜单（DAT_004be7c4/004be818/004be81c 菜单索引），
      以及 ANM mgr 两 update 任务（主/副池步进）
    draw walk 0x470c30：0x43c4b0 清屏+视口 → 场景 draw 分发 →
      mgr 29 个逐层任务 → 标题 draw 垫片 0x44cb70→0x44cab0（子状态 8..0x13
      才有专绘，菜单态由 mgr 层绘制）→ UP 刷
  输入注入：GetKeyboardState 桩每帧把受控键态（按=0x80 高位）写入调用方
    256B 缓冲，位打包/边沿/连打（0x471620/0x4711a0/0x474fb0）全真机计算；
    joyGetPosEx 桩返 167（无摇杆）。

输入位布局（实测：注入单键一帧读回 DAT_004e49f0；打包由 0x471620 内联
真机代码完成，0x4711a0 仅摇杆合并、joyGetPosEx 桩 167 后原样返回）：
  bit0(0x01)=Z   bit4(0x10)=↑   bit5(0x20)=↓
  菜单确认掩码 DAT_004e49fc & 0x102（bit0=Z，bit8 另一键）——0x44cd10
  case 2 反编译；↑/↓ 边沿走 DAT_004e49fc(按 下 边沿)或 DAT_004e49f8
  (连打) 双通道。

装载期空帧处理：真机帧 0（开场场景创建当帧）ops=0——唯一前缀帧；
帧 1 起开场 draw（sig.anm logo）持续出 ops。窗口起点定为**首个 ops>0
的帧**（实测 prefix=1），导出帧号从 0 重编；移植侧按同规则对齐帧号。

输入序列（窗口相对帧号，常量 INPUT_SCRIPT 按真机全局帧号给出；
实测 prefix=1，窗口帧=全局帧-1）：
  真机行为观察（--observe 0 输入轮 + 探测轮实证）：
    全局 f1   场景 1→4，TitleInf 创建，sig.anm logo 开始绘制（ops=6）
    全局 f181 开场计数 +0x5e4 达 0xb4(180)，标题 update 任务使能
             （0x44c0d0 线程尾段语义），子状态 0x1c=1、菜单 intro 起
    全局 f372 菜单 intro（180 帧插值，+0x2b8=0xb4）完成，+0x24 1→2，
             菜单可交互——真机 boot 没有独立「按任意键」提示幕
             （DAT_004dc670=0 直进菜单；0x40 位仅 demo 路径置位，
             0x4be830 写入点实测仅 0x44c58d/0x44c800 两处）
    光标 = TitleInf+0x28；↓ 一次 0→2（idx1 无存档数据被跳过——真机
    scoreth13.dat 缺省行为）、再一次 2→3；Z 确认后 +0x24 2→4（菜单项
    子菜单展开）
  最终序列（全局帧常量）：
    f380 ↓ 一帧（光标 0→2）、f420 ↓ 一帧（2→3）、f470 Z 一帧
    （子菜单展开）——均在菜单就绪（f372）之后、600 帧窗内，留出
    静止段供两侧对拍。

用法（容器内 /work）：
    python3 -m oracle.title_oracle [exe] [raw_dir] [out_jsonl]
        [--frames N] [--observe]
默认 out：artifacts/oracle-draws/title-draws.jsonl（600 帧窗）。
门禁自证三件套：同参数两跑 sha256 逐字节一致 / 窗口内每帧 ops>0 /
总帧数=600。
"""
import json
import os
import struct
import sys
import time

from unicorn import UcError
from unicorn.x86_const import (UC_X86_REG_EAX, UC_X86_REG_EBP, UC_X86_REG_EBX,
                               UC_X86_REG_ECX,
                               UC_X86_REG_EDI, UC_X86_REG_EIP, UC_X86_REG_ESI,
                               UC_X86_REG_ESP)

from .machine import Machine, RET_MAGIC
from .render_oracle import (RenderOracle, DEV_INIT, ANM_MGR_CTOR,
                            UP_FLUSH, UP_CLEAR, FOG_FLUSH, GAMESTATE)
from .stage1_oracle import OPEN_BY_NAME, GETPTD, MALLOC, FREE

# ---- 标题链锚点（decomp 实证，见模块 docstring）----
SCENE_TASKS_REG = 0x0043A440   # 场景任务注册（内经 0x470990 触发 0x43a040）
SCHED           = 0x004DC658   # 调度器（BSS，WinMain 内联构造）
SCENE_MGR       = 0x004DC6A0   # 场景管理器（BSS；+0x580 当前场景/+0x584 下一）
FRAME_UPDATE    = 0x00470AF0   # update walk（EBX=调度器；任务回调约定 ECX=owner）
FRAME_DRAW_WALK = 0x00470C30   # draw walk（自查 DAT_004dc658）
ANM_OPEN        = 0x0046DF40   # ANM 开档（EAX=槽号；等待环受 DAT_004dcc5c&0x180）
ANM_LOAD_TICK   = 0x0046E510   # ANM 装载推进（每槽 1 entry/趟）
OPENING_THREAD  = 0x00439730   # 开场装载线程体（桩 _beginthreadex 后补调）
TITLE_THREAD_IN = 0x0044C180   # 标题装载线程体首指令目标（0x44c0d0 首 call）
TITLE_OBJ       = 0x004C22E0   # TitleInf 指针
OPENING_OBJ     = 0x004C22A8   # 开场对象指针
OPENING_WAIT_N  = 0xB4         # 标题线程等待开场 +0x5e4 ≥ 300（0x44c0d0）
ANM_ENTRY_FREE  = 0x0046E620   # 槽卸载（EDI=file；0x44c12e 线程尾段）
CRT_FREE        = 0x0047A261   # free（cdecl 1 参）
SETVIEWPORT_HELPER = 0x0043B900  # 帧首视口预置（EDI=0x4dc9d0）
SOUND_MGR_FLAGS = 0x004DCC5C   # DAT_ANM_FLAGS 同址：|=0x180 → 装载等待环直破
KEYBOARD_ON     = 0x004DD0B4   # 键盘轮询开关（0x45da40 窗口创建时置 1）
INPUT_CUR       = 0x004E49F0   # 当帧输入 u32（0x471620 写入）
# 键（VK 码）：Z=0x5A ↓=0x28
VK_Z, VK_DOWN = 0x5A, 0x28

# 600 帧窗输入序列（真机全局帧 → 按下的 VK 集合；实测锚点见上文档）：
#   f380 ↓（光标 0→2）、f420 ↓（2→3）、f470 Z（子菜单展开 +0x24 2→4）。
# 依据：菜单在全局 f372 起可交互（intro 180 帧插值完成），三次按键均
# 在其后且留有前后静止段；实测见 docstring 与提交说明。
INPUT_SCRIPT = {
    380: frozenset({VK_DOWN}),
    420: frozenset({VK_DOWN}),
    470: frozenset({VK_Z}),
}
WINDOW = 600

# 模块级登记：输入位布局实测结论已写入模块 docstring（Z=0x01、
# ↑=0x10、↓=0x20，确认掩码 0x102）。


class TitleOracle(RenderOracle):
    def __init__(self, exe_path, raw_dir, out_path):
        super().__init__(exe_path, raw_dir, out_path)
        self.sched = 0
        self.frame_keys = set()       # 当帧按下的 VK（GetKeyboardState 桩用）
        self.phase = "init"
        self.opening_init_done = False
        self.title_load_done = False
        self.title_enabled = False
        self.inserts = []             # (帧, 池, 脚本号, 层) —— 蓝图黄金证据
        self.flags_writes = []        # 0x4be830 写入点（帧, EIP, 新值）
        self.observe = False
        self.func_names = {}
        path = "artifacts/decomp/jp/functions.json"
        if os.path.isfile(path):
            for fn in json.load(open(path))["functions"]:
                self.func_names[int(fn["address"], 16)] = fn["name"]

    # ---------- 环境搭建 ----------
    def install_overrides(self):
        super().install_overrides()
        m = self.m

        # 键盘态注入：0x471620 → GetKeyboardState(buf)。按=0x80 高位字节。
        def on_getkeyboardstate(mm):
            esp = mm.uc.reg_read(UC_X86_REG_ESP)
            buf = self.rd32(esp + 4)
            state = bytearray(256)
            for vk in self.frame_keys:
                state[vk] = 0x80
            mm.uc.mem_write(buf, bytes(state))
            return 1
        m.stub("GetKeyboardState", on_getkeyboardstate, args_pop=4)

        # 无摇杆：joyGetPosEx 返非 0 → 0x4711a0 原样返回键盘打包位。
        m.stub("joyGetPosEx", lambda mm: 167, args_pop=8)

        # GetModuleFileNameA：0x4584c0（CRT 文件打开包装）对相对路径取
        # EXE 目录（strrchr('\\')）；桩不写缓冲会让 strrchr 返 0 →
        # [0+1] 写崩。写真路径让目录逻辑走通（后续 CreateFileA 桩失败、
        # 文件按不存在处理，与 thbgm.dat 缺失的真实降级一致）。
        def on_getmodulefilenamea(mm):
            esp = mm.uc.reg_read(UC_X86_REG_ESP)
            buf = self.rd32(esp + 8)      # (hModule, lpFilename, nSize)
            path = b"C:\\th13\\th13.exe"
            mm.uc.mem_write(buf, path + b"\0")
            return len(path)
        m.stub("GetModuleFileNameA", on_getmodulefilenamea, args_pop=12)

        # 0x459e00 影像像素格式探针（CreateDIBSection/GetDIBits 链，仅
        # AVI 影像回放路径使用；桩返回 0=格式不支持，0x45ac90 两条探针
        # 均跳过、仅落 CreateFontA 字体句柄——标题窗无影像回放）。
        # ret_pop=4：原函数 ret 4（stdcall 弹参），不弹会泄漏栈。
        self.m.override_call(0x00459E00, lambda mm: 0, ret_pop=4)

        # _beginthreadex（CRT 0x47baf0，非导入、machine 桩不覆盖）：真实
        # 执行会走 CRT 线程挂接初始化（FLS/TLS/初始化器表），在桩环境
        # 必崩。桩返 0（=建线程失败语义，句柄 0 的等待/释放均安全 no-op），
        # 被桩线程的线程体由本 oracle 按真机时序手动补调（见 thread_bodies，
        # 与 stage1/render_oracle 对 ANM 装载线程的处理同源）。cdecl 调用
        # 方自清栈（0x44c4c0: call; add esp,0x18），ret_pop=0。
        self.m.override_call(0x0047BAF0, lambda mm: 0)

        # 0x47fde1（CRT get-or-create ptd，FLS 版）：本环境未跑 CRT 启动，
        # [0x4c1d64] 仍是构建期 EncodePointer 密文（0xff5440cf），真实执行
        # 会 DecodePointer 出桩地址再 call → 崩。与 render_oracle 对
        # _getptd(0x47fe5a) 的 override 同源同理由：ptd 已由 init_ptd 用
        # 真实初始化器 0x47fd2d 构好，这里直接返回。
        self.m.override_call(0x0047FDE1, lambda mm: self.ptd)

        # OPEN_BY_NAME 兜底 basename：开场装载请求带 "../../bgm/thbgm.fmt"
        # 目录前缀，按名喂料先试原名再试 basename（raw/ 平铺）。
        def seam(machine):
            esp = machine.uc.reg_read(UC_X86_REG_ESP)
            name = self.cstr(self.rd32(esp + 4)).decode("ascii", "replace")
            size_ptr = self.rd32(esp + 8)
            data = None
            for cand in (name, os.path.basename(name)):
                path = os.path.join(self.raw_dir, cand)
                if os.path.isfile(path):
                    data = open(path, "rb").read()
                    break
            if data is None:
                sys.stderr.write("seam miss: %s\n" % name)
                return 0
            buf = self.game_malloc(len(data))
            machine.uc.mem_write(buf, data)
            if size_ptr:
                self.wr32(size_ptr, len(data))
            self.stats["seam"] += 1
            return buf
        m.override_call(OPEN_BY_NAME, seam, ret_pop=12)

    def patch_globals_title(self):
        """标题窗全局：调度器按 WinMain 内联构造（0x45c110 LAB_0045c3d5 的
        19 处字段写），其余保持 BSS 零（真机 boot 时难度/菜单索引均 0，
        player/root 不预置——真机在玩家选择/开局才建）。"""
        self.wr32(0x004C1D40, 1)
        self.uc.mem_write(0x004DC324, b"\x00" * 8)          # RNG 对象
        self.wr32(KEYBOARD_ON, 1)   # 0x45da40 窗口创建的键盘开关效应
        sched = self.game_malloc(0x58)
        self.uc.mem_write(sched, b"\x00" * 0x58)
        s = sched
        self.wr32(s + 0x00, 0)
        self.wr32(s + 0x04, self.rd32(s + 0x04) & 0xFFFFFFFE)
        self.wr32(s + 0x08, 0)
        self.wr32(s + 0x0C, 0)
        self.wr32(s + 0x10, 0)
        self.wr32(s + 0x14, s)      # update 哨兵自链（sched[5]=sched）
        self.wr32(s + 0x18, 0)
        self.wr32(s + 0x1C, 0)
        self.wr32(s + 0x20, 0)
        self.wr32(s + 0x2C, self.rd32(s + 0x2C) & 0xFFFFFFFE)
        self.wr32(s + 0x30, 0)
        self.wr32(s + 0x34, 0)
        self.wr32(s + 0x38, 0)
        self.wr32(s + 0x28, 0)
        self.wr32(s + 0x3C, s)      # draw 哨兵自链（sched[0xf]=sched+0x3c）
        self.wr32(s + 0x40, 0)
        self.wr32(s + 0x44, 0)
        self.wr32(s + 0x48, 0)
        self.wr32(s + 0x54, 0)
        self.wr32(SCHED, sched)
        self.sched = sched
        # ANM 装载等待环直破（与对局窗同策略）：0x46df40 立即返回装载中，
        # 装载推进由 oracle 在驱动间隙用 0x46e510 补完（见 load_finish）。
        self.wr32(SOUND_MGR_FLAGS, self.rd32(SOUND_MGR_FLAGS) | 0x180)

    def install_hooks(self):
        super().install_hooks()
        # VM 池插入观察（trace_spawn 同款锚点）：脚本号清单 = 蓝图黄金证据
        from unicorn.x86_const import UC_X86_REG_EDI

        def on_insert_main(machine):
            vm = machine.uc.reg_read(UC_X86_REG_EDI)
            if vm:
                self.inserts.append((self.frame, "main", self._vm_script(vm),
                                     self.rd32(vm + 0x24)))
        self.m.watch(0x0046F4A0, on_insert_main)

        def on_insert_fore(machine):
            vm = machine.uc.reg_read(UC_X86_REG_EDI)
            if vm:
                self.inserts.append((self.frame, "fore", self._vm_script(vm),
                                     self.rd32(vm + 0x24)))
        self.m.watch(0x0046F5F0, on_insert_fore)

        # 0x46f3f0 主池尾插（标题菜单 VM 的实际插入路径，EBX=VM——
        # trace_spawn 的 on_tail_insert 同款）；0x46f4a0 头插兜底。
        # （0x461bf0 绑定锚点弃用：装载期 244 模板预绑定全量命中，
        # 淹没真实 spawn。）
        def on_insert_tail(machine):
            vm = machine.uc.reg_read(UC_X86_REG_EBX)
            if vm:
                self.inserts.append((self.frame, "tail", self._vm_script(vm),
                                     self.rd32(vm + 0x24)))
        self.m.watch(0x0046F3F0, on_insert_tail)

        # 0x4be830（任意键/菜单边沿标志）写入点：运行期定位 setter
        from unicorn import UC_HOOK_MEM_WRITE

        def on_flags_write(uc, access, address, size, value, user):
            eip = uc.reg_read(UC_X86_REG_EIP)
            self.flags_writes.append(
                (self.frame, eip, value & 0xFFFFFFFF,
                 self.func_names.get(eip, "sub_%08x" % eip)))
        self.uc.hook_add(UC_HOOK_MEM_WRITE, on_flags_write,
                         begin=0x004BE830, end=0x004BE834)

    def _vm_script(self, vm):
        return struct.unpack("<H", bytes(self.uc.mem_read(vm + 0x4AA, 2)))[0]

    # ---------- 线程体补调（_beginthreadex 桩返 0 的替代驱动） ----------
    def thread_bodies(self):
        """每帧 0x470af0 之后：按真机线程时序补调被桩掉的线程体。"""
        if not self.opening_init_done and self.rd32(OPENING_OBJ):
            self.phase = "opening_init"
            if self.drive(OPENING_THREAD, timeout_ms=600_000) != 0:
                self.fail("opening loader thread body failed")
            self.load_finish()
            self.opening_init_done = True
        if not self.title_load_done and self.rd32(TITLE_OBJ):
            self.phase = "title_load"
            if self.drive(TITLE_THREAD_IN, timeout_ms=600_000) != 0:
                self.fail("title loader thread body failed")
            self.load_finish()
            self.title_load_done = True
        if (self.title_load_done and not self.title_enabled
                and self.rd32(OPENING_OBJ)):
            opening = self.rd32(OPENING_OBJ)
            if self.rd32(opening + 0x5E4) >= OPENING_WAIT_N:
                # 0x44c0d0 线程尾段（0x44c12e..0x44c176 原指令序列）：
                # 卸载槽1 sig.anm → 使能标题 update 任务 → DAT_004df120=1
                self.phase = "title_enable"
                mgr = self.rd32(0x004DC688)
                slot1 = self.rd32(mgr + 0xB77B34 + 1 * 4)
                if slot1:
                    self.drive(ANM_ENTRY_FREE, regs={"edi": slot1},
                               timeout_ms=60_000)
                    self.drive(CRT_FREE, args=[slot1], timeout_ms=10_000)
                    self.wr32(mgr + 0xB77B34 + 1 * 4, 0)
                task = self.rd32(self.rd32(TITLE_OBJ) + 0x0C)
                self.wr32(task + 4, self.rd32(task + 4) | 2)
                self.wr32(0x004DF120, 1)
                self.title_enabled = True

    # ---------- 帧驱动 ----------
    def title_frame(self, out):
        """一帧：0x45d2f0 真实帧序（update walk + draw walk + 捕获）。"""
        d3d = self.d3d
        self.d3d.frame_ops = []
        self.d3d.cur_layer = None
        # 0x45d2f0:30 帧首 UP 刷（ESI=mgr）→ 视口预置 → update walk
        self.drive(UP_FLUSH, regs={"esi": self.anm_mgr}, timeout_ms=60_000)
        self.wr32(0x004DCC18, GAMESTATE)
        self.drive(SETVIEWPORT_HELPER, regs={"edi": GAMESTATE},
                   timeout_ms=10_000)
        self.com.com_call(self.d3d.dev_slots[0xBC], d3d.dev_obj,
                          [GAMESTATE + 0xD8])                    # SetViewport
        self.wr32(0x004DCC1C, 1)
        self.phase = "update"
        ret = self.drive(FRAME_UPDATE, regs={"ebx": self.sched},
                         timeout_ms=120_000)
        if ret == 0 or ret == 0xFFFFFFFF:
            self.fail("update walk aborted ret=%d frame=%d scene=%d->%d "
                      "title_state=%d" % (
                          ret, self.frame, self.rd32(SCENE_MGR + 0x580),
                          self.rd32(SCENE_MGR + 0x584),
                          self.rd32(0x004DC670)))
        self.thread_bodies()
        # 0x45d2f0 绘制块（DAT_004dc890=0 → 每帧绘制）
        self.phase = "draw"
        self.com.com_call(self.d3d.dev_slots[0xA4], d3d.dev_obj)  # BeginScene
        self.drive(UP_CLEAR, timeout_ms=10_000)
        self.wr32(0x004DCF30, 0xFF)
        self.drive(FOG_FLUSH, regs={"edi": SCENE_MGR}, timeout_ms=10_000)
        self.drive(FRAME_DRAW_WALK, timeout_ms=120_000)
        self.drive(UP_FLUSH, regs={"esi": self.anm_mgr}, timeout_ms=60_000)
        self.com.com_call(self.d3d.dev_slots[0x104], d3d.dev_obj, [0, 0])
        self.com.com_call(self.d3d.dev_slots[0xA8], d3d.dev_obj)  # EndScene
        self.com.com_call(self.d3d.dev_slots[0x44], d3d.dev_obj,
                          [0, 0, 0, 0])                           # Present
        self.write_frame_line(out)

    # ---------- 观察 ----------
    def observe_line(self):
        def s32(addr):
            return struct.unpack("<i", bytes(self.uc.mem_read(addr, 4)))[0]
        parts = [self.phase, "scene=%d->%d" % (s32(SCENE_MGR + 0x580),
                                               s32(SCENE_MGR + 0x584)),
                 "flow670=%d" % s32(0x004DC670), "ops=%d" % self.per_frame_ops[-1],
                 "in=%08x" % (self.rd32(INPUT_CUR) & 0xFFFFFFFF)]
        t = self.rd32(TITLE_OBJ)
        if t:
            parts.append("tsub=%d/%d cur=%d" % (s32(t + 0x1C), s32(t + 0x24),
                                                s32(t + 0x28)))
            parts.append("fade5c40=%08x" % self.rd32(t + 0x5C40))
        op = self.rd32(OPENING_OBJ)
        if op:
            parts.append("opn5e4=%d" % s32(op + 0x5E4))
        parts.append("menu=%d/%d/%d" % (s32(0x004BE7C4), s32(0x004BE818),
                                        s32(0x004BE81C)))
        parts.append("flags830=%08x" % self.rd32(0x004BE830))
        parts.append("idle834=%d" % s32(0x004BE834))
        sys.stderr.write("f%d %s\n" % (self.frame, " ".join(parts)))

    # ---------- 主流程 ----------
    def run(self, frames=WINDOW, total_limit=None):
        self.install_overrides()
        self.init_ptd()
        self.install_d3d()
        self.patch_globals_title()
        self.install_hooks()

        # WinMain LAB_0045c3d5 装载序（真机代码）
        d3d9_slot = next(slot for slot, imp in self.m.import_stubs.items()
                         if imp.name.lower() == "direct3dcreate9")
        d3d9_obj = self.drive(d3d9_slot, args=[0x20], timeout_ms=10_000)
        self.wr32(0x004DC6A4, d3d9_obj)
        if self.drive(DEV_INIT, timeout_ms=30_000) != 0:
            self.fail("device init failed")
        buf = self.game_malloc(0xF53C48)
        self.uc.mem_write(buf, b"\x00" * 0xF53C48)
        self.drive(ANM_MGR_CTOR, args=[buf], timeout_ms=60_000)
        self.wr32(0x004DC688, buf)
        self.anm_mgr = buf
        self.phase = "scene_tasks"
        if self.drive(SCENE_TASKS_REG, timeout_ms=120_000) != 0:
            self.fail("scene task registration failed")

        limit = total_limit or (frames + 400)
        out = open(self.out_path, "wb")
        t0 = time.time()
        try:
            for self.frame in range(0, limit):
                self.frame_keys = INPUT_SCRIPT.get(self.frame, set())
                self.title_frame(out)
                if self.observe:
                    self.observe_line()
                if self.frame % 60 == 0:
                    sys.stderr.write("frame %d ops=%d (%.1fs)\n" % (
                        self.frame, self.per_frame_ops[-1],
                        time.time() - t0))
                    sys.stderr.flush()
                    t0 = time.time()
        finally:
            out.close()
        return 0

    # ---------- 窗口截取与自证 ----------
    def cut_window(self, frames=WINDOW):
        """剥离装载前缀：首个 ops>0 帧起 600 帧，帧号重编 0..599。"""
        rows = [json.loads(l) for l in
                open(self.out_path, "rb").read().splitlines()]
        first = next((i for i, r in enumerate(rows) if r["ops"]), None)
        if first is None:
            self.fail("no frame with ops>0")
        window = rows[first:first + frames]
        if len(window) < frames:
            self.fail("window short: %d frames (prefix=%d total=%d)" % (
                len(window), first, len(rows)))
        for i, row in enumerate(window):
            row["frame"] = i
        with open(self.out_path, "wb") as f:
            for row in window:
                f.write(json.dumps(row, ensure_ascii=True,
                                   separators=(",", ":")).encode("ascii")
                        + b"\n")
        ops = [len(r["ops"]) for r in window]
        return {"prefix": first, "frames": len(window),
                "min_ops": min(ops), "max_ops": max(ops),
                "total_ops": sum(ops)}


def main(argv):
    flags = {}
    positional = []
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == "--frames":
            i += 1
            flags["--frames"] = int(argv[i])
        elif arg == "--observe":
            flags[arg] = True
        else:
            positional.append(arg)
        i += 1
    exe = positional[0] if positional else "games/th13/th13.exe"
    raw_dir = positional[1] if len(positional) > 1 else "artifacts/reference/raw"
    out_path = (positional[2] if len(positional) > 2 else
                "artifacts/oracle-draws/title-draws.jsonl")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    oracle = TitleOracle(exe, raw_dir, out_path)
    oracle.observe = "--observe" in flags
    frames = flags.get("--frames", WINDOW)
    started = time.time()
    oracle.run(frames=frames)
    stats = oracle.cut_window(frames)
    print("title-oracle prefix=%d frames=%d ops[min=%d max=%d total=%d] "
          "elapsed %.1fs" % (stats["prefix"], stats["frames"],
                             stats["min_ops"], stats["max_ops"],
                             stats["total_ops"], time.time() - started))
    scripts = {}
    for _f, pool, script, layer in oracle.inserts:
        scripts.setdefault((pool, script, layer), 0)
        scripts[(pool, script, layer)] += 1
    print("vm scripts (pool,script,layer)xN: %s" % " ".join(
        "%s#%d@L%dx%d" % (pool, script, layer, n)
        for (pool, script, layer), n in sorted(scripts.items())))
    if oracle.flags_writes:
        seen = {}
        for _f, eip, value, name in oracle.flags_writes:
            seen.setdefault((eip, name), value)
        print("0x4be830 writers: %s" % "; ".join(
            "%08x(%s)->%08x" % (eip, name, v)
            for (eip, name), v in sorted(seen.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
