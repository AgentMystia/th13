# th13 神灵庙移植 — AGENTS.md

本文件是工作区级操作守则，面向在此工作的所有代理与人类。进入 `eagler-touhou/`（monorepo）后同时遵守其根目录 AGENTS.md（文档「发布会原则」文风）。冲突时：红线 > 本文件 > monorepo 文风要求。

## 项目一句话

把 th13 东方神灵庙（v1.00c）行为还原为网页可玩版本，贡献到 eagler-touhou 的 `ports/th13`（本地分支 `port/th13`）。方法论承自参考工作流（GPT-6-Astra 会话记录，见 `东方c++.md`，叙事骨架在 `docs/reference-narrative.md`）；渲染基座承自 th15（portable/sdl + zunGraphics + AnmManager 句柄式 game/）。

## 红线（违反即停）

1. **原版资产永不入库**：EXE/DAT/音频/录像/视频帧只存在于 `games/` 与 `artifacts/`（gitignored）。dump 文本属开发证据，同样不入 monorepo。
2. **一切构建/测试/分析经容器**：`podman run --rm --userns=keep-id -e HOME=/home/dev -v /home/mystia/项目/th13:/work:Z -w /work/... localhost/th13-dev`。不在宿主装依赖。thtk 临时克隆、用后清理。
3. **凭证零接触**：密钥环、浏览器 cookie、口令类资产，未经用户明确同意不读取、不复制、不外传（本项目有前科教训，见记忆）。
4. **不伪造结果**：检查不可能通过、指令矛盾、证据不足时，如实上报而不是绕过。oracle/测试数字必须来自实际执行。

## 核心方法论（参考工作流沉淀）

**事实源 → 双目标重建 → oracle 对照 → 首分歧定位**，每个子系统过三重门：Ghidra 参考 → oracle 绿灯 → `node --test`。

- **事实源**：Ghidra 12.1.3 headless 导出（`artifacts/decomp/jp/functions.json` + 反编译体）。锚点登记 `web/cpp/rebuild/AddressMap.hpp`。
- **双目标**：同一份 C++ 编译两次——Emscripten+SDL3 网页（`portable/build.mjs`）/ wasi-sdk 无头 harness（`portable/build-harness.mjs`，`node --experimental-wasi-unstable-preview1 tools/run-harness.mjs` 运行）。
- **oracle**：Unicorn 直调原版函数（`oracle/`，`override_call` 桩内部依赖），事件流按 `docs/plans/event-contract.md` 契约逐帧逐字段比对，`RESULT first_divergence_frame=<n|none>` 是唯一裁决。**比对命令必须显式传参并先自证**（有 8 轮假 -1 的前科）。
- **首分歧是诊断信号，不是进度指标**（事故蒸馏 #2，见 `docs/rectification-2026-10-23.md` §1.1）：单窗首分歧推进只证明该窗对齐，不证明游戏更接近交付。战果度量见「主线窗定义」。
- **视觉（视觉是弱项，永远只作辅助）**：一致性的主驱动是**渲染语义对齐**——UI/HUD/精灵的位置、缩放、旋转、层级、混合全部由 ANM/STD 脚本数据与原版 .data 常量**按构造决定**，不做手工摆放；原版侧经 unicorn 钩内部绘制提交函数导出每帧语义绘制清单（纹理/变换/顶点/混合/层序），与移植侧同构导出逐帧比对（同 stage1_compare 模式）。像素 diff（`visual_diff.py`）与人眼/看图仅作最终确认，不作修复依据。速通视频黄金帧仅用于锚定时间轴与终检。视频帧≠游戏帧，必须走锚点拟合（结论：video = game + 400，斜率 1.0），禁用「秒×60」。**绘制 oracle 必须覆盖全场景链路**：标题 / 难度 / 角色 / 对局 / HUD / 暂停 / 结算 / Boss 战每一类画面各自独立 draws 窗口；扩展假设备捕获面是阶段任务，未覆盖层一律进覆盖矩阵的「未覆盖」清单。

