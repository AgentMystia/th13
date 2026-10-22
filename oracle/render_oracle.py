"""渲染语义 oracle：假 D3D9 COM 设备上驱动原版渲染链，逐帧导出语义绘制清单。

架构（docs/plans/render-chain.md 施工图 + 本模块实证）：
  - 环境：复用 stage1_oracle 的敌机管理器驱动（ECL 逻辑与事件流基线同拍），
    但 ANM mgr 换真实构造（0x46bc60），装载/步进/绘制真实执行。
  - 假设备：machine 的 Direct3DCreate9 导入桩返回假 IDirect3D9；CreateDevice
    落魔法 vtable 槽（override_call 拦截：ECX=this、[esp+4..]=实参、模拟
    stdcall ret N）。设备纹理指针经帧边界扫 mgr 26 槽 entry 表映射回
    (槽, entry, 纹理名)。
  - D3DX：矩阵/向量导入桩在 Python 侧按 D3D 语义真算（double 中间量）；
    表面装载类恒成功（语义比对不比像素内容）。
  - 每帧驱动（对照 0x45d2f0 帧序）：
      0x418e00(EDI=ctl)      ECL 逻辑（stage1 基线同拍）
      0x46e510()             ANM 增量装载推进
      0x46f170(EAX=mgr)      主池全部 AnmVm 步进 + 重建 31 层链
      0x46f240(EAX=mgr)      前景池步进 + 重建 25 层链
      BeginScene → 0x467960 清批 → 0x43b080 fog 刷
      → 逐层 0x46f380(EAX=层, EDI=mgr) → 0x4679a0(ESI=mgr) 终批
      → SetTexture(0,0) → EndScene → Present
  - 捕获：设备状态影子（transforms/blend/tfactor/fvf/viewport/纹理）+
    DrawPrimitive/DrawPrimitiveUP 落 op；层号经 0x46f380 入口 watch。

用法（容器内 /work）：
    python3 -m oracle.render_oracle [exe] [raw_dir] [out_jsonl]
        [--frames N] [--stage st01.ecl] [--replay <rpy> [--replay-stage N]]
默认 out：artifacts/oracle-draws/st01-draws.jsonl（回放窗 st01-draws-replay.jsonl）
"""
import json
import math
import os
import struct
import sys
import time

from unicorn import UcError
from unicorn.x86_const import (UC_X86_REG_EAX, UC_X86_REG_EBP, UC_X86_REG_ECX,
                               UC_X86_REG_EDI, UC_X86_REG_EDX, UC_X86_REG_EIP,
                               UC_X86_REG_ESI, UC_X86_REG_ESP)

from .machine import Machine, RET_MAGIC
from .stage1_oracle import (BYTE_TABLE, JUMP_TABLE, KILLALL_BLOCK, MGR_CREATE,
                            MGR_UPDATE, NOOP_BLOCK, NOOP_OPS, OPEN_BY_NAME,
                            RNG_OBJ, ROOT_SIZE, SOUND_PLAY, SPAWN, f32, fmt_f,
                            BULLET_SPAWN, DAT_ANM_FLAGS, DAT_ANM_MGR, DAT_DIFF,
                            DAT_MGR, DAT_PLAYER, DAT_RANK, DAT_ROOT, DAT_SPELL,
                            DEATH_RING, FREE, GAME_BUMP, GETPTD, MALLOC)

# ---- 渲染链锚点（render-chain.md §0/§1/§2/§7）----
DEV_INIT        = 0x45DC80   # 设备创建（CreateDevice→假 COM；返回 0 成功）
ANM_MGR_CTOR    = 0x46BC60   # ANM mgr 构造（cdecl [esp+4]=buf 0xf53c48 字节）
ANM_LOAD_TICK   = 0x46E510   # ANM 增量装载推进（26 槽，每趟 1 entry）
ANM_STEP_MAIN   = 0x46F170   # 主池步进（EAX=mgr；0x5bc 静态槽链 mgr+0xf48208）
ANM_STEP_FORE   = 0x46F240   # 前景池步进（EAX=mgr；池链 mgr+0xf48210）
ANM_DRAW_LAYER  = 0x46F380   # 画一层（EAX=层号, EDI=mgr；层头 mgr+0xf48784+层*0x5a4）
UP_FLUSH        = 0x4679A0   # UP 批提交（ESI=mgr；count mgr+0xb781f0）
UP_CLEAR        = 0x467960   # UP 队列清空（无参，全局 mgr）
FOG_FLUSH       = 0x43B080   # FOGENABLE 缓存刷（EDI=gamestate 0x4dc9d0）
GAMESTATE       = 0x004DC9D0 # .data 内 gamestate（0x45d2f0 的 param_1）
DAT_VIEWPORT_GP = 0x004DCC18 # 视口块指针（=&DAT_004dc9d0）

MGR_SLOTS_OFF   = 0xB77B34   # mgr 内 26 槽 ANM file 表
MGR_FILE_ENTRIES = 0x110     # file+0x110 entry 计数
MGR_FILE_ELEM    = 0x124     # file+0x124 entry 元素表（0x18/项，+0x0=纹理指针，
                             # +0x10=entry 头；头+0x10=name1 相对偏移）
FILE_LOADING     = 0x128     # file+0x128 装载游标（0=完成）

FRAMES = 3600

# D3DTS
TS_VIEW, TS_PROJ, TS_WORLD, TS_TEX0 = 2, 3, 0x100, 0x10
# D3DRS
RS_SRCBLEND, RS_DESTBLEND, RS_BLENDOP = 19, 20, 171
RS_TFACTOR = 60


def f32s(values):
    """16 元矩阵/向量 → %.9g 串（stage1 fmt_f 范式，float32 往返无损）。"""
    return [fmt_f(f32(v)) for v in values]


class ComStubHost:
    """假 COM 层：vtable 槽 = 魔法地址，override_call 拦截（stdcall ret N）。

    槽布局：COM_STUB 区每槽 16 字节，槽值=槽地址。方法注册时给出
    (名字, 参数 dword 数, handler(machine)->eax)；未注册槽指向共享默认桩
    （ret 0、不弹参——被意外调用时多半会崩，崩了按崩点补注册）。
    """

    def __init__(self, machine):
        self.m = machine
        self.base = 0xF3000000
        machine.uc.mem_map(self.base, 0x10000)
        self.next_slot = self.base
        self.default_stub = self._alloc_slot("com_default")
        machine.override_call(self.default_stub, lambda mm: 0, ret_pop=0)
        self.handlers = {}

    def _alloc_slot(self, key):
        slot = self.next_slot
        self.next_slot += 16
        self.m.uc.mem_write(slot, b"\xC3")
        return slot

    def method(self, key, nargs, handler):
        """注册方法 → 返回槽地址（放进 vtable）。"""
        slot = self._alloc_slot(key)
        self.handlers[slot] = key
        self.m.override_call(slot, handler, ret_pop=4 * nargs)
        return slot

    def com_call(self, slot, this, args=()):
        """oracle 主动直调某 COM 方法（this 为第一个栈参）。"""
        return self.m.call(slot, args=[this] + list(args))

    # ---- vtable 装配 ----
    def make_vtable(self, slots):
        """slots: {偏移: 槽地址} → 返回 vtable 地址（写入堆）。"""
        size = max(slots) + 4
        vt = self.m.malloc(size)
        buf = bytearray(size)
        for off, slot in slots.items():
            struct.pack_into("<I", buf, off, slot)
        self.m.uc.mem_write(vt, bytes(buf))
        return vt

    def make_object(self, vtable, extra=0):
        obj = self.m.malloc(8 + extra)
        self.m.uc.mem_write(obj, struct.pack("<I", vtable))
        return obj


