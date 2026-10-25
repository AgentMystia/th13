# 容器验收与无历史接手入口

默认由 GLM 直接担任主 Agent，自主 doctor/resume → 选卡/施工 → 按需独立 review → 验证/接手；不要求工作流、GPT 口头补充或特定会话工具。工作流仅用于本次演练，不持有唯一决策。

这是可执行工具协议，不是游戏完成证明。先读 `/work/AGENTS.md` 和当前任务卡，不凭旧聊天、HANDOFF 或文档中的“通过”恢复状态。

## 固定命令

所有命令在外层 `/work`，通过宿主 `make exec CMD='…'` 或 AGENTS 指定的临时 `localhost/th13-dev` 容器执行。不要在宿主安装依赖，也不要寻找同名常驻容器。

```sh
make exec CMD='python3 scripts/verify.py doctor'
make exec CMD='python3 scripts/verify.py resume'
make exec CMD='python3 scripts/verify.py task show foundation-runner'
make exec CMD='python3 scripts/verify.py task start foundation-runner'
# 只改卡中 allowed 范围，保留原有 outer/inner dirty tree。
make exec CMD='python3 scripts/verify.py run foundation-runner --tier fast'
make exec CMD='python3 scripts/verify.py run foundation-runner --tier full'
make exec CMD='python3 scripts/verify.py task finish foundation-runner'
make exec CMD='python3 scripts/verify.py handoff'
```

`doctor` 的 TOOL_PASS 只表示配置可解析、工具环境存在；其 `unconfigured_full` 会列出未实现门禁，不代表这些任务能完成。`resume` 的 `next` 仅包含依赖已认证且 fast/full 已配置的 READY 卡。空 checks、明确 blocked_reason、外部适配器未实现、缺结构结果都保持 BLOCKED。`handoff` 只写 ignored 的 JSON，不修改 HANDOFF.md。

本轮专用定向测试，不运行业务构建：

```sh
make exec CMD='python3 -m unittest discover -s scripts -p "test_*.py" -v'
```

foundation-runner 的 full 还包含真实 `python3 scripts/check_project_contract.py`。主会话负责补齐并实际执行本轮批准的其他 full 工具/构建/浏览器门禁；不得把定向测试视为整套完成。

## 状态、基线、轮次及恢复

状态为 PENDING/READY/ACTIVE/VERIFYING/DONE/FAILED/BLOCKED/STALE。仅 `task finish` 对当前输入的闭合 full 及其同修订 fast receipt、进程退出码、日志/结果哈希、范围做复验后可以得到 DONE；在 resume 时也重新验 DONE。手改状态不能构成证据。

`task start` 保存两层仓库现有内容基线，已有脏文件不会自动算本任务修改，也不会被清理。后续增删改按基线差量检查，allowed 不覆盖 protected。不允许借启动另一卡重新吞掉越权改动。全局工作区 claim 使未完成施工顺序进行；DONE 的 claim 不妨碍下一任务。工具维护也必须具名授权，不通过 reset/stash/删除状态绕过。

一次修订 fast→full 只计一轮。改了当前输入必须重新 fast；失败重试也消耗轮次，最多三轮。full 不能绕过 fast。进程被合法中断时保留 VERIFYING 的未闭合记录，resume 转 BLOCKED；同 tier、同输入的中断续跑不再次消耗轮次，不接受旧未闭合结果。可直接 `run` 续跑，或 `task start` 恢复 ACTIVE，但不会重建已有基线。第三轮的成功 fast 仍可执行配对 full。超过预算保存最小复现并独立机制复核，不修改 rounds。

后续任务在 allowed 内修改了上游已认证文件，可能使依赖 STALE。执行 `task recheck-dependencies <当前owner-id>`：保持当前 owner 原始基线，先检查其差量边界，按 DAG 只重跑已配置且曾启动的上游 fast/full，生成全新证据再 finish；重验过程不许改源码，不能借此首次启动未知依赖或吸收越权改动。依赖通过后 owner 恢复 ACTIVE，但仍需自身当前输入 fast/full/finish。只读上游重验不消耗上游实现轮次；当前 owner 的三轮限制保持。不继承旧绿、不手工 DONE、不重建基线。

锁是 `flock` 非阻塞原子锁，文件永久保留，进程退出自动释放；锁占用时报 BLOCKED，不删除锁文件。结果每次写入全新 run 目录，原子状态写入带 fsync。该机制不是抵抗具有同等仓库写权限的恶意代理的隔离边界。

