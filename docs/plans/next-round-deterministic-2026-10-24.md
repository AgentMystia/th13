# 下一轮：先认证Replay参考世界，再推进移植侧

## 本轮结束状态

可稳定重现的原版代码捕获器已建立，但Replay世界与原局的等价性尚未证明。

- 普通Stage1配置修复：SPELL_ID=-1，首个普通敌机f61，不再f12直接出Boss。
- 全长两跑：每次11675帧、2026812 ops，SHA256相同：`ad046f606389fc5c38d584527fc6fb04873056cb38b018e272f755f4492cbacb`。
- 当前场景终点：global f6972 GameOver，4706帧敌机零更新。不能把“全长无崩”当作“通关一致”。
- 标题窗两侧都可重现，首分歧f487；不作为下一轮第一任务。
- 详细纠错依据：`docs/rectification-2026-10-24.md`，当前交接：HANDOFF顶部。

## 第一工作包：Replay初值与输入链资格认证

**唯一主目标：解释并消除参考世界与录像的第一次行为分歧，不改渲染参数。**

1. **恢复原版Replay的stage初值。** 以`00449070.c`等原版读点为证据，解出实际stage seed、初始分数/power/残机/炸弹等；不要把默认种子0当成任意录像种子。字段未知就保留raw地址，不根据HUD标签猜。
2. **核对输入契约。** 同一stage的6字节state/press/release与真实输入消费点逐帧对齐；当前held→VK→再推导边沿路径先自证，不能假设等价。区分global、battle和Replay输入帧，在日志中同时记录三者。
3. **加状态/场景里程碑。** 敌人sub名、spawn、HP、玩家状态、拾取、score和GameOver写点；不得用纯位置积分模型的“还在移动”裁决真实碰撞是否错误。
4. **定位首差。** 首先检查当前f3345后玩家状态变化，随后global f6972 GameOver。对比的是认证后的原版Replay路径，不是旧Python旁路伤害模型或视频推断。

**验收：**初值与6字节输入自证；同输入两跑状态/绘制哈希相同；Boss击破、关末对话、Stage1结算/过场、Stage2入场都有具名原版事件。缺少任何一项，W-S1参考窗保持未认证。

## 第二工作包：完整渲染契约

在第一工作包通过后消费其稳定参考窗。

- 真STD定位：从st01.std buffer首消费者或st01wl.anm装载调用者开始，必须有文件→对象→更新→提交闭环。AsciiInf的0x404140族不再使用。
- 补齐UP全部顶点而不是图元数count个顶点；quad包含静态顶点/UV；每draw记录实际viewport和render target。
- state v3的每个语义字段必须有原版写点；当前raw-v1仅为诊断数据。
- 对话/字体纹理内容、设备共享件一致性和像素canary独立列门。移植侧冻结帧哈希只说明防回归，不独立证明原版一致。

**验收：**捕获面表覆盖用户会看到的所有draw来源，无未知纹理被静默忽略；完整契约下的负控能检出顶点、UV、viewport或render target的任意变动。

## 第三工作包：移植侧模块闭环

按玩家/option/自机弹、伤害/掉落/计分、HUD/STD/对话、菜单/真结算逐模块重建，使用同一完整主线窗回归。标题f487必须从VM身份/脚本/tick顺序解释，不允许绝对帧颜色表。

每个任务必须写明：具名模块、Ghidra锚点、负控、验证命令、产出契约。一个假设不成立即撤销补丁；三轮无实质推进回到对象身份/调用约定/初值，不继续改常数。

## 启动命令

所有命令在th13-dev容器运行；外层工作目录`/work`。

```sh
python3 -m unittest oracle.test_capture_integrity -v
python3 scripts/audit_baselines.py
python3 -m oracle.battle_startup_check artifacts/oracle-draws/audit-normal-s1-600.jsonl
```

port工具工作目录`/work/eagler-touhou/ports/th13`：

```sh
node portable/build-harness.mjs
node --experimental-wasi-unstable-preview1 tools/run-harness.mjs title /artifacts/reference/raw /artifacts/port-draws/next-title.jsonl
node --test tests/harness-paths.test.mjs
```

runner会打印真实宿主文件路径，且要求输出非空并本次重生成。不要用`/dev/null`跑需要截窗回读的捕获器，也不要重用昨天的同名产物证明新代码有效。
