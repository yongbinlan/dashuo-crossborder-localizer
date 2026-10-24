# 安装与运行

核心CLI只需Python 3.10+标准库和FFmpeg/ffprobe（含libass、libx264、AAC、loudnorm）。v1.7响度处理不增加Python依赖或模型；默认与输出证据见[响度与画质](loudness-and-picture.md)。不共享修改其他Agent的Python环境。下列依赖各自装在单独环境；安装包和模型下载是网络操作，但不会提交业务视频。

init自动读取已存在的references/local-runtime.json；`--runtime`指定其他环境，`--no-runtime`用于移机空配置或不加载真实模型的测试。显式参数互斥；仅字幕即使读到环境配置也不校验或运行TTS/分离器。

仅字幕简易通道使用 `init --mode subtitles_only`。已有中文SRT时，核心CLI即可运行；没有原稿时再使用本地ASR。它直接复制所有原音轨，不需要Qwen、Chatterbox或Demucs环境，不分离背景声、不生成配音；翻译默认仍使用当前Codex，并非宿主用量免费。仅字幕的音轨、无音轨和封装边界见[任务契约](job-contract.md)。

## 本地识别

当前用户去字幕走[线上Seedance 2.5编辑交接](online-subtitle-erasure.md)，不部署VSR。旧MI-GAN `providers.erasure`及`erase`命令仅保留历史复现，不是线上执行器；当前已知纹理模糊/漏字。旧模型和CPU ONNX环境仍保留，未修改Qwen或Comfy依赖。便携包不带28MB权重或本机配置。

```powershell
python -m venv .venv-asr
& '.venv-asr/Scripts/python.exe' -m pip install faster-whisper
```

在Hugging Face官方仓库下载完整 `Systran/faster-whisper-small` 或质量更高的模型到明确目录，再填写 `providers.asr.model_dir`。运行时强制offline/local_files_only，不隐式下载；原ASR结果必须校对。

## 本地配音

推荐先部署覆盖四种语言的Chatterbox Multilingual。官方安装建议Python 3.11；CUDA版torch按实际显卡环境安装，CPU可运行但速度取决于设备。不要把Chatterbox依赖装进现有Qwen环境：二者可能锁定不同torch/transformers版本。

```powershell
py -3.11 -m venv .venv-chatterbox
& '.venv-chatterbox/Scripts/python.exe' -m pip install chatterbox-tts
```

从 [ResembleAI/chatterbox](https://huggingface.co/ResembleAI/chatterbox) 核对下载当前多语种模型需要的完整文件，并填入模型根目录。当前适配调用 `ChatterboxMultilingualTTS.from_local(...).generate(text, language_id)`；以安装版本实际API为准。不要误用英语专用的Turbo或Nano并声称支持马来语。模型文件缺失时worker报错而不是自动下载。

可明确执行本包的公开权重下载器（处理视频不会自动调用）：

```powershell
python -X utf8 scripts/download_models.py chatterbox --output 'D:/models/chatterbox-multilingual'
python -X utf8 scripts/download_models.py whisper-tiny --output 'D:/models/faster-whisper-tiny'
```

下载器只访问上述官方仓库，记录具体修订、校验LFS SHA-256/配置Git blob身份；断网保留part文件，需显式重跑恢复，不自动反复请求。下载完成不等于模型加载或配音通过。tiny用于轻量烟测，复杂电商原稿建议部署更高质量模型并人工校对。

若已有Qwen3-TTS-CustomVoice模型和环境，可配置engine=qwen，当前市场路由覆盖英语、华语、日语、韩语。对应speaker为Ryan/Serena/Ono_Anna/Sohee；原女声时英语也改女声。模型和实际音色兼容性需烟测，不默认参考真人音频克隆。泰/菲/印尼不在Qwen和Chatterbox当前支持范围，显式阻断误选，不自动转云。

本包针对Chatterbox 0.1.7的实际加载问题做了进程内适配：非中文不提前初始化没有使用的中文仓颉分词器，避免马来语启动时隐式下载/写用户目录。中文Chatterbox仍需明确提供本地`pkuseg_model_dir`和仓颉文件；本机华语默认走已部署Qwen。适配不修改第三方包文件，不把缺词法模型静默降级成另一语言。

`provided`可接人工录音或其他已有引擎；音频须逐句与译文绑定。它是明确的已配音输入模式，不证明TTS模型已经部署。

## 翻译

默认host由当前Codex读完整原稿、翻译、校对后写译文；Codex运行于当前宿主，其费用/联网由宿主决定。全离线自动翻译可在本地已部署OpenAI兼容服务上配置 `engine: openai_compatible`、`base_url`和实际model名。不自动下载或启动Ollama；不把占位译文当成功结果。

## 背景声

完整配音现在默认本地BandIt对白/音乐/音效三轨通道，本机代码、权重及独立.venv-bandit已部署，init自动读取本机local-runtime路径。配置、身份、推理兼容、缓存和混音见[保声配音通道](preserved-audio.md)。便携Skill包不带模型或本机配置，移机需重新部署并实测；不静默下载或回退Demucs。

旧任务明确选Demucs时，使用已有独立demucs环境和含模型/YAML的本地repo，脚本用 `python -m demucs.separate --repo 本地目录 --two-stems vocals`，不使用自动下载路径。语音、敲击声和BGM的分离无法保证无损，必须实际听检。

## 在线TTS：按语种选择（v1.9.1）

配置`engine: bailian`，Key只读`DASHSCOPE_API_KEY`环境变量，模型/系统音色按`languages.json`绑定。泰语和印尼语沿用原Sambert渠道；Filipino默认独立Edge环境中的Blessica女声，MiniMax仅保留为明确选择的可选配置。百炼账户需先开通对应产品。实现、权限和错误恢复见[百炼在线TTS](bailian-tts.md)。

本机复用已有Python 3.12和DashScope SDK 1.25.20，无新增模型下载或安装。移机可在Codex专用虚拟环境安装`dashscope`并填写`python`；不把Key写入runtime。仅台词传出，不上传视频或真人样本。

菲律宾语默认`edge`＋`fil-PH-BlessicaNeural`，复用已有独立`.venv-edge`；只需任务online_tts_allowed，不需paid_tts_allowed。它不是百炼失败的自动后备；原MiniMax失败不自动换音色，Blessica失败也不自动转百炼。地区口音不能仅靠国家标签保证，首次选音仍需试听。

## 常用命令

脚本路径相对当前Skill根目录；实际调用时使用已安装目录下的绝对脚本路径。

```powershell
python scripts/localize.py doctor
python scripts/localize.py languages
python scripts/localize.py init --input 'D:/videos' --output 'D:/jobs/campaign-v1' --targets en-SG,zh-SG,ms-MY,en-MY,ja-JP,en-US
python scripts/localize.py transcribe --job 'D:/jobs/campaign-v1/job.json'
python scripts/localize.py requests --job 'D:/jobs/campaign-v1/job.json'
# 校对原稿与译文、配置引擎和底轨之后
python scripts/localize.py run --job 'D:/jobs/campaign-v1/job.json'
```

这些示例中的D盘路径由用户替换，不是已存在的测试输入。实际安装、依赖可用、模型加载、完整视听、海外转化五件事分别报告。
