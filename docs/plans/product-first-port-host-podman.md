# 《东方神灵庙》网页版：产品优先还原计划（宿主 Podman 版）

> 来源：容器内 Claude Code 计划 `/home/dev/.claude/plans/unicorn-stage-1-2-6-extra-dynamic-workf-eager-frost.md`（th13-yolo 容器，2026-10-26）。
> 本版为容器外（宿主）开发环境适配版：所有构建/测试/分析/浏览器验证改回两层 AGENTS 的容器纪律执行形态；产品优先的推进次序、证据标准与退出条件原文保留。
> 宿主仓库根 = `/home/mystia/项目/th13`，下文路径均以容器视角 `/work` 书写（同一目录）。

## Context

完成现有 th13 v1.00c 网页移植，使画面、操作、战斗和流程忠实于原作，并用反编译、Unicorn 与实际浏览器输出验证。严格按 **主菜单 → Stage 1 → Stage 2、3、4、5、6 → Extra** 推进。

按用户授权，施工以产品修复优先，不修旧验收框架死锁，不搞清理旧框架的大工程，不先填满完整交付验证器。保留能直接帮助修游戏的工具和证据标准；工具挡路时，只改解决当前产品问题所需的最小部分。宿主侧执行纪律不变：全部命令经 Podman 容器运行，不在宿主安装依赖，不触碰凭证，不伪造结论（见两层 `AGENTS.md`）。

本计划保持只读引用；施工直接从菜单批次开始，不再次展开泛化侦察。

## 1. 已核实的产品事实

以下产品路径相对 `/work/eagler-touhou/ports/th13`。

| 范围 | 当前源码事实 | 施工含义 |
|---|---|---|
| 主菜单入口 | `TitleMainMenu.cpp:127–177` 中 Practice、Spell Practice、Player Data 没有分派，落 default 不切屏；`:184–188` 无条件禁用 Extra | 第一批处理真实可达入口、返回及解锁，而不是只打磨截图 |
| 键位配置 | `sdl/ApplicationTitle.cpp:111–113` 的 `open_key_config()` 恒返回 false | 菜单阶段补齐原版对应行为和浏览器键位接线 |
| Replay 错误提示 | `sdl/BrowserRuntime.cpp:269–271` 返回临时字符串的 `c_str()`；`Application.cpp:100` 按值返回字符串，网页随后读取 | 确定的生命周期缺陷，菜单批次顺手修；不能把它充当菜单还原的全部成果 |
| 日文对话 | `RunScene.cpp:443–464` 跳过非 ASCII 行；`FontDevice::extent()` 恒返回 -1，字体绘制尚未接完 | Stage 1 首批可见缺口之一；不是简单删过滤条件 |
| 文本编码 | `MsgPlayer.cpp:293–310` 将 CP932 转 UTF-8，并丢弃 ruby 前缀参数；现有 `RuntimeFontRaster::draw()` 接收 CP932 | 保留原始文本及必要注音属性，接现有正文/ruby 动态纹理链，不能误喂 UTF-8 |
| 敌机本体碰撞 | `StageCommands.cpp:513–514` 的指令 501 直接返回；`StageBattle.cpp:781–793` 使用固定半径 | 从字段、脚本写点到碰撞一起修；原版 `00419a90` 使用 `+0x118/+0x11c` 与形状标志，不能复用自机弹受击用 hurtbox |
| 后续关卡 | `StageScript.cpp:154–155` 遇未实现 STD 指令报错；`Application.cpp:555–563` 的 Ending 实际回标题 | 逐关补实际需要的指令；Stage 6 必须实装结局与 staff roll |

**已有模块复用，不重写：**`StageSim::drop_table → ItemManager::spawn_item`、道具种类分支、`SpellPresentation::begin/end`、`MsgPresentation` 立绘、`RunScene::draw_scenery → StageScenery::draw`、`Application` 的 NextStage 路由均存在。陈旧 TODO、“完全没有 STD/掉落/符卡”等历史或代理断言不能作为修改依据。菜单中的逐帧颜色/层序修正是待验证区域，不预先认定每一处都错。

## 2. 最小环境准备，与产品修复同步

宿主开发环境基线：`localhost/th13-dev`（Node、g++、Emscripten、WASI SDK、Python/Unicorn，经 `make exec` 驱动）、`localhost/th13-shot`（浏览器验证，经 `make shot`）。标准位置/PATH 未找到 Chromium、Playwright、Wine、Xvfb，相关浏览器环境变量也未配置。

