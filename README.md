# TT 桌宠

Windows 上的 Live2D 桌宠。包含聊天、自定义人格、长期记忆、音乐、天气与每日塔罗。角色的五套服装共用一套 Live2D 运行模型。

## 本地启动

需要 Windows、Python、Node.js 和 Live2D Cubism SDK for Web。以下步骤以当前项目验证使用的 Cubism SDK **5-r.3** 为准。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File tools\build_live2d_desktop.ps1 -SdkZip '路径\CubismSdkForWeb-5-r.3.zip'
.\.venv\Scripts\python.exe desktop_pet_search.py
```

从 [Live2D 官方下载页](https://www.live2d.com/en/sdk/download/web/) 获取对应版本并阅读许可。也可以把已解压的该版本放入 `vendor/CubismSdkForWeb-5-r.3/`，再省略 `-SdkZip` 执行构建。构建脚本会应用 `live2d-overlay/` 中的桌面适配源码。程序安装后也可以双击 `启动TT.cmd`。

首次启动使用空白聊天和设置。聊天功能需要每位用户在应用内完成授权。Windows 音乐通过系统媒体会话读取和控制，实际控制能力由音乐客户端提供；音量混合器集成可按需安装 `pycaw`。可选的 `tt_smtc_events.cs` 助手缺失时，音乐状态通过轮询更新。

## 可选语音

`assets/voices/通用女声.wav` 是原创普通话合成参考音色，可在应用的语音设置中导入。来源及格式见[通用女声说明](assets/voices/README.md)。启用合成需要另行准备与桌宠语音工作进程兼容的本地 IndexTTS 2.0 环境（包括对应模型、Python 虚拟环境和桌宠用的低内存推理适配），并设置 `TT_INDEXTTS_ROOT`。FFmpeg 可放入系统 PATH，或设置 `TT_FFMPEG` 为其可执行文件的路径。语音默认关闭，配置前可使用文字聊天。

## 素材与许可

- `assets/character/` 和 `assets/tarot/` 包含 TT 的运行成品素材。使用和再分发请先确认项目作者给出的授权。
- `live2d-overlay/` 包含针对 Cubism Web Samples 和 Framework 的桌面适配。相关文件遵循[Live2D Open Software License](https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html)。Cubism Core 由安装者获取，适用 [Live2D Proprietary Software License](https://www.live2d.com/eula/live2d-proprietary-software-license-agreement_en.html)。
- 模型权重及本机语音引擎通过上游单独安装。IndexTTS 2.0 适用[上游模型协议](https://huggingface.co/IndexTeam/IndexTTS-2/blob/main/LICENSE.txt)。

## GitHub 提交

以此目录作为仓库根目录。提交应用源码、运行所需的成品资源、此处说明和通用女声参考 WAV。`.gitignore` 已覆盖运行目录、聊天和记忆数据库、授权文件、语音权重及录音。提交前可运行 `git status --short` 核对文件列表。代码和原创美术的公开再分发许可由项目作者决定。
