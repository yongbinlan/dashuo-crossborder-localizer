# 任务契约

所有路径支持绝对路径；视频、字幕、背景声和预录配音目录的相对路径基于job.json所在目录。模型目录、Python解释器和本地分离repo采用绝对路径。不要把密码、Key、授权头或真人参考录音内容写入任务。

## 女声匹配（v1.4）

`assets[].source_voice_type`记录female/male/unknown/mixed，`source_voice_evidence`记录实际听检依据或用户说明。新任务`require_voice_type_review: true`，unknown/mixed且无明确目标音色时阻断；旧任务未设此开关保持兼容，但female来源仍触发女声规则。仅字幕通道跳过本规则。

`providers.tts.voice_type`或`target_overrides[语种].voice_type`可指定female/male/unknown。来源female不能配male。Qwen无显式speaker时女声用Serena（日语Ono_Anna）；显式男声阻断。provided女声须声明female及`voice_evidence`来源并听检。

Chatterbox的女声配置示例（参考音频来自本地合成预设，不是真人录音）：

```json
{"engine":"chatterbox","voice_type":"female","synthetic_voice_reference":{
  "path":"合成女声参考.wav","sha256":"实际64位SHA256",
  "kind":"synthetic_preset","voice_type":"female",
  "provenance":"生成引擎、女声预设名称及真实生成回执路径"
}}
```

路径相对job.json解析，渲染前与worker加载前核验文件哈希；引用变化不会复用旧声线结果。只接受synthetic_preset，不能借此接入未经授权的真人克隆。预设元数据和来源不等于实际女声听感合格，换声后必须重验发音、时长和全片节奏。

`init`输出是真实盘点，`assets[].source_sha256`绑定原视频。任务清单和固定机器文件保留技术名称。人工标题来自原文件名，资产ID稳定；输出按 `variants/资产ID/区域语种/vN` 区分，不覆盖相似名称。

## 关键字段

| 字段 | 用法 |
|---|---|
| mode | `dub`（默认，旧任务缺字段时同此）：翻译＋配音＋字幕；`subtitles_only`：仅加目标字幕、复制全部原音轨，无TTS/分离/混音，不要求背景底轨。未知值阻断 |
| targets | 选择语言配置ID，如 `en-SG`, `zh-SG`, `ms-MY`, `en-MY`, `ja-JP`, `en-US` |
| providers.asr | `python`, `model_dir`, `device`（cpu/cuda）。仅本地模型，自动识别路径关闭 |
| providers.translation.engine | `host`：由当前Agent准备译文；`openai_compatible`：调用配置模型做初译，生成后仍需复核 |
| providers.translation.base_url/model | 例如本地 `http://127.0.0.1:11434/v1` 和实际已部署模型ID；默认不查询/启动服务 |
| providers.translation.api_key_env | 可选，Key只读取该环境变量，默认 `DASHUO_TRANSLATION_API_KEY` |
| providers.tts | 仅dub需要：`engine`: chatterbox/qwen/edge/provided；`python`和本地`model_dir`；`device`: cpu/cuda。subtitles_only不检查或加载此项 |
| target_overrides | 如仅 `ms-MY` 用chatterbox，其余用qwen。每项覆盖同名TTS配置，不能改变目标语种 |
| providers.tts.female_voice_preset | 例如`serena-female-v1`：只在选择女声时读取包内合成参考；本机马来语已配置。也可在synthetic_voice_reference中显式设置preset；身份仍由包内SHA绑定 |
| assets[].source_srt | 校对原稿路径；为空则本地ASR。人工修订先保存旧版并显式绑定新稿 |
| assets[].background | dub使用已分离、授权的无中文人声底轨。检查时长和音频；不凭文件名认定去人声成功。仅字幕忽略 |
| render.background_mode | 新dub默认`bandit`保留音乐/音效。旧required/demucs/none配置继续原义；不自动回退或更改旧任务。仅字幕忽略 |
| providers.separation | bandit需要engine/python/repo/checkpoint/checkpoint_sha256/device，详见保声通道。旧demucs仍使用python/repo/model。仅字幕不需要 |
| render.music_gain_db/sfx_gain_db | BandIt独立增益可选覆盖，-24到18dB；默认按RMS/峰值预算。修改后另建版本并重听，不对旧provided背景生效 |
| render.loudness | dub默认启用两遍旁白与混音响度校准，并测最终AAC。enabled/voice_i/voice_tp/delivery_i/delivery_tp/lra默认true/-20/-3/-16/-1.5/9，准确容差、范围及证据见[响度与画质](loudness-and-picture.md)。仅字幕不处理 |
| assets[].hard_subtitles | `unknown`阻断；`keep`为保留已有中文字幕，只叠目标语；`none`为干净原片，默认生成双字幕；`cover`未获当前用户采用 |
| assets[].source_subtitle_region | 原中文区域`[x,y,width,height]`，相对坐标；y取实际文字上沿。keep默认继承原区域的水平中心与宽度，新增区域下沿距离中文上沿为画面高度0.008（1920高约15px），预留高度0.16；目标语下沿固定、换行向上展开，区域重叠即阻断 |
| assets[].target_subtitle_region | 可手动指定新增字幕区域；keep按区域下沿锚定。当前用户要求紧邻原字幕正上方，不沿用旧画面顶部配置；删除此字段即可按source_subtitle_region自动定位，不能遮中文或商品。原字幕位置变化的素材应先核对各句，静态区域不能冒充自动追踪 |
| assets[].cover_region | cover使用 `[x,y,width,height]`，范围0到1，不能覆盖产品/手部/关键信息 |
| render.max_speedup | 新dub默认1.10；代码接受1到1.5供明确获准的旧任务使用，不能据此自行提高上限。超时必须修复配音/协商节奏，不截断台词。仅字幕不执行此检查 |
| render.trim_edge_silence | 新建dub任务true；旧任务缺字段为false。保留raw speech，仅裁约-65dB首尾空白并各留120ms，内部停顿不变；speech-preparation.json记录原始/处理后身份。仅字幕忽略 |
| providers.tts.seed | 本地模型可选整数种子；逐句使用seed+id，可追溯但不保证跨设备逐字节相同 |
| providers.tts.cfg_weight/exaggeration/temperature | Chatterbox可选生成参数，分别允许0—1、0—2、0.05—2；默认保持上游0.5/0.5/0.8。参数不是语速保证；Qwen不使用这些Chatterbox字段，0.6B不把风格指令当作可靠控制 |
| render.use_following_silence | dub默认true：目标语可延用该句后的原有空白，但不越过下一句、不延长/变速视频；要求逐句时间轴锁定时设false。仅字幕沿原稿时间轴 |
| render.font/font_size/bottom_margin | 字体默认微软雅黑；字体大小按分辨率计算，可手动指定；画面和字体缺字仍须实际检查 |
| render.subtitle_mode | 当前用户默认bilingual：有原中文硬字幕则只新增目标语，干净原片显示目标语＋中文；华语目标不重复中文。另输出translated.srt与bilingual.srt |
| glossary | 品牌、商品术语和各语言译法。Agent翻译时使用，自动初译也传模型 |
| protected_terms | 需要原样保留的品牌/型号。只放不应翻译的原词，不要锁住需翻译的普通卖点 |
| cloud_allowed/paid_calls_allowed | 默认false。单独允许在线TTS不等于允许远程付费翻译；显式true须源于用户当前有效授权 |

