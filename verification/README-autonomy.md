# GLM 持续自主开发协议

默认由 GLM 直接担任主 Agent：自主 doctor/resume → 选卡/施工 → 按需独立 review → 验证/接手。不要求工作流、强模型、GPT 口头补充或特定会话工具；CLI持有全部配置、续跑和验证入口。工作流只用于本次演练/Demo，不是后续默认，也不持有唯一决策。

这是用户已授权的工作方式，不构成 GLM 或 Demo 已达标声明。既定技术架构和五道门见外层 AGENTS、autonomous-refactor 及内层 stage1-acceptance。

## 每次接手

1. 在容器运行 doctor、resume，恢复活动任务；仅本次演练若存在 `artifacts/verification/active-workflow.json` 才核对实际运行状态避免重复；正常主Agent接手不依赖它。
2. 活动任务按同一基线继续；没有活动任务就领取 READY。不要通读旧战报，不查询损坏的会话工具。
3. 后续卡空门禁时，GLM 主工程师生成 proposal，另一独立上下文只读复核并生成绑定哈希的 review，用 `scripts/task_configure.py --proposal ... --review ...` 原子应用。配置通过不等于业务通过。
4. 写范围有遗漏时先提出具体接口适配和证据，由独立复核确认仍在用户批准的既定架构内；不准借机改golden、阈值、忽略字段或交付目标。

## 施工与效率

- 一次只处理一个具名机制；先取原版函数/数据证据，补能抓住错误的负控，再修改生产代码。
- 快检用最窄受影响测试。机制闭环才跑该卡 full；有效且输入未变的上游证据复用，不反复运行同一长套件。
- 修改使依赖 STALE：`task recheck-dependencies <owner>` 实跑失效门禁，保留 owner 原基线和上游失败记录。只验证，不消耗上游实现轮次。
- 同一假设最多三轮无进展必须换独立机制复核。review 绑定card/baseline/最后run，给出不同新假设和最小实验，经 `task mechanism-review` 开启具名新修订；原基线、原失败和固定验收不变，不能清场或重置历史。
- 曾DONE后工具/规则合法升级，且没有未完成claim：用 `task reopen <id>` 归档旧证据后建立新修订，并重新fast/full。失败或ACTIVE任务不允许借此吞掉越权改动。

## 到交付的固定路线

Replay初值/输入资格 → 全有效原版/生产提交与资源捕获 → 生产driver统一 → 独立后端与原版输出认证 → Stage1主线 → 完整游戏覆盖。每个阶段由GLM规划、施工、独立复核和固定工具验收，不需要请求强主Agent安排下一阶段。

固定最终门：`python3 scripts/verify_delivery.py --scope stage1` / `--scope full-game`。已实现协议入口；实际适配器和场景配置缺失时严格BLOCKED。适配器齐备时它启动独立原版与实际浏览器生产捕获进程，逐帧比较输入/行为/完整绘制及原始RGBA，缺适配器、未知状态、截短、自比或相位不明都失败。

固定生产适配器：`scripts/capture_original_delivery.py`、`scripts/capture_browser_delivery.mjs`。它们接收 `--context <json> --out <目录>`，按context提供的nonce/source_identity/input_identity/scenarios执行，输出 `manifest.json` 与每场景 `th13/delivery-capture/v1`流。每帧包含frame,input,input_index,phase,state,commands,events,pixels,complete,unknown,unqualified；pixels含path/sha256，RGBA8-top-left 640×480。末行complete/frames/write_errors。不得用相同renderer自比、手写provider或过去receipt代替真实进程。对局mainline必须覆盖整个实际Replay和自然Boss击破/Stage1清关/Stage2入场。

缺独立原版输出环境时先按具名容器任务修环境；不得从历史环境记录推定当前资格。旧观察器不具有完整资格，不可直接提升golden。

所有场景通过后准备原版资产零入库的发布分支、构建和可审查改动。远端提交/推送/PR/合并按用户明确授权及仓库保护执行；不能因目标是Merge而越权覆盖线上。

## 真实捕获进程边界

普通runner的 `external:true` 仍BLOCKED：没有经过独立复核的host/container进程适配器，不人工导入PASS。delivery适配器采用 `--context <json> --out <fresh-dir>`；context含nonce、源码/显式资产身份、scope及场景。每个适配器必须实际启动本角色生产进程、等待退出，核实生产端完成/写入错误，再生成manifest和流，父门禁保留进程argv/exit/log哈希。provider字符串和qualified布尔本身不证明独立性，必须在适配器授权审核里有真实进程及资格证据。

容器内不得嵌套podman。若必须由宿主启动既有th13-shot/独立原版环境，先以具名任务实现并复核受控桥接进程接口，绑定本次context、退出码、日志和输出；目前未实现，保持BLOCKED。不得扫描原版资产目录或凭证/profile，只能使用任务中明确授权的合成数据或项目输入路径。

最终场景合同路径为 `verification/delivery-scenarios.json`（由主会话另行具名授权，不在本工具修补范围）；至少schema `th13/delivery-scenarios/v1`，scope包含replay、显式inputs哈希、cases:[{id,frames,milestones}]。full-game另须覆盖full-game-scenes.json。协议合成测试只证明工具控制，不证明游戏或GPU。
