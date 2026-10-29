# th13 当前接手入口

先读 [AGENTS.md](AGENTS.md)，不要从旧战报挑选下一项任务。

```sh
make exec CMD='python3 scripts/verify.py doctor'
make exec CMD='python3 scripts/verify.py resume'
```

机器返回的任务卡、依赖和当前证据决定可做什么。`verification/README.md` 定义操作协议；[已批准架构](docs/plans/autonomous-refactor-2026-10-24.md) 定义技术路线，不代表实现已完成。

## 2026-10-26 产品优先攻坚（未完成最低交付、未提交）

用户本轮明确授权直接按 `docs/plans/product-first-port-host-podman.md` 推进，不修旧任务卡死锁；不使用 Browser 控制，全部验证走容器无头。完整事实记录见 [本轮结果](artifacts/verification/product-20261026/RESULTS.md)，不要从代理自报恢复通过状态。

- 已修真实生产链：命中读取积分后敌机位置、同帧扣血（原版 mgr+0x28 是统计而非次帧池）、击杀执行掉落表、道具得分指针、ECL501 独立本体框；Replay 跨关/重试重新恢复对应 header/seed、EOF 进入可操作 front134 结算；Extra 角色级禁用与跳过。已有用户修改全部保留。
- Web/WASI 构建通过；UI 168/0；死亡掉落3/3、射击几何同帧伤害3/3、本体/道具接线5/5；ECL 整套复验通过；正式网页 EOF 与导入错误回归2/2。命令、历史失败和构建身份均在结果目录。
- **Stage1 仍失败**：补原版出生无敌初值2及碰撞后递减相位后，Reimu Normal 11675帧输入最新长窗 **5587/11675帧、3死Game Over、score78668、destination0**，非通关；严格状态首差 **f168 score7/8**。最后数量以 `birth-full.log` / `birth-full-compare.log` 为准，不能把进程exit0当完成。旧5368帧/f125失败保留，不冒充最新修订。
- **真实网页覆盖**：最终无头矩阵18/18通过：四角色×四难度16格自然进入Stage1、新档Extra禁用、Practice逐级返回。仅开局/菜单短窗，不是16条完整参考轨迹，也不是整关认证（`web-smoke/matrix-final.log`）。
- **未完成**：PlayerData、SpellPractice、实际 PadConfig 屏（仅存储/输入基础）、日文字形动态纹理生产提交、全Stage1关末/Stage2贯通及完整像素认证。本体矩形角度VM来源仍须闭合。四角色四难度完整replay仅2格，缺14格；短demo不算整关。
- 独立原版GPU参考环境实际可运行，30帧捕获通过；旧Web producer的 INIT_MISMATCH 是硬编码过期断言，不是生产缺恢复的证据。input0原版图仍NowLoading，继续对照前需核实可见阶段资格，不得迎合错误参考。
- f168剩余首差的原版最小现场确定为16×16、damage18灵梦引导弹（`hit-cadence/f168-oracle-hits.jsonl` / `birth-400.log`）；目标选择/引导更新公式待机制还原，不再按帧改常数。出生初值修订后UI168/0、射击几何4/4、网页Replay2/2、完整ECL复验exit0。
- 多个实施子代理被提供方 `Individual quota reached` 中断（报错提示约1小时后恢复），未换模型或账户。不得把被中断的菜单/字体方案当作已实现。
- 32个本轮临时逆向探针已无损迁至 ignored `artifacts/verification/product-20261026/body-collision/probes/`，保留hash清单；没有提交、推送、清理既有工作或修改golden/容差。

## 2026-10-26 菜单批次二：18 个基线测试失败清零 + Extra 全链路由实装（未提交）

- **18 个基线失败全部修复，全量 159 ok / 0 fail**（run.sh exit=0；node 套件 6/6；
  web 构建 portable/build.mjs 通过）。逐一按原版证据判定均为**测试/fixture
  过期**（r5 原版化实装后未跟上），零生产修复需求：
  - pause_test 4：0043e780 case1 `count = 5 - practice`；case4 收场 cursor 仅
    0/1/4；**条目语义 1=タイトルへ（0x414fc0(4)＝场景请求 4＝Exit，与
    SessionState.hpp Practice 退出链同源）、4=やり直す（DAT_004dcc24=10/11）**；
    确认非继续项走 case5/7 的 20 帧闪烁 + Yes/No（默认 No）。中途曾误判
    RunPause::finish_selection 交叉错位，实为 port 正确、测试语义写反。
  - stage1_player_state 5：00444ca0/00443240 决死 8 帧窗口 + Dying 第 3 帧扣
    power；00443240 启动计时器>19 才可射 + 60 帧入场；bomb 分角色实装
    （霊夢 8 orbs 56px 内每帧 ≤80 上限、伤害经 pending_damage 池次帧应用）。
  - run_scene 8：GameHud 0x425170 灵界计 front script 171（注释已预言 fixture
    需自带）；StagePresentation 卫星特效 player bank scripts 16-21。
  - title_state 1：自机页不建 root2 名行树（title-draws-b f650-749 定案）。
