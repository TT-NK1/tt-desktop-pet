# TT 桌宠

Windows Live2D 桌宠，包含聊天、自定义人格、长期记忆、音乐、天气与每日塔罗。角色的五套服装共用一套 Live2D 运行模型。

## 本地启动

需要 Windows、64 位 Python、Node.js 和 Live2D Cubism SDK for Web。以下步骤使用项目验证过的 Cubism SDK **5-r.3**。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File tools\build_live2d_desktop.ps1 -SdkZip '路径\CubismSdkForWeb-5-r.3.zip'
.\.venv\Scripts\python.exe desktop_pet_search.py
```

从 [Live2D 官方下载页](https://www.live2d.com/en/sdk/download/web/) 获取对应版本并阅读许可。也可以把已解压的 SDK 放入 `vendor/CubismSdkForWeb-5-r.3/`，再省略 `-SdkZip` 执行构建。构建脚本会应用 `live2d-overlay/` 中的桌面适配源码。程序安装后也可以双击 `启动TT.cmd`。

首次启动会使用空白聊天和设置。聊天需要每位用户在应用内完成自己的授权。

## 聊天、媒体和音乐

- 支持粘贴截图、发送图片和在聊天中查看附件。使用「识别并朗读图片」可让已配置的聊天模型识别图片文字。
- 聊天可按请求分享 Apple Music、网易云音乐的单曲卡片，并接着最近的对话处理简单的换歌或点歌请求。联网查找在「聊天与语音设置」开启。
- Windows 音乐泡泡通过系统媒体会话读取和控制，实际控制能力由音乐客户端提供。Apple Music 的应用音量集成依赖可选的 `pycaw` 和 `psutil`。可选的 `tt_smtc_events.cs` 助手缺失时，音乐状态通过轮询更新。

### 可选本地音视频识别

把 FFmpeg 放入系统 PATH，或设置 `TT_FFMPEG` 为 `ffmpeg.exe` 的路径。给运行识别的 Python 安装 `faster-whisper`，从 [Whisper Base 模型页](https://huggingface.co/Systran/faster-whisper-base) 下载模型到 `runtime/multilingual/whisper-base/`，其中应包含 `model.bin`、`config.json` 和 `tokenizer.json`。可用 `TT_ASR_PYTHON` 指定另外的 Python 解释器，默认用运行 TT 的 Python。

## 可选本地朗读

`assets/voices/通用女声.wav` 是可公开分享的普通话合成参考音色。请在语音设置中导入参考录音，使用自己的音色时可一并填写准确的参考逐字稿。通用音色的来源、格式和原始文本见[通用女声说明](assets/voices/README.md)。

- **中文和英语**使用 IndexTTS 2.0。按 [IndexTTS 官方说明](https://github.com/index-tts/index-tts) 准备 2.0 的源码、CUDA 推理环境和模型权重。将 `TT_INDEXTTS_ROOT` 指向模型工作目录。TT 使用该目录的 `.venv/Scripts/python.exe`、`official-source/` 和 `checkpoints/`。默认工作目录为用户目录下的 `IndexTTS-2.0`。
- **日语和韩语**使用 Qwen3‑TTS 0.6B Base。可以单独建立一个带 CUDA PyTorch 的 Python 3.11 环境，按照 [Qwen3‑TTS 官方说明](https://github.com/QwenLM/Qwen3-TTS) 安装 `qwen-tts==0.1.1`。用 `TT_QWEN_PYTHON` 指向该环境的 `python.exe`。把 [0.6B Base 模型](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base) 下载到 `runtime/multilingual/qwen/model/`，并保留模型自带的 `speech_tokenizer/` 子目录。TT 使用 SDPA 推理。未设置 `TT_QWEN_PYTHON` 时，使用 `TT_INDEXTTS_ROOT` 下的 Python，需自行提供兼容的 Qwen 运行依赖。

两套模型轮流使用显卡，复用当前选中的参考音色。混排文字会按句子及换行切换语种。输入「おはようございます。帮我朗读出来」一类有明确原句的命令时，应用直接朗读原句。首次加载模型比后续朗读耗时更长。语音默认关闭，配置前可以使用文字聊天。

## 素材与许可

- `assets/character/` 和 `assets/tarot/` 包含 TT 的运行成品素材。使用和再分发请先向项目作者确认授权。
- `live2d-overlay/` 包含 Cubism Web Samples 和 Framework 的桌面适配。相关文件遵循 [Live2D Open Software License](https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html)。Cubism Core 由安装者获取，适用 [Live2D Proprietary Software License](https://www.live2d.com/eula/live2d-proprietary-software-license-agreement_en.html)。
- 语音识别和合成的模型由安装者从上游获取。使用前阅读各自模型的许可。

## GitHub 提交

以此目录作为仓库根目录。公开版包含应用源码、成品资源和通用女声参考音频。运行目录、聊天与记忆数据库、授权文件、个人录音及模型权重由 `.gitignore` 排除。提交前可用 `git status --short` 核对文件列表。
