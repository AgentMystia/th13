# DeepSeek 接手框架（2026-10-29；准备，不施工）

## 授权及本轮范围

用户要求：把两份附件换入活动 Replay 集；只在既有 `th13-yolo` 容器安装官方 DeepSeek Harness；提供新长程任务提示词；立即 commit & push 建立防回归检查点。本轮不继续产品实现、对齐、构建或游戏测试，也不启动模型任务。

本文件是接手索引，不是验收 receipt，不改变 `verification/` 的状态。AGENTS、golden、比较器、策略没有因本次接手而被重写。

## Git 恢复锚点

两个独立仓库，不是 submodule：

- 外层 `/work`：`72a098f`，https://github.com/AgentMystia/th13/tree/backup/pre-deepseek-20261029 。原版工具、状态采样、比较器和诊断脚本快照。
- 产品 `/work/eagler-touhou`：`03111aabe`，https://github.com/AgentMystia/eagler-touhou/tree/backup/pre-deepseek-20261029 。生产源码、测试和 Web 适配快照。
- 分支均为 `backup/pre-deepseek-20261029`；不改 main、不更新现有 PR、不 force push。后续施工先在两仓分别新建 `work/deepseek-<session>` 分支，不在备份分支直接施工。
- 上述提交是 WIP 保护点，不是审查通过、完整状态对齐、发布或 CI 成绩。本轮未重跑游戏测试。
- 受保护的既有 AGENTS/HANDOFF 修改按用户备份授权另行提交，不混入普通产品任务：外层 `c8c99ea`、产品 `ff3e0e158`。仅保存既有字节，它们未由本轮改写；不代表独立规则维护或验收放行。
- `test-img.png` 的既有删除、测试生成 ELF、空文件 `body_angle`/`flags` 不计入源码保护点，也不清理。完整 tracked 工作树补丁另存 ignored `artifacts/pre-deepseek-20261029/{outer,product}-worktree.patch`，原有状态保留。
- 两个新源文件原有 EOF 空行警告保留：`AnmText.cpp`/`.hpp`；为保护点不做格式修复。
- 恢复时先保存新工作的 patch，比较锚点再定向恢复；禁止直接 `reset --hard`/`clean`/整树覆盖。

## 活动 Replay 集（原样字节，仅 ignored 本地资产）

**两份新附件都是早苗，不是一个魔理沙加一个早苗。** 用户指定的替换集合照收，不改角色字段、不改名冒充。四份录像只覆盖三种角色；魔理沙 v1.00c Replay 缺口必须独立报告，不能宣称四角色覆盖。

| 活动文件 | 解码角色 / 难度 | USER 版本 | 六关 frame_count | 合计 |
|---|---|---|---|---:|
| `th13_01.rpy` | 灵梦 / Normal | 1.00c | 11675,16148,19408,17825,20481,24171 | 109708 |
| `th13_udN01.rpy` | 妖梦 / Lunatic | 1.00c | 12335,14608,18633,14168,15316,18067 | 93127 |
| `th13_udPn16.rpy` | 早苗 / Hard | 1.00c | 13768,15581,18370,17550,18531,23081 | 106881 |
| `th13_udMy26.rpy` | 早苗 / Easy | 1.00c | 13109,16847,18650,16472,21536,25020 | 111634 |

文件在 `/work/games/replays/`，SHA-256：

```text
2869c854167537e066579c1435d794bbefb06ed49b771a74904b0c394a5f9d09  th13_01.rpy
e1198327df4e349e801f6575095390e4f42634b1cca93035911f3a2888527a98  th13_udN01.rpy
4d4e0a750567727cfcc1dd3b970f960ca4dbd3f1fcf24145ad400e79ec8df43b  th13_udPn16.rpy
c3aa54108522c803f8e003bbedeaf20a9e7aa041725d4749eed0b14dfb49c0d3  th13_udMy26.rpy
```

顶层恰好四份；四份声明合计 421350 帧，不代表全部都应消费到 EOF。USER 版本只是元数据，不证明 EXE 来源资格。