输入身份包含当前任务定义、policy、相关源码/输入/允许范围、验证器及适配器、工具可执行文件内容、Python 版本与依赖身份。不直接把整个 tasks.json 当作所有任务共同输入：无关未来卡的配置更新不让 foundation 重验；当前卡/依赖/策略/工具变化仍使旧证据 STALE。修改运行中的当前卡会被拒绝。报告产物写 ignored artifacts，避免报告自己引发无限重验。

## Check 与 receipt

任务文件顶层必须精确为 `{schema:1,tasks:[...]}`。每卡精确字段为 `id goal non_goals read allowed protected depends inputs checks evidence stop max_rounds completion blocked_reason`；checks 具有 fast/full 列表；每项精确为 `id argv cwd timeout result_type window external`。argv 是数组，不经过 shell。禁止嵌套容器、shell、`--ignore`。任务依赖必须是 DAG。

运行器向子进程提供：

- `VERIFY_RESULT_PATH`：本次全新结构结果绝对路径；
- `VERIFY_RUN_ID`、`VERIFY_CHECK_ID`、`VERIFY_IDENTITY`：必须原样写回。

子进程成功必须退出 0，且写入以下结构（未知字段或重复 JSON key 也拒绝）：

```json
{"schema":1,"run_id":"由环境复制","check_id":"由环境复制","identity":"由环境复制","type":"TOOL_PASS","passed":true,"summary":"实际范围","ignored":[],"evidence":[]}
```

普通既有工具命令可用 `python3 scripts/verification_lib/check_command.py -- <argv...>` 生成 **仅 TOOL_PASS** 的进程 receipt。此 wrapper 不解析文字 PASS，不认证 fidelity；非零退出即失败，`RESULT none` 不能覆盖错误退出。已有原生 receipt 的命令可直接调用，例如 project-contract。

具有 window 的检查必须精确指定 `start/end/roles`，至少两个不同角色；结果 evidence 每项为 `{role,path,sha256}`。路径必须在本次 run 目录，文件不得别名/硬链接/符号链接，JSONL 的整数 frame 必须足量、唯一、按序连续。共同截断、共同缺帧不能靠两边相等通过。这个通用完整性检查**不验证业务语义**，不替代独立资格、稀疏事件生产端完成证明、全部字段比较或 GPU 认证：具名可信适配器必须先证明这些条件才能发 FIDELITY_PASS。当前未实现的业务适配器一律阻塞，不能让静态文档/空字段升级。一个 fidelity full 可以含辅助 TOOL_PASS，但至少必须有真正的 FIDELITY_PASS gate，全部 checks 都要成功。

当前 `external:true` 明确阻塞。真实浏览器测试使用既有 th13-shot；需先实现受审的进程/产物适配协议，不能嵌套 podman、人工导入 PASS 或伪装本地 check。未有独立原版 GPU 真值时最终输出仍 BLOCKED。

## 受控新增/激活任务（不依赖强主模型口头补充）

后续未知机制只能进入**具名研究任务**，列出原版锚点、输入、文件所有权、固定实验、可验证 artifact 和停止条件；研究 completion 为 TOOL_PASS，产物核验必须检查实验内容/当前身份而不是“报告存在”。研究完成不自动变成资格认证。

更新未来卡不应让工具证据失效，但也不等于任意 Agent 获得修改验收权限。流程是：

1. 用户或既有版本化决策规则授予具体 task-configuration-maintenance（策略更改则 policy-maintenance）独立任务。只授权未来卡/所需适配器，明确禁止改既有 golden、容差、窗口、忽略字段或当前业务目标；未知权限保持 BLOCKED，不能自行授予维护卡权限。
2. 在已授权维护卡中记录基线，固定运行本目录 schema/DAG 检查及 `test_verify.py` 正负控。给每个新适配器添加正控和变异负控；强门禁未实现仍保留 blocked_reason，不用 wrapper 升级 fidelity。
3. 独立 reviewer 在不同于施工的 review 步骤核对具体 diff、机制依据、五道门、范围及负控。评审证据必须绑定旧/新任务与适配器内容哈希、执行命令/run identity、实际产物和明确结论；不能把文字“reviewed”或文档存在视作独立 review。独立审核适配器未实现时该维护 full 必须 BLOCKED，不手工写 DONE。runner 不宣称能辨认同权限用户伪造的评审身份。
4. 维护 fast/full 与独立审核都通过后 finish，再 resume 选择新 READY 任务。没有完成的当前施工基线必须先解决，不另开卡吞掉越权修改。全局 claim 顺序执行，不要求弱 Agent 判断并行安全。
5. 当前卡/策略/验证器变化会使旧证据失效，必须实测重验；无关未来任务配置不会使已经完成的 foundation 永久 STALE。本轮首次创建入口是用户明确授权的 bootstrap 修复，不是泛化的自动免审规则。