## 译文格式

`requests`生成每个目标文件。`source_srt_sha256`由脚本生成，不手改；条目与原稿一一对应，不改时间轴。完成后如下所示（仅结构示例）：

```json
{
  "source_srt_sha256": "由requests写入的真实值",
  "target": "ms-MY",
  "reviewed": true,
  "reviewer": "实际逐句复核的Agent或人员",
  "items": [{"id":1,"source":"原句","text":"经核对的完整目标语台词"}]
}
```

输入SRT必须UTF-8，编号从1连续、时间不重叠且不超视频时长。ASS输入先转换并核对，不在此版本宣称支持任意字幕格式。翻译中的数字、货币符号、URL、占位符与保护词不得丢失；确需改写数字表达时先更新受审版本，不关掉校验硬塞。

`provided`允许用户提供已录好的逐句音频，`audio_dir`内文件名为 `00001.wav`, `00002.wav`等，对应当前译文。音频有改动会建立新成片版本，内容与授权仍须看听复核；不能拿测试音调冒充配音。

## 仅字幕输出

`init --mode subtitles_only` 后仍使用 `transcribe`（已有SRT仅核对、不需要ASR环境）、`requests`、`run`。原稿和译文复核要求相同；沿原字幕时间轴，不执行配音速度检查或延用下一段空白。

输出 `localized.mp4`、`translated.srt`、`bilingual.srt`、`captions.ass`、原稿时间记录 `timing.json`、逐句预览、`audio-preservation.json` 和回执。没有新配音WAV。全部原音轨以stream copy保留，逐轨比较编码数据、编解码配置和包时间戳；不支持的MP4音频封装或保留核验失败时阻断，不自动转码。原片无音轨则保持无音轨。

通道进入版本指纹；仅字幕和配音产物互不冒用，切换通道建立新版本。仅字幕不因无关的TTS模型、背景底轨或配音语速配置变化重复合成。

## 重跑和状态

- 同任务只允许一个执行者；遗留`.lock`先核实PID，不自动删锁。
- 成功版本可按输入指纹和产物身份复用；任何输入变化建立vN。
- 配音新版字幕ASS与时轴不变时另行复用已核验的视频流，仅替换音轨；字幕变更则从绑定原片编码一次。无新增字幕时直接复制原视频流。证据在picture-preservation.json，不把流复制称为恢复原画质。
- 翻译请求发出即落地request-state；超时结果未知时不自动再调。确认原请求失败/未产生结果后人工清理该请求状态才可重新调用；保留费用记录。
- `configured`/`translation_inputs_ready`不是成片。`verification_incomplete`表示技术生成通过但实际检查欠缺；`pending_user_review`只在实际检查记录齐全后出现；代码不产生`approved`或`released`。