- **Extra 全链路由实装**（生产代码，本轮新证据 0044cd10 case4 case1 +
  00450fb0 全态 + 00451450）：
  - 主菜单 ExtraStart case：mode=0、暂存难度（+0x59b0 语义→saved_difficulty）、
    难度强制 4、光标归 0；Start/Practice 进屏时 configured>3 重置 Normal
    （case0/2 的 `3 < bb4d0` 分支）。
  - 难度屏 Extra 态：count=`((d>=4)-1&3)+1`＝1 单行、根 124（`(be7c4>3)+0x7b`）、
    无 ↑↓ 导航、确认保持难度 4、Esc 按 0x59b0 恢复原难度（case4 尾）。
  - 角色屏：根 `(be7c4==4)+0x81`＝130、武器 139+（weapon_base_ex）；
    开局 `DAT_004be81c=7`（stage 表行 7，表基 0x4bb854）→ progress.stage=7。
  - 新测试 ×3：title_extra_entry_routes…（全链含 start_stage==7）、
    title_extra_cancel_restores_saved_difficulty、
    practice_v2_roundtrips_extra_unlock_mask（FileStore extra 位 v2 roundtrip
    + v1 缺省 0）。上轮已实装的 extra_gate/通关落位点（Application.cpp:580，
    难度>0）由此获得行为覆盖。
  - **未验证**：root124 子构成未 dump（EXTRA 行确认高亮 0x75/117 暂缺，已
    注释 TODO）；Extra 对局侧 st07 资源装载未验证（菜单路由忠实，对局属
    Stage 批次）；角色屏 0x451450 的角色级 Extra 解锁位灰显（0x1b26c 族
    bit0x10）未实装。
- 诊断辅助：外层 `tmp/ui-one.sh <suite>`（单套件快速编译运行，ignored）。

## 2026-10-25 菜单主批次已提交（monorepo 16bdc103）

- **Practice 全流程实装**（原版 0x44cd10/0x44dcd4/0x449360/0x451cb0 证据）：mode=0x10、
  screen8 六关选关、真实 played 位锁定、确认音 16/7+50；旧 SessionState 注释
  "Practice=Extra" 已纠正。
- **th13_replay_import_error 悬垂指针修复**（BrowserRuntime.cpp 函数级 static
  快照）+ 浏览器生命周期回归（web/tests/sdl/replay-import-error.lifetime.test.mjs）。
- **UI 测试进程修复**：spawnSync 1MiB maxBuffer ENOBUFS 根因实证；新
  `tools/run-child.mjs`（fd 落盘+进程组超时）；`tests/ui/run.sh` compile-once
  （74 TU 一次编译，全量 ~385s→~35s）。
- 双仓均已提交（外层 3dd2822 / monorepo 16bdc103）；UI 套件 138 ok，18 个
  失败经逐一 diff 确认为 HEAD 基线既有（pause count 5 vs 测试期望 3 等），
  非本批引入。
- **下批候选**：①~~修 18 个基线既有失败~~（2026-10-26 已清零，见上节）；
  ②~~Extra 解锁门实装~~（门+路由+持久化+测试已于 16bdc103/本轮闭合）；
  ③browser smoke 扩展 Practice 全链路（现仅基线 smoke 覆盖普通开局）——
  顺带补 Extra 全链键盘回归；④PlayerData（0x44cd10 case4 case5 → 屏 0xa）
  /SpellPractice（case3 → 屏 0x11，+0x5d14=1、+0x5c4c=4）入口实装——本轮
  反编译已顺带定位两条路由的屏号与参数；⑤角色屏 Extra 角色级解锁灰显
  （0x451450 case0 的 0x1b26c 族 bit0x10 检查）。

## 人工注意事项（不改变机器状态）

- 两个仓库在本轮开始时已有未提交修改；不要 reset、stash、清理 `tmp/`、探针或未跟踪计划。
- 本轮起始备份：`artifacts/verification/pre-refactor-20261024T084018Z/`，包含双仓 patch/status、未跟踪文件摘要和原始 AGENTS/HANDOFF。旧 HANDOFF 完整保留在该目录，仅供历史取证，不是任务入口。
- `docs/rectification-2026-10-24.md` 撤回了错误场景、错误 STD 身份及“全绿”结论。原版 Replay 资格和全链路画面还原尚待独立认证。
- 原版资产和派生证据只在 ignored `games/`、`artifacts/`。用户没有授权本轮提交或推送。
- 用户要求后续主 Agent 也可为弱模型；发现缺失规则时修可执行入口/任务卡，不把必要步骤只留在聊天里。

## 独立分析结论（2026-10-25，r4/sht-collision，具名任务追加）

0x446870 语义已由原版 objdump 定案（证据与可执行边界用例在
`artifacts/verification/r4/sht-collision/`）：循环对象是自机弹命中框登记项
（g+0xa9e4，256×0x9c），param_1/param_2 是敌 hurtbox（敌+0x44 中心、敌+0x110
全宽高，与 StageEnemy::hurtbox 一致）。判据三分支：flags&2 圆（中心距²≤r²，
不叠弹尺寸）；角度==0 AABB 全含等号；角度≠0 用 φ=−θ 经 0x472fc0/0x473020
（dst={x·sinφ−y·cosφ, x·cosφ+y·sinφ}）旋转中心偏移与弹全宽高向量（分量取
绝对值）后在敌系做 AABB。命中写点：mode==0 时 项+0x88 += 项+0x84 伤害、
回调、total（≤80）计 `(total/10+10)/10` 入 DAT_004be7c0；消弹仅 hp<9999999
且 hp≤累积时清 bit0（自机弹 hp=10000000 永不消）。pl00a 主弹实测 w=48 h=16
ang=−1.5708（≠精确 −π/2，旋转分支退化近似 |dx|≤24+ew/2、|dy|≤8+eh/2，
边界 1e−4 行为不同）：拟合的"h/2+敌半"是特例，生产须按 sht float 角度实算
通用式。UNKNOWN：项+0x70/+0x74/+0x90 非弹身份、g+0x664/0x668 字段名、
player vt+0xc 语义、DAT_004be7c0 名称。