- 浏览器侧一律复用既有 `localhost/th13-shot` 与 `make shot` 批次；确需新增浏览器工具（如 Playwright 脚本），以容器方式加装并固定版本——扩展 `th13-shot` 镜像或专用派生镜像，**不在宿主安装任何依赖**。用正式 `web/sdl-runtime/th13.html` 开局。
- 独立原版出帧复用 `artifacts/verification/sameframe-fast/original/producer.py` 及已有 D3D9 捕获代码；Wine、32 位支持、Xvfb 与编译工具全部在 `th13-dev`（或派生镜像）内安装适配，宿主零安装。镜像层改动在交付记录中登记来源与版本。
- 已有原版短窗和 qualification 文件可以复用，但先核对其输入/版本/相位及实际产物；旧报告的 PASS 不自动成为当前证明。短窗不冒充整关。
- 已检查的游戏资产、reference、网页资源及系统字体目录未找到 MS Gothic 候选。接通文字链与查找合法原字体/字形来源并行；若临时使用明确标记的开发替代字体，仅证明文字可读，**不计作字形一致通过**，最终仍需原字体/原字形参考。
- UI 测试包装器已有 `status=null` 失败记录，却未输出 child error/signal。使用该套件时先查清进程原因；必要时改为完整日志流和清晰错误报告，不删断言、不盲目延长超时、不重建测试系统。

环境工作只服务于本批浏览器或原版对照；原版出帧暂缺不冻结已经能用源码/Unicorn 证明的产品修复。

## 3. 第一阶段：把菜单做成真正可用且忠实的入口

### 首个施工批次

1. 从正式网页冷启动，记录 **标题 → 主菜单操作 → 难度 → 角色 → 自然进入 Stage 1**，加取消、返回、再次进入和长按轨迹；原版使用同等初始状态与按键。
2. 在原版新档和代表性合法解锁合成档中，确认 Practice、Spell Practice、Player Data、Extra 的可用条件、屏幕路由和返回行为。沿现有菜单结构补齐，不以统一禁用或空白页面掩盖缺实现。
3. 修 Replay 导入错误字符串寿命，覆盖损坏文件、连续失败、随后有效导入，确保错误提示与菜单恢复可靠。
4. 修本轮实际观察到的首个菜单动画/布局差异；追到菜单状态、ANM 指令/中断、更新次序或提交顺序，不追加按某次录像绝对帧拟合的颜色/坐标补丁。原版自己的定时器照实实现。

### 菜单阶段继续补齐

- 复用已存在的 Replay、Options、Music Room、Manual，验证真实宿主服务、音效、设置保存和返回重入。
- 补键位配置、Player Data 所需记录，以及练习/符卡选择所需的生产状态与保存数据；扩展 `FileStore` 时兼容已有有效存档，不另造测试专用解锁状态。
- 后续关卡内容仍按关卡顺序交付：菜单侧先正确选择、展示、路由和取消；Stage 1 练习启动随 Stage 1 一起闭合，不能将“入口可点击”说成对应整关已完成。

**主要文件：**`game/TitleMainMenu.*`、`TitleScene.*`、`TitleState.hpp`、`TitleSelectionMenu.*` 及相关子菜单，`sdl/ApplicationTitle.cpp`、`ApplicationState.hpp`、`FileStore.*`、`BrowserRuntime.cpp`；回归复用 `tests/ui/title_*`。确需新增菜单类时沿现有子菜单模式增加，不新建菜单框架。

**菜单退出条件：**新档/代表性解锁档下，原版可达菜单项与禁用条件正确；确认/取消/重复输入/返回、设置与音效有直接对照；四角色、四通常难度可正常开局。画面差异按明确输入与动画窗口记录，不以一次开局成功或无边界的“看起来一样”销账。

## 4. 第二阶段：Stage 1 真实闭环

先保留一个自然开局的整关基线，再按实际首差的因果关系修复；下列是定位地图，不是要求所有基础工作完成才能修下一项的瀑布流程。

### A. 已确认缺口优先

**日文对话：**
- 复用 `MsgPlayer` 解密/CP932 映射、`MsgPresentation`、现有 `FontDevice` 和 `common/include/eagler/text/RuntimeFontRaster.hpp`。
- 在生产对话状态保留原始 CP932 与结构化行/ruby 参数，UTF-8 仅为派生显示/观察值；参数意义核对原版 `00428ae0`，不按字符串形状猜。
- 补字体度量、正文/注音栅格、动态纹理上传和 `text.anm` VM 生命周期，全部经过现有生产渲染链，不用 DOM 或捕获专用补字。
- 验证换行、注音、说话人切换、跳过、释放及返回；字号、字重、位置、颜色/边缘和时机对原版。字体相同也不能自动推定栅格相同。

