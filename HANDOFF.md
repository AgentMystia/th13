# HANDOFF — th13 神灵庙网页移植（会话交接文档）

> **2026-10-23 整改轮更新**：本轮按用户令执行「方法论检讨与整改」（对照 th17
> 参考实录），完整证据链与整改记录见 **`docs/rectification-2026-10-23.md`**，
> 新制度落在外层 `AGENTS.md`（机制层优先/主线窗/渲染基座纪律/比对工具纪律/
> 子代理编排纪律/事故蒸馏 #2）。阅读顺序：**AGENTS.md → 本文件 → 检讨文档**。
> 工作区：`/home/mystia/项目/th13/`（外层工具仓）与 `eagler-touhou/`（monorepo，
> 分支 `port/th13`，贡献目标）。

## 2026-10-24 M2.0 STD 舞台正式销账：捕获面无缺口，视觉差=port 消费侧

- **终审（三探针闭环）**：①flush↔UP 对账 f30=19+10=29 无吞没；②
  TEXBIND（0x467786 修正对齐）实证 STD 面绑定 8 纹理指针帧尾
  refresh_tex_map 后**全部解析成功**——STD 面与 HUD/动态族**共用纹理
  图集**（f10 的 16 个 front00.png up 即 STD 面纹理名——ANM 名与纹理
  实体解耦，st01wl 面纹理在 front00 图集）；③op tex 生成时存指针 int
  （`_tex_id` 惰性设计）、帧尾统一解析——「帧中 ???」是时序正常非缺失。
- **结论重写**：oracle 捕获面**从未缺 STD**——旧台账 §七「残余真差=STD
  舞台（oracle 两窗零 STD op）」的判断作废（旧窗统计按纹理名找 stg1bg
  而 STD 面纹理名=front00 族）。**视觉差（墙艺不显示）的真根因=port
  消费侧**：port 未消费/未绘制 STD 面 op——M2.0 战果=oracle 侧零改动，
  修复面在 port（StageScenery/STD walk 的 port 侧重建，M2.5 关末链一并）。
- 探针工具沉淀：TEXBIND/FFDIST/UPTRACE（op 生成处 env 开关）三件套
  均入 artifacts/tmp-scratch，方法学两教训已录（上节）。

## 2026-10-24 M2.0 STD 舞台：机制链全活 + flush↔UP 对账闭环（探针累计 bug 侦破）

- **机制链全活铁证**：0x404b50（STD 主更新）/0x404140（视口组切换，
  B 组 {32,16,384,448} 实测正常）/0x4042a0（单面步进）/0x467350（UP
  裁剪+提交）/0x4679a0（flush→DrawPrimitiveUP）全程活跃；flush 顶点
  全在屏内。**旧「walk 未产 op」表象作废**。
- **flush↔UP 对账闭环**：f30 帧 STD flush=19 次、假设备 UP 总 29（19
  STD+10 其他族）——**捕获面无吞没**。此前「465 flush vs 33 up」的
  巨大缺口是**探针累计 bug**（FFINAL 探针的计数器跨帧累计未按帧清零，
  FFDIST 按帧分布侦破：f1=6/f4-13=15/f23-30≈19-21，累计恰=465）。
- **dev_obj/vt 链终审正确**：0x4dc6a8=0x01003000=FakeD3D dev_obj ✓
  （0x45df07 `push 0x4dc6a8`=CreateDevice out 直写）；vt 0x14c 槽=
  DrawPrimitiveUP stub（期望值逐位吻合）。深探针四级闸门全过。
- **探针方法学两条教训**：①实例属性替换计数法对 ComStubHost stub
  无效（注册时已绑定原函数引用）——用 op 生成处 env 开关（TH13_
  UP_TRACE 已入 render_oracle）；②跨帧累计计数器必须按帧清零。
- **唯一未决（下会话 10 分钟级）**：stg1bg/stg1bg2 纹理身份在 mgr
  slot4 已解析（TEXMAP 92 项含之）但 60 帧 op 里未出现——STD 面的
  SetTexture 绑定走 mgr+0xb78188（0x467785 `mov [esi+0xb78188],eax`
  传纹理指针）——挂「0xb78188 写值=stg1bg 档纹理？」探针定档 STD 面
  实际纹理来源；若 STD 面纹理本就解析为 front00 族（1072 up/60帧
  含量大族），则 STD 面已在捕获面、M2.0 直接销账。

## 2026-10-24 M2.0 STD 舞台：机制链全活铁证 + 捕获缺口定位（进行中）

- **推翻旧表象**：0x404b50（STD 主更新）/0x404140（视口组切换）/0x4042a0
  （单面步进 ~12 面/帧）/0x467350（UP 裁剪+提交）/0x4679a0（flush→
  DrawPrimitiveUP）**全链活跃**；视口组 B {32,16,384,448} 实测正常（与
  0x43bbc0 锚点一致）；flush 顶点全在屏内（127-417,15-419）；stg1bg/
  stg1bg2 纹理身份在 mgr slot4 已解析（TEXMAP 92 项含
  background/stage01/stg1bg.png）。「walk 未产 op」的旧结论作废。
- **缺口（待续）**：f30 单帧对账 465 次非空 flush vs 捕获面 33 个 up op
  ——约 430 个 STD/动态面 UP 未进捕获。深探针四级闸门（entry_nz/
  past_gate/at_9f3 全 465 通过）证明 flush 函数体全程执行、dev 调用
  （0x467a56 `call eax`=vtable 0x14c）全部走到。**探针方法学缺陷已
  澄清**：dev_up=0 的实例属性替换计数法无效（ComStubHost.method 注册
  时绑定原函数引用，后续实例替换不影响 stub 分发）——**下一会话第一
  事**：改用「在 _dev_draw_primitive_up 生成 op 处加 env 开关打印」
  直接对账 flush↔op，定位 430 缺失 op 的吞没点（嫌疑：fake vtable
  0x4dc6a8 指向对象的身份（0x01003000 vt=0x01002000，0x4679a0 的
  0x10c/0x14c/0x164 调用走的是 vt 越界槽=运气 stub，还是真 dev 表）。
- 探针工具：battle_oracle TH13_SHOT_PROBE 内 STD 段（404b50/404140/
  42a0/467350/4670e0/flush 计数+视口 dump）；scripts 侧 slot4tex/upcount/
  flushdeep 三探针（artifacts/tmp-scratch）。

## 2026-10-24 M1.3b GameOver 链修复五连 + M1.4 契约 v3 状态探针落地

