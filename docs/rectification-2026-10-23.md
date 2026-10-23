# th13 移植方法论检讨与整改记录（2026-10-23）

> 背景：用户提供 th17 参考工作流实录（约 3.5 小时端到端交付全程）作为正确
> 方法论范本，要求对照反思 `#配额重置后重派三路子代理`
> （session_df43f83d3c88677913fe67fe6f28ece2）会话的错误方法论。本文件是
> 检讨结论 + 本轮整改的执行记录。**本文件与 AGENTS.md「事故蒸馏」小节
> 配套：AGENTS.md 立规矩，本文件存证据。**

## §0 结论先行

五大病灶，全部带证据链：

| # | 病灶 | 一句话 | 证据 |
|---|------|--------|------|
| 1 | 单帧微雕战役形态 | 600 帧标题窗纠缠 10+ commit 的帧位修正，主线对局窗停滞 | port/th13 f8732ca9↔94daf443 间连续 10 条 commit 均为标题窗单帧推进（f0→f487）；HANDOFF L97-106 |
| 2 | 鸵鸟对齐 | `--ignore` 静默过滤字段，RESULT 照常输出 none 无从区分「比过了」与「跳过了」 | `oracle/draw_compare.py:145-147`（整改前）；commit 9853231 |
| 3 | 空泛编排与配额空烧 | DW 磨轮 8 轮假 -1（2 亿 token）；「倒下时代理」职责空泛 | HANDOFF L79-80 坑位 1/2；HANDOFF L16 |
| 4 | 覆盖声明执行不力 | 覆盖矩阵规则在（2026-10-22 蒸馏）但「三门禁绿」声明与 ui 套件实况脱节 | run_scene_test.cpp 自 94daf443（10-23 17:51）起编译破损至本轮修复（见 §2.4）；HANDOFF L12「三门禁绿」时序存疑 |
| 5 | 基座治理缺位 | 共享件分叉无登记、悬空引用、th15 式测试契约缺失 | GraphicsState.hpp 30 行分叉（2 处语义 + 注释漂移）；蓝图 §8.7/§8.8 从未存在而 8 处代码引用（pickaxe 全历史零命中）；渲染统计契约/golden 对跑在 th13 全无 |

## §1 病灶逐条与证据

### 1.1 单帧微雕战役形态

**现象**：2026-10-23 会话在标题窗 A（600 帧菜单动画）上连续推进
「boot tick → 层值 → 灰白交换 → 移动交叉曲线 → ver 批序 → Z 序三修 →
白条衰减精率 12.75/帧 → 末日帧位 age==14→15 → 背景亮化 Z+18」——每条
commit 的战果度量都是「首分歧 X→Y」。同一时段对局窗（回放 11065 帧、
无输入 3600 帧）停滞在 f9/f0。

**参照**（th17 实录）：作者在同一单位时间跨度内完成的是「HUD 5,760 帧
对照 → 9 机体组合 → 315 组过关流转 → 63 组资源装配 → 1,008 组开局态 →
各面 1,800 帧道中 → 三份完整录像 6,360/8,409/5,692 帧端到端验证」——
度量的都是**系统级覆盖**，不是单窗帧位。

**根因**：把「首分歧帧推进」当成了进度本身。首分歧是**诊断信号**，
不是进度指标——600 帧窗从 f487 推到 f600 只证明这一窗对齐，不证明
游戏更接近交付。参照工作流的度量单位是「场景×组合×帧数的覆盖矩阵」。

**整改**：AGENTS.md 新增「主线窗定义」（全链路录像窗为唯一进度主指标）
与「机制层优先原则」（同窗 3 轮修复无里程碑推进 → 强制升级机制层）。

### 1.2 鸵鸟对齐

**现象**：对局窗 f0 的 tfactor 分歧（port 0x4fff5ff vs oracle 0x6fff5ff，
alpha 通道一帧滞后族）以 `--ignore tfactor` 隔离后，RESULT 行照常输出
`first_divergence_frame=...`——外部无从知道有个字段被永久跳过了整窗
比对。提交信息把「粗筛开关」当作战果登记（9853231）。

**参照**：th17 作者面对第 4,013 帧中间状态差异（帧末一致）的处置是
「不把这份录像算作通过」+ 深挖出**工具采样时机**根因 + 顺手修出 Boss
血条清空时序 bug。分歧是深挖的入口，不是过滤的对象。

**根因**：把「隔离工作面」的工具需求与「验收绿灯」混在同一条输出里。
诊断模式与门禁模式没有形态区分。

**整改**：draw_compare.py 诊断模式三件套——RESULT 行尾强制
`ignored=<字段>` 登记、退出码 0/1/3 区分（3=诊断）、docstring 纪律；
draw-contract.md §3.5 落条文（绿灯链路禁用/限期销账）；tfactor 销账
条件登记（装载趟插值进度残留态建模）。本轮已实测：
`--ignore tfactor` 输出 `ignored=tfactor` + exit 3。

