# TouchChart 原生软件交付

日期：2026-10-28。先完成并封版网页工程，再在同一录制核心上实现 Windows 原生软件与 Android 原生 APK。桌面界面使用 Tk 控件与 Canvas；安卓界面使用 Activity、原生 View 和 MotionEvent，均不依赖 WebView，也不打开浏览器。

## 安装和连接

1. 完整解压 `TouchChart-Windows.zip`，进入 `TouchChart` 文件夹，双击 `TouchChart.exe`。不要单独移动 EXE，旁边的 `_internal` 是程序运行文件。
2. 手机可选网页或 APK：网页方式无需安装，直接打开桌面显示的 `http://电脑IP:8790/touch`；也可以安装 `TouchChart-Android.apk`。
3. 电脑和手机连接同一 Wi-Fi。桌面的「复制网页地址」可复制完整链接；APK 中填写 `IP:8790` 并点「连接」。只输入 `http://电脑IP:8790` 也会自动进入手机网页。
4. 电脑载入音乐、设置/分析 BPM，点「开始 / 停止录制」，手机五指敲击即可。
5. 点「导出 Phira 谱包」或按 Ctrl+E，保存 `.pez`。将这个 `.pez` 传到手机，在 Phira 的本地谱面导入入口选择它。

## 直接导出 Phira 游玩谱包

主导出按钮和 Ctrl+E 现在生成完整 `.pez`：根目录有 `chart.json`、`info.yml`、`info.txt`、`song.wav`、`background.png`。音乐使用当前载入歌曲解码后的 PCM WAV；背景自动生成。写入完成并通过 ZIP/EOCD、CRC、资源引用和音频校验后，才出现最终文件。

### 音符下落速度

预览区上方新增「音符下落速度」滑块：范围 **0.25×～5×**，连续无级调节，支持输入任意合法小数后回车，默认 1×，可点「恢复 1×」。录制中、播放中和已有音符时均可调整，立即影响 Tap / Hold / Flick / Drag 的预览。慢速下会按可见时间范围读取音符，包含超过 5 秒后的音符和跨越当前时间的长按。

这是整张谱面的统一下落速度；调整时不会录制成分段变速事件。**导出的 Phira `.pez`、RPE JSON 和 Phichain 项目均保存当前倍率**，通过标准 RPE 判定线 `speedEvents` 实现。BPM、音乐播放速度、音符判定时间、长按时长和输入补偿保持不变。预览保留原版 1× 的视觉基准；游戏实际像素速度还取决于客户端视口及设置。

调速后视为有未保存更改，需要重新导出；清空音符保留当前倍率，重新启动默认 1×。文件打包期间暂时锁定滑块。手机网页和 APK 继续负责输入，无需更新 APK。已有 JSON 转谱包菜单保留原 JSON 中的速度。

