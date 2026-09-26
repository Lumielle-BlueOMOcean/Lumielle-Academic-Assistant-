# Lumielle Academic Assistant

> 学术论文写作助手 —— 从课题到终稿，一站式体验
> Academic Paper Writing Assistant — From Research Topic to Final Draft, One-Stop Experience

基于 Streamlit 的本地学术论文写作辅助工具：逻辑大纲生成、正文写作、AIGC 检测与去味、终稿 Word 导出。

A Streamlit-based local academic paper writing assistant: logic outline generation, chapter writing, AIGC detection & de-smelling, and final Word export.

**Current development version / 当前开发版本：v1.0.1 (Unreleased / 未发布)**

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

### v1.0.1 — Unreleased

#### Fixed / 修复

- Chapter-generation prompts now include the global outline, upstream context, and downstream written content or boundaries.
- Custom writing-style prompts are interpolated into chapter-generation prompts.
- English chapter word counts now count word tokens, including generation correction, UI results, batch results, and structure review.
- English default prompts now initialize under `prompts.json`.
- Word exports now remap chapter-local citations through one deduplicated global reference order, preserving each draft's saved reference mapping and falling back to current bindings for legacy drafts.
- Empty responses and known LLM configuration/API failure messages are rejected before saving chapter text or memory; a failed memory distillation keeps the saved draft and prior summary.
- Batch chapter generation now reports per-chapter failures and continues with the remaining chapters.
- English memory status now reports word counts instead of character counts.
- Replaced executable LLM-generated chart scripts with validated bar, line, scatter, and pie chart data rendered by trusted local code.
- Chart requests without usable values now ask the user for data; explicitly illustrative charts are labeled as illustrative.
- 科研基座不再只发送文档开头 5,000–6,000 个字符，而是按顺序分块解析完整提取文本；任一分块重试后仍失败时，不保存部分模块。 / Research Foundation imports now parse the complete extracted document in ordered chunks instead of sending only its first 5,000–6,000 characters; if any part still fails after one retry, no partial modules are saved.
- 科研基座和文献导入会统一识别中英文文档提取错误。 / Research Foundation and literature imports consistently recognize document extraction errors in both locales.
- LLM configuration, API, and empty-output failures now cross one checked boundary as `LLMOutputError`; feature handlers keep them out of generated text, saved outlines, and review reports.
- 文稿逆向解析现在处理完整提取文本；长文稿按原顺序分块并合并，验证完成后才替换逻辑链路。 / Manuscript Reverse Engineering now analyzes the complete extracted text; long manuscripts are chunked in source order and consolidated before replacing the Logic Chain.
- Literature files are still imported when AI rating fails, with rating `0`, category `Unrated`, and `analysis_status: unavailable` instead of a neutral three-star result.
- AIGC detection failures now show an unavailable result instead of `0%`; failed logic-review blocks are excluded from saved reports, and an all-failed run preserves the prior report.
- Multi-model discussion keeps successful answers when another model fails, and consensus uses only successful answers; outline failures leave the existing outline untouched.

#### Changed / 变更

- Added `version.py` as the canonical SemVer version source and display the version in both app sidebars.
- Added focused `unittest` coverage and a Python 3.12 GitHub Actions CI workflow.

### v1.0.0 — 2026-08-26

#### Added / 新增

- Initial published release with Chinese and English packages for macOS and Windows.

## 📄 开源许可 / License

本项目基于 [MIT License](LICENSE) 开源，仅供个人学习使用，禁止倒卖或用于商业牟利。

Licensed under the [MIT License](LICENSE). For personal learning use only. Resale for profit is prohibited.

## 📮 交流反馈 / Feedback

- QQ 群聊 / QQ Group: **1029688024**
- 开发者 / Developer: **Lumielle**

---

© 2026 Lumielle. All rights reserved.
