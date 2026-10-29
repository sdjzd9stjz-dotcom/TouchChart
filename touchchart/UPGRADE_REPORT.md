# 视频对照、变速 BPM 与兼容性复核

日期：2026-10-28。对象为本地 TouchChart，参考本地 375.30 秒视频。已实现五指输入、四种音符、轨迹成串、变速录制、音频 BPM 候选分析和 LCM 网格。相对本项目旧版，网页响应与突发事件积压明显改善。Phichain 实际转换、项目载入和渲染通过；Phira 与视频中的 PhiEdit 未完成 GUI 导入，不能宣称全客户端无 bug。

## 视频对照

| 视频位置 | 观察 | 本版处理或边界 |
|---|---|---|
| 00:48–01:12 | 五指状态与手势识别 | 五槽、四类型、方向、多押，Edge 原生五点触摸通过。普通触摸板浏览器仅鼠标，未实现原生 HID 五指。 |
| 01:40–02:08 | PhiEdit 堆叠/形状异常 | 严格检查类型、坐标、尺寸、Hold 尾拍、分数拍；Phichain 实际渲染四类型。无法仅凭视频确认原程序内部根因。 |
| 02:42 | Phira JSON 解析失败、缺少 judgeLineList | 固定导出 RPE 根对象；拒绝错误/原生 JSON；修复 extended、offset 字段类型。 |
| 02:58 | file missing in folder | 五文件完整包、info.yml + info.txt 双清单、实际资源名与 CRC 检查。 |
| 03:52–04:08 | 连续 Drag 轨迹 | 8ms 轨迹采样、曲线路径识别、量化拍点插值成串。 |
| 05:54 附近 | 高密度打谱 | 1/768 网格上限、复合细分、批量输入、时间索引预览。 |

视频没有可重复的原程序测速环境，以下性能比较仅针对保存的 TouchChart 上一版与本次新版。

## 本项目已复现和修复的问题

1. 原 `moveY=400` 让真实渲染器的判定线出现在顶部。改为 `-330` 后，960×540 画面线中心从 y=29.5 到 y=467.5，与网页底部一致。证据为 `line-position-check.json`、`phichain-before.png` 和 `phichain-after.png`。
2. `extended=[]` 改为对象 `{}`；`META.offset` 为整数毫秒，小数毫秒明确拒绝，避免宽松解析器通过而另一客户端失败。
3. 非 Hold 尾拍等于首拍；Hold 至少占一格；分数拍规范化，检查有限值、字段类型、总数与资源。
4. 暂停/停止后待抬起手指标记为待释放，旧 up 不再产生幽灵 Tap；重试批次去重，不重复落音。
5. 长轮询事件唤醒、增量更新与时间索引绘制，减少全量扫描和请求积压。
6. BPM 分段使用原音频时间，偏移在同一积分映射中处理，避免变速点漂移；已测试正负偏移、反算和跨 BPM Hold。

7. Windows 服务使用独占端口绑定，重复启动明确失败，避免新旧服务共用端口；新增回归测试通过。

## 变速分析与 LCM

流程：本地音频解码 → 正向能量变化包络 → 起音候选 → 局部节奏间隔统计 → 稳定 BPM 段 → 试听校正 → 应用。包络分辨率 10ms，返回置信度和半速/倍速选项，不使用云 API。

浏览器实际解码 32 秒测试 WAV，得到：

| 音频起点 | BPM | 段置信度 |
|---|---|---|
| 0 秒 | 120 | 1.00 |
| 12 秒 | 180 | 0.75 |
| 20 秒 | 90 | 0.75 |

测试实际点击应用与 LCM 按钮后录制导出，确认三段 BPMList 和分数拍。最终演示有 209 音符、四类型及 Drag 串。文件为 `variable-demo.pez` 与 `variable-demo-phichain/`。

LCM 合并节奏细分，例如 16 与 24 取 48，对应每个四分音符拍 12 格。120、180、90 BPM 保持独立分段，不折算成巨大恒定 BPM。细分上限 768，分段上限 2,000。强切分、弱鼓点、半拍歧义或渐变速度仍需人工校正；当前只证明该合成变速样本及相关回归通过，没有真实歌曲全集的识别准确率结论。

## 实际性能

同一台 Windows 电脑、同脚本、无头 Edge；不含实体输入硬件、无线网络与屏幕扫描。

