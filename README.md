# Lumielle Academic Assistant

> 学术论文写作助手 —— 从课题到终稿，一站式体验
> Academic Paper Writing Assistant — From Research Topic to Final Draft, One-Stop Experience

基于 Streamlit 的本地学术论文写作辅助工具：逻辑大纲生成、正文写作、AIGC 检测与去味、终稿 Word 导出。

A Streamlit-based local academic paper writing assistant: logic outline generation, chapter writing, AIGC detection & de-smelling, and final Word export.

**Current release / 当前正式版本：v1.1.0 — 2026-09-29**

---

## 📦 版本下载 / Downloads

| 版本 Version | 语言 Language | 平台 Platform | 目录 Folder |
|---|---|---|---|
| v1.0.0 | 🇨🇳 中文 | macOS | `微光v1.0_中文版_MacOS/` |
| v1.0.0 | 🇨🇳 中文 | Windows | `微光v1.0_中文版_Windows/` |
| v1.0.0 | 🇬🇧 English | macOS | `微光v1.0_英文版_MacOS/` |
| v1.0.0 | 🇬🇧 English | Windows | `微光v1.0_英文版_Windows/` |

选择对应平台的文件夹，解压后双击启动脚本即可使用。

Pick the folder for your platform, unzip it, and double-click the launcher to start.

## ✨ 功能特性 / Features

- 🧠 **LLM 配置与多模型交叉讨论** — Multi-model configuration & cross discussion, with an optional real model connection test before saving
- 🏛️ **科研基座** — Structured research facts with editable filing and optional chapter grounding
- 📚 **文献处理** — Full-document literature analysis with source-linked evidence and chapter bindings
- 🧩 **逻辑链路** — Multi-level logic outline with per-node word count & chart instructions
- ✍️ **正文写作** — Single-chapter & batch writing with word-count auto-correction
- 🛡️ **AIGC 检测与去味** — AI probability detection & adversarial de-smelling
- ⚙️ **提示词配置** — Global style & format prompt control
- 📄 **终稿导出** — Versioned FormatSpec presets with deterministic Word rendering, templates, tables, figures and references

## 🧠 核心架构：上下文三明治 / Core Architecture: The Context Sandwich

写长论文时，长对话可能增加信息遗忘、修改冲突和上下文膨胀的风险。微光将全局大纲、已写章节记忆、当前章节和下游章节边界组合为章节生成上下文，帮助降低重复和前后不一致的风险。已写章节记忆会从完整章节正文按顺序分块提取，再合并为紧凑摘要；生成章节时使用的是摘要及相关边界，而不是每次都发送所有历史正文。

Long conversations can increase the risk of information loss, revision conflicts, and context bloat. Lumielle combines the global outline, memories of written chapters, the current chapter, and downstream boundaries as context for chapter generation to help reduce repetition and inconsistency. Memories of written chapters are extracted from the complete chapter text in ordered chunks and synthesized into compact summaries; generation uses those summaries and relevant boundaries rather than resending every earlier chapter in full.

### 🥪 三明治的层次 / The Layers

```
┌─────────────────────────────────────────────┐
│  ① 全局逻辑大纲（顶层罗盘）                    │
│     Global logic outline — the compass       │
│     将逻辑树压缩为课题全局方向参考              │
├─────────────────────────────────────────────┤
│  ② 上游记忆（面包片 · 已写部分）               │
│     Upstream memory — written chapters       │
│     已写章节：完整正文分块提炼出的紧凑记忆      │
│     未写章节只给大纲标题占位                    │
├─────────────────────────────────────────────┤
│  🎯 当前章节（三明治的核心 = 要生成的内容）      │
│     Current chapter — the meat of the sandwich│
├─────────────────────────────────────────────┤
│  ③ 下游记忆（面包片 · 边界部分）               │
│     Downstream context — boundary only       │
│     已写章节提供紧凑记忆，辅助回改时核对        │
│     未写章节只给轻量大纲边界（减少越界）        │
└─────────────────────────────────────────────┘
```

### 🎯 它解决了什么 / What It Solves

