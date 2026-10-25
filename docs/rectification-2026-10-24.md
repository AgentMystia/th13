# 2026-10-24 确定性战役复盘与可信起点

## 结论

前半轮的主要问题不是运算能力不足，而是**没有先验证参考世界和实验本身是否正确**：在符卡练习场景上调正常关卡、用旧文件评价新构建、把诊断结果当成验收完成。反复叙述和提交未经证实的结论进一步污染了后续推理。

本轮纠偏交付：恢复正常 Stage1 开局、让崩溃/短窗失败显式退出、统一 WASI 路径并检查输出确实重生成、撤销无证据补丁。游戏的全链路行为与渲染一致性尚未验收。

## 一、为什么耗时很久却没有主线进展

### 1. 参考世界启动错了，仍把它当权威

`BattleOracle.maybe_request_battle()` 复制菜单开局参数时漏了 `DAT_004be7d4=-1`。

- 原版菜单在 `00451450.c:323` 写 `-1`，然后才写 stage=1 和 Stage1 表地址。
- ECL `[-9907]` 在 `00420380.c:269-270` 返回这个字段。
- 字段默认 0 使 `st01.ecl main` 走 `MainBossSpell`，而不是正常道中。
- 错误签名：f12 Boss、f74 符卡背景；不是“正常游戏中的真死亡时间线”。

**纠偏：**补齐 SPELL_ID 和 replay shot_type；新增 `oracle.battle_startup_check`，用普通敌机首现 f61、前600帧无Boss资产作负控/正控。表地址 `0x4bb56c` 没有错，不改无辜模块。

### 2. 验证器允许把失败当成功

`battle_oracle.main()` 在 `crash=1213`、只截出1210帧时仍返回0。之前的“11700帧全跑成功”实际上是读取了错误的退出码，没有核对捕获长度。

**纠偏：**成功必须同时满足“无崩溃、捕获帧数等于请求、无敌机双更新异常”；截窗改为流式，完整帧连续性受检；`/dev/null` 不再被当成可回读的产物。

### 3. 构建成功，比较的却是旧产物

容器文件系统 `/work/...` 与 WASI guest `/...` 被混淆。多次构建后，比较对象仍是之前的 `title-a.jsonl`。程序输出目录也未做“本次重生成”检查。

**纠偏：**`harness-paths.mjs` 统一宿主/guest/相对路径，runner 在 WASI 返回后校验文件存在、非空且stat已改变，并打印最终宿主输出路径。标题窗两侧各自双跑哈希一致；双方仍在f487发生真实分歧，没有因修工具而假报游戏对齐。

### 4. 函数命名靠猜，导致修错子系统

- `0x47b16d` 是 `localtime64`，不是new；`0x483652` 是获取线程tm缓冲，ptd+0x44不是堆描述符。`time_t=-1` 合法触发EINVAL和NULL返回。把时间戳指针当malloc尺寸才制造了55MB/帧的假爆堆。
- `0x404140/0x4042a0/0x404b50` 是AsciiInf文字请求/逐字形渲染，不是STD。大量front00/ascii UP不能证明舞台背景被覆盖。
- 跨帧累计465次flush与单帧33个UP混比，制造了不存在的“430个绘制丢失”。

**纠偏：**恢复原128MB bump并加耗尽检查；撤销错参HeapReAlloc/HeapSize、未对齐回收堆、自动写score档、硬编码f1200以后跳过绘制等补丁。真实STD锚点暂记未确认，不能再从文字渲染器外推。

### 5. 不是确定性重建，而是在给单窗口抄答案

依据一次VM地址日志臆造白条时间表、RGB和alpha混用、改帧数后不验证新文件，既没有修正ANM机制，也没有证明其他菜单输入不退化。无消费者的pending标记只是实现外观。

**纠偏：**本轮未提交的TitleMainMenu.cpp/.hpp改动已存补丁后全部撤销；原版诊断dump移出monorepo。f487色差仍作为诊断信号，下一轮须重读interpreter/update顺序，不再按绝对帧抄色。

### 6. 汇报量被误当成工程进度

“终审”“销账”“全绿”等词在没有闭环时反复出现，且互相矛盾的段落不断追加到HANDOFF。主线W-S1从未通过，却被标为M1完成并继续下一阶段。

**纠偏：**错误段落移到gitignored审计归档，HANDOFF只保留当前事实；覆盖矩阵撤回“与原局死亡逐帧同拍”“STD销账”“事件v3已完成”等声明。后续任何结论必须有命令、输出、验收范围和未覆盖边界。

## 二、已经实际执行的验证

产物均在gitignored `artifacts/`，原版资产不入库。