class FakeD3D:
    """Direct3DCreate9 → IDirect3D9 → 假设备/纹理/VB/surface 全家。"""

    def __init__(self, ro):
        self.ro = ro
        self.m = ro.m
        self.uc = ro.m.uc
        host = ro.com
        # 设备状态影子（捕获契约字段源）
        self.transforms = {}       # D3DTS -> [16 float]
        self.texture0 = 0
        self.renderstates = {}
        self.fvf = 0
        self.viewport = None
        self.cur_layer = None      # 0x46f380 watch 维护
        # 纹理对象登记：指针 -> {w,h,fmt,usage,pool}
        self.textures = {}
        self.frame_ops = []
        self.stats = {"d3d9": 0, "textures": 0, "quads": 0, "ups": 0,
                      "vb_bytes": 0, "unknown_slots": {}}

        # ---------- IDirect3D9 ----------
        # 参数约定（0x45dc80/0x4032d0 汇编实证）：游戏对 COM 用纯 stdcall，
        # this=第一个栈参。self.arg(mm,i)=[esp+4+4i]，i=0 即 this。
        d3d9_methods = {
            0x00: ("QueryInterface", 3, lambda mm: 0),
            0x04: ("AddRef", 1, lambda mm: 1),
            0x08: ("Release", 1, lambda mm: 1),
            0x20: ("GetAdapterDisplayMode", 3, self._d3d9_get_display_mode),
            0x28: ("CheckDeviceFormat", 7, lambda mm: 0),
            0x40: ("CreateDevice", 7, self._d3d9_create_device),
        }
        d3d9_slots = {off: host.method("d3d9." + name, n, fn)
                      for off, (name, n, fn) in d3d9_methods.items()}
        vt = host.make_vtable(d3d9_slots)
        self.d3d9_obj = host.make_object(vt)

        # ---------- IDirect3DDevice9 ----------
        dev = {}
        def D(off, name, nargs, fn):
            dev[off] = host.method("dev." + name, nargs, fn)
        D(0x00, "QI", 3, lambda mm: 0)
        D(0x04, "AddRef", 1, lambda mm: 1)
        D(0x08, "Release", 1, lambda mm: 1)
        D(0x0C, "TestCooperativeLevel", 1, lambda mm: 0)   # 恒 OK（无设备丢失）
        D(0x1C, "GetDeviceCaps", 2, self._dev_get_caps)
        D(0x40, "Reset", 2, lambda mm: 0)
        D(0x44, "Present", 5, self._dev_present)
        D(0x48, "GetBackBuffer", 5, self._dev_get_backbuffer)
        D(0x4C, "GetRasterStatus", 3, self._dev_get_raster_status)
        D(0x5C, "CreateTexture", 9, self._dev_create_texture)
        D(0x68, "CreateVertexBuffer", 7, self._dev_create_vb)
        D(0x94, "SetRenderTarget", 3, lambda mm: 0)
        D(0xA4, "BeginScene", 1, lambda mm: 0)
        D(0xA8, "EndScene", 1, lambda mm: 0)
        D(0xAC, "Clear", 7, self._dev_clear)
        D(0xB0, "SetTransform", 3, self._dev_set_transform)
        D(0xBC, "SetViewport", 2, self._dev_set_viewport)
        D(0xC0, "GetViewport", 2, self._dev_get_viewport)
        D(0xE4, "SetRenderState", 3, self._dev_set_render_state)
        D(0x104, "SetTexture", 3, self._dev_set_texture)
        D(0x10C, "SetTextureStageState", 4, lambda mm: 0)
        D(0x114, "SetSamplerState", 4, lambda mm: 0)
        D(0x144, "DrawPrimitive", 4, self._dev_draw_primitive)
        D(0x14C, "DrawPrimitiveUP", 5, self._dev_draw_primitive_up)
        D(0x164, "SetFVF", 2, self._dev_set_fvf)
        D(0x190, "SetStreamSource", 5, lambda mm: 0)
        D(0x170, "SetVertexDeclaration", 2, lambda mm: 0)
        D(0x174, "SetVertexShader", 2, lambda mm: 0)
        D(0x354, "CreateVertexDeclaration", 3, lambda mm: 0)
        self.dev_slots = dev
        vt = host.make_vtable(dev)
        self.dev_obj = host.make_object(vt)

        # ---------- 纹理 / VB / surface ----------
        tex = {}
        def T(off, name, nargs, fn):
            tex[off] = host.method("tex." + name, nargs, fn)
        T(0x00, "QI", 3, lambda mm: 0)
        T(0x04, "AddRef", 1, lambda mm: 1)
        T(0x08, "Release", 1, lambda mm: 1)
        T(0x2C, "LockRect", 5, self._tex_lock_rect)
        T(0x30, "UnlockRect", 2, lambda mm: 0)
        T(0x44, "GetLevelDesc", 3, self._tex_get_level_desc)
        T(0x48, "GetSurfaceLevel", 3, self._tex_get_surface_level)
        T(0x54, "AddDirtyRect", 2, lambda mm: 0)
        self.tex_slots = tex
        self.tex_vtable = host.make_vtable(tex)

        vb = {}
        def V(off, name, nargs, fn):
            vb[off] = host.method("vb." + name, nargs, fn)
        V(0x00, "QI", 3, lambda mm: 0)
        V(0x04, "AddRef", 1, lambda mm: 1)
        V(0x08, "Release", 1, lambda mm: 1)
        V(0x2C, "Lock", 5, self._vb_lock)
        V(0x30, "Unlock", 1, lambda mm: 0)
        self.vb_slots = vb
        self.vb_vtable = host.make_vtable(vb)

        surf = {}
        def S(off, name, nargs, fn):
            surf[off] = host.method("surf." + name, nargs, fn)
        S(0x00, "QI", 3, lambda mm: 0)
        S(0x04, "AddRef", 1, lambda mm: 1)
        S(0x08, "Release", 1, lambda mm: 1)
        S(0x2C, "LockRect", 5, self._tex_lock_rect)
        S(0x30, "UnlockRect", 2, lambda mm: 0)
        S(0x3C, "GetDesc", 2, self._surf_get_desc)
        self.surf_slots = surf
        self.surf_vtable = host.make_vtable(surf)

        self.vb_data = {}       # 指针 -> (buf, size)
        self.locked = {}        # 指针 -> (buf, pitch, w, h)

        # Direct3DCreate9 导入桩（WinMain/DEV_INIT 的入口）
        self.m.stub("Direct3DCreate9", lambda mm: self._on_d3d_create(mm),
                    args_pop=4)

    # ---------- 参数工具 ----------
    def arg(self, mm, i):
        """第 i 个栈参（[esp]=retaddr、[esp+4]=arg0；COM 的 this=arg0）。"""
        esp = mm.uc.reg_read(UC_X86_REG_ESP)
        return self.ro.rd32(esp + 4 + 4 * i)

    def argf(self, mm, i):
        return f32(self.arg(mm, i))

    def this(self, mm):
        return mm.uc.reg_read(UC_X86_REG_ECX)

    # ---------- IDirect3D9 ----------
    def _on_d3d_create(self, mm):
        self.stats["d3d9"] += 1
        return self.d3d9_obj

    def _d3d9_get_display_mode(self, mm):
        mode = self.arg(mm, 2)   # (this, adapter, D3DDISPLAYMODE*)
        self.uc.mem_write(mode, struct.pack("<6I", 640, 480, 60, 21, 1, 0))
        return 0

    def _d3d9_create_device(self, mm):
        out = self.arg(mm, 6)    # (this, adapter, type, hwnd, flags, pp, *out)
        self.ro.wr32(out, self.dev_obj)
        return 0

    # ---------- 设备 ----------
    def _dev_get_caps(self, mm):
        caps = self.arg(mm, 1)
        self.uc.mem_write(caps, b"\x00" * 0xF8)
        return 0

    def _dev_present(self, mm):
        return 0

    def _dev_get_backbuffer(self, mm):
        out = self.arg(mm, 4)
        obj = self.ro.com.make_object(self.surf_vtable)
        self.locked[obj] = (self.ro.game_malloc(0x1000), 0x1000, 0, 0)
        self.ro.wr32(out, obj)
        return 0

    def _dev_get_raster_status(self, mm):
        rs = self.arg(mm, 2)
        self.uc.mem_write(rs, b"\x00" * 8)   # InVBlank=FALSE → vsync 轮询即过
        return 0

    def _dev_create_texture(self, mm):
        # (this, w, h, levels, usage, fmt, pool, *out, *sh)
        w, h = self.arg(mm, 1), self.arg(mm, 2)
        usage, fmt, pool = self.arg(mm, 4), self.arg(mm, 5), self.arg(mm, 6)
        out = self.arg(mm, 7)
        obj = self.ro.com.make_object(self.tex_vtable)
        self.textures[obj] = {"w": w, "h": h, "usage": usage, "fmt": fmt,
                              "pool": pool}
        # 像素 scratch 固定小缓冲：语义捕获不读像素，w*h*4 真尺寸会烧穿
        # bump 堆（2048² 纹理 ×多档 = 数十 MB → 敌机分配越界 unmapped）。
        self.locked[obj] = (self.ro.game_malloc(0x1000), w * 4, w, h)
        self.stats["textures"] += 1
        self.ro.wr32(out, obj)
        return 0

    def make_texture_for(self, w, h, usage, fmt, pool):
        """D3DX 路径共用：建假纹理对象并登记。"""
        obj = self.ro.com.make_object(self.tex_vtable)
        self.textures[obj] = {"w": w, "h": h, "usage": usage, "fmt": fmt,
                              "pool": pool}
        self.locked[obj] = (self.ro.game_malloc(0x1000), w * 4, w, h)
        self.stats["textures"] += 1
        return obj

    def _dev_create_vb(self, mm):
        size = self.arg(mm, 1)
        out = self.arg(mm, 5)
        obj = self.ro.com.make_object(self.vb_vtable)
        buf = self.ro.game_malloc(size)
        self.uc.mem_write(buf, b"\x00" * size)
        self.vb_data[obj] = (buf, size)
        self.stats["vb_bytes"] += size
        self.ro.wr32(out, obj)
        return 0

    def _dev_clear(self, mm):
        return 0

    def _dev_set_transform(self, mm):
        state = self.arg(mm, 1)
        mat = self.arg(mm, 2)
        self.transforms[state] = list(struct.unpack(
            "<16f", bytes(self.uc.mem_read(mat, 64))))
        return 0

    def _dev_set_viewport(self, mm):
        vp = self.arg(mm, 1)
        self.viewport = list(struct.unpack(
            "<6f", bytes(self.uc.mem_read(vp, 24))))
        return 0

    def _dev_get_viewport(self, mm):
        vp = self.arg(mm, 1)
        if self.viewport:
            self.uc.mem_write(vp, struct.pack("<6f", *self.viewport))
        return 0

    def _dev_set_render_state(self, mm):
        state, value = self.arg(mm, 1), self.arg(mm, 2)
        self.renderstates[state] = value
        return 0

    def _dev_set_texture(self, mm):
        self.texture0 = self.arg(mm, 2)
        return 0

    def _dev_set_fvf(self, mm):
        self.fvf = self.arg(mm, 1)
        return 0

    # ---------- 绘制捕获 ----------
    def _tex_id(self, ptr):
        """绘制事件记录纹理指针（int），帧尾 refresh_tex_map 后统一解析。"""
        return ptr

    def _blend(self):
        rs = self.renderstates
        return [rs.get(RS_SRCBLEND, 0), rs.get(RS_DESTBLEND, 0),
                rs.get(RS_BLENDOP, 1)]

    def _dev_draw_primitive(self, mm):
        # (this, primtype, startvertex, count) —— 矩阵路径四边形
        prim, start, count = self.arg(mm, 1), self.arg(mm, 2), self.arg(mm, 3)
        rs = self.renderstates
        op = {
            "op": "quad",
            "prim": prim,
            "anchor": start,
            "count": count,
            "tex": self._tex_id(self.texture0),
            "layer": self.ro.d3d.cur_layer,
            "blend": self._blend(),
            "tfactor": rs.get(RS_TFACTOR, 0),
            "fvf": self.fvf,
            "world": f32s(self.transforms.get(TS_WORLD, [0.0] * 16)),
            "texmat": f32s(self.transforms.get(TS_TEX0, [0.0] * 16)),
            "view": f32s(self.transforms.get(TS_VIEW, [0.0] * 16)),
            "proj": f32s(self.transforms.get(TS_PROJ, [0.0] * 16)),
        }
        self.frame_ops.append(op)
        self.stats["quads"] += 1
        return 0

    def _dev_draw_primitive_up(self, mm):
        # (this, primtype, count, verts, stride)
        prim, count, vptr, stride = (self.arg(mm, 1), self.arg(mm, 2),
                                     self.arg(mm, 3), self.arg(mm, 4))
        n = count * stride // 4
        raw = struct.unpack("<%df" % n, bytes(self.uc.mem_read(vptr, n * 4)))
        colors = struct.unpack(
            "<%dI" % n, bytes(self.uc.mem_read(vptr, n * 4)))
        verts = []
        per = stride // 4
        for i in range(count):
            base = i * per
            if per >= 5:   # 0x144: x y z rhw diff u v / 0x44: x y z rhw diff
                u = fmt_f(raw[base + 5]) if per >= 7 else 0
                v = fmt_f(raw[base + 6]) if per >= 7 else 0
                verts.append([fmt_f(raw[base]), fmt_f(raw[base + 1]),
                              fmt_f(raw[base + 2]), fmt_f(raw[base + 3]),
                              colors[base + 4] & 0xFFFFFFFF, u, v])
            else:          # 0x102: x y z u v（理论上 UP 不用，兜底）
                verts.append([fmt_f(raw[base]), fmt_f(raw[base + 1]),
                              fmt_f(raw[base + 2]), 0, 0,
                              fmt_f(raw[base + 3]), fmt_f(raw[base + 4])])
        rs = self.renderstates
        op = {
            "op": "up",
            "prim": prim,
            "count": count,
            "stride": stride,
            "tex": self._tex_id(self.texture0),
            "layer": self.ro.d3d.cur_layer,
            "blend": self._blend(),
            "tfactor": rs.get(RS_TFACTOR, 0),
            "fvf": self.fvf,
            "verts": verts,
        }
        self.frame_ops.append(op)
        self.stats["ups"] += 1
        return 0

    # ---------- 纹理 / VB / surface ----------
    def _tex_lock_rect(self, mm):
        # (this, level, D3DLOCKED_RECT*, rect, flags)
        lr = self.arg(mm, 2)
        this = self.this(mm)
        buf, pitch, w, h = self.locked.get(this,
                                           (self.ro.game_malloc(0x1000), 0x100,
                                            0, 0))
        self.uc.mem_write(lr, struct.pack("<II", pitch, buf))
        return 0

    def _tex_get_level_desc(self, mm):
        # this = 第一个栈参（COM stdcall；曾误读 ECX → 纹理查不到回落 0×0）。
        # D3DSURFACE_DESC 为 8 dword：fmt,type,usage,pool,msType,msQual,W(+0x18),H(+0x1c)
        # （曾多写一个幻影 Size 字段 → W/H 错位 4 字节，精灵记录 X 维 0/0=NaN）。
        desc = self.arg(mm, 2)
        info = self.textures.get(self.arg(mm, 0), {"w": 0, "h": 0, "fmt": 0})
        self.uc.mem_write(desc, struct.pack(
            "<8I", info["fmt"], 2, info.get("usage", 0),
            info.get("pool", 0), 0, 0, info["w"], info["h"]))
        return 0

    def _tex_get_surface_level(self, mm):
        out = self.arg(mm, 2)
        obj = self.ro.com.make_object(self.surf_vtable)
        info = self.textures.get(self.this(mm), {"w": 0, "h": 0})
        self.locked[obj] = (self.ro.game_malloc(0x1000), 0x100,
                            info["w"], info["h"])
        self.ro.wr32(out, obj)
        return 0

    def _surf_get_desc(self, mm):
        return self._tex_get_level_desc(mm)

    def _vb_lock(self, mm):
        # (this, off, size, BYTE**, flags)
        pptr = self.arg(mm, 3)
        this = self.this(mm)
        buf, size = self.vb_data.get(this, (0, 0))
        off = self.arg(mm, 1)
        if buf:
            self.ro.wr32(pptr, buf + off)
        return 0