该流程可以由任何获授权的模型执行，不要求隐藏历史信息或强模型特权。缺少具体核验器的任务本身保持阻塞，不得把维护流程说明作为实现完成。

## 任务图与交付边界

foundation-runner → tool-protocol → submission-boundary → handoff-drill → Replay 研究 → Replay 资格认证 → complete-capture → production-driver → backend-certification → stage1-full-chain → full-game-coverage。Stage1 也直接依赖 production-driver。后端独立输出资格在 Stage1 最终认证前，不反向依赖 Stage1。

基础工具可以 TOOL_PASS/DONE，不因游戏尚未一致而卡死；但是这个结果不授予“框架好”的结论。按本轮用户要求，必须指定 CPA `new-provider/zai-glm-5-3` 真正完成并严格认证 Stage1 菜单到 Stage2 主线（含独立最终输出），才评价框架有效。首条 Stage1 主线仍不等于全游戏覆盖。TOOL_PASS、确定性、BASELINE_HELD、严格一致、最终输出、全覆盖分别报告，不能相互替代。

## 已实现的自主配置与修订入口

- 空门禁未启动卡（即使 blocked_reason 为空）可通过 `python3 scripts/task_configure.py --proposal <ignored.json> --review <ignored.json>` 配置，格式见该脚本 docstring。保留目标/非目标/依赖/完成等级/轮次/保护，绑定旧卡和proposal字节哈希；不会运行门禁或写任务状态。缺适配器仍不能通过。
- 现有测试默认不可改。增加正负控需 proposal 增加 `additive_tests:{具名测试路径:修改前sha256}`，路径同时在 allowed/read/inputs，独立review增加 `test_change_policy:"additive-controls-only; no acceptance weakening"`。这不是自动证明语义；实际patch仍须独立审核，不允许删除、跳过或降低判据。protected仍优先。
- `task mechanism-review <id> --review <ignored.json>` 绑定card/baseline/最后闭合失败run及新的机制假设/实验，要求独立批准。原baseline、失败与上一轮完整状态归档，开启新的最多三轮修订；不能直接 DONE。字段见脚本方法及负控。
- `task reopen <id>` 仅用于曾DONE现STALE且没有未完成owner的受授权维护，归档并核验旧fast/full；ACTIVE/FAILED不得借此吞掉越权。

## 具名维护接续（同一个 runner，不扩大全局豁免）

用户授权独立、无依赖且完成级别TOOL_PASS的维护卡后，可 `task maintenance-start <maintenance> --owner <owner> --review <ignored.json>`。它核对owner原baseline、当前状态/工作区差量和精确具名文件；无关越权或policy改动拒绝。原owner暂停，不能start/run/finish/reopen；只允许维护卡按正常fast/full/finish。

之后真实独立审核实际patch，再 `task maintenance-return <maintenance> --review <ignored.json>`。return必须有维护卡当前闭合证据及当时的独立review。只为该owner/该baseline记录具名文件精确after值，不改runner_baseline，不改变原baseline与失败历史。再次漂移阻断；owner旧fast/full失效，仍需重验依赖和自身fast/full。维护未完成或review拒绝保持暂停，不能伪造批准。

review精确字段为 `schema:1, phase:"start"或"return", owner, maintenance, owner_card_sha256, maintenance_card_sha256, owner_state_sha256, workspace_sha256, files, approved:true, reviewer_role:"independent", issues:[]`。前三个sha为verify.digest对应卡/owner state；workspace为maintenance_workspace的digest；files与维护卡allowed顺序一致。review是独立上下文证据，不是同权限攻击者认证。维护工作区材料不发现或读取games资产/profile。

临时bootstrap仅用于旧runner尚无该入口时，通过已审核ignored loader显式设置ROOT=/work后调用相同main、容器检查和flock。它不是免审或免锁通道。入口完成后使用正常scripts/verify.py；不要把临时bootstrap作为第二套框架。

本次bootstrap唯一兼容项：review可选 `preauthorized_changes:{"AGENTS.md":{before:<原owner基线record>,after:<当前record>},"verification/tasks.json":{before:...,after:...}}`。仅这两个用户明确授权入口/任务图路径可列，start逐项核对原/现record；return须带相同map且after不漂移，归还delta仅属于原owner。其他路径、未列出越权、过期after均拒绝，后续正常维护先领取卡再施工，不扩大此兼容面。三轮耗尽的被中断VERIFYING run可独立机制复核；原run不改写，只在revision记录INTERRUPTED事实，不认证成功。
