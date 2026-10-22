# th13 神灵庙移植 — AGENTS.md

本文件是工作区级操作守则，面向在此工作的所有代理与人类。进入 `eagler-touhou/`（monorepo）后同时遵守其根目录 AGENTS.md（文档「发布会原则」文风）。冲突时：红线 > 本文件 > monorepo 文风要求。

## 项目一句话

把 th13 东方神灵庙（v1.00c）行为还原为网页可玩版本，贡献到 eagler-touhou 的 `ports/th13`（本地分支 `port/th13`）。方法论承自参考工作流（GPT-6-Astra 会话记录，见 `东方c++.md`，叙事骨架在 `artifacts/reference/narrative.md`）。

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
- **视觉（视觉是弱项，永远只作辅助）**：一致性的主驱动是**渲染语义对齐**——UI/HUD/精灵的位置、缩放、旋转、层级、混合全部由 ANM/STD 脚本数据与原版 .data 常量**按构造决定**，不做手工摆放；原版侧经 unicorn 钩内部绘制提交函数导出每帧语义绘制清单（纹理/变换/顶点/混合/层序），与移植侧同构导出逐帧比对（同 stage1_compare 模式）。像素 diff（`visual_diff.py`）与人眼/看图仅作最终确认，不作修复依据。速通视频黄金帧仅用于锚定时间轴与终检。视频帧≠游戏帧，必须走锚点拟合（结论：video = game + 400，斜率 1.0），禁用「秒×60」。**绘制 oracle 必须覆盖全场景链路**：标题 / 难度 / 角色 / 对局 / HUD / 暂停 / 结算 / Boss 战每一类画面各自独立 draws 窗口；扩展假设备捕获面是阶段任务，未覆盖层一律进覆盖矩阵的「未覆盖」清单（见下）。

### 数值语义纪律

原版 MSVC x87 80 位中间量 ≠ 移植侧 SSE2。凡双精度链/超越函数/连加累乘后单次取整：先 oracle 锁原版结果再落 C++；「分歧在小数第 N 位」优先怀疑浮点语义（参照 th10 的 machine.hpp+softfloat；可证明等价的路径才允许换普通 float——参考项目用 2040 万次数值比较证明过 832 处替换）。

## 已锁事实（勿重推导）

- RNG = MSVC CRT `_rand()` @ `0x47BBA2`（`_getptd()` @ `0x47FE5A`，oracle 伪造 ptd）；ECL 主分发 `0x41B260`；ANM `0x462450` 族；replay 装载 `0x448C40`/保存 `0x4484D0`。
- replay 格式全解（`docs/plans/replay.md`）：t13r、双趟 XOR `(0x7D,+0x3A,0x100)`/`(0x5C,+0xE1,0x400)`、LZ77 MSB、每帧 6 字节输入。
- 已达成：无输入窗 3600 帧/10187 事件 + 回放驱动 10662 帧/48406 事件与原版逐字节一致；Boss 1非帧两侧同 10362。
- 资源格式：ECL 13 / ANM 8 / STD 13 / MSG 13（`docs/ASSETS.md`）。

## 覆盖矩阵与交付门禁（从「菜单漏网」事故蒸馏，2026-10-22）

形式化 oracle 只证明**它覆盖的范围**对齐。参考工作流（东方c++.md）用 FIDELITY 差异表 + 全链路浏览器实测守住交付；我们曾因「覆盖声明」未升格为门禁，让完全不符的主菜单漏网。规则：

1. **覆盖矩阵**：每个阶段交付前必须列出「本阶段 oracle 覆盖什么 / 未覆盖什么」；**未覆盖清单必须被下一阶段消费**，不得默认通过。
2. **场景级回归窗**：交付必跑全链路语义绘制清单比对——标题 / 难度 / 角色 / HUD / 对局 / Boss / 结算各自独立 draws 窗口，不只对局窗。
3. **终审材料完整性**：呈现给人类的截图/清单由「用户会看到的每一类画面」清单驱动，不得取样偏差。

## 汇报纪律（从参考会话蒸馏）

1. **结论先行 + 证据 + 边界**：每份交付说明「做成了什么（带实测数字）— 验证了什么 — 没验证什么（真机？逐像素？）」。「完成了」永远附验收范围，不宣称未经证明的全称判断。
2. **主动纠偏**：发现自己的表述超出事实（如把「指令层转译」说成「完整重建」）立即更正，并解释差异。
3. **用户反馈即黄金用例**：每个 bug 报告 → 定根因 → 修复 → 回归测试 + 主动扩大排查范围（「不要只修我说的点」）。会炸的样本永远留着当测试。
4. **进度百分比是工程量估计**，不是测量值；给出时必须标注置信度与剩余清单。
5. **区分**：桌面模拟 vs 真机；架构相同 ≠ 性能相同；能运行 ≠ 行为一致。

## 性能与移动端检查单（做渲染/音频改动时逐条过）

- 网页顶点上传：**每批 `glBufferData` 整体替换**，不用 `glBufferSubData` 分段（Mali/Adreno 真机卡顿实锤，桌面 Intel 无感）。
- 音频补充预算：回调频率 × 每次补充上限必须 ≥ 采样率（44.1k@30fps 的坑）；按实际混音进度补，不按计时。
- 赶帧要有时间预算，否则越追越卡；逻辑与显示分离后再谈高刷插值；重复绘制不得改写游戏状态（高刷插值前置条件）。
- 场景资源创建按 ~2ms 预算分片让出；标题/音乐室/结算资源预加载；新着色器组合提前编译。
- 命名避免 D3D 字样误导（th09 的 `backend{8}` 是精度配置被误读为 D3D8）。
- Emscripten 缓存认 `EM_CACHE`（不是 EM_CACHE_DIR）；SDK 安装路径变更会清缓存。

## 工作区速查

- 外层仓库（工具/脚本/oracle）与 monorepo（`eagler-touhou/`，分支 `port/th13`）各自独立提交，**提交信息用中文、分模块小提交**。
- 容器入口：外层 `Makefile`（`make image / shell / exec-port CMD=… / thtk / thtk-clean`）；镜像 th13-dev（全工具链）/ th13-ci / th13-shot（chromium 无头取帧）。
- 注册 th13 需三处：`launcher/config/workspace.json`、`runtime-builds.json`、`common/contracts/product-catalog.mjs`（PRODUCT_GAMES）。
- **交付目标导向**（用户令，2026-10-22）：一切手段服务于「真实可玩、行为一致的 th13 网页版」交付。编排按战役形态选——研究分叉为主的战役由主会话直接编排+专家子代理（主会话跑门禁、读输出、定方向）；纯机械磨轮循环（比对→修→再比对 ×N）可按需起动态工作流。用动态工作流时：**已缓存阶段的 ask 文本一个字都不能改**（改=缓存全失效重付）；修订用 Edit+AmendWorkflow；子代理 GLM-5.3$max 有周配额，超了等重置 ResumeWorkflowRun 或用户指示换模型。草稿归档在 `artifacts/workflow-drafts-archive/`。
- **身份/文本类事实必须从数据解出**（msg 对话、ANM 名称、ECL 子程序名、strings.json），禁止凭模型记忆断言——有过子代理幻觉「一面 Boss 是幽谷响子」的前科（实为西行寺幽幽子，st01 对话原文『幽々子様』实证）。msg dump 文本是 **cp932** 编码，按 UTF-8 直读全是乱码。
- 坑位：workflow `world.run` 的 podman `-w` 必须用容器侧路径 `/work/...`；工作流 workspace 锚定 Bash 会话 cwd（提交前先 `cd /home/mystia/项目/th13`）。