# ---------------- D3DX 数学桩（D3D 语义，double 中间量）----------------

def _mat_mul(a, b):
    """行主序 4x4：out = a·b（out[i][j] = Σ_k a[i][k]*b[k][j]）。"""
    return [a[r] * b[c] + a[r + 1] * b[c + 4] + a[r + 2] * b[c + 8]
            + a[r + 3] * b[c + 12]
            for r in (0, 4, 8, 12) for c in range(4)]


class D3DXStubs:
    """D3DX9_43 导入桩：矩阵/向量按 D3D 公式真算；表面装载恒成功。"""

    def __init__(self, ro):
        self.ro = ro
        self.m = ro.m
        uc = ro.m.uc

        def stub(name, nargs, fn):
            self.m.stub(name, fn, args_pop=4 * nargs)

        def rd_matrix(addr):
            return list(struct.unpack("<16f",
                                      bytes(uc.mem_read(addr, 64))))

        def wr_matrix(addr, mat):
            uc.mem_write(addr, struct.pack("<16f", *[f32(x) for x in mat]))
            return addr

        def mat_LookAtLH(mm):
            out, eye, at, up = (ro.d3d.arg(mm, 0), ro.d3d.arg(mm, 1),
                                ro.d3d.arg(mm, 2), ro.d3d.arg(mm, 3))
            ex, ey, ez = struct.unpack("<3f", bytes(uc.mem_read(eye, 12)))
            ax, ay, az = struct.unpack("<3f", bytes(uc.mem_read(at, 12)))
            ux, uy, uz = struct.unpack("<3f", bytes(uc.mem_read(up, 12)))
            zx, zy, zz = ax - ex, ay - ey, az - ez
            zl = math.sqrt(zx * zx + zy * zy + zz * zz)
            zx, zy, zz = zx / zl, zy / zl, zz / zl
            xx, xy, xz = uy * zz - uz * zy, uz * zx - ux * zz, ux * zy - uy * zx
            xl = math.sqrt(xx * xx + xy * xy + xz * xz)
            xx, xy, xz = xx / xl, xy / xl, xz / xl
            yx, yy, yz = zy * xz - zz * xy, zz * xx - zx * xz, zx * xy - zy * xx
            mat = [xx, yx, zx, 0.0, xy, yy, zy, 0.0,
                   xz, yz, zz, 0.0,
                   -(xx * ex + xy * ey + xz * ez),
                   -(yx * ex + yy * ey + yz * ez),
                   -(zx * ex + zy * ey + zz * ez), 1.0]
            return wr_matrix(out, mat)

        def mat_PerspFovLH(mm):
            out = ro.d3d.arg(mm, 0)
            fov, aspect, zn, zf = (ro.d3d.argf(mm, 1), ro.d3d.argf(mm, 2),
                                   ro.d3d.argf(mm, 3), ro.d3d.argf(mm, 4))
            ys = 1.0 / math.tan(fov / 2.0)
            xs = ys / aspect
            mat = [xs, 0.0, 0.0, 0.0, 0.0, ys, 0.0, 0.0,
                   0.0, 0.0, zf / (zf - zn), 1.0,
                   0.0, 0.0, -zn * zf / (zf - zn), 0.0]
            return wr_matrix(out, mat)

        def mat_Multiply(mm):
            out, m1, m2 = ro.d3d.arg(mm, 0), ro.d3d.arg(mm, 1), ro.d3d.arg(mm, 2)
            return wr_matrix(out, _mat_mul(rd_matrix(m1), rd_matrix(m2)))

        def mat_Translation(mm):
            out = ro.d3d.arg(mm, 0)
            x, y, z = ro.d3d.argf(mm, 1), ro.d3d.argf(mm, 2), ro.d3d.argf(mm, 3)
            mat = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
                   0.0, 0.0, 1.0, 0.0, x, y, z, 1.0]
            return wr_matrix(out, mat)

        def _rot(axis, idx):
            def fn(mm):
                out = ro.d3d.arg(mm, 0)
                a = ro.d3d.argf(mm, 1)
                c, s = math.cos(a), math.sin(a)
                mat = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
                       0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
                if axis == 'x':
                    mat[5], mat[6], mat[9], mat[10] = c, s, -s, c
                elif axis == 'y':
                    mat[0], mat[2], mat[8], mat[10] = c, -s, s, c
                else:
                    mat[0], mat[1], mat[4], mat[5] = c, s, -s, c
                return wr_matrix(out, mat)
            return fn

        def vec3_Normalize(mm):
            out, v = ro.d3d.arg(mm, 0), ro.d3d.arg(mm, 1)
            x, y, z = struct.unpack("<3f", bytes(uc.mem_read(v, 12)))
            l = math.sqrt(x * x + y * y + z * z) or 1.0
            uc.mem_write(out, struct.pack("<3f", f32(x / l), f32(y / l),
                                          f32(z / l)))
            return out

        def vec2_Normalize(mm):
            out, v = ro.d3d.arg(mm, 0), ro.d3d.arg(mm, 1)
            x, y = struct.unpack("<2f", bytes(uc.mem_read(v, 8)))
            l = math.sqrt(x * x + y * y) or 1.0
            uc.mem_write(out, struct.pack("<2f", f32(x / l), f32(y / l)))
            return out

        def vec3_Transform(mm):
            out, v, mat = ro.d3d.arg(mm, 0), ro.d3d.arg(mm, 1), ro.d3d.arg(mm, 2)
            x, y, z = struct.unpack("<3f", bytes(uc.mem_read(v, 12)))
            m = rd_matrix(mat)
            r = [x * m[0] + y * m[4] + z * m[8] + m[12],
                 x * m[1] + y * m[5] + z * m[9] + m[13],
                 x * m[2] + y * m[6] + z * m[10] + m[14],
                 x * m[3] + y * m[7] + z * m[11] + m[15]]
            uc.mem_write(out, struct.pack("<4f", *[f32(t) for t in r]))
            return out

        def vec3_Project(mm):
            # (out, v, viewport, projection, view, world)
            out, v, vpp, proj, view, world = (ro.d3d.arg(mm, 0),
                                              ro.d3d.arg(mm, 1),
                                              ro.d3d.arg(mm, 2),
                                              ro.d3d.arg(mm, 3),
                                              ro.d3d.arg(mm, 4),
                                              ro.d3d.arg(mm, 5))
            x, y, z = struct.unpack("<3f", bytes(uc.mem_read(v, 12)))
            vx, vy, vw, vh, vmin, vmax = struct.unpack(
                "<6f", bytes(uc.mem_read(vpp, 24)))
            full = _mat_mul(_mat_mul(rd_matrix(world), rd_matrix(view)),
                            rd_matrix(proj))
            for _ in range(2):
                pass
            cx = x * full[0] + y * full[4] + z * full[8] + full[12]
            cy = x * full[1] + y * full[5] + z * full[9] + full[13]
            cz = x * full[2] + y * full[6] + z * full[10] + full[14]
            cw = x * full[3] + y * full[7] + z * full[11] + full[15]
            sx = vx + (1.0 + cx / cw) * vw / 2.0
            sy = vy + (1.0 - cy / cw) * vh / 2.0
            sz = vmin + cz * (vmax - vmin)
            uc.mem_write(out, struct.pack("<3f", f32(sx), f32(sy), f32(sz)))
            return out

        def vec3_ProjectArray(mm):
            # (out, stride, v, n, viewport, proj, view, world)
            out, stride, v, n, vpp, proj, view, world = (
                ro.d3d.arg(mm, 0), ro.d3d.arg(mm, 1), ro.d3d.arg(mm, 2),
                ro.d3d.arg(mm, 3), ro.d3d.arg(mm, 4), ro.d3d.arg(mm, 5),
                ro.d3d.arg(mm, 6), ro.d3d.arg(mm, 7))
            full = _mat_mul(_mat_mul(rd_matrix(world), rd_matrix(view)),
                            rd_matrix(proj))
            vx, vy, vw, vh, vmin, vmax = struct.unpack(
                "<6f", bytes(uc.mem_read(vpp, 24)))
            for i in range(n):
                x, y, z = struct.unpack(
                    "<3f", bytes(uc.mem_read(v + i * stride, 12)))
                cx = x * full[0] + y * full[4] + z * full[8] + full[12]
                cy = x * full[1] + y * full[5] + z * full[9] + full[13]
                cz = x * full[2] + y * full[6] + z * full[10] + full[14]
                cw = x * full[3] + y * full[7] + z * full[11] + full[15]
                sx = vx + (1.0 + cx / cw) * vw / 2.0
                sy = vy + (1.0 - cy / cw) * vh / 2.0
                sz = vmin + cz * (vmax - vmin)
                uc.mem_write(out + i * stride,
                             struct.pack("<3f", f32(sx), f32(sy), f32(sz)))
            return out

        stub("D3DXMatrixLookAtLH", 4, mat_LookAtLH)
        stub("D3DXMatrixPerspectiveFovLH", 5, mat_PerspFovLH)
        stub("D3DXMatrixMultiply", 3, mat_Multiply)
        stub("D3DXMatrixTranslation", 4, mat_Translation)
        stub("D3DXMatrixRotationX", 2, _rot('x', 0))
        stub("D3DXMatrixRotationY", 2, _rot('y', 1))
        stub("D3DXMatrixRotationZ", 2, _rot('z', 2))
        stub("D3DXVec3Normalize", 2, vec3_Normalize)
        stub("D3DXVec2Normalize", 2, vec2_Normalize)
        stub("D3DXVec3Transform", 3, vec3_Transform)
        stub("D3DXVec3Project", 6, vec3_Project)
        stub("D3DXVec3ProjectArray", 8, vec3_ProjectArray)
        stub("D3DXCreateTexture", 8, self._d3dx_create_texture)
        stub("D3DXCreateTextureFromFileInMemoryEx", 15,
             self._d3dx_create_texture_from_file)
        stub("D3DXLoadSurfaceFromMemory", 10, lambda mm: 0)
        stub("D3DXLoadSurfaceFromSurface", 8, lambda mm: 0)
        stub("D3DXLoadSurfaceFromFileInMemory", 9, lambda mm: 0)

    def _d3dx_create_texture(self, mm):
        # D3DXCreateTexture(dev, w, h, mips, usage, fmt, pool, *out, *shared)——
        # cdecl 9 参：[esp+8]=w、+0xc=h、+0x14=usage、+0x18=fmt、+0x1c=pool、
        # +0x20=out（曾整体错位 8 字节：w/h 读成 mips/usage，纹理登记宽高失真）。
        esp = mm.uc.reg_read(UC_X86_REG_ESP)
        rd = self.ro.rd32
        w, h = rd(esp + 8), rd(esp + 0xC)
        usage, fmt, pool = rd(esp + 0x14), rd(esp + 0x18), rd(esp + 0x1C)
        out = rd(esp + 0x20)
        obj = self.ro.d3d.make_texture_for(w, h, usage, fmt, pool)
        self.ro.wr32(out, obj)
        return 0

    def _d3dx_create_texture_from_file(self, mm):
        # (dev, src, srclen, w, h, mips, usage, fmt, pool, filter, mipfilter,
        #  colorkey, *srcinfo, *palette, *out) —— w/h 可为 D3DX_DEFAULT(-1)，
        #  PNG 头（IHDR）解真实尺寸回填。
        esp = mm.uc.reg_read(UC_X86_REG_ESP)
        rd = self.ro.rd32
        src = rd(esp + 8)
        w, h = rd(esp + 16), rd(esp + 20)
        usage, fmt, pool = rd(esp + 28), rd(esp + 32), rd(esp + 36)
        out = rd(esp + 60)
        if w == 0xFFFFFFFF or h == 0xFFFFFFFF:
            w = h = 0
            data = bytes(self.uc.mem_read(src, 32))
            if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
                w = struct.unpack(">I", data[16:20])[0]
                h = struct.unpack(">I", data[20:24])[0]
        if fmt == 0 or fmt == 0xFFFFFFFF:
            fmt = 21   # A8R8G8B8
        obj = self.ro.d3d.make_texture_for(w, h, usage, fmt, pool)
        self.ro.wr32(out, obj)
        return 0