**敌机本体碰撞：**
- 在 `StageEnemy` 增加与 hurtbox 分开的本体字段，核实构造默认值、ECL 501 写点、ANM 角度来源和形状标志。
- 依据 `artifacts/decomp/jp/functions/00419a90.c` 及碰撞辅助函数，用 Unicorn 实验确定圆形/旋转矩形、偏移、边界、无敌/对话/灵界等门控和执行相位。
- 一并修改 `StageSim.hpp`、`StageCommands.cpp`、`StageBattle.cpp`；不是只换固定半径公式。回归真实碰撞、擦边、不同尺寸/角度、受击框与本体框相互独立。

### B. 同一条生产流程逐项收敛

- **初值/输入：**复用 `ReplaySession::restore_stage`，核对 stage 头、seed/RNG 计数；BrowserRuntime → Application → RunScene → StageBattle 只消费同一套输入及边沿状态。
- **战斗：**移动/集中、四角色射击、伤害/击杀、敌机与弹幕、掉落/回收/计分/碎片、死亡/复活、Bomb/决死/灵界。先核对已有机制，再修改实测差异。
- **画面：**STD 相机/可见性、雾/深度、RT/viewport 合成、ANM 层序、HUD、符卡、对话和特效。复用 `PlayfieldTargets`、`StageScenery`、`SceneMatrices`、`AnmRenderer` 与 `GraphicsDevice`；不得用藏绘制来减小差异。
- **流程：**道中、道中 Boss、Boss 对话及全部阶段、击破/超时、关末奖励、自然进入 Stage 2；覆盖暂停/重开/回标题、Game Over/Continue、Replay 保存/回放/EOF。
- **音频/帧钟：**复用 `AudioDevice::mix/pump/statistics`，核对 BGM/SFX 时机、循环和暂停恢复；真实 rAF、高刷、失焦与恢复不得重复推进模拟或发生输入错拍。

**主要文件：**`RunScene.*`、`MsgPlayer.*`、`MsgPresentation.*`、`sdl/FontDevice.*`、`StageBattle.*`、`StageSim.*`、`StageCommands.cpp`、`PostGameFlow.*`、`ReplaySession.*`、`sdl/Application*.cpp`，以及差异实际落到的 ANM/HUD/渲染文件。新增回归放现有 UI/combat/ECL/items/bomb 套件，不重写已工作的系统。

**Stage 1 退出条件：**
- 四角色 × E/N/H/L 的 16 格各有至少一条有意义的完整参考轨迹，包含射击、道中、对话、Boss 全阶段及自然进入 Stage 2。
- 状态/事件持续对照；完整绘制与 framebuffer 能力逐步扩大到声明的验收范围。若目前只比对视觉连续窗口，明确标注，不能宣称整关逐像素一致。
- 角色专属、难度敏感机制分别覆盖；死亡、决死、灵界、暂停、Continue 等用定向案例补，不机械组合所有边界条件。
- 正常网页实际试玩无已知但被忽略的目标路径问题。声明限定到实际版本、平台、语料与覆盖；“菜单/Stage1 完成”不等于全游戏完成。

## 5. 第三阶段：逐关 Stage 2–6，再 Extra

每关实际运行真实 ECL/STD/ANM/MSG/音频，围绕可达缺口补共用机制；不在开工前做全游戏全指令普查。沿“道中 → 道中 Boss → 对话 → Boss 全阶段 → 关末/下一关”延伸固定输入语料，共享修改回归菜单和 Stage 1。

- Stage 2–5：补真实用到的 STD/ECL/表现分支和跨关资源继承。
- Stage 6：完整 Boss 后实现真正结局、staff roll、成绩与解锁，不再以回标题替代。
- Extra：按四角色和 Extra 自身难度/解锁条件验证，不虚构 E/N/H/L 四难度。
- 最后贯通正常故事流程、练习/符卡练习、Replay 跨关/结束、保存/读取等全局路径。复用 `verification/full-game-scenes.json` 作为最低覆盖清单，不依赖它的旧任务状态。

## 6. 最小但可靠的验证方式

**每批只做与改动相关的闭环：**复现差异 → 原版依据 → 生产修复 → 定向测试 → 真实网页确认 → 扩窗回归。机制/行为、生产绘制、独立原版最终输出分别给结论；双跑确定性不能替代原作一致性。

### 可直接使用的入口（宿主形态：一切经容器）

产品目录 `/work/eagler-touhou/ports/th13`（外层 Makefile 的 `exec` 目标默认落在 `/work`），按修改范围选择：