旧 `th13_udAs01.rpy`（魔理沙 Hard，1.00a）及 `th13_udSr01.rpy`（早苗 Normal，1.00a）原样移入 `/work/games/replays/archive-pre-deepseek-20261029/`，未删除、未覆盖。它们只用于有明确身份的历史诊断，不进入新活动语料认证。禁止给新文件起旧别名或改写旧 golden 来延续成绩。

### 旧路径迁移仍未实现

本轮只替换资产，没有改代码/测试。以下入口仍绑定旧名字、seed、hash、帧数，直接运行可能找不到文件；这属于已知接手状态，不能先恢复旧活动资产来掩盖：

- `scripts/check_replay_mechanism.py`、`oracle/test_replay_qualification.py`、`oracle/test_replay_practice_field.py`。
- `verification/tasks.json` 的旧输入/full 窗口；`scripts/check_replay_certification.py` 的部分文案。
- 产品 `tests/replay/replay_test.cpp`、`tests/baseline/run.sh`、`tools/replay-verifier/corpus.json`、`run-oracle.mjs`。
- `oracle/battle_oracle.py --menu-replay` 枚举及专用菜单 tape，`tools/marisa_registration_probe.py`、`tools/shot_probe.py` 等历史探针。

迁移需先决定“历史固定夹具”与“新活动认证”的职责，历史固定夹具可显式读取归档，不能静默改期望。受保护配置/golden/比较器变更必须具名维护、负控和独立复核，不由普通修复吸收。不得复制旧 seed/帧窗到新录像。菜单排序已变化，旧 tape 不能直接认证新菜单选择。

## Harness 安装及启动

仅容器内：

- 官方源码 `/home/dev/deepseek-harness`，克隆当时 HEAD `f3803c96974ad4a94195fb97e5c637e9c2a3afe3`（源码版本 `0.2.2-alpha.2`）。作为文档/源码参考，未构建它。
- 实际安装的是官方 npm `@deepseek-ai/dsh@0.2.1-rc.2`，与源码 HEAD 不同。版本固定，以已安装 CLI/schema 为准。
- 私有 Node 24 和安装包：`/home/dev/.local/share/th13-deepseek-runtime/`；不替换 `/opt/node`，不修改 th13 package lock。
- 专用入口 `/home/dev/.local/bin/dsh-th13`；专用 `DSH_HOME=/home/dev/.local/share/th13-deepseek-home`，不导入 Codex 凭证。
- 已通过 `--version`、`--help`、`headless --help`、`web --help`，并启动 Web profile。没有模型调用或产品测试。
- 服务绑定 `127.0.0.1:13080`；容器为 host 网络。Web 会输出带访问令牌的 URL，令牌只从用户自己的运行终端获得，不写入此文档或 Git。

从宿主运行（若服务已在，不重复启动）：

```sh
podman exec -it -w /work th13-yolo /home/dev/.local/bin/dsh-th13 web --no-open --host 127.0.0.1 --port 13080
```

进入 Web 后用户自行配置 Settings → Models 的提供方/API key，选 `/work` 工作区、`standard` preset 和所需模型/思考强度。不要让代理寻找、读取、复制现有 Codex 或宿主凭证。

本次没有配置模型密钥，没有开始会话/Goal。安装启动通过不等于端到端模型调用验证。

### 准确的长程入口

在 Web 新会话中粘贴 `docs/plans/deepseek-long-goal-prompt-20261029.md` 的正文。它授权模型调用已安装版本的 `create_goal`，避免把很长的提示词错误地当作 shell 命令。

安装版本支持：

```text
/goal [<objective>|clear|edit <objective>|pause|resume]
```

默认 `standard` 启用 Goal 自动续轮，默认轮数上限 256。模型工具 `create_goal(objective, max_goal_rounds?)`，`get_goal()`，`update_goal(goal_id, revision, action, ...)`；不要使用 Codex 的 `status` 参数冒充本工具的 `action`。重启后 active Goal 恢复为 disarmed，用户需 `/goal resume`；不是后台永远自动重启。`/goal` 查看状态，`/goal pause` 立即暂停。已安装 headless 没有 `--permission-mode`、`--approval-policy` 或 `--add-dir`，不照抄 HEAD 文档。

## 历史证据与当前未完成