class RenderOracle:
    """stage1 环境上的渲染链 oracle。"""

    def __init__(self, exe_path, raw_dir, out_path):
        self.raw_dir = raw_dir
        self.m = Machine(exe_path)
        self.uc = self.m.uc
        self.out_path = out_path
        self.frame = 0
        self.bump = GAME_BUMP
        self.ctl = 0
        self.anm_mgr = 0
        self.tex_map = {}          # 纹理指针 -> {"slot","entry","name"}
        self.replay = None
        self.model = None
        self.replay_stage_index = 0
        self.stats = {"mallocs": 0, "malloc_bytes": 0, "seam": 0}
        self.frames_out = 0
        self.per_frame_ops = []

    # ---------- 基础工具（同 stage1） ----------
    def rd32(self, addr):
        return struct.unpack("<I", bytes(self.uc.mem_read(addr, 4)))[0]

    def wr32(self, addr, value):
        self.uc.mem_write(addr, struct.pack("<I", value & 0xFFFFFFFF))

    def cstr(self, addr, limit=256):
        out = bytearray()
        while len(out) < limit:
            chunk = bytes(self.uc.mem_read(addr + len(out), 32))
            if b"\x00" in chunk:
                out += chunk[:chunk.index(b"\x00")]
                break
            out += chunk
        return out

    def game_malloc(self, size):
        ptr = self.bump
        self.bump = (self.bump + max(size, 1) + 15) & ~15
        self.stats["mallocs"] += 1
        self.stats["malloc_bytes"] += max(size, 1)
        return ptr

    def fail(self, message):
        sys.stderr.write("render-oracle FAIL frame=%d: %s\n"
                         % (self.frame, message))
        raise SystemExit(1)

    # ---------- 环境搭建（stage1 同源，ANM 真实化差异见注） ----------
    def install_overrides(self):
        m = self.m
        m.override_call(0x0047FC7F, lambda machine: 0, ret_pop=4)
        m.override_call(0x00485E0F, lambda machine: 1)

        def do_malloc(machine):
            size = self.rd32(machine.uc.reg_read(UC_X86_REG_ESP) + 4)
            return self.game_malloc(size)
        m.override_call(MALLOC, do_malloc)
        m.override_call(FREE, lambda machine: 0)

        def seam(machine):
            esp = machine.uc.reg_read(UC_X86_REG_ESP)
            name = self.cstr(self.rd32(esp + 4)).decode("ascii", "replace")
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

        m.override_call(SOUND_PLAY, lambda machine: 0, ret_pop=4)
        m.override_call(DEATH_RING, lambda machine: 0, ret_pop=24)
        m.override_call(0x00419A90, lambda machine: 0, ret_pop=4)
        # 注：0x461cb0（弹体 ANM 挂接）/0x462450（ANM 步进）不再空转——
        # 本 oracle 的对象就是渲染链本身。

        for slot, imp in list(m.import_stubs.items()):
            if imp.name.lower() in ("encodepointer", "decodepointer"):
                self.uc.mem_write(slot + 8, b"\xC3")
                m.stub(imp.name, (lambda s: (lambda machine: s + 8))(slot))

        def heap_alloc(machine):
            esp = machine.uc.reg_read(UC_X86_REG_ESP)
            size = self.rd32(esp + 0xC)
            return self.game_malloc(size)
        m.stub("HeapAlloc", heap_alloc, args_pop=12)
        m.stub("HeapFree", lambda machine: 1, args_pop=12)
        m.stub("InitializeCriticalSectionAndSpinCount",
               lambda machine: 1, args_pop=8)
        m.stub("InitializeCriticalSection", lambda machine: 1, args_pop=4)
        m.stub("Sleep", lambda machine: 0, args_pop=4)
        m.stub("timeBeginPeriod", lambda machine: 0, args_pop=4)
        m.stub("timeEndPeriod", lambda machine: 0, args_pop=4)

    def init_ptd(self):
        self.ptd = self.game_malloc(0x214)
        self.uc.mem_write(self.ptd, b"\x00" * 0x214)
        self.m.call(0x0047FD2D, args=[self.ptd, 0], timeout_ms=10_000)
        self.m.override_call(GETPTD, lambda machine: self.ptd)

    def patch_globals(self):
        self.wr32(DAT_DIFF, 3)
        self.wr32(DAT_RANK, 3)
        self.wr32(DAT_SPELL, 0xFFFFFFFF)
        player = self.game_malloc(0x1000)
        self.uc.mem_write(player, b"\x00" * 0x1000)
        self.uc.mem_write(player + 0x5B8, struct.pack("<f", 192.0))
        self.uc.mem_write(player + 0x5BC, struct.pack("<f", 420.0))
        self.wr32(DAT_PLAYER, player)
        root = self.game_malloc(ROOT_SIZE)
        self.uc.mem_write(root, b"\x00" * ROOT_SIZE)
        self.wr32(DAT_ROOT, root)
        node = self.game_malloc(0x2000)
        self.uc.mem_write(node, b"\x00" * 0x2000)
        self.wr32(root + 0x74, node)
        ctx = self.game_malloc(0x100)
        self.uc.mem_write(ctx, b"\x00" * 0x100)
        self.wr32(0x004C2180, ctx)
        # game_main（DAT_004c2160）由 run() 的 0x4038a0 真实构造。
        scheduler = self.game_malloc(0x60)
        self.uc.mem_write(scheduler, b"\x00" * 0x60)
        self.wr32(0x004DC658, scheduler)
        scoreboard = self.game_malloc(0x400)
        self.uc.mem_write(scoreboard, b"\x00" * 0x400)
        self.wr32(0x004C2190, scoreboard)
        score_ctx = self.game_malloc(0x200)
        self.uc.mem_write(score_ctx, b"\x00" * 0x200)
        self.wr32(0x004C2178, score_ctx)
        game_ctx = self.game_malloc(0x100)
        self.uc.mem_write(game_ctx, b"\x00" * 0x100)
        self.wr32(0x004C2194, game_ctx)
        self.wr32(0x004C1D40, 1)
        self.uc.mem_write(RNG_OBJ, b"\x00" * 8)
        # ANM 装载等待环快进（装载推进由本 oracle 的 0x46e510 循环接管）
        self.wr32(DAT_ANM_FLAGS, self.rd32(DAT_ANM_FLAGS) | 0x180)

    # 渲染窗 no-op 集 = stage1 基线集剔除 ANM 精灵指令族（302/303/306/307/
    # 308/313/316/319——渲染窗要真实执行它们产生精灵；其余与事件流基线/
    # 移植侧 StageCommands.cpp return 0 集保持同源）。
    RENDER_NOOP_OPS = tuple(op for op in NOOP_OPS if op not in
                            (302, 303, 306, 307, 308, 313, 316, 319))

    def patch_dispatch_table(self):
        """跳表项改写到默认 no-op 块（渲染窗集合）；518 定向 525 尾块。"""
        patched = 0
        for op in self.RENDER_NOOP_OPS:
            dense = bytes(self.uc.mem_read(BYTE_TABLE + (op - 300), 1))[0]
            addr = JUMP_TABLE + dense * 4
            if self.rd32(addr) != NOOP_BLOCK:
                self.wr32(addr, NOOP_BLOCK)
                patched += 1
        dense = bytes(self.uc.mem_read(BYTE_TABLE + (518 - 300), 1))[0]
        self.wr32(JUMP_TABLE + dense * 4, KILLALL_BLOCK)
        return patched

    # ---------- 假 COM / D3DX ----------
    def install_d3d(self):
        self.com = ComStubHost(self.m)
        self.d3d = FakeD3D(self)
        self.d3dx = D3DXStubs(self)

    # ---------- 纹理映射 ----------
    def refresh_tex_map(self):
        """扫 mgr 26 槽 → file → entry 表，登记 纹理指针→(槽, entry, 名)。

        entry 名走 file+0x108 数据区的 entry 链（anm-pipeline.md §二：
        头 +0x10=name1 相对偏移、+0x24=nextentry 相对偏移、0=链尾），
        链序与 0x46e0b0 建 entry 元素表的顺序一致。
        """
        if not self.anm_mgr:
            return
        for slot in range(26):
            fp = self.rd32(self.anm_mgr + MGR_SLOTS_OFF + slot * 4)
            if not fp:
                continue
            n = self.rd32(fp + MGR_FILE_ENTRIES)
            base = self.rd32(fp + MGR_FILE_ELEM)
            data = self.rd32(fp + 0x108)
            if not base or not data:
                continue
            head = data
            for i in range(n):
                elem = base + i * 0x18
                tex = self.rd32(elem)
                if tex and tex not in self.tex_map and head:
                    noff = self.rd32(head + 0x10)
                    name = ""
                    if noff:
                        raw = self.cstr(head + noff, 64)
                        try:
                            name = raw.decode("cp932")
                        except UnicodeDecodeError:
                            name = raw.decode("latin-1")
                    self.tex_map[tex] = {"slot": slot, "entry": i,
                                         "name": name}
                nxt = self.rd32(head + 0x24)
                if not nxt:
                    break
                head = head + nxt

    def tex_name(self, ptr):
        if not ptr:
            return None
        info = self.tex_map.get(ptr)
        if info:
            return [info["slot"], info["entry"], info["name"]]
        # 未知纹理：报成 [-1,-1,hex]（自证可见，不静默）
        return [-1, -1, "ptr:%08x" % ptr]

    # ---------- 挂钩 ----------
    def install_hooks(self):
        def on_layer(machine):
            self.d3d.cur_layer = machine.uc.reg_read(UC_X86_REG_EAX)
        self.m.watch(ANM_DRAW_LAYER, on_layer)



    # ---------- 驱动 ----------
    def drive(self, address, args=(), regs=None, timeout_ms=120_000):
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
        try:
            uc.emu_start(address, RET_MAGIC, timeout=timeout_ms * 1000)
        except UcError as exc:
            self.fail("emu error at 0x%08x driving 0x%08x: %s" % (
                uc.reg_read(UC_X86_REG_EIP), address, exc))
        return uc.reg_read(UC_X86_REG_EAX)

    def load_finish(self, max_ticks=4000):
        """循环 0x46e510 直到 26 槽全部装载完成。"""
        for _ in range(max_ticks):
            pending = False
            for slot in range(26):
                fp = self.rd32(self.anm_mgr + MGR_SLOTS_OFF + slot * 4)
                if fp and self.rd32(fp + FILE_LOADING):
                    pending = True
                    break
            if not pending:
                return True
            self.drive(ANM_LOAD_TICK, timeout_ms=20_000)
        return False

    def render_frame(self, out):
        """一帧渲染驱动 + 语义清单落盘。"""
        d3d = self.d3d
        d3d.frame_ops = []
        d3d.cur_layer = None
        # 0x45d2f0:30 每帧视口块指针（0x467350 UP 裁剪读 +0xd8..0xe4）
        self.wr32(DAT_VIEWPORT_GP, GAMESTATE)
        self.drive(ANM_LOAD_TICK, timeout_ms=20_000)
        self.drive(ANM_STEP_MAIN, regs={"eax": self.anm_mgr}, timeout_ms=60_000)
        self.drive(ANM_STEP_FORE, regs={"eax": self.anm_mgr}, timeout_ms=60_000)
        self.com.com_call(self.d3d.dev_slots[0xA4], d3d.dev_obj)  # BeginScene
        self.drive(UP_CLEAR, timeout_ms=10_000)
        self.drive(FOG_FLUSH, regs={"edi": GAMESTATE}, timeout_ms=10_000)
        # 主池层 0..30 + 前景池层 32+k（0x46f240 桶头 mgr+0xf53c04+k*0x5a4
        # 恰与 0x46f380 公式 layer=32+k 同址，数值闭环验证）
        for layer in range(0x39):
            self.drive(ANM_DRAW_LAYER,
                       regs={"eax": layer, "edi": self.anm_mgr},
                       timeout_ms=60_000)
        self.drive(UP_FLUSH, regs={"esi": self.anm_mgr}, timeout_ms=60_000)
        self.com.com_call(self.d3d.dev_slots[0x104], d3d.dev_obj, [0, 0])
        self.com.com_call(self.d3d.dev_slots[0xA8], d3d.dev_obj)  # EndScene
        self.com.com_call(self.d3d.dev_slots[0x44], d3d.dev_obj,
                          [0, 0, 0, 0])                            # Present
        self.refresh_tex_map()
        for seq, op in enumerate(d3d.frame_ops):
            op["tex"] = self.tex_name(op["tex"])
            # 契约 §2：seq 前置（键序固定）
            saved = dict(op)
            op.clear()
            op["seq"] = seq
            op.update(saved)
        line = json.dumps({
            "frame": self.frame,
            "viewport": d3d.viewport and [fmt_f(v) for v in d3d.viewport],
            "ops": d3d.frame_ops,
        }, ensure_ascii=True, separators=(",", ":"))
        out.write(line.encode("ascii") + b"\n")
        self.frames_out += 1
        self.per_frame_ops.append(len(d3d.frame_ops))

    # ---------- 主流程 ----------
    def run(self, frames=FRAMES):
        self.install_overrides()
        self.init_ptd()
        self.install_d3d()
        self.patch_globals()
        self.install_hooks()

        # 0) 战斗 gamestate 初始化（0x43bbc0，真版 0x42b990 装载序内调用）：
        #    写视口块 gamestate+0xd8..0xe4 = (0,0,640,480)——0x467350 的 UP
        #    裁剪直接读该块，极小环境曾恒 0 → 全部 UP 精灵被裁（"敌机不出
        #    op"的真相）；fov（gamestate+0x54=0.5236）与设备初建同值不扰。
        self.drive(0x0043BBC0, timeout_ms=10_000)

        # 1) 设备创建（先补 WinMain 的 Direct3DCreate9 调用→DAT_004dc6a4，
        #    再 0x45dc80 内部建假设备）
        d3d9_slot = next(slot for slot, imp in self.m.import_stubs.items()
                         if imp.name.lower() == "direct3dcreate9")
        d3d9_obj = self.drive(d3d9_slot, args=[0x20], timeout_ms=10_000)
        self.wr32(0x004DC6A4, d3d9_obj)
        if self.drive(DEV_INIT, timeout_ms=30_000) != 0:
            self.fail("device init failed")

        # 2) 真实 ANM mgr（0x46bc60 构造 + 0x46c950 静态 VB 走假设备）
        buf = self.game_malloc(0xF53C48)
        self.uc.mem_write(buf, b"\x00" * 0xF53C48)
        mgr = self.drive(ANM_MGR_CTOR, args=[buf], timeout_ms=30_000)
        self.wr32(DAT_ANM_MGR, buf)
        self.anm_mgr = buf

        # 2.5) ECL no-op 指令族跳表改写（stage1 基线同源：移植侧
        # StageCommands.cpp return 0 集；数据改写不动代码）
        self.patch_dispatch_table()

        # 3) 战斗初始化最小集（模拟 0x42b990/0x4145c0 的装载序）：
        #    a. game_main（0x4038a0 构造，DAT_004c2160，≥0x191a4 字节）
        #    b. 弹幕管理器（0x40d920 自建 root 0x9752b8 + 开 bullet.anm
        #       mgr 槽 7 → root+0x9752b4；root 挂 DAT_004c2174，
        #       0x4187d0 拷入 ctl+0x80）
        #    c. game_main+0x19194 = bullet.anm file（0x462450 case 300
        #       弹体脚本 fallback：0x4635ac 读）
        #    d. effect.anm（mgr 槽 8）→ gamectx+0x10 → ctl+0x84
        #    ECL 文件索引表（ctl+0x80+idx*4）：0=bullet、1=effect、
        #    2..=st01.ecl anim 列表（enemy.anm/st01enm.anm）。
        game_main = self.game_malloc(0x191B0)
        self.uc.mem_write(game_main, b"\x00" * 0x191B0)
        self.drive(0x004038A0, regs={"esi": game_main}, timeout_ms=30_000)
        root = self.drive(0x0040D920, timeout_ms=60_000)
        if not root:
            self.fail("bullet manager creation failed")
        self.wr32(DAT_ROOT, root)
        gamectx = self.rd32(0x004C2180)
        bullet_file = self.rd32(root + 0x9752B4)
        self.wr32(game_main + 0x19194, bullet_file)
        nb = self.game_malloc(0x40)
        self.uc.mem_write(nb, b"effect.anm\x00")
        eff_file = self.drive(0x0046DF40, regs={"eax": 8, "ecx": nb},
                              timeout_ms=30_000)
        if not eff_file:
            self.fail("global ANM open failed: effect.anm")
        self.wr32(gamectx + 0x10, eff_file)
        if not self.load_finish():
            self.fail("global ANM load did not finish")

        # 4) 敌机管理器 + st01 装载链（0x418990 ANIM 列表真实打开）
        stage_name = getattr(self, "stage_name", b"st01.ecl")
        name_buf = self.game_malloc(0x100)
        self.uc.mem_write(name_buf, stage_name + b"\x00")
        ctl = self.drive(MGR_CREATE, args=[name_buf], timeout_ms=60_000)
        if not ctl or self.rd32(DAT_MGR) != ctl:
            self.fail("enemy manager creation failed (ctl=0x%08x)" % ctl)
        self.ctl = ctl
        self.wr32(ctl + 0xBC, 0)

        # 5) 装载推完（真实游戏在 0x418990 等待循环里等装载线程，这里接管）
        if not self.load_finish():
            self.fail("ANM load did not finish")

        # 6) root spawn（frame 0 首趟在构造内）
        param2 = self.game_malloc(0x54)
        self.uc.mem_write(param2, b"\x00" * 0x54)
        self.uc.mem_write(name_buf, b"main\x00")
        root = self.drive(SPAWN, args=[name_buf, param2], timeout_ms=60_000)
        if not root:
            self.fail("root enemy creation failed")

        out = open(self.out_path, "wb")
        try:
            self.frame = 0
            self.sink_frame_hook()
            self.render_frame(out)
            if self.replay is not None:
                return self.run_replay(frames, out)
            t0 = time.time()
            for frame in range(1, frames):
                self.frame = frame
                self.sink_frame_hook()
                self.drive(MGR_UPDATE, regs={"edi": self.ctl})
                self.render_frame(out)
                if frame % 300 == 0:
                    sys.stderr.write("frame %d ops=%d tex=%d (%.1fs)\n" % (
                        frame, sum(self.per_frame_ops[-300:]),
                        len(self.tex_map), time.time() - t0))
                    sys.stderr.flush()
                    t0 = time.time()
        finally:
            out.close()
        self.report()
        return 0

    def sink_frame_hook(self):
        if self.model is not None:
            # 回放窗：同 stage1 run_replay 帧序（移动→写位→发射→更新→击破）
            stage = self.replay.stages[self.replay_stage_index]
            player = self.rd32(DAT_PLAYER)
            if self.frame >= 1 and self.frame <= stage.frame_count:
                state = stage.frame(self.frame - 1)[0]
                x, y = self.model.step_move(state)
                self.model.step_fire(state)
                self.uc.mem_write(player + 0x5B8, struct.pack("<f", x))
                self.uc.mem_write(player + 0x5BC, struct.pack("<f", y))

    def run_replay(self, frames, out):
        from .replay_model import PlayerModel

        stage = self.replay.stages[self.replay_stage_index]
        sht_name = "pl%02d%s.sht" % (self.replay.character,
                                     "a" if self.replay.shot_type == 0 else "b")
        sht_path = os.path.join(self.raw_dir, sht_name)
        if not os.path.isfile(sht_path):
            self.fail("replay needs %s in raw dir" % sht_name)
        self.model = PlayerModel(open(sht_path, "rb").read())
        self.wr32(DAT_DIFF, self.replay.rank)
        player = self.rd32(DAT_PLAYER)
        self.uc.mem_write(player + 0x5B8, struct.pack("<f", self.model.x))
        self.uc.mem_write(player + 0x5BC, struct.pack("<f", self.model.y))

        total = min(frames, stage.frame_count)
        t0 = time.time()
        frame = 0
        while frame < total:
            frame += 1
            self.frame = frame
            self.sink_frame_hook()
            self.drive(MGR_UPDATE, regs={"edi": self.ctl})
            self._apply_shots_compat()
            self.render_frame(out)
            if frame % 300 == 0:
                sys.stderr.write("frame %d ops=%d (%.1fs)\n" % (
                    frame, sum(self.per_frame_ops[-300:]), time.time() - t0))
                sys.stderr.flush()
                t0 = time.time()
        out.close()
        self.report()
        return 0

    def _apply_shots_compat(self):
        """回放窗击破写入（stage1 apply_shots 的本体拷贝，事件部分省略）。"""
        model = self.model
        if model is None:
            return
        consumed = []
        VM = 0x11EC
        enemies = self._live_enemies_compat()
        for shot in model.shots:
            for base in enemies:
                flags = self.rd32(base + VM + 0x4030)
                if flags & 0x4000000 or flags & 0x21:
                    continue
                life = struct.unpack("<i", bytes(
                    self.uc.mem_read(base + VM + 0x3F4C, 4)))[0]
                if life < 1:
                    continue
                ex = f32(bytes(self.uc.mem_read(base + VM + 0x44, 4)))
                ey = f32(bytes(self.uc.mem_read(base + VM + 0x48, 4)))
                hw = f32(bytes(self.uc.mem_read(base + VM + 0x110, 4)))
                hh = f32(bytes(self.uc.mem_read(base + VM + 0x114, 4)))
                if not model.shot_hits(shot, ex, ey, hw, hh):
                    continue
                life -= int(model.SHOT_DAMAGE)
                self.wr32(base + VM + 0x3F4C, life & 0xFFFFFFFF)
                if life < 1:
                    self.wr32(base + VM + 0x4030, flags | 0x4000000)
                    slot = struct.unpack("<i", bytes(
                        self.uc.mem_read(base + VM + 0x4040, 4)))[0]
                    group = self.rd32(base + VM + 0x40C8)
                    if 0 <= slot < 3 and self.rd32(
                            self.ctl + 0x5C + slot * 4) == group:
                        self.wr32(self.ctl + 0x5C + slot * 4, 0)
                consumed.append(shot)
                break
        if consumed:
            dead = set(id(s) for s in consumed)
            model.shots = [s for s in model.shots if id(s) not in dead]

    def _live_enemies_compat(self):
        out = []
        node = self.rd32(self.ctl + 0xB0)
        for _ in range(2000):
            if not node:
                break
            base = self.rd32(node)
            nxt = self.rd32(node + 4)
            if base:
                out.append(base)
            node = nxt
        return out

    def report(self):
        st = self.d3d.stats
        zero = [i for i, n in enumerate(self.per_frame_ops) if n == 0]
        print("render-oracle frames=%d ops_total=%d quads=%d ups=%d "
              "textures=%d zero_op_frames=%d malloc=%d seam=%d" % (
                  self.frames_out, sum(self.per_frame_ops), st["quads"],
                  st["ups"], st["textures"], len(zero),
                  self.stats["mallocs"], self.stats["seam"]))