| 痛点 Pain Point | 传统对话 Traditional Chat | 上下文三明治 Context Sandwich |
|---|---|---|
| 🧠 **信息遗忘风险** Information loss | 长对话中前文信息可能难以持续追踪 | 完整章节正文参与记忆构建，压缩记忆随章节上下文提供 |
| 🔄 **修改冲突风险** Revision conflicts | 改中间章节时可能与后文已写内容不一致 | 下游已写章节记忆可供核对，降低冲突风险 |
| 🚀 **上下文膨胀** Context bloat | 每次修改都把全文发回去，增加上下文长度 | 使用摘要和边界，并按上下文预算取舍 |
| 🔮 **越界跑题** Going off-topic | 模型可能写到其他章节的内容 | 全局大纲和未写章节边界可帮助限定当前章节范围 |

### ⚙️ 细节设计 / Design Details

- **上下游辅助核对**：已写章节的紧凑记忆和下游边界可帮助模型减少重复与冲突；生成结果仍需作者核验。/ Upstream and downstream context can help reduce repetition and conflict; generated text still needs author review.
- **上下文预算**：上下文超过预算时会按现有取舍规则截断，并附带截断提示。/ When context exceeds its budget, the current selection policy truncates it and adds a notice.
- **未写章节边界**：下游未写章节只提供大纲标题，帮助当前章避免提前展开后续内容。/ Unwritten downstream chapters are represented by outline titles to help keep the current chapter in scope.

这个设计为章节生成提供结构化上下文，帮助作者检查章节衔接；生成结果仍可能遗漏信息或出现冲突，需要作者核验。

This design supplies structured context for chapter generation and helps authors review continuity; generated text may still omit information or contain conflicts and needs author review.

## 🚀 快速开始 / Quick Start

1. 解压对应平台的版本文件夹 / Unzip the folder for your platform
2. macOS：双击 `Launch.command`（中文版 `一键启动.command`）/ Windows：双击 `Launch.bat`（中文版 `一键启动.bat`）
3. 首次启动自动安装依赖（约 1-3 分钟）/ First launch auto-installs dependencies (~1-3 min)
4. 在「LLM Configuration」填写 API Key，拉取模型列表并选择模型，建议测试连接后保存 / Enter the API Key, fetch and select a model, test the connection if possible, then save
5. 侧边栏填写研究课题与预期字数，开始写作 / Fill in topic & word count, start writing

### DeepSeek API 配置 / DeepSeek API Setup

DeepSeek 开放平台 / DeepSeek Open Platform: <https://platform.deepseek.com><br>
API Keys: <https://platform.deepseek.com/api_keys>

创建 API Key 后，将完整的 `sk-...` 字符串填入微光，使用 Base URL `https://api.deepseek.com`，拉取模型列表并从当前可用模型中选择，再点击“测试模型连接”并保存。新配置默认使用 `deepseek-flash`；`deepseek-flash` 和 `deepseek-v4-pro` 是当前建议尝试的模型，实际以拉取模型列表返回的账号可用模型为准。

Create an API Key, paste the complete `sk-...` value into Lumielle, use Base URL `https://api.deepseek.com`, fetch the model list, select an available model, test the connection, and save. New profiles default to `deepseek-flash`; `deepseek-flash` and `deepseek-v4-pro` are current models to try, while the fetched list is authoritative for models available to your account.

DeepSeek App/web free chat is separate from the Open Platform API. API access depends on the Open Platform account status and balance. / DeepSeek App 或网页版免费聊天与开放平台 API 是不同的使用方式；API 调用取决于开放平台账户状态和余额。

## 🖥️ 平台说明 / Platform Notes

- **macOS**: 内置 Python 3.12 运行时（arm64），无需手动安装 Python；Intel 芯片会自动下载匹配版本。Bundled Python 3.12 (arm64); Intel Macs auto-download a matching build.
- **Windows**: 内置嵌入式 Python + 离线依赖包，首次安装无需联网。Bundled embedded Python + offline wheels; first install needs no internet.

## Changelog / 更新日志

### v1.1.0 — 2026-09-29

#### Added / 新增

