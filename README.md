# Lumielle Academic Assistant

> 学术论文写作助手 —— 从课题到终稿，一站式体验
> Academic Paper Writing Assistant — From Research Topic to Final Draft, One-Stop Experience

基于 Streamlit 的本地学术论文写作辅助工具：逻辑大纲生成、正文写作、AIGC 检测与去味、终稿 Word 导出。

A Streamlit-based local academic paper writing assistant: logic outline generation, chapter writing, AIGC detection & de-smelling, and final Word export.

**Current version / 当前版本：v1.0.1 — 2026-09-27**

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

- 🧠 **LLM 配置与多模型交叉讨论** — Multi-model configuration & cross discussion
- 🏛️ **科研基座** — Research base (background, modules)
- 📚 **文献处理** — Literature batch upload, auto parsing, rating & classification
- 🧩 **逻辑链路** — Multi-level logic outline with per-node word count & chart instructions
- ✍️ **正文写作** — Single-chapter & batch writing with word-count auto-correction
- 🛡️ **AIGC 检测与去味** — AI probability detection & adversarial de-smelling
- ⚙️ **提示词配置** — Global style & format prompt control
- 📄 **终稿导出** — Word export with proper layout (fonts, spacing, tables, references)

## 🧠 核心架构：上下文三明治 / Core Architecture: The Context Sandwich

写长论文时，网页端 AI 对话有三个绕不开的痛点：**对话一长就"失忆"、反复修改时前后矛盾、上下文无限膨胀导致成本失控**。微光用「上下文三明治」机制一次解决这三个问题——它不是把整篇论文一股脑塞给模型，而是每次只喂给模型**恰到好处**的一段上下文。

When writing a long paper, web-based AI chat suffers from three unavoidable pain points: **memory loss in long conversations, contradictions when revising earlier chapters, and uncontrollable context bloat**. The Context Sandwich solves all three at once — instead of cramming the whole paper into the model, it feeds the model **exactly the right amount** of context for every single chapter.

### 🥪 三明治的层次 / The Layers

```
┌─────────────────────────────────────────────┐
│  ① 全局逻辑大纲（顶层罗盘）                    │
│     Global logic outline — the compass       │
│     整棵逻辑树实时压缩，始终指向课题全局        │
├─────────────────────────────────────────────┤
│  ② 上游记忆（面包片 · 已写部分）               │
│     Upstream memory — written chapters       │
│     当前章节之前【已写】章节的完整记忆，全量带上 │
│     未写章节只给大纲标题占位，不挤占窗口        │
├─────────────────────────────────────────────┤
│  🎯 当前章节（三明治的核心 = 要生成的内容）      │
│     Current chapter — the meat of the sandwich│
├─────────────────────────────────────────────┤
│  ③ 下游记忆（面包片 · 边界部分）               │
│     Downstream context — boundary only       │
│     已写章节全量提供（防回头修改产生冲突）       │
│     未写章节只给轻量大纲边界（防剧透 / 越界）    │
└─────────────────────────────────────────────┘
```

### 🎯 它解决了什么 / What It Solves

| 痛点 Pain Point | 传统对话 Traditional Chat | 上下文三明治 Context Sandwich |
|---|---|---|
| 🧠 **记忆幻觉** Memory loss | 对话一长，AI 忘记前文，甚至编造前文 | 上游已写章节**全量记忆**实时加载，绝不遗忘 |
| 🔄 **修改冲突** Revision conflicts | 改中间章节，跟后面已写的内容打架 | 下游已写章节**全量提供**，修改时强制保持一致 |
| 🚀 **上下文膨胀** Context bloat | 每次修改都把全文发回去，越改越贵 | 只给当前章需要的窗口，**预算自动截断**，成本可控 |
| 🔮 **越界跑题** Going off-topic | 模型自由发挥，写到别处去 | 全局大纲 + 下游边界双保险，始终紧扣课题 |

### ⚙️ 细节设计 / Design Details

- **双向一致性**：向上记住"已写过什么"，向下防止"和后面冲突"——改中间任何一章，前后文都自动对齐。/ Bidirectional consistency: remembers what was written above, prevents conflicts below — revise any chapter and the whole paper stays coherent.
- **智能预算**：上下文超长时按「离当前节点远近」取舍，已写记忆优先保留，自动截断并注明。/ Smart budget: when context exceeds the limit, it keeps memories nearest to the current chapter, truncating gracefully with a clear notice.
- **防剧透机制**：下游未写章节只给标题边界，防止模型提前"偷看"后面的内容、写成流水账。/ Anti-spoiler: unwritten downstream chapters are exposed as titles only, so the model can't peek ahead and spoil the narrative.

这个设计让 AI 只专注写好**每一章**，章节之间的衔接由工具自动管理——这也是微光最核心的工程创新。

This design lets the AI focus on writing **one chapter at a time**, while the tool manages the connections between chapters automatically — the core engineering innovation of Lumielle Academic Assistant.

## 🚀 快速开始 / Quick Start

1. 解压对应平台的版本文件夹 / Unzip the folder for your platform
2. macOS：双击 `Launch.command`（中文版 `一键启动.command`）/ Windows：双击 `Launch.bat`（中文版 `一键启动.bat`）
3. 首次启动自动安装依赖（约 1-3 分钟）/ First launch auto-installs dependencies (~1-3 min)
4. 在「LLM Configuration」填入 API Key，拉取模型 / Enter API Key and fetch models
5. 侧边栏填写研究课题与预期字数，开始写作 / Fill in topic & word count, start writing

## 🖥️ 平台说明 / Platform Notes

- **macOS**: 内置 Python 3.12 运行时（arm64），无需手动安装 Python；Intel 芯片会自动下载匹配版本。Bundled Python 3.12 (arm64); Intel Macs auto-download a matching build.
- **Windows**: 内置嵌入式 Python + 离线依赖包，首次安装无需联网。Bundled embedded Python + offline wheels; first install needs no internet.

## Changelog / 更新日志

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