```sh
make exec CMD='cd eagler-touhou/ports/th13 && node --test tests/anm.test.mjs tests/replay.test.mjs tests/submission.test.mjs'
make exec CMD='cd eagler-touhou/ports/th13 && node --test tests/ui.test.mjs tests/ecl.test.mjs tests/rng.test.mjs'
make exec CMD='cd eagler-touhou/ports/th13 && node portable/build-harness.mjs'   # WASI harness（已增量缓存，热构建 <0.1s）
make exec CMD='cd eagler-touhou/ports/th13 && node portable/build.mjs'           # Web 构建
make exec CMD='cd eagler-touhou/ports/th13 && TH13_WEB_CAPTURE=1 node portable/build.mjs'
```

外层 `/work`：

```sh
make exec CMD='python3 -m unittest discover -s oracle -p "test_*.py" -v'
# 浏览器批次：既有 th13-shot 容器入口
make shot CMD='node tools/shot-web-title.mjs'   # 按该脚本文件头说明的参数运行
```

`tools/shot-web-title.mjs` 已有正式页面启动/截图能力；只补本批需要的键盘轨迹、状态观察和独立输出路径，避免覆盖旧证据，不另建浏览器验证框架（浏览器依赖经 `th13-shot` 镜像，不落宿主）。

现有 battle 对照命令模板（`OUT` 为本批新建 ignored 目录，`RPY/SHOT/N` 取自同一核实后的参考案例，不默认错配机体或缩短验收窗；三条命令均经 `make exec`）：

```sh
make exec CMD='python3 -m oracle.battle_oracle games/th13/th13.exe artifacts/reference/raw "$OUT/original.jsonl" --replay "games/replays/$RPY" --replay-stage 0 --frames "$N"'
make exec CMD='node scripts/web_draws_capture.mjs --replay "$RPY" --sht "$SHOT" --stage 0 --frames "$N" --out "$OUT/web.jsonl"'
make exec CMD='python3 -m oracle.draw_compare "$OUT/web.jsonl" "$OUT/original.jsonl" --expected-frames "$N"'
```

标题参考复用 `python3 -m oracle.title_oracle` 的 `--window a/b/c` 与现有输入脚本；新菜单轨迹只补必要输入场景。上述 debug Replay 捕获只作机制/提交对照，不替代正式页面菜单贯通，也不证明未捕获字段或最终像素。独立原版出帧与浏览器 framebuffer 另比；读取 framebuffer 必须在提交有效的同一浏览器任务内。

### 证据边界

- 每个案例保存必要的原版/资产/输入与构建身份、初值、相位、窗口和完成结果；能核实资格的旧参考可继续使用，不无故重录。
- 检查进程 error/signal/exit 与足量、唯一、连续帧；新观察能力补能检出本类错误的最小负控。改比较器不得删除有效字段、放宽容差或更换 golden 来制造通过。
- 无独立原版出帧只影响最终视觉结论；合法字体未齐只影响相关字形结论，其余可证明的修复继续。问题明确标记未完成，不包装成“完美”。

## 7. 执行与交付节奏

- 派工方式：主力工派子代理（文件写范围互斥），关键复杂机制由主会话亲做或独立复核；所用模型按当前宿主会话环境自行选择，不在此处固定。
- 无人为的统一并发上限；按独立问题派工，文件写范围互斥，实际构建按容器资源调度。代理结论抽查源码/原版证据后再采用，不凭模型或语气信任。失败不静默换模型/账户。
- 不重新引入旧任务卡流程，不重写整套引擎。共享后端确需修改时说明原版依据并回归受影响游戏，避免无关漂移。
- 每批报告：修了什么、原版依据、实际命令和结果、真实网页改善、剩余差异。连续几轮同假设无进展即换取证路径，不无限磨帧。
- 原版资产、截图、反编译与运行记录留 ignored `games/`、`artifacts/`；不触碰凭证，不清理用户工作，不自行提交、推送或发布。

**首批交付是菜单可见/可操作改善和自然开局；随后是日文对话、本体碰撞及 Stage 1 完整闭环。完成度由游戏和直接证据决定，不由框架绿灯、代理自报或文档数量决定。**

## 附：宿主版相对容器原版的改动清单

1. §2 环境策略反转：容器版「本容器直接安装、不使用 Podman」→ 宿主版「一切经 Podman（`th13-dev`/`th13-shot`），宿主零安装」；Wine/Xvfb/Chromium 类新增依赖一律落在镜像或专用容器层并登记。
2. §6 全部命令改为 `make exec` / `make shot` 包装（含 battle 对照三连与 oracle unittest）；补注 `build-harness.mjs` 已是增量构建。
3. §7 删除容器内网关的模型分配（宿主与容器所用模型不同，不在计划中固定）；保留派工纪律与证据抽查要求，其余产品事实、阶段划分、退出条件、证据边界未改动。