def main(argv):
    flags = {}
    positional = []
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == "--frames":
            i += 1
            flags["--frames"] = int(argv[i])
        elif arg == "--stage":
            i += 1
            flags["--stage"] = argv[i]
        elif arg == "--replay":
            i += 1
            flags["--replay"] = argv[i]
        elif arg == "--replay-stage":
            i += 1
            flags["--replay-stage"] = int(argv[i])
        else:
            positional.append(arg)
        i += 1
    exe = positional[0] if len(positional) > 0 else "games/th13/th13.exe"
    raw_dir = positional[1] if len(positional) > 1 else "artifacts/reference/raw"
    default_out = ("artifacts/oracle-draws/"
                   + ("st01-draws-replay.jsonl" if "--replay" in flags
                      else "st01-draws.jsonl"))
    out_path = positional[2] if len(positional) > 2 else default_out
    frames = flags.get("--frames", FRAMES)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    started = time.time()
    oracle = RenderOracle(exe, raw_dir, out_path)
    if "--stage" in flags:
        oracle.stage_name = flags["--stage"].encode("ascii")
    if "--replay" in flags:
        from .replayfmt import ReplayFile
        oracle.replay = ReplayFile().decode(
            open(flags["--replay"], "rb").read())
        oracle.replay_stage_index = flags.get("--replay-stage", 0)
        if "--frames" not in flags:
            frames = oracle.replay.stages[
                oracle.replay_stage_index].frame_count
        sys.stderr.write("replay: rank=%d chara=%d shot=%d frames=%d\n" % (
            oracle.replay.rank, oracle.replay.character,
            oracle.replay.shot_type, frames))
    code = oracle.run(frames=frames)
    print("elapsed %.1fs -> %s" % (time.time() - started, out_path))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