### 机制层优先原则（事故蒸馏 #2，2026-10-23）

同一窗内连续 3 轮修复无里程碑级推进，或同一子系统累计 10 commit 仍未 none 的，**强制升级机制层**：回 Ghidra 反编译导出重读原版机制（对照 th15 同源件为底重建），禁止继续在参数/帧位层面微调。静态单窗的帧位精修（衰减率/末日帧位/批序类）累计不得作为战役主线；触发本原则时在 HANDOFF 登记升级决定与反编译锚点。

### 主线窗定义（事故蒸馏 #2）

进度主指标 = **端到端全链路录像窗**（菜单→Stage1 过关→结算→进入 Stage2，行为+渲染双一致）与**场景覆盖矩阵**（`ports/th13/docs/plans/coverage-matrix.md`）。静态单场景窗（标题窗/难度窗等）是辅助，其首分歧推进必须附带：①窗口占比（如 f487/600=81%）②主线窗当前状态 ③是否触发机制层升级判据。任何战报不得以孤立的单窗帧位推进充当战役进展。

### 数值语义纪律

原版 MSVC x87 80 位中间量 ≠ 移植侧 SSE2。凡双精度链/超越函数/连加累乘后单次取整：先 oracle 锁原版结果再落 C++；「分歧在小数第 N 位」优先怀疑浮点语义（参照 th10 的 machine.hpp+softfloat；可证明等价的路径才允许换普通 float——参考项目用 2040 万次数值比较证明过 832 处替换）。

## 渲染基座纪律（事故蒸馏 #2，2026-10-23）

基座 = `portable/sdl`（GLES3 语义后端）+ `web/cpp/game/ZunGraphics.hpp`（语义边界）+ AnmManager 句柄式管理。当前状态：Renderer/Shaders/RenderCommands/LegacyGraphics 与 th15 逐字节一致；**GraphicsState.hpp 已归一**（2026-10-23），th13 语义落在两个本作配置点（`GraphicsDevice::initialize` 与 `DrawCapture::open_output` 的 TFACTOR 初始 0——真机 D3D 设备态，蓝图 §8.8）。

1. **共享件零漂移**：与 th15 孪生的共享文件必须逐字节一致（`diff` 验证）或有登记分叉。分叉必须同时具备：①oracle 实证注释 ②本作配置点归宿（不改共享文件改调用点）③上游候选标记（未来可回馈 monorepo 的参数化方案）。禁止无登记静默分叉。
2. **设备补件分期登记制**：GraphicsDevice 裁剪件（PresentationFrame 高刷插值/SceneCapture 离屏捕获/copy_surface/resample_surface/upload_png）每项必须在覆盖矩阵或 HANDOFF 登记「分期位置 + 入场条件」，禁止只留 stub 注释。
3. **精灵句柄化**：敌机本体 VM 默认走 ECL 303/306 → AnmManager 句柄池（`enemies_from_ecl`，0x41fe80/case3 同构，th15 EnemyAnmHost 桥接模式的 th13 对应物；RunScene::initialize 默认开启，`ecl_slot_anm=false` 为逃生开关）；敌机销毁必须经 `StageSim::retire_sink` 回收槽位 VM。弹体/自机/自机弹的表现层自备直绘（th15 PlayerDraw/GameDraw 同式）是基座允许的模式。
4. **测试契约**（th15 同式，已落地）：渲染统计契约浏览器测试（`web/tests/sdl/graphics-stats.test.mjs`——真实浏览器断言 glBufferData 整体上传/无 subData 流式/直通占比/布局缓存有界）+ draws golden 对跑（`tools/replay-verifier/` run-oracle→run-gate，BASELINE-HELD 退化检测，golden 只升不降）——两者均为交付门禁的组成部分。

## 比对工具纪律（事故蒸馏 #2）