**M1.3b（提交 0c6d7fc）**：全窗长跑推进至 GameOver 入口链（f1202 首触
0x43f570）。四层环境洋葱修复：①0x476e00 帧率步进空转桩（x87 读假 D3D
caps 页未映射——纯等待语义）；②0x47fde1 ptd 兄弟访问器（C++ new 链
0x483652 读 ptd+0x44 phd，未覆写→恒 NULL）；③分配族语义补全（HeapReAlloc/
HeapSize/VirtualAlloc/LocalAlloc/GlobalAlloc——原默认桩返 0=分配失败）；
④game_malloc 回收堆升级（first-fit 空闲表+合并+HeapFree 接线；原纯 bump
每帧 ~140KB 不可持续）+ HEAP 128→384MB。
- **f1213 结算排行渲染崩链定档**：0x43de80→0x47b16d（nothrow-new）→
  0x483652→0x47aee5 CRT 块头校验恒 EINVAL(0x16)（假 CRT 堆环境无块头）→
  返 NULL→0x43e2cb [0+0xc] 裸读。**分期登记 M4 W-RESULTS 第一任务**：
  修复两路已备（真 CRT 堆头实现 / wine 自举真 scoreth13.dat——哑档 24B
  已喂 raw 目录激活默认表 0x449760 路径，CreateFileA/WriteFile/CloseHandle
  写档捕获桩已落 render_oracle，游戏自己的存档器 0x449e50 可驱动自举）。
- 0x43de80 按需分期桩（battle_f>1200 挂，ret_pop=4 thiscall 形状——ret 0
  版 f1205 0x470ca6 任务链裸跳实证形状要求）。
- **战斗窗（f0-1202）全窗零崩**，GameOver 前 1210 战斗帧稳定捕获
  （ops[min=9 max=438 total=283492]）。
- 残余谜题登记（待 M4 复核）：f1213 桩在位时 0x43e28f 仍到达且
  new 返 0（probe4 铁证），probe2/3（无 0x43e28f hook）不崩——hook 拓扑
  影响 unicorn 执行流的机理未明；f1213 的到达路径（不经 0x43de80 入口的
  排行循环执行）未定位。M4 修复真 CRT 堆头后该谜题自然消解。

**M1.4 事件契约 v3 状态探针（TH13_STATE_LOG=1 → battle-state-v3.jsonl）**：
battle_oracle 帧循环每战斗帧一行 JSONL：score/graze（ctl+0x28/+0x24，
0x419a90:123/132 计分写点反推）、残机/灵击（0x4be810/0x4be814，0x427910
HUD 引用实证；值源=scoreth13.dat [diff*0x118+0x18]（0x42b990:93）——哑档
下为默认表占位 100000，M4 真档自举后自然修正）、power/step、按键集。
验证：400 帧 399 行、score f41=15 起涨（神霊收集分）、keys 逐帧正确。
draw v2 的 src 字段顺延 M2.3 ctx 桶定档时一并落（派生语义依赖 0x4032d0
五桶），verts 哈希为体积优化非阻塞——现有逐顶点比对已覆盖。



## 2026-10-24 M1.3a 死亡时间线终审：f278「伪死亡」勘误为**真死亡**——全链忠实叙事闭环

**结论：battle_oracle 对局窗自 f0 起与原局（rpy+速通视频交叉验证）行为一致；
此前「GameOver f1202 不忠实」判断被推翻，根因是我方两处仲裁误读。**

- **原局真值叙事（三源交叉）**：开局 3 命（HUD 心=备用机 lives−1，视频开局
  恒 2 心）→ **f278 死亡#1**（vf-750=死亡后 1 心实证）→ f814 死亡#2 → f1160
  死亡#3 → **f1202 GameOver → Continue 续关**（rpy 输入 f1200=state9 Z+Shift
  恰为续关确认）→ 续完全程 11675 帧、终分 16,999,054（视频 HiScore=本局终分，
  同局铁证三重：轨迹/HiScore/输入流）。我方 oracle 死亡时间线 f278/f814/f1160
  +GameOver f1202 **逐帧同拍**。
- **两处仲裁误读（蒸馏）**：①「rpy 输入流连续走位=无死亡」——死亡期间输入
  照录，输入流不可能证伪死亡；②「视频 f690 玩家中场存活」——video=game+400
  映射在早期段有 ~40 帧漂移（C≈360-400），且 analyze_image 的 y 坐标在弹幕
  稠密帧不可靠（x 坐标与模型轨迹吻合）。
- **f278 死亡机制（draws 取证）**：杀手弹 = f278 新生 bullet2.png 闪光态弹
  （着色 0xECD000FF/blend[5,2,1] 单独批）生成于 (220,168)，玩家 (224,178)
  生成即中；源头 = f279 etbreak（**Ecl_EtBreak 脚本自爆**，非击杀）@ (237,182)
  半径 ~22 爆环。自机弹系统完全忠实（sht 文件直证：主弹 +0x2c=0 直线弹
  dmg24×2/3帧、卫星 +0x2c=1→0x446cb0 追 option dmg4；power=100 块1 ✓
  dcc30=1 replay 态 ✓ 额外残机 0 ✓）。
- **勘误链（本轮证伪的三条旧「事实」）**：①「玩家轨迹←按190帧贴左墙」=
  稀疏采样错觉（f60-240 未 dump，实为织布走位；模型/真机/视频三源一致）；
  ②「左墙=−96.117」= 死亡冻结误读（实际坐标屏移 +320/+16，全场自由）；
  ③「敌机 life=40 早期可杀」= 旧 bypass 读错字段（ECL bf74 setLife=1500，
  0x41d35b=ECL 指令写点实证；原局同样杀不动，妖精到路径终点自爆）。
- **旧 bypass 世界解剖**：击杀=python `apply_shots` 模拟伤害（AABB 写
  vm+0x3f4c+置 0x4000000），0x419a90 真击杀链全程桩死→无掉落/无死亡效果/
  无死亡开火——旧事件基线的击杀/弹幕面从未对照过原版（视频 f290 弹幕浓密
  vs 旧世界 f0-450 零弹出生）。W-S1 参考基准必须以 battle_oracle 为准。
- **新登记锚点**：0x4460d0=玩家状态机（死/复活/Z门/出膛触发，0x43240 战斗
  主任务调用）；0x446790=自机弹池更新（256×0x9c，ctx=DAT_004c22c4+0x6a0）；
  0x4464d0=单弹更新（record+0x2c 每弹型函数派发）；0x446f20=敌追踪弹/
  0x446cb0=option 卫星弹（主表 0x4b9ad8）；0x4179e0=life 扣减器（0x419a90
  每 enemy 每帧调用）；0x448200=**原版自带 replay 输入注入器**（ESI=replay
  mgr，u16→0x4e49f0 键盘——未来 replay 模式正道，替代 python 注入）；
  0x4c0ae0=[3,3,6,9] 难度表；0x4b9adc 起 5 项 shot 描述表。
