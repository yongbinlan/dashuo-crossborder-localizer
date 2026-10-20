# 来源、复刻范围与能力边界

研究日期：2026-10-20。此实现独立编写，使用公开契约理解用户需要的行为，未提取GhostCut闭源代码、服务端权重或私人凭据，未运行逆向工具。

## REA研究

- [morluto/rea](https://github.com/morluto/rea)，研究快照 `833136507dd79d959340b09862a53af344e4f1b3`，MIT。
- 已阅读README、reverse-engineer-anything的SKILL及evidence-workflows参考。README说明其为本地MCP/CLI逆向工具，Skill本身不带分析引擎。入口明示完整公开源码分析应使用普通仓库工具，无需REA。
- 采用的是“观察→证据→重建→有限范围验证”的方法；没有安装REA MCP，没有对GhostCut程序进行二进制反编译，因此没有GhostCut原程序等价性证明。

## GhostCut行为参考

- [公开Skill](https://github.com/zhaoli-ai/GhostCut-ai/tree/main/skills/ghostcut-api-guide)，研究快照 `038d466e74beca96a04be0c9578e0cd2e217f24a`。
- 核对了视频翻译配音、源/目标语言、背景声、字幕压制、硬字幕擦除、批量数量说明。该服务的20条URL批量限制不约束本独立本地队列，不复制成自己的限制。
- 本地实现覆盖多视频×多语言任务、译文和原稿版本绑定、配音、时间适配、背景音合成、字幕、失败隔离和恢复；不同模型的效果不能由接口相似推出。
- v1.6的MI-GAN样片被用户指出纹理模糊及漏字；v1.6.1按最新要求停止VSR测试，去字幕改走线上Seedance 2.5编辑交接。旧erase命令仍为本地MI-GAN，不是线上执行器；线上效果尚未实测。口型同步、真人声音克隆、云端并发调度、广告投放和转化效果未实现。

## 独立引擎依据

| 引擎 | 一手来源 | 明确用途/边界 |
|---|---|---|
| faster-whisper | https://github.com/SYSTRAN/faster-whisper | 本地中文ASR；模型目录和实际识别质量须验证 |
| Chatterbox Multilingual | https://github.com/resemble-ai/chatterbox | 官方列有Malay/English/Chinese/Japanese；只说明适配依据，不等于本机加载成功 |
| Qwen3-TTS | https://github.com/QwenLM/Qwen3-TTS | 现有本地备选；不支持本任务的马来语 |
| MI-GAN | https://github.com/Picsart-AI-Research/MI-GAN ; https://huggingface.co/andraniksargsyan/migan | 作者README指向的ONNX修复pipeline；权重LICENSE-WEIGHTS为MIT，副本随包保留；不等于时序视频模型 |
| BandIt v2 | https://github.com/kwatcharasupat/bandit-v2 ; https://zenodo.org/records/12701995 | 当前默认三轨分离，作者代码Apache-2.0、已用multi权重CC-BY-SA-4.0；本机实测/用户试听不证明无损，见保声通道 |
| Demucs | https://github.com/facebookresearch/demucs | 旧任务明确选择时保留；不作为BandIt自动回退 |
| FFmpeg | https://ffmpeg.org/ffmpeg-filters.html | atempo、ass、amix、drawbox、alimiter等本地确定性合成 |
| edge-tts | https://github.com/rany2/edge-tts | 可选在线服务客户端，代码许可证不等于服务使用或商业授权 |

没有复制上游代码片段作为本地发行代码。第三方包/模型各自遵守其许可证，不因REA为MIT就取得其他产品、模型、声音或商业工具的授权。此包不附带第三方模型，依赖与模型实际部署情况见交付回执。
