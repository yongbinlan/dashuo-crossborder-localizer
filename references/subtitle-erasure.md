# 本地去字幕通道（v1.6，独立选择）

**历史复现入口：**2026-10-20用户反馈此样片存在纹理模糊和漏字幕，随后取消VSR测试，指定去字幕走[线上Seedance 2.5交接](online-subtitle-erasure.md)。下文保留旧命令和证据，不能作为当前默认路线，也不能把旧扫描零检出视为实际无残留。

用途：修复已烧进画面的字幕，完整保留原中文旁白、BGM、音效与视频时序。此通道不翻译、不配音，不改变已有仅字幕/保声配音的默认行为。仅用户明确要去原字幕时调用；不擦商品Logo、包装文字或画面内原生文字。

## 本地实现和边界

MI-GAN作者ONNX pipeline v2，当前权重28,079,181字节，SHA256 `6f1f3530a1a2324b19752018ce756088b07973cda8d7d890034ace5c8a48c40b`；来源由作者README指向的andraniksargsyan/migan仓库修订406830d0fa60666da0071c342ad2fbc8f30c5c64提供。权重MIT条款随包保留在licenses/MI-GAN-LICENSE-WEIGHTS.txt；代码/模型来源见sources-and-capabilities.md。本机CPU ONNX Runtime运行，模型文件只在本地读取，不上传业务画面、不调用付费API。复用既有环境中的OpenCV/NumPy/ONNX Runtime，未修改共享环境。

只处理**已明确定位区域内的中性白字、深色描边/阴影**，支持居中单字（如片头“嗯”）及多字行。不是通用OCR；彩色、半透明、移动位置、背景板/滚动字幕或混合多种样式，先试片确认，未覆盖不能称已去干净。默认12px扩展字形遮罩、3px内侧渐变融合，只把修复结果写回遮罩；编码前遮罩外RGB必须保持一致。MP4采用正常有损编码，不能将这个不变量表述为成片逐像素不变。

预扫描所有帧的压缩遮罩；遇单帧漏检时，仅在前后遮罩IoU至少0.8且当前帧该区域实际仍有白字/深色笔画时，用相邻遮罩并集补检，再送同一修复模型。该补检不等于时序视频修复：必须检查闪烁、纹理抖动、桌沿/座椅边缘和商品图案。遮挡的真实像素不可从原片直接恢复，模型是推测填补；不承诺“任何画面无痕”。不通过裁切、模糊、盖条、改镜头或云端生成冒充本地擦除。

## 使用

原片先看完整字幕范围、字幕出现/切换/消失和高运动帧，定位区域并记录review_note；不要仅依赖ASR时间轴，它可能漏掉单字或片头语气词。给Logo、手部、脸等不能被修复的区域配置protected_regions；发生遮罩重叠时阻断。字幕直接压住商品纹理时必须实际检查修复细节，不得声称恢复原始真值。

去字幕任务是独立schema，不使用翻译任务的targets/译文字段。复制本机local-runtime.providers.erasure到任务providers.erasure；该环境已部署，不重复下载。示例结构（将示例路径/哈希换为实际值）：

```json
{"schema_version":1,"mode":"erase_subtitles",
 "providers":{"erasure":{"engine":"migan","python":"本地解释器绝对路径",
   "model":"本地ONNX绝对路径","model_sha256":"实际模型SHA256","mask_dilate":12}},
 "assets":[{"id":"video-stable-id","video":"原视频绝对路径","source_sha256":"原视频SHA256",
   "erasure":{"regions":[[0.12,0.755,0.76,0.055]],"region_reviewed":true,
     "review_note":"真实看过的字幕区域、敏感细节与样式说明","expected_subtitles":true,"protected_regions":[]}}]}
```

regions/protected_regions格式均为[x,y,width,height]比例；上例仅属于簇绒坐垫，不作为其他视频固定位置。不要把reviewed示例当审查证据。处理CFR、无旋转元数据视频；不满足时先明确规范时轴，不自动改变原时长或丢帧。所有原音轨映射并复制，容器不支持原音频编码时阻断，不静默转码/丢轨。

```powershell
python scripts/localize.py erase --job '去字幕任务.json' --check-only
python scripts/localize.py erase --job '去字幕任务.json'
```

check-only只读检查来源、模型身份、区域和时轴，不加载模型、不生成视频。一个任务可有多条assets，逐条处理、失败隔离；输出erasures/资产ID/vN/clean.mp4与worker/逐帧指标、遮罩/修复前后样图、原音轨保持证据及receipt。重复且身份一致时复用，变更另建版本；旧候选保留。修复失败后先诊断再重跑，不做无界随机尝试。

仅为定向修复旧测试漏擦帧，可显式提供repair_cache（旧worker request/receipt路径和各自SHA）。只在原片、模型、区域和遮罩参数相同、旧多字遮罩与新遮罩相同时复用旧候选遮罩内像素；单字等新遮罩帧重新推理。其余画面始终取原片，不把旧候选整帧当原片；需重新整片检查。普通新任务不设置此高级字段。

## 验收和接后续翻译

技术检查：输入/模型/代码身份、全部帧处理、帧数/尺寸/fps/时长、全解码、全部原音轨编码包和时间戳一致、编码前遮罩外不变。实际查看最终MP4，不只看worker截图：首尾、所有字幕切换、高运动和全部去字区域；检查文字/黑边/阴影、修复边界、闪烁、手部/衣物/商品纹理。检测器无白字不证明没有残影。

缺完整观察或发现待修复问题时保持verification_incomplete，不给“无痕成功”标签。技术合成与用户试片分开记录；素材不会自动投放。review使用clean.mp4哈希，需要visual/subtitle_erasure/temporal_consistency/protected_details/original_audio_preserved；有原音轨时也需audio_full_playback。不能用用户审阅状态跳过已发现缺陷。

用户另行要求“去中文字幕再翻译”时，先生成并检查clean.mp4，再以新输入SHA绑定现有字幕/配音任务，保留原稿来源；若用户只要外语字幕，设置hard_subtitles=none、subtitle_mode=target_only，避免又加回中文。原有双字幕任务继续保留原中文字幕，不能自行迁移。