1. **诊断模式与门禁模式分离**：`draw_compare.py --ignore <field>` 是诊断开关（隔离工作面用），带过滤的 RESULT 强制 `ignored=<字段>` 登记、退出码 3。**绿灯链路（交付门禁/战果声明/golden 登记）禁止使用 --ignore**（draw-contract.md §3.5）。
2. **限期销账**：每个进入 --ignore 的字段必须在 HANDOFF 登记销账条件与期限（当前唯一登记：tfactor——装载趟插值进度残留态建模完成即销账）。
3. **语义豁免进契约**：NaN==NaN 相等性是 draw-contract §3 的既定语义（两侧同式），不是遮蔽；新增任何豁免必须先落契约再落代码。
4. **基线体检**：引用历史基线数字（如「对局窗 f511/f9」）前必须重跑实测——绿基线会腐烂（前科：f9 神灵坐标差实测已不存在、真实前沿 f61；replay 事件窗基线漂移）。

## 已锁事实（勿重推导）

- RNG = MSVC CRT `_rand()` @ `0x47BBA2`（`_getptd()` @ `0x47FE5A`，oracle 伪造 ptd）；ECL 主分发 `0x41B260`；ANM `0x462450` 族；replay 装载 `0x448C40`/保存 `0x4484D0`。ECL 与 ANM 是**两条独立种子 0 的 0x472c90 流**（[-9987] 抽签互不推进）。
- replay 格式全解（`docs/plans/replay.md`）：t13r、双趟 XOR `(0x7D,+0x3A,0x100)`/`(0x5C,+0xE1,0x400)`、LZ77 MSB、每帧 6 字节输入。
- 已达成：无输入窗 3600 帧/10187 事件 + 回放驱动事件窗与原版逐字节一致（当前实测见 HANDOFF 硬成果表——引用前重跑）；Boss 1非帧两侧同 10362。
- 资源格式：ECL 13 / ANM 8 / STD 13 / MSG 13（`docs/ASSETS.md`）。

## 覆盖矩阵与交付门禁（从「菜单漏网」事故蒸馏，2026-10-22；强化 2026-10-23）

形式化 oracle 只证明**它覆盖的范围**对齐。参考工作流（东方c++.md）用 FIDELITY 差异表 + 全链路浏览器实测守住交付；我们曾因「覆盖声明」未升格为门禁，让完全不符的主菜单漏网。规则：

1. **覆盖矩阵**（home = `ports/th13/docs/plans/coverage-matrix.md`）：每阶段交付前更新「本阶段 oracle 覆盖什么 / 未覆盖什么」；**未覆盖清单必须被下一阶段消费**，不得默认通过。
2. **场景级回归窗**：交付必跑全链路语义绘制清单比对——标题 / 难度 / 角色 / HUD / 对局 / Boss / 结算各自独立 draws 窗口，不只对局窗。
3. **终审材料完整性**：呈现给人类的截图/清单由「用户会看到的每一类画面」清单驱动，不得取样偏差。
4. **门禁复跑**（2026-10-23 新增）：声明「全绿」的门禁集（容器单测四套 + node --test + 浏览器统计契约 + golden 对跑）在任何战报前全部实跑一遍；「回归干净双构建」不等于「测试绿」。

## 汇报纪律（从参考会话蒸馏）

1. **结论先行 + 证据 + 边界**：每份交付说明「做成了什么（带实测数字）— 验证了什么 — 没验证什么（真机？逐像素？）」。「完成了」永远附验收范围，不宣称未经证明的全称判断。
2. **主动纠偏**：发现自己的表述超出事实立即更正，并解释差异（本轮示范：初版反思报告的「三路探索子代理=形式主义」「f9 崩」两处断言已按证据更正，见检讨文档 §3）。
3. **用户反馈即黄金用例**：每个 bug 报告 → 定根因 → 修复 → 回归测试 + 主动扩大排查范围（「不要只修我说的点」）。会炸的样本永远留着当测试。
4. **进度百分比是工程量估计**，不是测量值；给出时必须标注置信度与剩余清单。
5. **区分**：桌面模拟 vs 真机；架构相同 ≠ 性能相同；能运行 ≠ 行为一致。

## 性能与移动端检查单（做渲染/音频改动时逐条过）