| 验证 | 实测 |
|---|---|
| 容器 `node --test tests/*.test.mjs` | 11/11顶层测试通过，含ECL/UI/Replay原生包装与6个新增路径用例 |
| 容器 `bash tests/anm/run.sh` | 25个用例通过，含4个STD解析/相机脚本单测；不等于STD渲染oracle已覆盖 |
| oracle捕获完整性单测 | 10/10通过：完整窗/短窗/崩溃/断帧/设备输出拒绝/普通开局配置/raw状态命名/场景负控/原版time64时钟转换/分配器对齐与越界拒绝 |
| 修复前1250帧负控 | 捕获1210帧，global f1213崩；明确exit 1，`CAPTURE status=failed` |
| 正常开局600帧 | 600帧、65009 ops、无崩溃，普通敌机f61，无提前Boss |
| 同600帧带探针/无探针双跑 | SHA256完全一致：`0c7212a33939fb1b4eaa485886c187402ae53ff2d1e2f81e3ba2a53015b8c0a4` |
| 正常开局11675帧请求（修时钟前） | 实际捕获6980帧/1097202 ops，global f6983崩于0x43e2cb，exit 1；不是完整窗通过 |
| 固定合法FILETIME后全长双跑 | 两次均11675/11675帧、2026812 ops、无崩溃且SHA逐字节相同；global f6972进入GameOver，4706帧敌机零更新，**不是Stage1通关一致**；SHA `ad046f606389fc5c38d584527fc6fb04873056cb38b018e272f755f4492cbacb` |
| raw状态实际跑 | frame0..9共10行，SPELL_ID=-1；无错误的lives/score命名 |
| 标题窗fresh对跑 | f487：port颜色4282532418≠oracle4282400832，600帧窗，9119最大ops计数 |
| 标题窗各侧独立双跑 | port SHA `abe21e323c835891cbe17ae4bbc0add340688baea4644815c4549f9e967a005a`；oracle SHA `71a9b74035ce4293bf2cd58815ffacbe38887b2bbe5a48d23de3db0fd7291d78`，各自一致但双方不同 |

**未跑/未通过：**本轮未重跑网页Emscripten构建、浏览器统计、像素canary、golden全门禁；W-S1/W-MENU/W-RESULTS均不能声明通过。

完整任务分解与启动命令见 [下一轮执行计划](plans/next-round-deterministic-2026-10-24.md)。

## 三、下一轮的工作顺序与停损点

### P0：先把Replay参考世界资格做成硬门禁

1. 原版Replay载入路径的stage seed、初始资源、输入采样相位和6字节state/press/release逐字段自证。目前只注入held，经键盘再推导边沿，不能称Replay权威输入完全等价。
2. 对齐stage初值与首批spawn时间线，再判断f3345后的玩家死亡是否为真实轨迹。旧纯位置积分模型不能裁决碰撞忠实度。
3. `localtime64(-1)`的源头必须从当前成绩插入/Win32时钟桩取证，不能改分配器、填虚假tm或隐藏绘制。当前真失败边界是global f6983。
4. 满11675帧后仍须检查Boss击破、关末转场与Stage2入场；“文件长度足够”不等于场景链完成。

**验收：**正确场景、同初值/同输入、全长无崩溃、双跑哈希相同，行为状态含义有原版写点证据。全部满足后才能冻结W-S1参考。

### P1：补真实渲染捕获边界

- 从 `st01.std` 文件buffer首消费者或 `st01wl.anm`装载调用者定位真实STD链；不要再用AsciiInf地址。
- quad静态顶点/UV、UP完整顶点数量、per-draw viewport/render target等都要进入覆盖审计。现有清单只序列化count个顶点而非图元对应的全部顶点，尚不能从“清单一致”推出画面必然一致。
- 字体内容、纹理内容、设备共享件信任与像素canary分别列门，不能把冻结移植侧哈希当作原版真值。

### P2：移植侧机制对齐

- 先修完整模块、再扩窗；标题f487须用VM身份+脚本指令+tick顺序取证，禁止绝对帧颜色表。
- 同一假设最多一个最小实验，结果不支持就撤销，不累积猜测补丁。连续三轮无实质推进时回到调用约定/对象身份/输入初值核验。
- 进度只报完整主线窗与覆盖矩阵，不再用“提交数/探针数/报告数”度量。

## 本轮代码保留状态

纠偏后的改动保留在两个仓库的工作区，尚未新增提交；没有推送。原版日志/资产与完整JSONL留在gitignored `artifacts/`，monorepo中误入的诊断dump已移出。既有未跟踪的计划、`tmp/`和用户探针文件未纳入本轮清理。

## 四、可直接复跑的命令

以下都在th13-dev容器、工作目录`/work`执行；WASI runner工作目录为`/work/eagler-touhou/ports/th13`。

```sh
python3 -m unittest oracle.test_capture_integrity -v
python3 -m oracle.battle_oracle games/th13/th13.exe artifacts/reference/raw artifacts/oracle-draws/audit-normal-s1-600.jsonl --replay games/replays/th13_01.rpy --frames 600
python3 -m oracle.battle_startup_check artifacts/oracle-draws/audit-normal-s1-600.jsonl
python3 -m oracle.draw_compare artifacts/port-draws/audit-title-restored.jsonl artifacts/oracle-draws/audit-title-fresh.jsonl
```

最后一条当前应exit 1并报告f487；这是明确的未对齐结果，不是工具失败或可被过滤掉的字段。
