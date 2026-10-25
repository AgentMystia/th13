# th13 当前接手入口

先读 [AGENTS.md](AGENTS.md)，不要从旧战报挑选下一项任务。

```sh
make exec CMD='python3 scripts/verify.py doctor'
make exec CMD='python3 scripts/verify.py resume'
```

机器返回的任务卡、依赖和当前证据决定可做什么。`verification/README.md` 定义操作协议；[已批准架构](docs/plans/autonomous-refactor-2026-10-24.md) 定义技术路线，不代表实现已完成。

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
- **下批候选**：①修 18 个基线既有失败中真实过期断言（pause_test 期望值 vs
  PauseMenu::configure_entries count=5 实际）；②Extra 无条件禁用 → 按
  0x44c570 case0 +0x5c40 bit1 解锁门实装；③browser smoke 扩展 Practice
  全链路（现仅基线 smoke 覆盖普通开局）。

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
