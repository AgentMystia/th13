# th13 开发入口与交付边界

目标：把原版 v1.00c 还原为可验证的 eagler-touhou 网页版。模型身份、代理数量和局部绿灯都不授予验收豁免。

## 接手与施工

- 先只读查看外层及 `eagler-touhou/` 的 `git status --short`；已有修改属于当前工作，不得 reset、stash、clean、删除或覆盖。
- 构建、测试、原版分析和浏览器验证使用既有容器入口：产品命令走 `make exec CMD='...'`，浏览器命令走 `make shot CMD='...'`；宿主不安装依赖。
- `python3 scripts/verify.py doctor`、`resume`、`task show`、`handoff` 是诊断/记录工具，不是产品施工的唯一入口。任务式施工可使用 `task start → run --tier fast → run --tier full → task finish`；旧卡死锁不能阻止用户已授权的产品修复。
- 当前事实以源码、当前输入、当前构建和可复现实验为准；不从聊天、旧 HANDOFF、旧战报或代理自报恢复“通过”。
- 每个改动绑定一个具名假设和最窄验证；并行写范围必须互斥。连续三轮没有机制进展就保存最小复现并换证据路径，不无限磨帧。
- 不自行提交、推送、回退或清理用户已有修改。

## 不可破坏的红线

- 原版 EXE/DAT/音频/录像/视频帧及派生 dump 不入库，只放 `games/`、`artifacts/` 等既定 ignored 目录。
- 凭证零接触；测试只使用合成数据和项目资产。
- 不通过改测试、golden、比较器字段、容差、窗口、输入初值、堆或隐藏绘制制造通过；无法证明就原样记录 BLOCKED。
- 生产绘制与 trace 必须消费同一有效状态/几何；legacy DrawCapture/WASI harness 只能做局部诊断，不能单独认证真实网页。
- 共享后端、资源句柄、音频时序、整体 buffer 上传和数值语义不得静默漂移；原版函数/字段需有反编译或数据依据。

## 正确性口径

- 参考资格、捕获完整、严格行为/绘制一致、独立原版与真实浏览器最终输出、场景覆盖分别报告，互不替代。
- 不用截图、人眼、相同假设备清单或同 renderer 自比替代数值认证；没有独立原版 GPU 真值时最终画面认证保持 BLOCKED。
- `TOOL_PASS`、确定性、`BASELINE_HELD`、严格一致、最终输出和覆盖范围必须分开说明；`--ignore` 与非精确 ULP 只作诊断。
- Stage 1 的详细验收口径见 `eagler-touhou/ports/th13/docs/plans/stage1-acceptance.md`；产品推进顺序见 `docs/plans/product-first-port-host-podman.md`。

## 工具契约

- `scripts/verify.py` 是状态、receipt 和 DONE/fidelity 结论的唯一程序入口；详细 schema、依赖、轮次、维护和配置规则见 `verification/README.md`。
- 不手改 `verification/state.json`、`verification/tasks.json`、`verification/policy.json`、golden 或比较器来放行；需要维护时走具名授权和独立 review。
- AGENTS、HANDOFF、policy、games 等受保护内容不得由普通任务吸收；缺工具、缺资源或未实现门禁保持 BLOCKED。
- `resume` 只能说明当前记录状态，不能把旧证据升级为新源码的证明；`handoff` 只保存交接材料，不替代测试。

## 索引

- 任务与工具：`verification/README.md`、`verification/tasks.json`。
- 原版事实：`artifacts/decomp/jp/functions.json`、对应函数体、`ports/th13/web/cpp/rebuild/AddressMap.hpp`。
- 产品契约：`eagler-touhou/ports/th13/docs/plans/`。
- 当前状态：源码、`artifacts/verification/` 和实际命令输出；历史文档只作背景。