- 网页顶点上传：**每批 `glBufferData` 整体替换**，不用 `glBufferSubData` 分段（Mali/Adreno 真机卡顿实锤，桌面 Intel 无感）——已被 `graphics-stats.test.mjs` 固化为契约（stats[7]==0）。
- 音频补充预算：回调频率 × 每次补充上限必须 ≥ 采样率（44.1k@30fps 的坑）；按实际混音进度补，不按计时。
- 赶帧要有时间预算，否则越追越卡；逻辑与显示分离后再谈高刷插值；重复绘制不得改写游戏状态（高刷插值前置条件）。
- 场景资源创建按 ~2ms 预算分片让出；标题/音乐室/结算资源预加载；新着色器组合提前编译。
- 命名避免 D3D 字样误导（th09 的 `backend{8}` 是精度配置被误读为 D3D8）。
- Emscripten 缓存认 `EM_CACHE`（不是 EM_CACHE_DIR）；SDK 安装路径变更会清缓存。

## 工作区速查

- 外层仓库（工具/脚本/oracle）与 monorepo（`eagler-touhou/`，分支 `port/th13`）各自独立提交，**提交信息用中文、分模块小提交**。
- 容器入口：外层 `Makefile`（`make image / shell / exec-port CMD=… / shot CMD=… / thtk / thtk-clean`）；镜像 th13-dev（全工具链）/ th13-ci / th13-shot（chromium 无头取帧+playwright）。
- 注册 th13 需三处：`launcher/config/workspace.json`、`runtime-builds.json`、`common/contracts/product-catalog.mjs`（PRODUCT_GAMES）。
- **交付目标导向**（用户令，2026-10-22）：一切手段服务于「真实可玩、行为一致的 th13 网页版」交付。编排按战役形态选——研究分叉为主的战役由主会话直接编排+专家子代理（主会话跑门禁、读输出、定方向）；纯机械磨轮循环（比对→修→再比对 ×N）可按需起动态工作流。用 DW 时：**已缓存阶段的 ask 文本一个字都不能改**（改=缓存全失效重付）；修订用 Edit+AmendWorkflow；子代理 GLM-5.3$max 有周配额，超了等重置 ResumeWorkflowRun 或用户指示换模型。草稿归档在 `artifacts/workflow-drafts-archive/`。
- **子代理编排纪律**（事故蒸馏 #2，2026-10-23）：任务定义 = 具名模块 + 闭环验证命令 + 产出契约（例：「对照反编译补齐 ItemManager 道具掉落算法并通过 ecl 套件」），禁止「探索现状/看看文档」类空泛派发；探索类任务必须带明确产出物（行号清单/结构报告）且产出直接进决策；架构决策与门禁执行由主会话独占。
- **身份/文本类事实必须从数据解出**（msg 对话、ANM 名称、ECL 子程序名、strings.json），禁止凭模型记忆断言——有过子代理幻觉「一面 Boss 是幽谷响子」的前科（实为西行寺幽幽子，st01 对话原文『幽々子様』实证）。msg dump 文本是 **cp932** 编码，按 UTF-8 直读全是乱码。
- 坑位：workflow `world.run` 的 podman `-w` 必须用容器侧路径 `/work/...`；工作流 workspace 锚定 Bash 会话 cwd（提交前先 `cd /home/mystia/项目/th13`）。

## 事故蒸馏 #2（2026-10-23，方法论检讨）

完整证据链与整改记录：`docs/rectification-2026-10-23.md`。蒸馏要点：
①单帧微雕战役形态 → 机制层优先原则 + 主线窗定义；
②`--ignore` 鸵鸟对齐 → 比对工具纪律（诊断/门禁分离 + 限期销账）；
③空泛编排与 2 亿 token 空烧 → 子代理编排纪律；
④「三门禁绿」与 ui 套件实况脱节 → 门禁复跑制度 + ADAPTATION 验收清单化；
⑤共享件 30 行无登记分叉与蓝图悬空引用 → 渲染基座纪律（零漂移/分叉三要素/补件登记制）。