- Added an eight-section Research Fact Layer with full-source ordered parsing, editable Smart Inbox suggestions, provenance, and explicit chapter grounding controls. / 新增八类科研事实分区，支持全文顺序解析、可编辑的 Smart Inbox 建议、来源追溯和显式章节引用控制。
- Added an all-section, bounded Research Planning Digest so late facts and facts excluded from chapter grounding can still inform logic planning. / 新增覆盖所有分区的有界科研规划摘要，使靠后的事实和未启用正文 Grounding 的事实仍可参与逻辑规划。
- Added full-document literature analysis for PDF, DOCX, TXT, and pasted text, with source-linked evidence and per-chapter use limited to bound literature. / 新增 PDF、DOCX、TXT 和粘贴文本的全文文献分析与来源关联证据；章节写作只取用已绑定文献的证据。
- Literature profile synthesis uses the current research topic and planning digest; relevance ratings are separate from quality assessments, and chapter evidence selection supports Chinese and English terms. / 文献档案合并会使用当前课题与规划摘要；相关性评级与质量评价分开，章节证据选择支持中英文术语。
- Added versioned FormatSpec presets with validated import/export, custom preset management, reviewed model-generated previews, and deterministic Word rendering. / 新增带版本的 FormatSpec 预设、校验后的导入导出、自定义预设管理、需审阅确认的模型预览和确定性 Word 渲染。
- FormatSpec v1 supports grid and three-line tables, page-number starts, per-heading pagination controls, and figure width limits; General Academic and Legacy Compatible provide distinct layouts. / FormatSpec v1 支持网格表与三线表、页码起始值、各级标题分页控制和图片宽度；General Academic 与 Legacy Compatible 使用不同版式。
- Finalized the Chinese and English v1.1.0 user manuals with detailed AI API setup, model-list retrieval, connection testing, a first-paper quick start, and chapter revision with memory updates; updated the Lumielle poster. / 完成中英文 v1.1.0 用户手册最终版，加入详细的 AI API 配置、模型列表获取、模型连接测试、论文快速开始、章节回改与记忆更新流程；更新 Lumielle 海报。
- Added a real, minimal LLM model-connection test with clear guidance for authentication, balance, unavailable models, rate limits, and network errors. / 新增真实的最小 LLM 模型连接测试，并针对认证、余额、模型不可用、限流和网络错误提供清晰提示。

#### Fixed / 修复

- Chapter Memory now processes the complete chapter in ordered chunks before synthesis instead of analyzing only the first 3,000 characters. / 章节记忆现在按顺序分块处理完整章节后再合并，不再只分析开头 3,000 个字符。
- A failed memory rebuild preserves the previous valid chapter memory. / 章节记忆重建失败时保留此前有效记忆。
- Global literature clearing now distinguishes clearing library records from deleting imported source files. / 全局清空文献库时，清除记录与删除已导入原始文件现在明确区分。
- Failed literature chunk analysis does not replace the previously valid profile or evidence store. / 文献分块分析失败时，不替换此前有效的文献画像或证据库。
- Accepted Smart Inbox facts now retain the editable suggested role. / 接受 Smart Inbox 建议后，现在会保存可编辑的用途字段。
- Restored continuous document flow and the original Lumielle poster artwork in both user manuals. / 恢复中英文手册的连续分页排版，并还原 Lumielle 海报原图。
- macOS first-run dependency setup now automatically retries the official Python package index when the preferred mirror is unavailable. / macOS 首次启动安装依赖时，首选镜像不可用会自动切换至官方 Python 软件源。

#### Changed / 变更

- New DeepSeek profiles use `https://api.deepseek.com` and `deepseek-flash`; existing saved profiles are not migrated automatically. The fetched model list remains authoritative for the account. / 新初始化的 DeepSeek 配置使用 `https://api.deepseek.com` 和 `deepseek-flash`；已有配置不会自动迁移，具体可用模型以拉取列表为准。
- Context Sandwich documentation now describes complete-chapter memory construction and compact summaries without promising perfect recall or consistency. / 上下文三明治说明现准确描述完整章节记忆构建与摘要使用，不再承诺绝对记忆或一致性。
- Logic planning uses a bounded digest of persisted facts from every section rather than a fixed head-only prefix; grounding toggles affect chapter retrieval, not planning inclusion. / 逻辑规划使用覆盖所有分区已存事实的有界摘要，不再依赖固定开头前缀；Grounding 开关控制正文检索，不控制规划是否纳入事实。
- Literature relevance is evaluated against the current topic and planning context; source quality is assessed separately, and oversized library reviews use ordered profile batches before hierarchical synthesis. / 文献相关性结合当前课题和规划上下文评价，来源质量单独评估；大型文献库按档案顺序分批审查后再分层合并。
- General Academic and Legacy Compatible now have distinct page, table, caption, and pagination behavior. / General Academic 与 Legacy Compatible 现在具有不同的页面、表格、标题和分页行为。
- Word export formatting is now driven by validated FormatSpec data, while DOCX templates retain their existing content and styles in template-preserving mode. / Word 排版现在由经过校验的 FormatSpec 数据驱动；模板保留模式会保留模板已有内容与样式。

