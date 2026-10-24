# 百炼在线TTS（v1.9）

**v1.9.1最新选择：菲律宾语默认改为用户已认可的Edge Blessica女声；泰语/印尼语百炼及其他本地路线不变。本页MiniMax配置与成功短测保留为显式可选和历史证据，不是菲律宾语默认或自动后备。**

本用户2026-10-24要求将在线配音切回之前配置的阿里云百炼，并明确允许菲律宾语接入MiniMax及一句验证。本地Qwen、Chatterbox和所有后期流程不变。机器映射只维护在[languages.json](languages.json)的`bailian`字段；不把原中文Sambert知柜音色用于所有外语。

## 配置

```json
{
  "online_tts_allowed": true,
  "paid_tts_allowed": true,
  "target_overrides": {
    "th-TH": {
      "engine": "bailian",
      "python": "D:/runtimes/bailian/Scripts/python.exe",
      "api_key_env": "DASHSCOPE_API_KEY",
      "speech_rate": 1.0,
      "volume": 100
    }
  }
}
```

路径为移机示例。本机runtime复用原Python 3.12和DashScope SDK 1.25.20；Key只读取现有环境变量，不复制到Skill、请求JSON、GitHub或交接。移机可建Codex专用虚拟环境并安装`dashscope`，不共享修改其他Agent环境。

已有用户授权时，新任务可加`init --allow-online-tts --allow-paid-tts`。两个权限独立于远程翻译，`paid_calls_allowed`仍默认false。runtime配置不包含授权；本账号授权不能推广至其他用户。旧job不自动改写，续办切换时保留旧版并定向迁移对应target_overrides和TTS权限，不重初始化任务或覆盖旧结果。

仅台词发送至百炼北京渠道。Sambert走原DashScope官方SDK WebSocket，MiniMax按当前官方API使用原百炼HTTP域名（不套用通用工作空间域名迁移建议），请求显式设置`input.action: tts`，走百炼HTTP接口，不另建MiniMax直连账号。当前仅允许映射表内已核对的模型与系统女声；其他音色需先核对语言和声音类型后更新映射，不提供隐式真人复刻。MiniMax通用女声配Filipino language_boost，并不证明菲律宾本土口音。

## 用量、缓存与恢复

每句请求前在目标目录下`paid-tts-cache`独占登记，指纹包含译文、模型、音色及合成参数。成功原始音频与规范PCM16音频保存后，后续vN渲染复用成功音频；不因后期渲染失败重复付费生成。未知/失败/损坏记录会阻断，不自动删除、不跨引擎后备、不自动收费重试。此缓存是同一任务目标目录的保护，不承诺跨任务、改稿或改模型免费。

`tts-usage.json`记录每句request_id、服务返回usage、输入字符数和复用状态。usage是调用用量，不是账户余额或剩余免费额度；缺服务usage时保持null，不把本地字符数当已结算账单。字幕、BandIt、节奏、响度与视频流复用均沿原流程执行。

超时或失败后先核对原请求状态、费用及已保存原始音频；成功音频可本地修复转换。确需重新调用时，保留旧记录并记录明确原因及有效费用授权，由操作者显式处理该请求；不能用新建vN绕开未知请求保护。产品未开通则先完成账号开通，不连续试模型或收费重试。

## 本机验证状态（2026-10-24）

- 泰语：Sambert Waan，短句生成及PCM16/非静音检查通过；需用户试听自然度。
- 印尼语：Sambert Indah，短句生成及PCM16/非静音检查通过；需用户试听自然度。
- 菲律宾语：已在旧版控制台单独开通MiniMax-Speech系列；`MiniMax/speech-2.8-turbo`＋通用女声原短句生成成功，2.217542秒，服务返回32个计费字符。PCM16、非静音、峰值检查通过；当地发音/自然度仍待用户试听，不证明已适合正式投放。本地tiny ASR转写不准确，不能据其声称逐字准确。
- 未上传业务视频或参考真人音频，未更改账户凭据；账户余额/免费余量未核实。本轮未重做完整商品视频，不将短句合成等同完整成片验收。

## 官方来源

- [Sambert API及系统音色表](https://help.aliyun.com/zh/model-studio/sambert-python-sdk)：Waan为泰语女声，Indah为印尼女声。
- [百炼MiniMax同步TTS](https://help.aliyun.com/zh/model-studio/minimax-synchronous-speech-synthesis-api)：Filipino语言选项；turbo公开标价2元/万字符、无免费额度，实际以账号及账单为准。
- [MiniMax系统音色表](https://platform.minimax.cn/docs/faq/system-voice-id)：通用女性系统预设，不创建/解锁复刻音色。

上述模型可用性、价格和许可需按实际调用时核对；本机验证状态不替代其他账号开通或最终听审。
