# 通用女声

`通用女声.wav` 是 TT 公开版的参考音色。音频为普通话合成女声，使用文字描述设计。生成时没有提供任何真人录音，也没有指定要模仿的人物。

## 在 TT 中使用

打开「聊天与语音设置」中的语音设置，选择「添加参考音色」，导入 `通用女声.wav`。音色名称填入「通用女声」。

音频格式为 24 kHz、单声道、16 位 PCM WAV，时长 8.86 秒。已验证 TT 的参考音色导入流程，并用 IndexTTS 2.0 官方 Demo 成功合成新句子。 [IndexTTS 2.0 官方 Demo](https://huggingface.co/spaces/IndexTeam/IndexTTS-2-Demo)

## 音频原文

> 你好，我是桌宠提提。今天也陪着你。需要我读消息，或者提醒你休息的话，随时叫我就好。

## 生成来源

通过 Qwen 官方 Demo 的 Voice Design 功能生成，导出后仅调低峰值电平。Qwen 的 VoiceDesign 模型按 Apache-2.0 发布。 [模型与许可](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign) · [官方 Demo](https://huggingface.co/spaces/Qwen/Qwen3-TTS)

发布参考音色时，可将本说明和 WAV 一起放入公开仓库。IndexTTS 2.0 的模型权重适用单独的使用协议，由安装者从上游获取。 [IndexTTS 2.0 模型使用协议](https://huggingface.co/IndexTeam/IndexTTS-2/blob/main/LICENSE.txt)