实现依据：[Phira v0.8.2 RPE 解析器](https://github.com/TeamFlos/phira/blob/v0.8.2/prpr/src/parse/rpe.rs)。新增回归覆盖调速时序不变、四种音符预览、慢速可见范围、越界输入、复位与导出后的真实 Phichain 转换；不将转换通过等同于 Phira 实机试玩。

`Could not find EOCD` 表示输入文件缺少可识别的 ZIP 结束记录，尚未进入谱面解析。单独 JSON 或被截断的 ZIP 都能触发这一类问题；没有取得用户失败文件，不能断言是哪一种。**不要把 JSON 改名为 .pez/.zip，这不会把它变成谱包。** 也不要在 Phira 中选择 Windows 软件安装 ZIP。

已经导出 JSON 的谱面不用重打：选择「其他导出 → 已有 JSON 转成 Phira 谱包」，依次选择原 JSON、对应音乐和 `.pez` 保存位置。需要继续在制谱器编辑时，才使用「其他导出 → 仅导出 JSON」。

如果录制时未载入歌曲，停止录制后可以补载对应音乐，已有音符保持不变，再导出谱包。软件不会以无音乐的半成品冒充完整游玩包。生成物是节奏初稿，没有自动谱面美化。

本次增加纯 JSON、截断 EOCD、错误扩展名、写入中断和后补音乐的回归测试。附带的 `Phira-Variable-Tempo-Demo.pez` 是 209 音符、32 秒、120→180→90 BPM 的完整示例，便于检查导入路径。Phira GUI 自动化连接仍不可用，未将 ZIP 校验称为已在 Phira 试玩通过。

Windows x64；不需要用户安装 Python、Node 或 Java。安卓最低 Android 8.0 / API 26，目标 API 35，APK 内没有特定 CPU 架构的本地库。仅电脑与手机局域网通信；服务端口默认 8790，和旧网页 8787/8788 分开。

如果端口已被占用，软件会明确报错；可用 `TouchChart.exe --port 8791` 启动，并在手机填对应端口。Windows 网络许可选择当前使用的专用网络；无需关闭整个防火墙。

手机网页与 APK 可以连接同一个桌面会话，共用五个手指槽和同一份谱面；一端重连只释放自己的手指。录制、播放和导出由桌面软件控制。请使用本次新版 EXE，早期原生版本会对 `/touch` 返回 404。已有 APK 无需更新，也不必另外启动旧的 8787/8788 网页服务。

### 「触控 id 无效」修复

旧版服务错误地要求触控 ID 非负，而网页直接发送浏览器的 pointerId。已用负数 ID 复现同样的 400 错误；尚未抓取用户实体手机的原始请求，因此不把复现样例称为该手机的实际数据。新版网页把每次按压映射成独立内部编号，保留按下/移动/抬起/取消的一致性；服务也接受旧网页的合法有符号整数 ID，并继续拒绝空值、布尔值、小数及超出安全范围的数值。编号不会取绝对值，避免正负两个手指被合并。依据：[W3C Pointer Events 的 pointerId 定义](https://www.w3.org/TR/pointerevents/#dom-pointerevent-pointerid)。

更新时先导出当前谱面并退出旧软件，再将新版 ZIP 解压到新目录运行；当前窗口标题为「下落速度控制版」，包含此前的 Phira 谱包导出和触控 ID 修复。手机重新打开或刷新该窗口显示的网页地址。当前运行中的旧进程不会因下载或解压自动更新；APK 无需更新。

## 功能

- 桌面原生音乐载入/播放/暂停、定位、录制、预览、撤销、单个音符删除和导出。
- Tap/Hold/Flick/Drag、滑动轨迹成串、1/768 细分上限、32/64 轨吸附。
- 本地音频 BPM 候选分析、变速段编辑和 LCM 网格，例如 `16,24 → 48`。
- 安卓原生五指触控、8ms 移动采样、最小 RTT 时钟同步、批量发送、批次重试去重、断线重连和后台释放。
- 手机浏览器直接进入 `/touch`，沿用网页五指输入、时钟同步和重连；网页资源内嵌于 EXE，无需联网下载前端库。
- 完整 `.pez` 一键打包。可调用已安装 Phichain 转换器生成原生项目。
- 音频解码器、Python 与 Tk 随 Windows 目录携带；音频支持 WAV/MP3/OGG/FLAC/M4A，导出完整包使用已解码 WAV。

BPM 分段文本每行 `音频秒数,BPM`，第一行从 0 秒开始；留空表示恒定 BPM，开始录制时应用。候选分析仍需试听校正半速/倍速与复杂节奏，不保证任意歌曲识别准确。LCM 合并细分分母，BPM 变化用分段映射保存。

已有音符时锁定节奏设置；先导出、清空再改。普通笔记本触摸板仍是单鼠标输入，五指由安卓手机提供。状态保存在进程内，退出前导出。原生软件当前只提供最近 30 个音符的列表和完整时间轴预览，不是多轨完整制谱器。

## 测试证据与边界

- 当前打包 EXE 自检 23/23，包含下落速度、默认 Phira 导出和中断写入防护；此前 Edge 手机模拟验收 7/7 覆盖五点触摸、正负 ID、编号重用、取消、后台释放、混用和重连。原始输出为 `artifacts/native-mobile-browser-output.txt` 和 `desktop-portable-selftest.txt`。这不等于所有实体手机浏览器都已测过。
- `artifacts/desktop-exe-selftest.txt`：实际打包 EXE 的独立自检，包括原生控件、真实 HTTP、LCM、变速音频解码分析、Windows MCI 播放、鼠标回调、五指协议、删除、RPE/PEZ、真实 Phichain 转换。
- `artifacts/android-transport-selftest.txt`：APK 使用的同一份 Java Wire 类，在真实 JVM 中向录制服务发送五指批次并验证重试去重、五押及导出。
- `artifacts/android-build.txt`、`apk-signature.txt`、`apk-manifest.txt`：实际编译、DEX、对齐、v2/v3 签名验证与清单。
- `artifacts/adb-devices.txt`：没有连接的安卓设备；**未做实体手机 APK 安装、触摸延迟、系统后台行为实测**。JVM 通信测试不等于 Android GUI/生命周期验证。
- 桌面使用自动化原生控件/事件回调与实际音频路径自检；Windows 桌面控制通道仍不可用，没有人工 GUI 点击和试听验收。
- Phichain 的原工程转换、项目载入与真实渲染通过；Phira/PhiEdit GUI 仍未验证。原工程的详细证据在 `touchchart/UPGRADE_REPORT.md`，不把其他客户端通过等同于 Phira 通过。

APK 为本地签名的可安装包，不是商店发布版本。签名私钥和密码保留在本机 `native/.signing/`，不放入发布 ZIP；后续更新保持该签名即可覆盖安装。Windows EXE 未购买商业代码签名证书。

## 自检和构建

```powershell
# 已打包的软件自身运行测试；Phichain 项目测试需要现有转换器
.\TouchChart.exe --selftest "C:\Temp\touchchart-native-selftest.txt"
```

源码目录结构应保留 `native/` 和相邻的 `touchchart/`。源码运行：

```powershell
python native/desktop.py
python native/desktop.py --selftest native/artifacts/desktop-source-selftest.txt
python native/download_tools.py
python native/build_android.py
python native/test_android.py
python native/test_mobile_browser.py
python native/build_desktop.py
```

Android 构建使用固定的官方 SDK 35 工具、Android 35 平台和 Microsoft JDK 21，下载清单和校验值在 `build-tools.json`；均放在项目 `.tools` 内，不修改系统 PATH。Windows 构建需要 PyInstaller 和 `touchchart/.tools/ffmpeg.exe`，本机已经具备；生成物为 onedir，保留目录能缩短启动解包时间。

`build_android.py` 将输入临时放在 ASCII 路径，规避 Windows AAPT2 无法读取中文目录的问题。原始版本仍在工作区中文路径，未移动用户工程。构建工具和私钥不包含在源码 ZIP。

## 第三方

Windows 目录包括 `FFmpeg-LICENSE.txt`、`FFmpeg-README.txt` 和 `THIRD-PARTY.txt`。FFmpeg 为独立的音频解码程序，来源与源码信息见附带 README；Python/Tcl/Tk 由 PyInstaller 收集。

官方构建依据：[AAPT2](https://developer.android.com/tools/aapt2)、[APK 签名验证](https://developer.android.com/tools/apksigner)、[Microsoft OpenJDK](https://learn.microsoft.com/en-us/java/openjdk/download)。


## 互联网独立配对（本次新增）

1. 将 TouchChart-Cloud.zip 部署到有域名和 HTTPS 的服务器（见其中 README）。
2. Windows 软件的「互联网服务器」填写根地址，例如 https://touch.example.com，点击「创建配对」。
3. 复制上方新生成的手机网页地址，在唯一一台手机的浏览器打开。每台电脑获得独立房间，第二台手机会被拒绝。
4. 电脑照常载入音乐、录制、调速、导出完整 .pez。音乐和谱面留在电脑；云端仅转发触控事件。
5. 要换手机：停止录制，点「解除配对 / 返回局域网」，再创建新配对。旧链接和令牌作废。

手机身份以浏览器保存的随机凭据区分，并非不可伪造的硬件序列号；同一手机换浏览器/无痕模式视为新设备。不要分享专属链接。原有 APK 继续用于局域网，互联网模式使用手机浏览器。互联网配对启用时拒绝局域网网页/API 写入，避免另一部手机混入。

中继房间一小时无任何活动后过期；服务器重启需重新配对，本地已有音符仍保留。丢线会释放按住的手指；超过两秒的积压触控丢弃，避免旧触控污染新录制。公网延迟取决于网络，不承诺与局域网相同。

已在本机完成真实 HTTP 双房间并发和原生桥接测试；未实际部署公网服务器，也未做实体手机跨公网延迟验收。


## Hold / Drag / Flick 手势灵敏度

窗口上方点击「手势灵敏度」，可随时调整四个识别参数：

- Hold 最短按住时间：默认 200 ms；调低更容易识别为长按，范围 50～2000 ms。
- Drag 起始位移 / 长按容差：默认 5%，范围 0.5～30%。移动未超过此值时，按住够久就是 Hold；越大越能容忍长按抖动，也越需要明显移动才能触发 Drag。
- Flick 最小位移：默认 10%，最大 80%，不能小于 Drag 起始位移；调高后更难触发 Flick。
- Flick 最长划动时间：默认 300 ms，范围 50～1000 ms；调低后需要划得更快。

针对“想打 Hold 却变 Drag”，先点「长按防抖」预设再应用：200 ms / 8% / 12% / 250 ms。若仍误判，继续提高移动容差；这也会让短距离 Drag 更难触发。若本段全是长按，也可在开始录制前选固定 Hold 模式。

位移是按触控区域宽高分别归一化后计算的直线距离。Hold/Drag 使用整次手势离按下点的最大距离，不累计来回抖动路径；Flick 使用按下到抬起的位移及整次手势时间。真正划出去再划回来的轨迹仍可识别为 Drag。普通模式和滑动成串模式使用同一容差，去掉了旧成串模式硬编码 3% 强制转 Drag 的规则。

设置应用于下一次按下；当前已经按住的手势使用按下时的参数，已生成音符不重分类。设置在本次程序运行期间保留，清空音符不重置，重启恢复默认。局域网手机网页、APK 与互联网手机浏览器最终都由电脑同一识别器处理，无需更新手机。

新增自检覆盖长按反复抖动、闭合 Drag 轨迹、三个音符类型的阈值变化、固定 Hold、按住期间改参数及非法参数无副作用；实物手感仍应根据个人操作调整。
