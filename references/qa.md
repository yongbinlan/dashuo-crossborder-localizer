# 实际验收

## 仅字幕简易通道

两通道都检查目标语位于原中文字幕的正上方、水平居中、间距清晰；逐句检查单行与双行，双行向上展开且下沿稳定。不能仅凭区域不重叠判为排版合格，也不能复用旧顶部字幕的画面审阅记录。

`mode: subtitles_only` 保留原中文旁白、音乐和音效。技术检查除字幕、来源绑定、全解码、尺寸和时长外，还比较所有原音轨的数量、编解码配置、编码包内容和包时间戳，证据在 `audio-preservation.json`；原片无音轨时输出也无音轨。不执行配音或分离质量检查，不将有意保留的中文旁白当残留错误。

实际检查译文完整、字幕同步/可读/断行、无缺字和不遮挡原中文或商品。有音轨时仍需实际播放确认；没有播放证据不冒填通过。`review-evidence.json`须含当前视频哈希、真实reviewer和notes，以及 `visual`、`translation_semantics`、`subtitle_sync`、`original_audio_preserved` 均为true；原片有音轨时还需 `audio_full_playback: true`。不要求配音通道的 `background` 或 `no_unintended_chinese`。

## 完整配音通道

v1.7默认检查loudness-receipt.json：旁白校准后驱动混音、最终AAC综合响度和真峰值通过[当前响度标准](loudness-and-picture.md)，原始旁白保留。检查picture-preservation.json：需要烧录时从原片单次编码；音频改版且ASS不变时复用已核验视频流，全部包/时轴/编解码配置一致。技术检查不能证明发音/背景比例好听或感知无损。

默认BandIt通道另外检查共享三轨来源、music/sfx两轨实际进入混音、原speech未混入、音乐随新旁白避让、音效响度与尾部完整，听纯背景和最终混音判断中文残留/伪影。查看mix-receipt及separation-reference；缓存命中不能代替听检。后期默认1.10上限，pacing-report只作相邻语速辅助，末句也须实际听检。用户已认可v5女声样片不代表未来批量视频已获批准。

原片为女声时目标语也为女声；记录声音来源、选定预设/合成参考与实际听检，不从人物画面推断。unknown/mixed先澄清，用户已指定可直接按指定执行。换声后重新验证全部台词、句尾完整、相邻句节奏、最大倍率和混音；旧声线的时长/听审证据不能代替新声线。没有完整听检时保留verification_incomplete及试听样片标记。

`run`做的技术检查：输入绑定、字幕序号/时间轴、数字品牌保护、逐句配音时长适配、不截断台词、成片全解码、尺寸/总长/音轨、每句抽帧。它们不能证明发音自然、语义完整、字幕可读或已无中文残留。

Agent对每条变体实际检查：

- 对照完整原稿、完整译文、音频，覆盖每个卖点/动作/否定/价格；不能用中文注释当外语语义通过。
- 完整观看与听音：字幕同步、断行、日/中等字符缺字、原中文语音混回、尾字截断、机械音/语速、字幕遮挡商品。不会该语种时需要可靠目标语复核；ASR回译只能作辅助，不冒充母语审校。
- 用户要求保留中文字幕：检查原中文完整可见、新增外语不重叠，原商品和镜头不变，包装/Logo未误删。中文字幕是预期保留，不能当作应擦除的残留。
- 图像缺陷或声音缺陷发现后保留版本，修复后重跑；技术通过未解决的问题保持verification_incomplete。

实际检查通过后可手写`review-evidence.json`：

```json
{
  "reviewer":"真实执行检查的Agent/人员",
  "video_sha256":"当前receipt中的localized.mp4哈希",
  "visual":true,
  "audio_full_playback":true,
  "translation_semantics":true,
  "subtitle_sync":true,
  "background":true,
  "no_unintended_chinese":true,
  "notes":"具体检查了哪些时间段、字幕/发音和原中文残留，实际观察结果"
}
```

然后运行 `review --variant 变体vN目录 --evidence 证据文件`。命令会绑定当前产物身份后设为pending_user_review；证据是审阅者声明，不能由脚本自行填true。所有双字幕版本都保留有意的中文字幕/商品文字，`no_unintended_chinese`只指非预期原中文语音或无关文字。用户批准/投放需要用户真实行动，脚本不产生批准或发布记录。