### v1.0.1 — 2026-09-27

#### Added / 新增

- Added one canonical SemVer source in `version.py`; both applications display the version. / 在 `version.py` 建立唯一 SemVer 版本来源，并在两个应用中显示版本号。
- Added focused regression tests and Python 3.12 GitHub Actions CI. / 增加针对核心行为的回归测试与 Python 3.12 GitHub Actions CI。
- Drafts now retain reference snapshots and mappings so citation exports remain stable. / 草稿保存文献快照与映射，保证导出引用稳定。
- Added a structured chart pipeline that validates chart data and renders it with trusted local code. / 增加结构化图表流程，由本地可信代码校验数据并绘图。
- Added ordered chunk parsing for complete Research Foundation documents and complete Manuscript Reverse Engineering. / 增加科研基座全文顺序分块解析，以及完整文稿逆向解析能力。
- Both application sidebars now identify the MIT open-source license, GitHub repository and QQ community. / 中英文应用侧边栏均明确展示 MIT 开源许可、GitHub 仓库和 QQ 交流信息。

#### Changed / 变更

- Chapter generation now receives the global outline, upstream context and downstream written content or boundaries from the Context Sandwich. / 章节生成现在会接收上下文三明治中的全局大纲、上游内容和下游已写内容或边界。
- The saved custom writing-style prompt is interpolated into each chapter-generation prompt. / 用户保存的写作风格提示词现在会实际进入章节生成提示词。
- English word counts use word tokens across generation correction, UI results, batch results, structure review and memory status. / 英文生成纠偏、界面结果、批量结果、结构审查和记忆状态统一按英文单词计数。
- English default prompts initialize inside `prompts.json` without replacing an existing user prompt file. / 英文默认提示词正确初始化在 `prompts.json` 中，且不覆盖已有用户配置。
- Word export now orders and deduplicates references globally, remaps chapter-local citations, and uses saved snapshots with a legacy-binding fallback. / Word 导出按全文顺序统一去重文献并转换章节局部编号；使用草稿快照，旧草稿回退到当前绑定。
- Long Research Foundation documents use ordered chunks; long manuscripts use ordered extraction and bounded consolidation before the outline is replaced. / 长科研基座文档按顺序分块处理；长文稿顺序提取并有界合并，验证后才替换大纲。
- Multi-model consensus uses successful answers only; a failed outline operation leaves the previous outline intact. / 多模型共识只使用成功回答；大纲生成失败时保留原有大纲。
- Literature remains imported when AI rating is unavailable and is marked `Unrated`; failed deep-review blocks are excluded, and an all-failed review preserves the previous report. / AI 评级不可用时仍导入文献并标记为 `Unrated`；失败的深度审查区块不进入报告，全部失败时保留旧报告。
- MIT licensing information is consistent across `LICENSE`, README, manuals and both application interfaces. / `LICENSE`、README、手册和中英文应用界面的许可信息均统一采用 MIT 表述。

#### Fixed / 修复