- 探针工具（battle_oracle TH13_SHOT_PROBE）：链活性矩阵/life 写哨兵
  （EIP 取证）/弹 record 指针 dump/power 状态行。`--out /dev/null` 长跑模式。

## 2026-10-24 确定性对齐战役 M0+M1 战报（oracle 真机全链路打通）


**主线窗（用户令：菜单/难度/机体/UI/HUD/游戏系统/Stage1 全链路确定性 100% 对齐，
代码级门控不看图）。计划全文见本会话批准的战役计划（M0-M5）。**

**M0（门禁奠基 + GL -96 定案销账）**：
- web DrawCapture 影子落地（GraphicsDevice 内嵌转发/提交时快照 pipeline/
  present=帧界/纹理身份按原版 26 槽登记；捕获构建 `TH13_WEB_CAPTURE=1 node
  portable/build.mjs` → `web/artifacts/sdl3-capture/`；驱动
  `scripts/web_draws_capture.mjs`；debug 页 `?artifact=` + capture/pixels API）。
- **GL -96 定案：GL 层并无偏移——旧测量是陈旧构建假象**（旧主产物 builtAt
  早于拔 hack 提交 162abd36 十分钟）。fresh 构建三重验证：提交位红像素 325/
  -96 位 0，glReadPixels 与 toDataURL 逐位一致。**残余真差 = STD 舞台**
  （stg1bg 墙艺 [32,416]）：oracle 两窗均零 STD op（0x404140 walk 不在捕获面）；
  Ghidra 新锚点：0x43bbc0 两组相机视口 {0,0,640,480}/{32,16,384,448}、0x404140
  组切换、0x43ba20 zf=1800（移植误 8000 已修）。教训：视觉结论前必须核对产物
  sha↔源码提交；「运动带」类测量会被单一子系统主导。
- 详见台账 §七/§四改写（docs/plans/visual-ledger-2026-10-24.md）。

**M1.1+M1.2（battle_oracle 真机全链路对局窗，五项验收全绿）**：
- `oracle/battle_oracle.py`：TO 子类直投场景 7 + 0x42b990 装载线程体**屏障感知
  整体驱动**（对策：预开六档+load_finish/mgr+0x20 直写 -1/Sleep 排空/0x418d30
  屏障原子续驱）。
- **四层崩溃洋葱全解**：①TO 屏障钩子刹停整体驱动→屏障感知；②title_enable
  块战斗期对已废弃对象误触发（f180）→请求后不回头；③**render_oracle.py:835
  把通用 ANM VM 创建 0x46f820 误桩为「死亡炸裂环弹返 0」（事件窗语境）——
  窗C f1003 崩溃与本窗 f471 同根（寄存器轨迹实证：调用方 eax=有效档、入口
  eax=0）→拆桩**；④0x462450（ANM 脚本启动）/0x461cb0 同族误桩→拆桩+**有
  score 路径定档**（DAT_004dcc30=1=score 系统初始化语义，与存档存在无涉；
  窗C 清零属旧权宜）。BGM 变体 0x405a20 按其 <200 帧早退分支语义桩（音频域，
  登记偏差：跳过 0x4be830|=0x100 位写）。
- **验收数字**：1200 战斗帧零崩溃；0x418e00 每帧恰 1（双跑防护）；捕获面
  对照 hud-capture-plan 十族表爆发——HUD 全族（front00×22874/agauge 神灵槽
  ×1199×2/lifebar/ename_full/ascii×4748）、**敌弹批 bullet1/2/3**（族8）、
  神灵 littlegods×12612、符卡面 cardbg0/1+eff_aura+Boss 立绘脸 face01ct；
  双跑 sha256 逐字节一致。
- **M1.2 真输入路径**：replay u16→VK 键态注入（真机 0x471620 打包/边沿/连打
  全原生）；**真玩家管理器（0x4065a0 系，调度器自然驱动）与事件窗验证过的
  python PlayerModel 前 278 帧逐帧吻合（<0.02px）；f279 玩家真死亡链首活**
  （真碰撞/复活运行；旧旁路模型不建模死亡=预期分歧面——f122/f124 卫星弹
  根因「option 恒零」随真机管理器运行已具备拔除条件）。
- 遗留登记：STD walk（0x404140 族）仍未产 op（M2.0）；自机弹族待有输入窗
  确认；0x419a90（击杀/掉落/计分）事件窗桩待拆评估（M2.4）；player 预设
  character=0 时 plXX 预开已按角色定档（窗C 硬编码 pl01 修正）。

## 2026-10-24 视觉补全战役·阶段 0 战报（golden 勘误 + 回归根因拔除 + 确定性对齐立项）

**本轮主线（用户令：修复「画面不堪入目」回归，特派视觉子代理两轮读图，
随后升格为确定性对齐——「通过即与原版一致，不依赖看」）。完整证据台账：
`docs/plans/visual-ledger-2026-10-24.md`。**

**三重根因定案（全部 oracle/程序化数据裁定，非目测）：**
1. **golden 工具勘误**：速通视频缺游戏左 97px（video_x=2.2556×game−219.7，
   三点锚定）；crop 0:0→1224:1080:0:0 + scale 544:480，74 帧全量重抽；
   visual_diff 适配（ours 裁 [98,640]）。游戏左 98px 无 golden 佐证（登记）。
2. **回归根因=上一战役的 -96 图像对齐 hack**（StagePresentation 四 pass
   renderer.offset-=96，其「图像实证」参照系正是裁坏的 golden）。oracle
   470,056 个敌机顶点分布（f0-6000, L56）：中位 323.5、主体 [160,448]
   ——**playfield=[128,512]×[16,464] 铁证**；a8e54eee 的 320 正确，hack
   错误，已拔除。
3. **ScreenViews 游戏相机视口 th15 遗产 32→128** 修正（与 2 同根）。

**门禁实况（改动后复跑）**：双构建绿；harness candidate sha 与改动前
逐位一致（捕获面不含表现层）；run-gate **BASELINE-HELD f511/f124**。
渲染统计契约 1/1 绿。visual_diff honest 基线：mean=0.301（阈值 0.02）。