起点为意外中断的 Codex 会话 `01a1865b-0561-7a12-b64f-e183ff0e1ef7`。11:56 UTC 发生 `stream disconnected ... Account selection timed out`，不是任务完成。摘要只作导航，实际证据在 `artifacts/th13-longrun-20261029/`。

- `root-resume-v11.md`、`feedback-progress.json` 是停止前笔记，其中 RUNNING/旧首差可能过时。
- 当前 v11 构建输入238个在准备时全部与manifest一致；WASM `8d492acab4c245de6946fd0939680c72b75772c1f8455f366f7a9c85de07ad95`，manifest `00cdfeb6f5ea2b417e6e3bb1fabfbb8aa8744bab9aecc2ff3ce407cb2224455b`。新改动后必须重新验证身份。
- v10 core严格窗口：灵梦第五关2500、早苗第五关3920、魔理沙第一关6100、妖梦第二关1100；是旧录像、旧构建的特定投影，不是完整状态证书。v11旧早苗/魔理沙窗口也留存完整同种子core数据，准备时只读逐行核对零差异，未新增正式receipt。
- core只含输入、19个全局u32、自机xy、两条RNG种子/计数。实体、ECL、ANM、几何、激光、伤害、资源、音频等未全覆盖。`completeStateCertified=false`。
- v11灵梦六关Web `web-reimu-six-stage-v11/capture.json`：请求109708，采到109328，连续600宿主tick无输入而失败。原版 `replay-six-stage-boot116-v2/result.json` 也只消费109328（六槽11675/16148/19408/17825/20481/23791），退出1。声明第六槽24171，少380；必须取证EOF/尾部含义，不能截尾或改窗口放行。没有连续整窗比较结果。
- 原版堆复用已修，保留128MB，第四关12000诊断exit0；Windows线程/堆地址策略和GPU仍未认证，`reference_qualified=false`。
- 四角色前600旧core相等、若干实体/标题VM/音频请求窗口相等，不是四份全关卡状态齐。
- 历史测试：ECL v6 168/168、ANM v6 42/42；UI v6 364通过/5失败；命中回调v7 21/21；浏览器v10 4/4；连续比较器12/12。当前v11完整回归未完成，旧碰撞边界/保护测试仍有失败。不改断言造绿。
- 群友反馈仍有完整浏览器/独立参考缺口：音乐室字形、持续激光具体现场、Boss爆炸/蓝线、神灵加点、通关奖励、全部暂停特效、音频波形/工作线程、跨关标题等。
- 独立原版GPU framebuffer不可用时最终输出认证持续BLOCKED，不能改成人眼通过。

## 工具入口与资源

已在指定容器内可以直接工作，不需要嵌套 Podman。宿主构建/浏览器仍走规定容器入口；不在宿主装依赖。

```sh
# 产品构建，只在冻结源码/没有正在使用旧产物的采样时
cd /work/eagler-touhou/ports/th13
EMSDK=/opt/emsdk node portable/build.mjs
# 真实网页采样的已存环境（使用前验证路径仍存在）
export NODE_PATH=/tmp/th13-browser-tools/node_modules
export CHROMIUM_PATH=/home/dev/.cache/ms-playwright/chromium-1194/chrome-linux/chrome
export LD_LIBRARY_PATH=/tmp/th13-browser-deps/root/usr/lib/x86_64-linux-gnu
```

`/opt/venv/bin/python`、`/opt/emsdk` 已存在。原版输入 `games/th13/th13.exe`，资源 `artifacts/reference/raw`。`thbgm.ogg` 是无效占位，不因它阻塞已有行为路径；不删除或伪造资源。

`oracle/replayfmt.py::ReplayFile.decode` 解码，`scripts/web_replay_state.mjs` 单槽，`scripts/web_replay_continuous.mjs` 连续每宿主tick，`oracle/replay_core_compare.py`/`replay_continuous_compare.py` 严格投影比较，`tools/batch_replay_oracle.py` 独立快照原版采样。先读当前 `--help`/schema，禁止凭摘要拼参数。

并发受pids.max=2048限制：最多两项重型采样并行，先测资源再扩大；新启动的浏览器必须可靠回收。不要因超时重复启动仍活着的任务，不杀无关进程，不启动九个Chromium。三轮没有机制新证据就换取证路径。
