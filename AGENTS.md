# th13 神灵庙网页移植：所有 Agent 的执行入口

## 先做这六步

**假设你没有历史会话，且主 Agent 也不能凭判断豁免验收。** 目标是把原版 v1.00c 还原为 eagler-touhou 网页版，不是让某段日志相同。

1. 只读检查外层及 `eagler-touhou/` 的 `git status --short`。已有修改属于当前工作，不得 reset、stash、清理或覆盖。
2. 在外层运行以下接手命令；缺少工具/配置时报告 BLOCKED，不猜下一项游戏任务：
   ```sh
   make exec CMD='python3 scripts/verify.py doctor'
   make exec CMD='python3 scripts/verify.py resume'
   ```
3. 按返回的任务 ID 读取任务卡：`make exec CMD='python3 scripts/verify.py task show <id>'`。只读卡中必需文件；不要先通读旧战报。
4. `task start <id>` 成功后，在允许范围施工。每次只处理一个具名模块及可验证假设。
5. `run <id> --tier fast` 后执行 `run <id> --tier full`；最后 `task finish <id>`。测试进程、产物完整性、资格与范围都满足才可完成。
6. `make exec CMD='python3 scripts/verify.py handoff'` 生成交接。下一会话从 resume 继续，不能从聊天总结手工恢复“全绿”。

运行状态存于 ignored `artifacts/verification/`；工具用法见 [verification/README.md](verification/README.md)。重构路线见 [已批准架构](docs/plans/autonomous-refactor-2026-10-24.md)。未实现/未配置的门禁必须阻塞，不能用文档代替代码。

## 红线

- **原版资产不入库**：EXE/DAT/音频/录像/视频帧及派生 dump 只放 `games/`、`artifacts/`（gitignored），不进入 monorepo。已有 ignored 运行资产不复制进源码。
- **所有构建、测试、分析均在容器**，不在宿主装依赖：
  ```sh
  podman run --rm --userns=keep-id -e HOME=/home/dev \
    -v /home/mystia/项目/th13:/work:Z -w /work localhost/th13-dev <命令>
  ```
  产品 cwd 为 `/work/eagler-touhou/ports/th13`。浏览器测试用既有 `localhost/th13-shot`。普通文件读取和 git 只读状态不需要构建容器。
- **凭证零接触**：未经明确授权不读取、复制、解密或外传 cookie、密钥环、口令。测试只用合成数据和项目资产。
- **不伪造结论**：无法证明、缺环境、检查失败就记录原样。不能改测试、过滤字段、缩窗、隐藏绘制、伪造初值或扩堆来制造通过。
- **不自行提交/推送**：用户明确要求后才做；外层和 monorepo 独立、小型中文提交。未经授权不删除文件、不回退已有改动。

## 权威与权限

用户授权及红线 > 版本化验收契约 > 当前任务卡 > 匹配当前输入的运行证据 > HANDOFF/报告摘要 > 历史文档。事实是否成立取决于证据，不取决于文风、模型强弱或写作时间。

外层本规则和 `ports/th13/AGENTS.md` 对 th13 有效；monorepo 的发布会文风不得隐藏失败、未覆盖或改变比较口径。

- 主 Agent、子 Agent 都不能手改状态为 DONE、提升 golden、增加容差或忽略字段。验证器/契约维护须是独立授权任务，带负控。
- 任务卡只能收窄范围，不能覆盖红线。越权改动、未知状态、缺依赖、旧证据一律阻塞。
- runner 是防误操作护栏，不是针对同等文件权限恶意代码的安全隔离。策略和测试自身修改需要独立复核。
- 不使用旧记忆中的帧数、锚点身份或“已销账”作现状。引用历史数字前重新执行；源码与资产事实从数据读取。

## 正确性标准：五道门，互不替代

1. **参考资格**：原版版本、场景、Replay 初值/seed、六字节输入及消费相位有证据。
2. **捕获完整**：足量、唯一、连续的帧；稀疏事件有生产端完成证明；无崩溃/写入错误。
3. **严格一致**：行为及全部有效绘制语义、资源内容相同。比对必须显式输入并同时检查退出码和结构结果。
4. **后端与最终输出**：独立原版参考与真实浏览器 framebuffer 自动数值对照；不用人眼和“看起来像”。假设备清单相同或同 renderer 自比不能证明像素相同。
5. **场景覆盖**：必需画面/输入路径缺失不能 skip 后整体全绿。首条菜单→Stage1→关末→Stage2 主线不是完整游戏交付。

`TOOL_PASS`、捕获确定性、`BASELINE_HELD`、严格一致和最终画面认证必须分别报告。缺独立原版 GPU 真值环境时最终输出保持 BLOCKED，不能降低为看图通过。只声明实测版本/配置/覆盖范围，不宣称任意输入和 GPU 全等。

`--ignore` 与非精确 ULP 是诊断工具，不能进入严格验收。NaN/正负零等按版本化契约处理，禁止临场加豁免。首次差异是诊断信号，不是项目进度。