**⚠️ 未决最高优先（下会话第一件事）**：**GL 渲染层 -96 偏移**——运行时
探针（th13_probe_camera）证实提交坐标正确（玩家 quad @305=游戏坐标、
相机 {128,16,384,448}/view-320 生效），但像素仍 -96（与 hack 时代逐位
同图）。裁决协议：实现 web 侧 DrawCapture 导出（提交清单已证正确）+
glReadPixels 定点采样 → 提交/GL/呈现三段二分。**修复前勿再做视觉比对
结论**（当前所有 ours 截图含此偏移）。

**确定性对齐架构（用户令，进行中）**：① web 侧语义清单导出入门禁（拔
-96 类「矩阵对、像素错」盲区）；② oracle 捕获面扩展 0x40e4d0 族（HUD/
玩家/弹/道具——现清单 f1242 仅 61 effect op + L56 敌机批）；③ 栅格层
共享件信任+统计契约。视频 golden 降级为最终人审辅助。

**阶段 2 字体战役就绪度**：th13 字体链反编译全解（8 HFONT/6 唯一记录，
Quality=4≠th15 的 2；slot 0/1=正文、2=名牌、4/5=整行；2× 超采样 DIB
ARGB4444 + 四偏移描边 + 0x45a080 平滑；名牌框 0x42a120/0x42a280；对话
ANM 面 bank0=text.anm）——台账 §六。烘焙环境待裁定（wine/unicorn 直调）。

**其他**：标题窗 f421 实拍=正确主菜单（旧截图难度画面系旧构建行为）；
探针基建入库（probe-cam/quad/world + BrowserRuntime th13_probe_camera
导出，默认休眠）；二轮视觉审计 14 项裁定与修复优先级 Top5 见台账 §三。

## 2026-10-23 对局窗机制层战役战报（f61+f0 双销账，RAW 无过滤恢复）

**下一战役登记（自机卫星追尾弹重建——回放窗 f124 / 事件窗 f122 同根因）：**

- **诊断完成**：fresh oracle 事件基线（`artifacts/tmp-verify/udsr01-oracle-2026-10-23.jsonl`，
  复跑 f122 与旧值同 → 旧基线非腐烂、f122 为真分歧）对拍：流全同至
  f121，oracle kill(enemy 2)@f121 vs port @f124（3 帧迟）；敌机轨迹/自机
  位置/主弹（dmg24，±10 偏移，12px/帧）**全部一致**；分歧只在
  **卫星弹（dmg12，pl00a 块1/6 类别8）**：port=出膛后 ±π/12 外扩角射
  （vx±3.1/vy11.59），oracle python=自机位直上 12px/帧——两侧都是
  **临时占位**（replay-twin-divergence.md §3「卫星弹/特殊块分期未做/
  垂直等效值待实证」登记在案）。回放窗 draws f124 op[16] count 6≠4
  即此分歧的视觉面（port 敌 2 多活 3 帧）。
- **原版机制入口（已锚定待重建）**：①0x445c40 出膛：anchor≠0 时
  spawn 位 = option[i] 位（player+0xa230+i*0xe4，+0xa230/+0xa234 位、
  z=0），角度域 ≥1000 = 敌瞄准特殊（对 +0xa27c 向量 atan2 + π/12、
  伤害 = rand+rand+记录值），995≤ang<1000 = 卫星特殊（方向 = option
  +0xa27c 浮点角），<995 = 固定角；spawn 位修正 −(shot+0x60/+0x64)
  （0x44736a0 乘积）；②0x446000 出膛节拍（帧%interval==相位 +
  类别2占用槽 0x146d8 门）；③option 对象轨道更新函数未定位（写
  +0xa230/4/+0xa27c 者，0x441dd0=init 不含）；④飞行中追尾转向函数
  未定位（0x446bf0=消亡回调非转向）。重建需先 oracle watch option
  数组逐帧值定轨道模型。
- **追尾转向已定位（0x446f20，弹每帧更新）**：弹位 = option 位
  （+0xa230 系，**1/128 子单位存储**，×0.0078125 换算）→ shot+0x2c/30；
  方向角 shot+0x48 每帧 `+= normalize(记录角 − 当前角) × 0.1`（10%/帧
  比例转向，向 sht 记录 +0x14 的目标角收敛——ReimuA 卫星散射后竖直
  收拢的机制本体）；0x438220=角度差归一。剩：option 轨道 writer
  （+0xa230/4/+0xa27c 的每帧写者）与 oracle 玩家管理器驱动扩展。
- **战役规模升级（2026-10-23 option_probe 实测）**：当前 oracle 最小
  环境下 **player+0xa230 option 数组恒零**——原版玩家管理器每帧更新
  未被 oracle 驱动（replay 会话只 init 玩家对象供瞄准，玩家/弹全走
  python 模型）。⟹ 卫星重建的前置 = **oracle 侧扩展驱动真实玩家
  管理器更新链**（0x441dd0 init 已通、每帧 update 函数待定位挂接），
  然后原版自己的 option 内存即真值源，port 照抄。探针：
  `oracle/option_probe.py`（apply_shots 挂点逐帧 dump option×4）。
- **主弹语义已对齐无需动**（P0 轮 + 本轮探针双证）。

**两前沿全部机制层修复，对局双窗回到无过滤全字段比对：**

- **f61 敌机行走位差 96px（已销账）**：根因 = **0x40000 屏幕原点常量错误**。
  原版 th13 的游戏层 VM（layer<21 → flags 0x40000）屏幕原点 = **+320/+16**
  （.data 双精度 0x4ae9f0=320.0 / 0x4ae8e0=16.0；0x466a80 up 路径、0x468670
  quad 路径、0x46f820 创建模式 bit0 三处一致 + oracle anm_sync_probe
  0x41a9d1 写点实测 off=0/attach=-1 排除偏移表与挂接支路）。port 误用
  th15 遗产的 **224**（screen_offsets[2]，四处 setter：harness×2 +
  Application×2）——差值恰 96px。修复 = 统一改 {32,16,**320**,16}。
  排除法证据链：f9 世界坐标（0x419450 界检查 x∈[-192,192]）与事件流
  全对齐 → 运动链无罪；oracle SYNC 探针证明 VM+0x574 写入两侧同值 →
  位置跟随无罪；u/v 对上 script5 sprite12 → VM/脚本无罪；差值恒定
  96px 逐帧无漂移 → 常量性偏移 → 锁定 adjust 阶段。