### 1.3 空泛编排与配额空烧

**现象**：a) 渲染对齐 DW 工作流门禁硬编码臆断路径 → 8 轮 -1 空烧
2 亿 token（HANDOFF 坑位 1）；b) 「倒下时代理：菜单工程师…研究员…」
（HANDOFF L16）任务定义是角色名不是交付物。

**参照**：th17 作者全程主会话闭环推进，一次性重构 46 文件
（+1,378 -46），每步带自证（帧数/组合数/对照组），无空转轮次。

**根因**：编排决策让位于「派发仪式」——先派子代理再想任务；DW 磨轮
用于了带研究分叉的战役（AGENTS.md 已于 10-22 改「交付目标导向」，
但「倒下时代理」形态说明执行仍走样）。

**整改**：AGENTS.md 强化「子代理编排纪律」（具名模块 + 闭环验证；
禁空泛探索派发）。本轮自身执行即示范：三路探索代理各带具体产出
契约（基座形态/偏离清单/工具病灶行号），全部产出直接进入本报告。

### 1.4 覆盖声明执行不力

**现象**：2026-10-22 已立「覆盖矩阵与交付门禁」三条，但：
a) ui 套件自 94daf443（10-23 17:51，read_asset 加 required 参数）起
**编译破损**至本轮（run_scene_test.cpp 签名未同步），期间两条 commit
自称「回归干净双目标构建」——「三门禁绿」的实际范围从未包含 ui
套件的编译健康；b) HANDOFF 登记的对局窗基线「f9 神灵出生坐标差」在本轮
实测中已不存在（f9 world[12] 两侧全同），真实前沿是 f61 敌机行走精灵
x 差 96px + f0 tfactor——登记与实况脱节，绿基线在腐烂而无人体检。

**参照**：th17 作者每轮验证后登记的是**实测数字与场景清单**（315 组/
63 组/5,760 帧），且终审材料由「用户会看到的每一类画面」驱动。

**根因**：覆盖矩阵没有**体检驱动**——规则只说「未覆盖清单必须被下一
阶段消费」，没有要求「已声明绿的门禁定期复跑」。

**整改**：本轮顺手修复 ui 套件编译破损（62 例全绿）；ADAPTATION.md
升格为 th15 式编号验收清单（BUILD/RUNTIME/…，每项带实测命令）；
coverage-matrix.md 独立化（§2.5）；HANDOFF 基准勘误（f9→f61/f122）；
golden 对跑骨架（run-gate BASELINE-HELD 机制）让「基线只升不降」
从纪律变成脚本。

### 1.5 基座治理缺位

**现象**：a) `portable/sdl/GraphicsState.hpp` 与 th15 有 30 行分叉
（textureFactor 缺省 0xffffffff→0、configure_game 第二参数 Factor→
Diffuse），两处都是 oracle 实证的 th13 语义，但**改在共享文件副本上**
无分叉登记、无参数化归宿、无上游候选标记；b) 蓝图 §8.7/§8.8 被代码
注释引用 8 处而蓝图里**从未存在**这两个小节（pickaxe 全历史零命中；
轮4/轮5 回写跳号所致）——证据链悬空；c) th15 式测试契约（渲染统计
断言、golden 对跑）在 th13 完全缺失。

**参照**：th17 作者采用既有基座（portable/sdl + zunGraphics）时的纪律
是「渲染架构/前端 UI/性能基线全部继承 + 本作机制逐项恢复」——基座
继承与本作语义分层清晰。

**根因**：「逐字节共享」没有制度承载——没有分叉登记表，就没有人知道
哪些差异是本作语义、哪些是漂移。

**整改**（本轮代码已落地）：
1. **GraphicsState.hpp 与 th15 逐字节归一**（`diff` 全等）——两处 th13
   语义下沉到本作配置点：`GraphicsDevice::initialize` 设
   `backend.pipeline().textureFactor = 0`（真机 D3D 初始 TFACTOR，
   oracle 实证）+ `DrawCapture::open_output` 同设（影子管线同语义）。
   configure_game 的 Factor 第二参数不再被 th13 路径依赖（th13 全树
   零调用 configure_game——AnmRenderer::material 显式 Diffuse 承担
   语义）。验证：双构建绿、标题窗 A f487/对局窗 f0-f61 与基线逐字节同。
2. **敌机句柄化扶正**：`enemies_from_ecl`+`ecl_slot_anm` 成为 web
   默认路径（RunScene::initialize 落位，th15 EnemyAnmHost 桥接模式的
   th13 对应物），敌机死亡经 `StageSim::retire_sink` 钩回收槽位 VM
   （0x418e00 销毁支路的 ANM 面），harness draws/draws-replay 双模式
   同步接线。验证：stage1 事件窗 3600/10187 **none**（句柄化零污染
   事件契约）、draws/draws-replay 与基线逐字节同、62 例单测全绿。