- Fixed the Context Sandwich display/runtime mismatch so its context constrains the actual chapter-generation request. / 修复上下文三明治仅展示、不参与实际生成的问题。
- Fixed literal `{style_prompt}` text being sent instead of the user's saved style prompt. / 修复把字面量 `{style_prompt}` 发给模型的问题。
- Fixed English word counts being measured as non-whitespace characters. / 修复英文篇幅被按非空白字符统计的问题。
- Fixed English defaults being initialized at the wrong configuration level. / 修复英文默认提示词写入错误配置层级的问题。
- Fixed macOS first-run environment creation by using bundled uv to create a pip-enabled environment because standalone Python omits venv and pip. / 修复 macOS 首次启动环境创建：独立版 Python 未包含 venv 和 pip，因此改用包内 uv 创建带 pip 的虚拟环境。
- Fixed cross-chapter citation numbers pointing to different references by applying one global ordered registry at export. / 修复章节局部引用编号在全文导出时错指文献的问题。
- Empty or known failed LLM responses are rejected before they can be saved as generated prose. / 空响应和已知 LLM 失败结果不会再作为正文保存。
- A failed chapter-memory distillation no longer overwrites the saved draft or prior summary. / 章节记忆提炼失败时，不再覆盖已保存草稿或原摘要。
- Fixed chart execution safety by removing arbitrary execution of LLM-generated Python and requiring validated chart specifications. / 移除任意执行模型生成 Python 的图表路径，改为校验图表规格，修复图表执行安全问题。
- Illustrative charts require explicit permission; negated requests do not authorize invented values. / 示意图必须获得明确授权；否定表达不会被误解为允许编造数据。
- Fixed silent 5,000–6,000-character prefix truncation in Research Foundation parsing. / 修复科研基座静默截断在文档开头 5,000–6,000 字符的问题。
- Fixed silent 4,000-character truncation in Manuscript Reverse Engineering. / 修复文稿逆向解析静默截断在开头 4,000 字符的问题。
- Chinese and English document extraction failures now receive consistent handling. / 统一处理中英文文档提取失败。
- Configuration, API and empty-output failures now cross a checked `LLMOutputError` boundary and stay out of drafts, outlines and reports. / 配置、API 和空输出错误统一经 `LLMOutputError` 检查，不再混入草稿、大纲和报告。
- Failed literature rating no longer appears as a neutral three-star or `Other` result; it is explicitly unavailable and `Unrated`. / 文献评级失败不再显示中性三星或 `Other`，而会明确标记为不可用和 `Unrated`。
- AIGC detection failure now reports unavailable instead of a misleading `0% AI`. / AIGC 检测失败现在显示不可用，不再误报为 `0% AI`。
- Failed logic-review blocks are excluded from normal reports, and an all-failed run preserves the previous report. / 失败的逻辑审查区块不进入正常报告；全部失败时保留既有报告。
- Existing unreadable/corrupted user JSON files are now preserved and further writes to those files are blocked instead of silently replacing them with defaults. / 现有不可读或损坏的用户 JSON 文件会被保留，并阻止后续写入，不再静默替换为默认值。
- Failed model answers no longer affect multi-model consensus, and outline failures no longer discard the prior outline. / 失败的模型回答不会影响多模型共识，大纲生成失败也不会丢弃原大纲。

#### Removed / 移除

- Removed arbitrary execution of LLM-generated Python for chart rendering. / 移除执行模型生成 Python 代码来绘制图表的做法。
- Removed silent fixed-prefix truncation from Research Foundation and Manuscript Reverse Engineering. / 移除科研基座与文稿逆向解析中的静默固定前缀截断。
- Removed README and UI non-commercial restrictions that conflicted with the MIT License. / 移除与 MIT License 冲突的 README 和界面非商业限制。
- No core user-facing workflow was removed in v1.0.1. / v1.0.1 未移除任何核心用户工作流。

### v1.0.0 — 2026-08-26

#### Added / 新增

- Initial public release with Chinese and English desktop packages for macOS and Windows. / 首次公开发布，提供 macOS 与 Windows 中英文桌面安装包。

## 📄 开源许可 / License

本项目基于 [MIT License](LICENSE) 开源。项目源码、许可文本和反馈渠道见 [GitHub 仓库](https://github.com/Lumielle-BlueOMOcean/Lumielle-Academic-Assistant-)。

This project is open source and released under the [MIT License](LICENSE). Find the source code, license, and feedback channels in the [GitHub repository](https://github.com/Lumielle-BlueOMOcean/Lumielle-Academic-Assistant-).

## 📮 交流反馈 / Feedback

- QQ 群聊 / QQ Group: **1029688024**
- 开发者 / Developer: **Lumielle**

---

© 2026 Lumielle · MIT License 开源 / Released under the MIT License.