- **f0 tfactor 残留（已销账，`--ignore tfactor` 台账清零）**：根因 =
  **ECL 30x/镜像创建点色/α 插值缺一步「ctor 趟步进」**。原版 0x41fe80
  case3 与 0x419450 镜像块在敌机更新趟（0x41a8a0 直调）内创建 VM，
  创建趟对色通道多推进一步（oracle VM+0x15c α=6/timer(+0x160)=2 而
  port 4/1 位级实测；α 插值 0→64/30tick）。非装载趟特例——f9 第二个
  环境精灵同症证明是**每次创建**的普遍行为。修复 =
  `StagePresentation::create_color_step`（三创建点：kind0 特效/kind1
  槽位/镜像重挂；`AnmInterpolators::advance_color` 仅色/α 两通道——
  整池 update 会推 world 矩阵，实测破坏 f0 对齐，故外科化）。
- **销账实测（RAW 无过滤）**：对局无输入窗 f0→**f511**（3600 帧窗
  14%），回放窗 f0→**f124**；标题窗 A f487 / B f379 无回归；
  golden 对跑 BASELINE-HELD（run-oracle 顺手修 ERR_STRING_TOO_LONG：
  1.1GB 候选整读 toString 撞 Node 512MB 上限 → 首行头部块读 + sha256
  流式）。
- **新前沿登记**：无输入窗 f511 = op[64].verts[12][1] 446.5≠447.5
  （1px y——x87/SSE 数值语义面，AGENTS.md 数值纪律管辖）；回放窗
  f124 = op[16].count 6≠4（批内精灵数差——回放输入驱动面）。
- **取证脚手架入库**（外层仓）：`oracle/anm_sync_probe.py`（0x41a9d1
  同步趟写点 + VM 位写入跟踪 + up 调用者追踪 + TFACTOR 写点 VM dump）、
  `oracle/load_trip_probe.py`（装载趟驱动序列）、`scripts/
  disasm_enemy_sync.py`（capstone 指令级反汇编）。

## 2026-10-23 整改轮战况（基座治理 + 测试契约，全部容器实测）

- **GraphicsState.hpp 与 th15 逐字节归一**：30 行无登记分叉清除——th13 语义
  （TFACTOR 初始 0）下沉到两个本作配置点（`GraphicsDevice::initialize` +
  `DrawCapture::open_output`，蓝图 §8.8 oracle 实证）；`configure_game` 的
  Factor 第二参数 th13 全树零依赖（AnmRenderer::material 显式 Diffuse）。
- **敌机句柄化扶正**：`enemies_from_ecl`+`ecl_slot_anm` 成为 web 默认路径
  （RunScene::initialize；0x41fe80/case3 同构），敌机死亡经 `StageSim::
  retire_sink` 回收槽位 VM（0x418e00 销毁支路 ANM 面）。**验证：st01 事件窗
  3600/10187 复跑 `none`（句柄化零污染）；draws/draws-replay 两窗与基线
  逐字节同；容器四套单测 62 例全绿**（顺手修复 ui 套件自 94daf443 起的
  read_asset 签名编译破损——「三门禁绿」脱节的实证，检讨 §1.4）。
- **测试契约（th15 式）**：渲染统计契约浏览器测试
  `web/tests/sdl/graphics-stats.test.mjs`（真实 chromium 泵 demo0 回放
  180 帧：无 subData 流式/glBufferData 整批/quad 直通主导/布局缓存有界，
  **实测通过**）+ draws golden 对跑 `tools/replay-verifier/`（corpus +
  run-oracle→run-gate，BASELINE-HELD 退化检测，两案例实测通过，golden
  只升不降）。配套：`th13_debug_stats` 导出 + HEAPU8 运行时导出。
- **draw_compare 去鸵鸟化**：`--ignore` = 诊断模式（RESULT 尾 `ignored=`
  登记、exit 3 与门禁区分），契约条文 draw-contract.md §3.5（绿灯链路
  禁用 + 限期销账）；NaN 相等性入契约。**`--ignore tfactor` 唯一登记项**，
  销账条件 = 装载趟插值进度残留态建模完成。
- **基准勘误（体检实测，旧登记已腐烂）**：①对局窗「f9 神灵出生坐标差」
  **已不存在**（f9 world[12] 两侧全同）；当前真实前沿 = **f61 敌机行走精灵
  x 差 96px（ECL 运动链）+ f0 tfactor 一帧滞后族**（port 0x4fff5ff vs
  oracle 0x6fff5ff，alpha 通道差）——两窗同源（下「下一步」）。②udSr01
  replay 事件窗实测 **f122**（基线 commit 与整改后同值；旧登记 2652 已
  漂移，oracle 侧基线文件 udsr01-oracle-fix2b.jsonl 时代不符，需重锚）。
- **文档制度**：ADAPTATION.md 升格 th15 式编号验收清单（战报叙事移交本
  文件）；`ports/th13/docs/plans/coverage-matrix.md` 独立化（8 类场景窗
  ×状态×销账条件 home）；蓝图 §8.7/§8.8 补锚点（悬空引用修复，检讨
  §1.5——pickaxe 证实两节从未存在而 8 处代码引用）。
- **主线切换声明**：下一战役主线 = **对局窗机制层**（f61 ECL 运动链 +
  f0 tfactor 残留态建模——按 AGENTS.md 机制层优先原则走反编译导出，
  不做参数微调）；标题窗 A f487 ins_408 冻结为引擎语义工作面（机制层
  升级判据已触发登记）。

## 2026-10-23 凌晨战况（全链路战役 S1 进行中，AGENTS.md 已改「交付目标导向」）

- **交付验收协议（用户令，2026-10-23）**：v1 交付门=①逻辑层与 decomp/oracle 对齐（事件流窗口全绿）②渲染语义全链路对齐（各场景 draws 窗口 none）③**最终视觉验收由主会话执行**：图像分析 MCP（analyze_image）将 Web 版截图与原作对应视频帧（`artifacts/reference/golden/`，video=game+400 锚定）并列提问「这两张图是否一样？如不一样哪里不一样？」——一样→交付；不一样→定位原因→修复→再验→交付。多场景覆盖（标题/难度/角色/对局/HUD/暂停/结算/Stage2 入场），采样不得有偏。

