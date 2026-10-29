# TouchChart 手打谱机

用手机五指触控或电脑触摸屏，把跟着音乐敲出的 Tap / Hold / Flick / Drag 生成单判定线 RPE 谱面。支持变速 BPM、音频节奏候选分析、复合节奏网格、滑动成串和高密度预览。运行服务只需 Python 3.10+ 标准库，无需 pip/npm。

交付日期：2026-10-28。视频对照和测量依据见 [升级报告](UPGRADE_REPORT.md)。

触控兼容修复：网页为每次按压分配独立内部编号，不再直接发送浏览器 pointerId；服务兼容旧网页的合法负数 ID。解决了负数编号触发「触控 id 无效」的已复现问题。更新运行程序后，请刷新手机页面。

## 启动与录制

1. 双击 `start.cmd`，或在本目录运行 `python touchchart.py`。
2. 电脑打开 `http://127.0.0.1:8787`；手机与电脑处于同一局域网，打开页面或控制台显示的 `/touch` 地址。
3. 载入音频，设置 BPM、偏移、补偿、量化和轨道。变速歌曲先配置下述 BPM 分段。
4. 点击「开始录制」，在手机或电脑预览区敲击。停止后导出 `chart.json`。

```powershell
# 本机已验证的解释器，start.cmd 会优先使用它
& "$env:USERPROFILE\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" .\touchchart.py
# 端口被占用时
python touchchart.py --port 8788
```

普通笔记本触摸板在浏览器中通常只暴露鼠标事件；五指输入使用手机/触摸屏。本程序没有原生触摸板 HID 驱动。状态保存在内存，关闭服务前请导出；没有跨会话自动恢复。

## 变速歌曲：分析、校正、应用

展开「变速 BPM · 本地音频分析与分段编辑」，点击「分析音频 BPM」。浏览器解码音频、提取能量起音包络，本地服务估计稳定速度段并返回置信度。音频不发送到云端。

检查候选，必要时用「候选 ×0.5 / ×2」或直接修改文本。每行是 `时间秒,BPM`，第一段从 0 秒开始，后续时间递增，BPM 范围 30～600。例如：

```text
0,120
12,180
20,90
```

点击「应用分段」后再录制。分段时间对应原音频秒数；偏移不会挪动音频中的实际变速位置。录制、预览、跨变速 Hold 和导出 `BPMList` 使用同一套分段积分与反算。

自动分析是辅助候选功能。强切分、弱鼓点、半拍/倍拍歧义和连续渐变可能误判，需要试听校正。已验证 32 秒合成音频的 `120 → 180 → 90`，不代表所有真实歌曲均能准确识别。也可完全手动填写分段，识别失败不会自动覆盖录制设置。

## 最小公倍数与高密度

「复合细分」填写 `16,24`，点击「LCM 合并网格」，得到 `LCM(16,24)=48`，在同一个 `1/48` 全音符网格上表达十六分和二十四分位置。合并的是**节奏细分分母**；各段 BPM 保留自身速度，不对 BPM 数值求公倍数来替代变速映射。

- 量化支持 4 的正整数倍，最大分母 768；超过上限明确报错。
- 「高密度预设」启用 `1/64` 和「沿轨迹成串」；也可选择 `1/192` 等细网格。
- 成串模式按量化拍点沿轨迹插值生成 Drag；弯曲或回到起点的轨迹也可识别。
- 固定 Tap / Hold / Flick / Drag 模式可明确指定类型；轨道吸附增加 32 / 64 轨。
- 单谱上限 100,000 音符；移动采样间隔 8ms，实际采样受硬件和浏览器限制。

细网格的表示能力不等于手指或硬件采样频率。成串位置是轨迹插值。已有音符后锁定节奏设置以避免重定时，导出并清空后可重新设置。

## 操作与手势

默认自动模式用按压时长 dt 与归一化起终点距离 d 分类：

| 手势 | 条件 | RPE 类型 |
|---|---|---|
| Tap | dt < 200ms 且 d < 0.02 | 1 |
| Hold | dt ≥ 200ms 且 d < 0.03 | 2 |
| Flick | d ≥ 0.10 且 dt ≤ 300ms | 3 |
| Drag | 其余；成串模式另检查完整轨迹 | 4 |

支持五个独立槽、八方向与五押。手机同步五次选最小 RTT；失败显示状态并自动重连，断线期间不离线补录。按下和抬起立即入队，移动按 8ms 采样；短批量发送、批次去重与长轮询唤醒减少拥塞。

| 快捷键 | 功能 |
|---|---|
| Space / Esc | 播放暂停 / 暂停 |
| R | 开始或停止录制 |
| Ctrl+E | 导出 |
| Ctrl+Z | 撤销最后一个音符 |

暂停、定位和停止会结算未抬起手指；恢复后的旧抬起不会产生幽灵 Tap。支持单个删除，清空需确认，空谱不能导出。

## 导入 Phira

```powershell
python pack_chart.py chart.json "D:\Music\我的音乐.wav" -o "D:\Charts\我的初稿.pez"
# 自备背景可增加 --background "D:\Images\封面.png"
```

包根目录包含 `chart.json`、`info.yml`、`info.txt`、音乐、背景五个文件；明确指定 RPE 格式，校验字段、资源名与 CRC，拒绝覆盖已有输出。包额外偏移为 0，避免重复叠加。未指定背景时生成渐变图。