| 测量 | 旧版 | 新版 | 范围 |
|---|---:|---:|---|
| 输入回调到桌面 Canvas 提交平均 | 30.01ms | 10.01ms | 各 24 样本 |
| 同上 P95 | 49.80ms | 17.50ms | 约降低 65%，非触摸到发光 |
| 100 事件突发清空，每请求人为附加 40ms | 2338.30ms | 44.70ms | 两版 50/50 音符，批量减少串行积压 |
| 持久 HTTP 单次往返平均 | 0.283ms | 0.339ms | 各 120 请求，此项略慢 |
| 100,000 音符索引预览绘制 P95 | 未测 | 0.30ms | 120 帧，仅绘制当前可见部分 |
| 50,000 音符 / 100,000 输入核心处理 | 未测 | 0.647 秒 | 缺失 0，随后真实转换器退出 0 |

核心约 154,587 事件/秒是无网络批处理结果，不是手机吞吐。100,000 音符索引预览不等于 100,000 同屏，也不等于 100,000 转换测试；实际转换测试为 50,000 音符。

数据见 `browser-performance.json`、`browser-performance-output.txt`、`transport-benchmark.json`、`dense-metrics.json`。旧源码保存在 `baseline/touchchart.py` 供复现。

## 客户端验收边界

| 目标 | 实际证据 | 未覆盖 |
|---|---|---|
| Phichain v0.5.0 转换器 | 100 音符回归、50,000 音符和变速示例转换通过 | 任意谱面无限组合 |
| Phichain v0.5.0 编辑器 | --project 启动原安装程序，进程响应；成功载入后记录 TouchChart Variable Tempo Demo。官方源码确认记录发生在项目和游戏资源加载成功之后。 | 无桌面控制通道，未做点击编辑保存往返与人工试听 |
| Phichain v0.5.0 渲染器 | 原安装渲染器输出 32 秒、961 编码帧、960×540、退出 0；12.5 / 20.5 秒截图覆盖两次变速之后 | 渲染不等于交互游玩 |
| Phira v0.8.2 | 官方解析与资源源码核对、严格字段和包校验 | **GUI 导入与游玩未验证** |
| PhiEdit（视频中的编辑器） | 提供 # 开头的 info.txt 和实际资源名 | 本机未安装，GUI 未验证 |

桌面控制失败原文：`Computer Use native pipe is unavailable: failed to connect native pipe: 系统找不到指定的文件。 (os error 2)`。重置并重试仍失败，没有以源码检查替代 Phira 实测。

`client-acceptance.json` 为实测，`phichain-variable.mp4` 为真实渲染。原始 `client-output.txt` 写的 150/960 是请求区间 nominal 帧数；ffprobe 实际为 151/961（含端点），以 JSON 为准。编辑器测试自然更新了最近项目配置；未修改安装程序二进制。

## 真实日志摘录

基础 `selftest_output.txt`：

```text
Ran 11 tests in 0.744s

OK
ALL TESTS PASSED (11/11)
```

扩展 `artifacts/review/advanced-output.txt`：

```text
Ran 17 tests in 37.899s

OK
ADVANCED TESTS PASSED (17/17)
```

浏览器 `artifacts/browser-output.txt`：

```text
BROWSER ACCEPTANCE PASSED (10/10)
```

变速与性能 `artifacts/review/browser-performance-output.txt`：

```text
PASS apply BPM segments + LCM(16,24)=48 -> recorded chart with 3 BPM segments
PERFORMANCE AND TEMPO BROWSER TESTS PASSED
```

完整原文随交付保存。没有把 Phira 未验证计为 PASS。运行方法和工具依赖见 README。

## 官方依据

- [Phira v0.8.2 RPE 解析](https://github.com/TeamFlos/phira/blob/v0.8.2/prpr/src/parse/rpe.rs)：字段、BPM、坐标。
- [Phira v0.8.2 文件系统](https://github.com/TeamFlos/phira/blob/v0.8.2/prpr/src/fs.rs)：清单与资源。
- [Phichain v0.5.0 RPE 格式](https://github.com/Ivan-1F/phichain/blob/v0.5.0/phichain-chart/src/format/rpe.rs)：转换与音符。
- [Phichain v0.5.0 项目结构](https://github.com/Ivan-1F/phichain/blob/v0.5.0/phichain-chart/src/project.rs)：原生项目。
- [Phichain v0.5.0 编辑器入口](https://github.com/Ivan-1F/phichain/blob/v0.5.0/phichain-editor/src/main.rs)：加载与最近项目记录顺序。

结论限定于上述版本和测试环境，未安装版本不声称已通过。
