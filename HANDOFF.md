# HANDOFF — th13 神灵庙网页移植（会话交接文档）

> **2026-10-23 整改轮更新**：本轮按用户令执行「方法论检讨与整改」（对照 th17
> 参考实录），完整证据链与整改记录见 **`docs/rectification-2026-10-23.md`**，
> 新制度落在外层 `AGENTS.md`（机制层优先/主线窗/渲染基座纪律/比对工具纪律/
> 子代理编排纪律/事故蒸馏 #2）。阅读顺序：**AGENTS.md → 本文件 → 检讨文档**。
> 工作区：`/home/mystia/项目/th13/`（外层工具仓）与 `eagler-touhou/`（monorepo，
> 分支 `port/th13`，贡献目标）。

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
