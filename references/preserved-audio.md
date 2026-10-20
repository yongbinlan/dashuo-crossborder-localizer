# 保留BGM和音效的默认配音通道（v1.5）

2026-10-20用户认可簇绒坐垫马来语女声v5，并明确要求此流程封装为后续批量翻译配音默认。该认可覆盖此样片与流程选择；未来视频仍逐片验收，不自动继承批准或发布权限。仅字幕固定v1.2.1包保持原字节。

## 一次配置，批量执行

已安装机器使用`references/local-runtime.json`自动绑定现有Qwen、Chatterbox、ASR与BandIt环境。`init`无显式`--runtime`时自动读取该文件。移机先配置真实环境；便携包不带数百MB权重、Python环境或本机路径，不会自行下载。

```json
{"render":{"background_mode":"bandit","max_speedup":1.10,"trim_edge_silence":true,"use_following_silence":true},
 "providers":{"separation":{"engine":"bandit","python":"本地解释器绝对路径","repo":"作者代码目录绝对路径",
   "checkpoint":"本地checkpoint绝对路径","checkpoint_sha256":"已核验权重哈希","device":"cuda"}},
 "target_overrides":{"ms-MY":{"engine":"chatterbox","female_voice_preset":"serena-female-v1"}}}
```

当前已部署BandIt v2 multi权重SHA256为`abcfccf65446752a057f4a302c941479a54b7560ebf8d7bca039d2ea98e64cfc`。CUDA不可用或身份不符会阻断，不静默换分离器。推理为48kHz立体声、8秒窗口/1秒步长、batch1；不足反射填充长度的短片只临时补零后按原样本数去掉新增尾部。torchaudio旧StreamReader导入与类型注解兼容只在worker内存处理，不改作者源码/模型运算或共享环境。

本机马来语女声选中后使用包内合成参考；`female_voice_preset`仅在目标女声时激活，不把男声/未选声音的任务默认为女声。参考身份和Qwen Serena来源见voice-presets.json，音频位于assets/serena-female-v1.wav。未确认原声音且未指定目标声音时先澄清。

## 缓存、混音与节奏

- 每批次`separations/身份/vN`按原视频SHA、权重、配置和作者/worker代码绑定缓存，复用前核验三轨文件SHA。多个语种复用同一分离结果；更换旁白/译文只重新配音和混音。损坏轨道留存并另建尝试，不覆盖旧证据。
- 同身份分离失败后，不为每个语言重复执行。查error.log，修复后定向增加`providers.separation.retry_revision`，保留失败目录，不盲重试。
- `speech.wav`只留作审查，不作为背景混入。music/sfx分别依据RMS和峰值设增益，音乐默认最多+12dB、音效最多+9dB且增益后峰值预算-3dB；音乐随校准后的新旁白避让（ratio2.5，attack15ms，release220ms）。背景限幅0.8、总混音0.95；amix的normalize与限幅器自动补偿增益仍关闭，响度由独立的[两遍校准与最终AAC检查](loudness-and-picture.md)负责。独立增益可配置`render.music_gain_db`、`sfx_gain_db`（-24到18dB），生成新版本并重听。
- 同任务提供去对白底轨时优先使用该文件，按旧provided背景逻辑混音；不要把原片完整音轨当去人声底轨。需要背景与音效独立调整时使用BandIt模式且清除旧asset.background。
- 默认先按每句原时窗精炼忠实译文，保持完整含义；使用同一音色、保守首尾准备及已有空白，后期最多1.10倍。不截词、不改变镜头/总时长。新增候选有界，仍超时就报告具体句与时间缺口；不静默删卖点或放宽上限。

## 结果与验收

每个变体输出localized.mp4、原始dub.wav、校准dub-calibrated.wav、混音mix.wav、交付mix-delivery.wav、background.wav、双语/目标SRT和ASS，附timing、speech-preparation、pacing-report、mix-receipt、loudness-receipt、picture-preservation、separation-reference及receipt.json。separation-reference指向批次共享三轨及来源回执。原片无声/多音轨来源需先决定对白来源，本实现分离按默认音频流读取，不自动声称全部音轨已保留；仅字幕仍完整复制所有原音轨。

先核验完整原稿与译文、原片/模型身份、时轴/最大倍率、全解码、尺寸时长、实际字幕画面；再听完整混音与必要的纯背景，查尾字、突快、女声类型、中文残留、音效损失与声音伪影。不熟悉的语言保留可靠当地审听缺口；ASR、元音/字符率、限幅和SHA都不能证明听感。可修复问题先修复；缺必要听审证据保持verification_incomplete。用户审核仅针对对应版本，不自动投放。

## 来源和许可边界

BandIt作者代码：https://github.com/kwatcharasupat/bandit-v2（Apache-2.0）；multi模型：https://zenodo.org/records/12701995（该权重记录CC-BY-SA-4.0）。本包是本地调用适配，不捆绑作者代码或模型，也不把代码许可替代权重许可；移机或对外分发另核对相应许可与素材权利。模型不会保证音乐/音效无损，失败不自动调用付费服务。
