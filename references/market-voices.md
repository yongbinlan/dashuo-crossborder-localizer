# 多市场音色与本地化（v1.9.1）

语言和默认音色的机器来源为[languages.json](languages.json)，不要维护另一套映射。2026-10-24本机已核对模型公开语言支持与百炼官方音色与API；新增语种的真实短测和最终视听分开记录。

| 市场 | 目标 | 默认路线 | 女声选择与边界 |
| --- | --- | --- | --- |
| 日本 | ja-JP | 本地Qwen | Ono_Anna；原有路线保留 |
| 韩国 | ko-KR | 本地Qwen | Sohee；新接入，需韩语实际试听 |
| 泰国 | th-TH | 百炼Sambert | Waan官方泰语女声；本地两个引擎不支持 |
| 菲律宾 | fil-PH | Edge在线Blessica | fil-PH-BlessicaNeural女声；用户已认可本轮同句试听更自然，后续默认使用 |
| 马来西亚 | ms-MY / en-MY | 马来语Chatterbox；英语Qwen | 马来语用已有合成女声参考；英语通用女声，不保证当地英语口音 |
| 新加坡 | en-SG / zh-SG | 本地Qwen | 通用英语/普通话女声；当地英语音色可显式选获准Edge Luna，不把区域标签当已验证口音 |
| 印尼 | id-ID | 百炼Sambert | Indah官方印尼女声；不可用马来语引擎代替 |
| 台湾市场 | zh-TW | 本地Qwen普通话；地区音色另选 | 繁中及当地用词；本地不保证台湾口音，地区音色为明确选择的在线HsiaoChen |

英语、普通话原有配置不删除，越南语仍待协商。各语种可仅字幕，也可使用`provided`外部逐句配音；没有对应本地模型支持时，不暗中用相近语言替代。

## 授权与费用

本用户最新明确选择：菲律宾语默认Blessica，其他语言保持原配置。菲律宾语仅需online_tts_allowed；泰语/印尼语百炼还需paid_tts_allowed。runtime不授予用户权限，远程付费翻译仍按原门槛。

泰/印尼Sambert已返回音频与字符用量，仍按百炼用量收费。菲律宾语Blessica为Edge在线通道，非Azure计费API；不套用百炼额度/免费额度或Azure服务承诺。原MiniMax短测记录保留，只作明确选择的可选路线，不自动回退。使用系统预设，不上传样本、不复刻真人；配置及收费来源见[百炼在线TTS](bailian-tts.md)。

## 语境与交付

目标语言、字形、当地术语、音色、性别类型和语气独立选择。泰语检查组合字符和断行，韩语检查敬语/品牌读法，Filipino避免用整段英语替代，印尼语不用马来西亚词汇模板。台湾繁中需当地表达，不只转换字形。

任何新语种继续保留女声匹配、完整台词、1.10倍上限与匀速处理、BGM/音效保留、双字幕邻接和响度校准。首次选音后先短试听；生成成功、技术合格、自然度审阅和完整成片验收分别报告，不从一个短句推断批量口音合格。

## 核对来源

- Qwen3-TTS官方语言与音色：https://github.com/QwenLM/Qwen3-TTS#custom-voice
- Chatterbox官方语言列表：https://github.com/resemble-ai/chatterbox#supported-languages
- Edge客户端与实时音色查询：https://github.com/rany2/edge-tts
- Microsoft音色名称：https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=tts

以上名称与引擎能力截至2026-10-24核对，服务可用性仍在每次调用前检查。