- **验收标准（用户令）**：首个验收版=全链路（主菜单→Stage1 过关→结算→进入 Stage2）渲染+行为双一致；裁决=draws/事件 oracle；主会话直接编排+子代理（DW 仅机械磨轮）。
- **标题窗 A/B（600/750 帧）**：oracle 双窗冻结（`oracle/title_oracle.py`，输入序列/位布局/起终点规则见 docstring）；移植侧 harness `title`/`title-b` 同构导出；**首分歧已从 0 推进到 290**（0-289 全对齐：boot logo 幕+@R 边条+背景层值+blend 全过）。
- **P0 已修**：提交 74f54c1a 曾静默打破 udSr01 事件基线（旧「10662 帧一致」作废——绿基线会腐烂的现行案例）；现已两侧同改**原版弹型语义**（.sht 弹型域：帧%间隔==相位出膛、伤害=记录+0x18、块=功率/100+聚焦×5，字节依据 `docs/plans/replay-twin-divergence.md`）：无输入窗 3600/10187 恢复 none；**udSr01 事件窗 105→2652**、th13_01 st01 105→2625（残留=敌 78 族 ECL 出生时机差 6 帧，待查）；对局绘制窗 511 基线无倒退；三门禁绿。
- **关键机制发现（本轮实证，蓝图 §8.8）**：TFACTOR=持续设备态（缺省 0、跨帧保持、仅 world_quad 族批设置按 VM 色+变化缓存写入、up 路径色调全走顶点色）；@R=text.anm 离屏 RT 场景栈常量提交（blend 恒 (One,Zero)）；sig 幕止于 raw f179（f180 空档帧）；「3D 变换期层+1」现象（sig 右半/LOGO 翻转期 L26，机制待 trace）。
- **S2/S3 前置研究全入库**：`hud-capture-plan.md`（对局窗路A/10 op 族/暂停菜单脚本 132-133）、`results-flow.md`（场景全表/Destination 真值/结局表）、`results-overlay-anatomy.md`（**过关=对话+fade 直进下关，无中间结算屏**；31 态覆盖层/计分 9 分量）。
- **f752 边界（真全链路窗）**：装载线程体 0x42b990 已通、方向1装载分片成立（前沿 f754）、缺口队列（D3DX 除零/场景7特效表）与战报在 `artifacts/tmp-scratch/f752-attack-report.md`；窗C 现为拼接版（`fullchain-draws.jsonl`）。
- **子代理配额**：GLM-5.3 周配额 10-23 03:51 重置；倒下时代理：菜单工程师（轮3 崩溃遏制已由主会话收尾提交）、研究员（P0 修复已由主会话验证提交）。
- **下一步优先级**：①标题窗 290→600/750（LOGO 层+1 机制 trace→菜单工程师；菜单条顶点色/粒子 quad 路径/TFACTOR 渐变）②udSr01/th13_01 2652/2625（敌 78 ECL 出生时机）③f752 前沿→HUD 窗（按 hud-capture-plan）④S3 PostGameFlow+msg 播放器。


## 一句话状态

**游戏逻辑已与原版行为对齐（事件流逐字节一致），画面/菜单尚未与原版一致**——主菜单是当前明确要攻克的下一仗，方法论与验证器已完备，正等执行。

## 已达成（硬成果，均容器内实测）

| 成果 | 证据 | 窗口 |
|---|---|---|
| 事件流对齐（无输入） | 3600 帧 / 10187 事件逐字节一致，`RESULT first_divergence_frame=none` | st01 无输入 |
| 回放驱动对局对齐 | 10662 帧 / 48406 事件逐字节一致；Boss 1非帧两侧同 10362 | `th13_udSr01.rpy` |
| RNG 语义 | 原版 `0x47BBA2`（MSVC CRT `_rand()`）unicorn 直调序列 = C++ 镜像 = MSVC srand(1) 标准 | — |
| 录像格式全解 | t13r / 双趟 XOR (0x7D,+0x3A,0x100)/(0x5C,+0xE1,0x400) / LZ77 MSB / 6 字节帧输入；`th13_udSr01.rpy` 6 关 105476 帧差分零错；`docs/plans/replay.md` | — |
| 渲染 oracle（假 D3D9 设备） | 两窗导出 30 万 + 77 万 op 绘制清单，两跑字节一致自证；纹理指针→ANM 名映射实证 | 对局窗 |
| 移植侧同构导出 | WASI DrawCapture，385 万浮点串与 Python %.9g 零差异 | 对局窗 |
| 绘制语义对齐进度 | 帧 0–91 逐字段一致 → 修复轮推进到 **分歧帧 511** | 对局窗 |
| 双目标构建 | Emscripten+SDL3 网页构建 + wasi harness，均绿；浏览器实机可启动、回放自动驾驶可复现任意帧 | — |

## 方法论（AGENTS.md 已固化，摘要）

- **事实源→双目标→oracle→首分歧定位**：Ghidra 12.1.3 导出（`artifacts/decomp/jp/`，2291 函数）→ C++ 双目标（网页 / wasi 无头）→ unicorn 假设备导出语义绘制清单 → `draw_compare.py` 逐帧逐字段 `RESULT first_divergence_frame` 裁决
- **视觉永远辅助**：看图/像素不是门禁；一致性靠「数据按构造驱动 + 语义清单比对」；视频帧仅锚定时间轴（**video = game + 400，斜率 1.000000**），禁用「秒×60」
- **覆盖矩阵与交付门禁**（菜单漏网事故蒸馏）：①每阶段列覆盖/未覆盖，未覆盖必须被下一阶段消费；②标题/难度/角色/对局/HUD/暂停/结算各自独立 draws 窗口；③终审材料按「用户可见画面清单」出全
- **数值纪律**：x87 80 位 vs SSE2 差异优先怀疑；可证等价才换 float
- **汇报纪律**：结论先行+证据+边界；百分比标置信度；主动纠偏；bug 样本留作回归

## 关键数据事实（从数据解出，勿凭记忆）

- 原版 v1.00c（PE 时间戳 2011-08-17）；SHA256 见 `artifacts/games-sha256.txt`
- **一面 Boss = 西行寺幽幽子**（st01 对话原文『幽々子様』，cp932 解码实证；子代理曾幻觉「幽谷响子」，已纠正）
- 主菜单右侧立绘是**角色**（`face/pl0X/title_pl0X.png`），**th13 无幽香立绘**；`title_v.anm` 只有版本号条（锚点 256,224）
- title.anm 242 脚本锚定：主菜单根 script0（70,280，子脚本 3-22）、难度根 script1（70,312）、角色根 script2（70,312）、背景 script89-93（title00a/select00 全屏，layer 21/22，60 帧呼吸）、LOGO script94-100（東方靈廟 script96 @(132,150)、TEN DESIRES script97 3D 翻转 (448,368)→(132,150)、版本条 script100 @(360,480)）、菜单高亮+光标 script101-112（select01.png）、角色立绘入场 script125-128（@(144,480) 滑入）、角色菜单根 script129/130、武器图标 script131-142
- msg dump 是 **cp932** 编码（thmsg -d13 未带 -j，按 UTF-8 直读全是乱码）
- 速通视频 `artifacts/reference/video/th13-speedrun.mp4`（1920×1080@60 CFR，33:03）与 `games/replays/th13_01.rpy` 同局
- 视频游戏区裁剪：x∈[0,1440) 全高，→ 640×480 lanczos；golden 帧在 `artifacts/reference/golden/`（77 帧 + index.md/README.md）