3. **测试契约**：渲染统计契约浏览器测试
   （`web/tests/sdl/graphics-stats.test.mjs`，真实 chromium+SwiftShader
   泵 demo0 回放 180 帧，断言 bufferSubUpdates==0 / bufferReplacements>0 /
   directBytes>0 / 布局缓存有界——**已跑绿**，并顺手抓出一个真实现察：
   th13 场景的 copiedBytes>0 来自 shape/trail/mesh 族的合法累积批，
   与 th15 fixture 的「已合批三角形二次拷贝」不同义，断言按 th13
   语义落）；golden 对跑骨架（`tools/replay-verifier/` corpus +
   run-oracle + run-gate，BASELINE-HELD 退化检测，两案例实测通过）。

## §2 本轮整改清单与验证记录

| 项 | 动作 | 验证 | 状态 |
|---|---|---|---|
| 2.1 悬空引用 | 蓝图补档 §8.7（捕获栈纪律+注册池崩溃根因）/§8.8（TFACTOR 设备态模型）锚点；TitleFrame 两处 §8.8 引用更正为 §8.9 | 全树 grep 无悬空引用 | ✅ |
| 2.2 GraphicsState 归一 | 共享件拷回 th15 原件；语义下沉 GraphicsDevice/DrawCapture 两配置点 | `diff` 与 th15 全等；双构建绿；两窗比对与基线逐字节同（f487/f0+f61） | ✅ |
| 2.3 敌机句柄化 | RunScene 默认置句柄化路径 + retire_sink 回收钩 + harness 双模式接线 | stage1 事件窗 none；draws/draws-replay 与基线逐字节同；62 例单测绿 | ✅ |
| 2.4 ui 套件修复 | run_scene_test.cpp read_asset 签名同步（94daf443 引入的既有破损） | `bash tests/ui/run.sh` exit 0，62 例全通过 | ✅ |
| 2.5 覆盖矩阵独立化 | `docs/plans/coverage-matrix.md` 新建（8 类场景窗×状态×销账条件） | 文档落档（见 2.5） | ✅ |
| 2.6 ADAPTATION 升格 | 战报叙事移交 HANDOFF，本文件改为 th15 式编号验收清单 | 文档落档 | ✅ |
| 2.7 golden 对跑骨架 | replay-verifier corpus/run-oracle/run-gate + golden/baseline.json | 两案例 BASELINE-HELD，退化检测 exit 语义实测 | ✅ |
| 2.8 统计契约测试 | th13_debug_stats 导出 + graphics-stats.test.mjs + HEAPU8 导出 | node --test pass（真实浏览器） | ✅ |
| 2.9 draw_compare 去鸵鸟 | 诊断模式三件套（ignored= 登记/exit 3/docstring 纪律）+ 契约 §3.5 | 实测 filtered-none exit 3、ignored=tfactor 登记 | ✅ |
| 2.10 AGENTS.md 重构 | 机制层优先原则/主线窗定义/渲染基座纪律/比对工具纪律/编排纪律/事故蒸馏 #2 | 本轮交付 | ✅ |

## §3 对初版反思报告的修正（防检讨本身变形式主义）

初版报告（基于长截图解读）有两处**超出证据**的断言，如实更正：

1. **「三路探索子代理是形式主义并发」——过苛**。本次核查 session 记录：
   10-22 10:11 的三路探索（移植侧现状/oracle 菜单捕获能力/工作流草稿）
   的产出直接喂出了 10:34 的蓝图落档与标题 oracle 设计（agent_b123ee61
   的 title-anm-blueprint.md 是现行主文档）。真正的空烧是 DW 磨轮
   8 轮假 -1 与后续微雕循环（§1.3/§1.1），不是探索本身。整改针对的
   是「空泛任务定义」，不是「并发探索」这个手段。
2. **「对局窗 f9 崩」——不实**。f9 是语义绘制清单的首分歧帧（神灵
   出生坐标差），非程序崩溃；且本轮实测发现该分歧已不存在（f9 world
   两侧全同），当前真实前沿是 f61 敌机行走位差 + f0 tfactor 残留。
   战报引用旧基线而不实测，正是 §1.4 的病灶体现。

另：th17 实录作者的「1:1 完全还原」表述有明确的边界声明（行为等价
目标、不保证零 bug、非找回原始源码）——其方法论的可贵处恰是**边界
诚实**，而非「完全」二字。

## §4 未竟工作面（转入 HANDOFF 统一登记）

- 对局窗主线（新战役）：f61 敌机行走精灵 x 差 96px（ECL 运动链）+
  f0 tfactor 一帧滞后族（装载趟插值进度残留态建模，--ignore tfactor
  销账条件）+ replay 事件窗 f122（基线漂移勘误后需重锚 oracle 侧）。
- 标题窗 A f487 ins_408 首帧语义差：按机制层优先原则升级引擎语义
  工作面（oracle 侧 VM 层写 watch），不继续参数微调。
- PresentationFrame/SceneCapture/离屏链设备补件：S3 入场条件登记。
