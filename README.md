# TouchChart 手打谱机

[下载最终版](https://github.com/sdjzd9stjz-dotcom/TouchChart/releases/tag/v2026.10.29.1) · [按顺序查看历史版本](VERSIONS.md)

用手机五指敲击录制 Tap / Hold / Flick / Drag，导出包含音乐、背景和谱面的 Phira `.pez`，也可导出 RPE JSON 与 Phichain 项目。

## 下载与使用

发布附件包括 Windows 便携软件、Android APK、互联网中继部署包、源码包和 SHA-256 校验值。Windows ZIP 必须完整解压，保留 EXE 旁的 `_internal` 目录。

1. 在 Windows 程序载入音频，设置或分析 BPM。
2. 手机打开电脑显示的局域网地址，或通过互联网服务器创建专属配对。APK 当前用于局域网；互联网模式使用手机浏览器。
3. 开始录制并敲击，结束后点「导出 Phira 谱包」。在 Phira 中导入 `.pez`，不要导入软件 ZIP 或仅改名的 JSON。

## 功能

- 四类音符、五指多押、滑动成串、最高 1/768 细分与 64 轨吸附。
- 变速 BPM 分段、音频 BPM 候选分析和复合量化网格。
- 0.25×～5× 无级下落速度，支持小数输入并保存到谱面；预览和 Phira 导出按相同屏幕高度比例换算。
- Hold / Drag / Flick 阈值可调，提供长按防抖预设；长按轻微抖动不再被成串模式硬编码强制转成 Drag。
- 互联网独立房间：每台电脑仅配对一台手机浏览器，触控输入互相隔离，支持解除配对。
- 音乐与谱面保留在电脑，中继服务器只转发触控。

## 源码与说明

| 目录 | 内容 |
| --- | --- |
| [native](native/README.md) | Windows 原生程序、Android 原生客户端、构建脚本与自检 |
| [touchchart](touchchart/README.md) | 共享录制核心、局域网页面、谱包导出和中继服务 |
| [cloud](cloud/README.md) | Docker Compose 与 Caddy HTTPS 部署配置 |

源码运行需要 Python 3.10+；Windows 界面需要 Tk，音频解码需要 FFmpeg。打包脚本使用 PyInstaller，Android 构建工具由 `native/download_tools.py` 下载。可直接使用发布的 Windows 包，无需安装 Python。

```
python native/desktop.py
python native/desktop.py --selftest native/artifacts/desktop-source-selftest.txt
python native/test_cloud.py
```

构建 Windows 前将 FFmpeg 放到 `touchchart/.tools/ffmpeg.exe`；构建脚本还使用该目录下的 `ffmpeg-essentials.zip` 提取许可证。Phichain 原生项目导出及相关自检需要安装 Phichain 转换器。

仓库中的 cloud 目录是部署模板；发布的中继 ZIP 已包含运行所需的 `touchchart.py` 和 `cloud_server.py`，可直接按其 README 部署。

## 已有验证与边界

真实日志位于 [native/artifacts](native/artifacts)：Windows EXE 自检 24/24；云中继真实 HTTP 隔离测试 6/6；Edge 手机模拟触控测试 4/4；共享核心 11/11；网页完整回归（含 Phira 速度一致性）11/11。

Phichain 转换和渲染已验证。Phira GUI、实体 Android 手机及实际公网部署尚未完成验收。BPM 分析是候选结果，需要试听校正。设备按浏览器凭据区分，换浏览器视为新设备；互联网延迟取决于网络。程序退出前应导出谱面。

不包含私人谱面备份、签名私钥、工具缓存或参考视频。第三方运行时的许可证随 Windows 发布包附带。