## 现状病灶（主菜单）

- 根因（蓝图已出）：**精灵实例化链路脚本号锚定错误**——背景/LOGO/光标/版本号精灵未创建；菜单项 label 语义待按 dump 核对。ANM 管线骨架已与 th15 同构，修的是 `TitleSceneResources` + `TitleMainMenu::update` 各 substate 的 create/interrupt 序列
- **蓝图第二轮（agent_2697c80c，2026-10-22）关键锚定**：
  - th15 主菜单 = **纯 ANM 脚本画面**（无 STD 场景）；标题不经 SceneDisplay
  - th13 数据布局与 th15 不同：**bank17(title_v.anm) 只有版本号条**（锚点 256,224，layer26），**无幽香立绘**——立绘在 bank16 entry8-11 `face/pl0X/title_pl0X.png`，入场 script125-128（底锚 (144,480) 滑入），根 script129/130
  - **背景 = 两层叠加**：森林底图 `title00a.png`（512×480 + 右半 xOffset512 拼 640，script90/91，ins_6(170)） + 红色星光 overlay `select00.png`（script92/93，ins_404(64,64,64) 压暗，ins_6(340) 高层，ins_408 224↔192 呼吸），根 script89
  - **LOGO**：bank16 script94/95（根）→ 96（東方靈廟 sprite76 @(132,150)）、97（TEN DESIRES sprite77 3D 翻转 (448,368) scale2.5 → (132,150)）、98（闪爆）、99（复位）、100（副标题 @(360,480)）
  - **菜单文字**：烘焙在 `title01.png` 的 128×16 字条精灵（entry0 sprite0-49），10 项 × 2 态 = script3-12（正常灰 192,192,192）与 script13-22（选中白 255,255,255），根 script0 锚点 (70,280)，子脚本 y 步距 18，label 27-31 供选中切换（与 th15 paint() 体系同构，可原样移植）
  - **难度根 = script1**（70,312，子脚本 23-48）、**角色根 = script2**（70,312，子脚本 49-82）——th13 现状沿用 th15 的 124/125、134/135、子脚本 126/147（th13 不存在，必须重锚）
  - **光标**：难度/角色菜单用 select01.png sprite89（14×14），script111 @(254,270) / script112 @(382,270)；高亮条 script101-110（x=-256 滑入到 32）
  - **提示「按任意键」**：ascii bank2 script19（逐字符渲染，16 字符位循环 ins_502(script18)）
  - **差距清单 13 项**见代理报告（portrait 用错、背景缺失、提示幕脚本号错、LOGO 缺失、版本号缺失、菜单项数/枚举错、难度/角色脚本号未锚、角色选中态子脚本错、TitleFrame 缺 demo/音乐重播/return_reason/Quit 位、TitleScene 缺屏、析构漏 bank19、Draw pass 占位、测试用合成 ANM）
- 运行时资产错误已修：`th13.html` MEMFS 清单曾漏 `st01.std`/`st01wl.anm`/四机体 sht → 已补齐 24 项并与 `prepare-assets.mjs` 对齐（提交 `833118aa`，**强刷即过**）
- th15 蓝图研究结论详尽（见下「蓝图落点」）

## 蓝图落点

- 工作流脚本（已清理出草稿目录，归档保全）：`artifacts/workflow-drafts-archive/`（4 份：Stage-1 攻坚 / 渲染语义对齐 / 渲染语义对齐-终审填洞版 / 主菜单ANM重做）
- 下一仗蓝图文档待落档：`eagler-touhou/ports/th13/docs/plans/title-anm-blueprint.md`（工作流阶段 2 任务；蓝图内容已在 CONTEXT 摘要，代理研究结论见会话记录 agent_42f25449 输出）
- 既有方案文档：`docs/plans/{ecl-vm,anm-pipeline,ui-base,oracle-entry,replay,render-chain,draw-contract,event-contract}.md`

## 坑位（血泪，必读）

1. **工作流门禁路径必须先实测存在**：渲染对齐工作流门禁硬编码臆断路径（移植清单实际在 `artifacts/port-draws/`，不是 `web/artifacts/wasi/`）→ 8 轮 -1 空烧 2 亿 token
2. **oracle 比对脚本需显式位置参数并先自证**：`stage1_compare.py <wasi.jsonl> <oracle.jsonl>` 少传参 → exit 2 被当成无 RESULT（前科：8 轮假 -1）
3. **workflow `world.run` 的 podman `-w` 用容器侧路径**（`/work/...`），卷挂载是宿主路径
4. **工作流 workspace 锚定 Bash 会话 cwd**——提交工作流前先 `cd /home/mystia/项目/th13`（曾锚定到 ports/th15/web，全路径失效）
5. **emscripten 缓存认 `EM_CACHE`**（不是 `EM_CACHE_DIR`，会被 `EM_CONFIG` 压过）
6. **注册 th13 需三处**：workspace.json、runtime-builds.json、**common/contracts/product-catalog.mjs 的 PRODUCT_GAMES**（漏第三处 → launcher 直接抛错；launcher 依赖先 `npm --prefix launcher ci --ignore-scripts`）
7. **Makefile dev 目标 `-e HOME=/home/dev`** 防 XDG 落脏工作区（曾出 `.config/.local` 污染）
8. **YouTube 下载**：机器人验证挡一切无 cookie 路径；bgutil/cobalt/Invidious 全不通；**Brave cookie 离线解密失败**（KWallet 口令 GCM tag 101/102 不符，v11 新式加密）；最终靠用户粘贴会话 cookie 解决（25 条，用后**已删明文**）。视频已下载就位
9. **凭证纪律**：读 KWallet/浏览器 cookie 前必须先征得用户同意（前科教训，见记忆 `never-touch-credentials-without-asking`）
10. **并行子代理文件集边界**：同阶段 Promise.all 的工程师要明确文件所有权，冲突时按专长裁决并立即 git 提交立保护
11. **动态工作流缓存**：已 settle 阶段的 ask 文本一个字不能改（改 = 全失效重付）；修订用 Edit + AmendWorkflow；尾洞填洞用 FillWorkflowHole
12. **GLM-5.3 个人订阅周配额**（5 小时/周，code 1308/1310）→ 配额满时用户指示切模型（zai-glm-5-3$max）或等重置后 ResumeWorkflowRun