打包会先写临时文件、检查 ZIP 结束记录（EOCD）和资源，再原子发布 `.pez`。中断不会留下最终名称的半成品。不要仅将 JSON 改名为 .pez；那仍然不是 ZIP 谱包。原生软件的主导出与 Ctrl+E 已直接生成 `.pez`，已有 JSON 可从「其他导出」选择对应音乐转换。

可使用 `sample_touchchart.pez` 或变速示例 `artifacts/review/variable-demo.pez`，在 Phira 本地谱面导入入口选择。

**Phira GUI 导入尚未实测通过。** 本次核对官方 v0.8.2 解析源码和完整资源包，但电脑控制 native pipe 不可用，无法完成 GUI 导入与试听。格式检查不等于客户端全流程通过。

## 打开 Phichain

Phichain v0.5.0 打开项目目录，需要原生 `chart.json`、`meta.json` 和 `music.*`。下载的 RPE JSON 应先转换：

```powershell
python pack_chart.py chart.json "D:\Music\我的音乐.wav" --phichain -o "D:\Charts\我的初稿"
```

脚本调用已安装的转换器，创建完整新目录。默认路径为 `%USERPROFILE%\Apps\Phichain\app\phichain-v0.5.0-x86_64-pc-windows-msvc`，其他位置设置 `PHICHAIN_CONVERTER`。项目音乐支持 WAV/MP3/OGG/FLAC；不支持的音频不能只改扩展名。

已提供 `sample_phichain/` 与 `artifacts/review/variable-demo-phichain/`。本次实际启动编辑器载入变速项目，成功加载记录与官方源码顺序相符；实际渲染器完整输出 32 秒变速视频，覆盖两次变速、退出码 0。视频中的编辑器是 PhiEdit，本机未安装，不能把 Phichain 结果等同于 PhiEdit 通过。

## 测试与原始记录

环境：Windows、Python 3.12.14、Edge、Node 24、Phichain v0.5.0。

| 验证 | 实际结果 | 原始日志或数据 |
|---|---|---|
| 基础 selftest | 11/11，退出 0，无 SKIP | `selftest_output.txt` |
| 扩展测试 | 17/17，退出 0 | `artifacts/review/advanced-output.txt` |
| 浏览器操作 | 10/10 | `artifacts/browser-output.txt` |
| 音频分析、LCM、变速录制导出 | 通过 | `artifacts/review/browser-performance-output.txt` |
| 50,000 音符生成及真实转换 | 缺失 0，转换退出 0 | `artifacts/review/dense-metrics.json` |
| 实际 Phichain 渲染 | 变速视频 961 编码帧，退出 0 | `artifacts/review/client-acceptance.json` |

```powershell
python selftest.py
python advanced_test.py
# 可选，需要现有 Node 24+ 和 Edge，无需 npm
node browser_test.mjs
node performance_browser.mjs
```

测试使用临时端口，不清空常规录制服务。基础测试需要真实转换器；缺失时 SKIP 且总体退出码为 1，不计为通过。`browser_test.mjs` 可用 `TOUCHCHART_PYTHON` / `TOUCHCHART_EDGE` 指定工具；性能脚本记录了本机工具路径，换机器需调整。

`client_acceptance.py` 是可选渲染复验，需要已安装的 Phichain、`.tools/ffmpeg.exe` / `ffprobe.exe` 和随附示例资源。FFmpeg 仅用于验证，不是程序依赖，也不放入 ZIP。原始日志按请求区间写 150/960 帧，ffprobe 实测为 151/961（含端点）；JSON 记录实际编码帧数。

旧版→新版输入回调至 Canvas 提交 P95：49.80→17.50ms；100,000 音符时间索引预览绘制 P95：0.30ms。这不是实体手机触摸到发光延迟，也不是对视频程序的测量。详细范围与未改善的 HTTP 指标见升级报告。

## 文件与边界

- `touchchart.py`：单文件服务、纯算法、三张内嵌页面；`pack_chart.py`：校验和打包。
- `selftest.py` / `advanced_test.py`：测试；`fixtures/gestures.json`：固定回归输入。
- `sample_chart.json` / `chart_selftest*.json`：示例和真实转换产物。
- `artifacts/review/`：变速示例、原始日志、数据、视频和截图。
- `setup_env.ps1`：保留需求原脚本，UTF-8 BOM 兼容 PowerShell 5.1，本次未执行。谱面 JSON 均为无 BOM UTF-8。

手机应使用电脑局域网 IP，而非手机自己的 127.0.0.1。检查同一 Wi-Fi、设备隔离和专用网络访问许可，无需关闭防火墙。VPN 可能影响自动选网卡，可用 `ipconfig` 检查。端口变更后手机地址也要更新。

没有实体手机无线网络实测、原生五指触摸板驱动、多人协作、多判定线动画、谱面自动美化和游戏判定计分。音频解码、震动和常亮依赖设备能力。服务仅供本机/同局域网使用，默认 `0.0.0.0:8787`，可用 `--host` / `--port` 修改。

网页 `/selftest` 可运行基础自测。新增 `/api/analyze` 接收包络、`/api/grid` 合并细分；其余录制、同步、导出接口保持可用。所有响应 no-store，错误以 JSON 返回。