## 固定工程边界

- 方法：Ghidra 原版证据 → 同一 C++ 双目标（Emscripten/SDL3 与 WASI）→ 可信 oracle → 模块及全链路测试。
- 基座：`portable/sdl` + `ZunGraphics` + `AnmManager`。孪生 Renderer/Shaders/GraphicsState/RenderCommands/LegacyGraphics 必须与 th15 一致或有授权分叉、原版证据和上游候选；本作常量落配置点。
- **真实提交单一真值**：生产绘制与 trace 消费同一有效状态/几何；禁止捕获专用补画。legacy DrawCapture/harness 只是局部诊断，不可认证网页。
- 敌机 ANM 经句柄池管理、销毁经 retire 回收。不得为方便捕获重建一套平行游戏状态。
- 保持每批 `glBufferData` 整体上传，禁止 subData 流式退化；重复展示不改游戏状态，赶帧有预算。
- 音频按实际混音进度补充，预算不得低于采样率；资源分片装载并保持原版逻辑时序。
- x87 80 位中间量不等于 SSE/wasm float；数值分歧先认证原版语义，不以调常数解决。
- 原版函数、字段和人名必须从反编译/数据确认。MSG dump 为 cp932。`0x404140` 族旧 STD 身份已撤回，不再引用。

## 失败处理与效率

- 环境/构建失败：停止游戏修改，修工具；不消费旧 wasm/JSONL。
- 参考资格失败：修参考世界，不让 port 迎合错误 oracle。
- 缺顶点/纹理/状态：补捕获，不删除原版操作或比对字段。
- 每个假设一个最小实验；最多三轮无机制进展后保存最小复现，进入机制复核，不无限磨帧。累计十次同子系统修改仍无闭环也触发复核。
- 无法从既定决策规则处理的架构/契约问题：记录证据和所需决策，阻塞该分支；可以继续无依赖且 READY 的任务，不擅自改变目标。
- 派发 = 具名模块 + 文件所有权 + 固定验证 + 产物契约。禁止“随便探索现状”。并行写范围必须不重叠；主会话也不重复已委派的调查。
- 不以提交数、探针数、报告数或局部首差推进作为主线成果。报告先结论，再命令/结果/边界。

## 默认开发方式与本次演练

**默认是 GLM-5.3 直接担任主 Agent，像普通开发会话一样从本文件和 `doctor/resume` 接手；不需要工作流、GPT 监管、旧聊天或另一位模型安排下一阶段。** 主 Agent 自己查原版证据、选择合法工作、实现、诊断失败、按需派独立复核、执行验证并交接。模型身份不赋予验收豁免。

- 既定架构内的普通生产修复由主 Agent 自主决策；不要把每个小改动升级成配置或审批任务。参考世界、验证器、策略、共享架构的变更需要具名授权及独立复核，不降低正确性标准。
- 后续卡未配置或轮次耗尽时，使用 `verification/README.md` 中已实现的配置、机制复核和维护接续入口；不能等待 GPT，也不能调用尚未部署的计划命令。缺真实外部条件时保留可复现阻塞，继续无依赖且 READY 的工作。
- 施工顺序、证据身份、验证命令和失败处理必须在仓库可找到；任何工作流脚本都不是唯一执行入口。未通过真实 Stage1 全链路及网页交付前，不宣布自主体系成熟。

本次 CPA `zai-glm-5-3` 工作流（宿主 ID `new-provider/zai-glm-5-3`）仅用于本轮接手演练及初版 Demo 施工。仍在运行时先读取实际记录，避免重复创建；演练结束不要求未来会话新建或恢复工作流。

- 演练工作流固定命令执行门禁，不能相信 Agent 自报“绿”。每个机制假设最多三轮。
- 停止的运行按实际 stop reason 恢复；用户取消的不自行恢复，提供方故障不偷偷换账户。
- 修改既有运行用 Edit + AmendWorkflow；未变化 ask 文本保持字节一致。保存脚本和运行 ID 的引用，不把整个会话当接手材料。
- 缺外层工具或资源的独立 monorepo checkout 应明确 BLOCKED，不能复制/猜测另一套协议。

## 索引（按任务需要读取）

- 当前任务与命令：`verification/README.md`、`verification/tasks.json`。
- 原版事实：`artifacts/decomp/jp/functions.json` 与函数体；地址登记 `ports/th13/web/cpp/rebuild/AddressMap.hpp`。
- 本作契约：`eagler-touhou/ports/th13/docs/plans/` 的 event/draw/replay 等文档；旧版只证明其声明的字段。
- 覆盖入口：`eagler-touhou/ports/th13/docs/plans/coverage-matrix.md`。
- 事故证据：`docs/rectification-2026-10-24.md`、`docs/rectification-2026-10-23.md`；它们是历史资料，不是任务队列。
- 路径：宿主 `/home/mystia/项目/th13` → 容器 `/work` → WASI guest `/`；使用已有 harness 路径工具，禁止手猜。