## 渲染一致化战役进展（2026-10-23 会话）

**可靠渲染比较器已落地**：标题窗 A（600 帧）与对局回放窗（11065 帧）的语义绘制清单
oracle 双窗比对链路啣动。`oracle/draw_compare.py`（+`--ignore` 粗筛开关）为唯一裁决。

**标题窗 A 推进记录**（f0 → f487，81% 窗口）：
- boot tick 对齐（advance_loading 首帧补 update）→ f0
- select00 右半创建期层 21→23 → f180
- 灰白交换模型（灰条入场 = 旧隐+新 VM 尾插 rearm_grey + w13 poke 窗收窄 181-191）→ f371
- 移动交叉曲线（w15 降 5 帧 {163,91,40,10,0}、旧灰 s2 出、新灰恒驻）→ f381
- ver 批序（requeue_to_back 新 API + ver 延帧创建）→ f389
- Z 序（ver label1 当帧中断 + retire age==9 + LOGO retire age==15 + fade 12.75/帧精率
  + 黑条 -8 独立率 + 跳过已隐项 + w14 poke 收窗）→ f483
- 背景亮化 label 3（Z+18）→ f487
- **f487 起暂停**：ins_408 首帧语义差（oracle 首两帧不步进、port 首帧步进）——引擎语义工作面

**对局回放窗**（旧基线 f8 → 现 f9）：
- f8 ops len 2!=1 = port 弹型脚本 spawn 的子弹入层桶（真机走 IShot 独立遍历）——
  draws-replay 层桶绘制隐藏 player-bank 池 VM（flags bit0）镜像捕获面 → f9
- f0 tfactor 残留（a=4 vs 6）= 装载趟插值进度残留态（§8.8 族，`--ignore tfactor` 隔离）
- **f9 真工作面**：神灵出生坐标差（port world[12]=(188,190) vs oracle (57,208)）=
  ECL 独立种子 0 流（[-9987] 抽）不同步 —— 需 ECL RNG trace 对拍

**【2026-10-23 整改轮勘误】**上段 f9 神灵坐标差实测已不存在（world 两侧全同），
现前沿 = f61 敌机行走精灵 x 差 96px（ECL 运动链）；`--ignore tfactor` 已改
诊断模式（draw-contract §3.5），本节保留作历史登记。

## 已知缺口（覆盖矩阵「未覆盖」清单——home 已迁 `ports/th13/docs/plans/coverage-matrix.md`，本节为摘要）

- oracle 捕获面：HUD/玩家/弹幕遍历/关卡 ctx 5 叠加层**未捕获**（需扩展假设备捕获范围）
- 场景 draws 窗口：难度/角色/HUD/暂停/结算/Boss **未建**（标题窗已推进至 f487）
- 对局绘制对齐：现前沿 **f0（tfactor 残留）/f61（敌机行走位差）**——「511」旧基线为 tfactor 可见时代产物（体检勘误，检讨 §1.4）
- udSr01 replay 事件窗 **f122**（旧登记 2652 漂移；oracle 侧基线需重锚）
- `th13_01.rpy` 回放驱动在道中 Boss 后**自机孪生模型分歧**（10553 帧才击杀 MBoss）——对局对齐只用 `th13_udSr01` 窗
- th13_01 视频同步：Boss 段游戏帧号由锚点拟合换算（非 oracle 事件直出）
- 设备补件分期：PresentationFrame（高刷插值）/SceneCapture/离屏链（S3 入场条件登记，GraphicsDevice stub）
- 手机真机/高刷/联机等现代化功能：**按用户要求不做**

## 下一步建议（优先级；主线窗纪律见 AGENTS.md）

1. **对局窗机制层战役（新主线）**：f61 敌机行走精灵 x 差 96px（ECL 运动链，
   0x41a8a0 位置跟随族反编译重读）+ f0 tfactor 一帧滞后（装载趟插值进度
   残留态建模——`--ignore tfactor` 唯一登记项的销账路径）。机制层优先
   原则执行：不做参数微调。
2. 标题窗 A f487 ins_408：引擎语义工作面（oracle 侧 VM 层写 watch），
   参数微调冻结。
3. 扩展 oracle 捕获面（HUD/玩家/弹幕层）→ HUD/暂停窗建立。
4. 难度/角色/结算 draws 窗口逐步补齐（全链路完备性令）+ udSr01 事件窗
   oracle 侧基线重锚。

## 快速入口

```sh
# 容器（外层 Makefile）
make shell                 # 交互式
make exec-port CMD='node portable/build.mjs'
make exec-port CMD='node --experimental-wasi-unstable-preview1 tools/run-harness.mjs draws /work/artifacts/reference/raw /work/artifacts/port-draws/port-draws.jsonl 3600 st01.ecl'
# 关键命令（容器内 /work）
python3 -m oracle.draw_compare /work/artifacts/port-draws/port-draws.jsonl /work/artifacts/oracle-draws/st01-draws.jsonl
python3 -m oracle.stage1_compare /work/eagler-touhou/ports/th13/web/artifacts/wasi/stage1-events-fresh.jsonl /work/eagler-touhou/ports/th13/web/artifacts/wasi/stage1-oracle.jsonl
# 整改轮新增门禁（monorepo 侧，容器内）
node tools/replay-verifier/run-oracle.mjs && node tools/replay-verifier/run-gate.mjs   # golden 对跑（退化即 FAIL）
bash tests/{anm,ecl,ui,replay}/run.sh                                                 # 单测四套
# 浏览器统计契约（th13-shot 镜像）
make shot CMD='node --test /work/eagler-touhou/ports/th13/web/tests/sdl/graphics-stats.test.mjs'
# 浏览器实测
python3 -m http.server -d eagler-touhou/ports/th13/web 8131   # → http://127.0.0.1:8131/sdl-runtime/th13.html
# 回放复现任意帧
# → http://127.0.0.1:8131/sdl-runtime/th13-debug.html?replay=th13_01.rpy&sht=pl00a.sht&stage=0
```

## 参考资产

- 参考会话叙事骨架：`docs/reference-narrative.md`（东方c++.md 剥离命令块后 1746 行，已全读）
- th15 范本：`eagler-touhou/ports/th15/`（= Ritosa2333/th15 同源上游）；monorepo AGENTS.md 文风「发布会原则」
- 速通视频 + 同款录像：见上「关键数据事实」
