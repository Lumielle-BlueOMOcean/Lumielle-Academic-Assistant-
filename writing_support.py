"""Pure writing helpers shared by the English and Chinese Streamlit apps."""

import re


ENGLISH_DEFAULT_PROMPTS = {'style_prompt': '[Role]\n'
                 'You are an academic paper writing engine. The following rules are the '
                 'highest-priority constraints and must never be violated.\n'
                 '\n'
                 'I. GENERAL STYLE\n'
                 '1. Academically rigorous, objective and restrained, yet natural and fluent. No '
                 'AI-template tone.\n'
                 '2. Each chapter must have a clear argumentative thread with logical progression '
                 'between paragraphs. Avoid fragmented piling of statements.\n'
                 '\n'
                 'II. SENTENCE LEVEL\n'
                 '1. Vary sentence length: alternate long sentences (30+ words) with short ones '
                 '(under 15 words). No more than three consecutive sentences of the same length.\n'
                 '2. Use dashes, parenthetical insertions and hedges where natural, mimicking '
                 'human academic writing.\n'
                 '3. Do not open consecutive paragraphs with the same pattern (e.g., repeating '
                 '"First... Second... Finally...").\n'
                 '\n'
                 'III. PARAGRAPH STRUCTURE\n'
                 '1. Keep paragraphs between 80-200 words; a single paragraph should not exceed '
                 '250 words.\n'
                 '2. Avoid identical structures across adjacent paragraphs. Do not chain multiple '
                 'paragraphs of "claim + example + mini-conclusion".\n'
                 '3. Connect claims and evidence with explicit logical relations, not filler '
                 'connectors.\n'
                 '\n'
                 'IV. FORBIDDEN EXPRESSIONS (HARD)\n'
                 '1. Clichéd connectors: in conclusion, in summary, to sum up, it is worth noting '
                 'that, not surprisingly, it is obvious that, as we all know, etc.\n'
                 '2. AI buzzwords and empty phrases: leverage, granularity, paradigm-shifting, '
                 'game-changer, seamless, vague "robust", in today\'s fast-paced world, plays an '
                 'important role, etc.\n'
                 '3. Mechanical parallelism: do not deliberately craft parallel or antithetical '
                 'sentences.\n'
                 '\n'
                 'V. EVIDENCE\n'
                 '1. Every claim must be supported by data, literature, case evidence or logical '
                 'derivation. No hollow assertions.\n'
                 '2. When citing specific numbers, state the source or explicitly mark them as '
                 'estimates.\n'
                 "3. When referring to others' views, cite with [1][2] bracket numbers.\n"
                 '\n'
                 'VI. TABLE OUTPUT (HARD)\n'
                 '1. When the text needs to present tabular data (comparison, statistics, lists, '
                 'workflows), use standard Markdown tables.\n'
                 '2. Standard format: first row = header (columns separated by |, with leading and '
                 'trailing |); second row = separator (|---|, same column count); following rows = '
                 'data.\n'
                 '3. No Markdown nesting inside cells (no **, *, [], links).\n'
                 '4. No full-width vertical bars.\n'
                 '5. Leave one blank line before and after a table.\n'
                 '6. Outside tables, no Markdown at all (#, *, -, >, --- are forbidden).\n'
                 '\n'
                 'VII. OUTPUT FORMAT\n'
                 '1. Plain text only; no heading markers, no list bullets.\n'
                 '2. For subheadings use plain-text numbering such as "1.", "1.1", "(1)".\n'
                 '3. One paragraph per line; separate paragraphs with a single newline; no blank '
                 'lines between paragraphs.',
 'format_prompt': 'I. PAGE SETUP\n'
                  '1. Paper: A4; top/bottom margins 2.54cm; left/right margins 3.17cm.\n'
                  '2. Page number: bottom center, 10.5pt.\n'
                  '\n'
                  'II. PARAGRAPH FORMAT\n'
                  '1. First-line indent of 2 characters for body text.\n'
                  '2. Line spacing 1.5.\n'
                  '3. 6pt space before and after paragraphs.\n'
                  '4. Justified alignment for body text.\n'
                  '\n'
                  'III. FONT SPECIFICATIONS\n'
                  '1. Body: Times New Roman, 12pt.\n'
                  '2. Heading 1: Bold, 16pt, centered.\n'
                  '3. Heading 2: Bold, 14pt, left-aligned.\n'
                  '4. Heading 3: Bold, 12pt, left-aligned.\n'
                  '5. Table/figure captions: 10.5pt, bold.\n'
                  '6. One blank line between headings and body text.\n'
                  '\n'
                  'IV. TABLE SPECIFICATIONS\n'
                  '1. Use the three-line (booktabs) style: top rule 1.5pt, bottom rule 1.5pt, '
                  'header rule 0.75pt.\n'
                  '2. Table captions centered above the table, e.g., "Table 1-1 Caption Text".\n'
                  '3. Text inside tables: 10.5pt.\n'
                  '\n'
                  'V. FIGURE SPECIFICATIONS\n'
                  '1. Figure captions centered below the figure, e.g., "Figure 1-1 Caption Text".\n'
                  '2. Figure width must not exceed the text width.\n'
                  '3. Keep one blank line between figures and body text.\n'
                  '\n'
                  'VI. CHAPTER NUMBERING\n'
                  '1. Level-1 headings: "Chapter 1" or "1, 2, 3".\n'
                  '2. Level-2 headings: "1.1, 1.2" or "1.1.1".\n'
                  '3. The numbering system must be consistent throughout the document; never mix '
                  'styles.',
 'aigc_rewrite_prompt': '[Task]\n'
                        'Rewrite the following academic text in a more human, natural academic '
                        'style. Remove the mechanical feel of LLM generation. The rewrite must '
                        'preserve all content.\n'
                        '\n'
                        'I. CONTENT FIDELITY (HARD)\n'
                        '1. Keep all arguments, evidence, data and conclusions. Do not add or '
                        'remove substantive content.\n'
                        '2. Keep citation numbers [1][2] exactly as they are.\n'
                        '3. Do not arbitrarily replace technical terms.\n'
                        '\n'
                        'II. SYNTACTIC RESTRUCTURING (HARD)\n'
                        '1. Use passive constructions liberally (e.g., "The data were validated '
                        'before..." instead of "We validated the data...").\n'
                        '2. Nominalize verbs where natural (e.g., "an analysis of X was conducted" '
                        'instead of "we analyzed X").\n'
                        '3. Change the core sentence structure. Do not merely swap words while '
                        'keeping the original skeleton.\n'
                        '4. Deliberately break neat parallel and symmetrical structures.\n'
                        '5. Vary sentence length; allow natural pauses, insertions and dashes.\n'
                        '\n'
                        'III. FORBIDDEN EXPRESSIONS (HARD)\n'
                        'Avoid: in conclusion, in summary, obviously, moreover, additionally, '
                        'crucially, it goes without saying, it is worth noting that, it is not '
                        'hard to see that, through the analysis of, with the deepening of, in the '
                        'context of, plays an important role.\n'
                        '\n'
                        'IV. LEXICAL DIVERSITY\n'
                        '1. Do not express the same concept with identical sentence patterns '
                        'within one paragraph.\n'
                        '2. Vary connectors; avoid relying on only one or two types throughout.\n'
                        '\n'
                        'V. OUTPUT FORMAT\n'
                        '1. Plain text only.\n'
                        '2. No Markdown markers (#, *, -, >, ---).\n'
                        '3. Exception: if the original contains tables, keep them as standard '
                        'Markdown tables.\n'
                        '4. Output only the rewritten text. No prefixes, explanations or '
                        'commentary.',
 'aigc_detect_prompt': '[Task]\n'
                       'You are an AIGC text detection expert. Determine whether the following '
                       'text was generated by AI.\n'
                       '\n'
                       'I. OUTPUT REQUIREMENTS (HARD)\n'
                       '1. Output ONLY one JSON object with this exact format:\n'
                       '{"ai_probability": integer from 0 to 100, "judgment": "AI generated" or '
                       '"Human written" or "Likely mixed", "reasons": "reasoning, under 50 '
                       'words"}\n'
                       '2. ai_probability: 0-100 integer; higher means more likely AI-generated.\n'
                       '3. judgment: exactly one of the three specified values.\n'
                       '4. reasons: concise, 1-2 key pieces of evidence.\n'
                       '5. Output nothing outside the JSON: no code fences, no explanations, no '
                       'thinking process, no prefix/suffix text.\n'
                       '\n'
                       'II. JUDGMENT CRITERIA (reference)\n'
                       '1. Are sentences too uniform and heavily parallel?\n'
                       '2. Is there overuse of template connectors (in conclusion, first/second, '
                       'moreover)?\n'
                       '3. Is the expression hollow, lacking concrete evidence?\n'
                       '4. Is the vocabulary mechanically repetitive?\n'
                       '\n'
                       'III. INPUT\n'
                       'Text to detect: {text}'}


def apply_english_prompt_defaults(default_files):
    """Apply English prompt values to the prompts.json default object only."""
    prompts = default_files.get("prompts.json")
    if not isinstance(prompts, dict):
        raise TypeError('default_files["prompts.json"] must be a dictionary')
    prompts.update(ENGLISH_DEFAULT_PROMPTS)


def count_english_words(text):
    """Count English word tokens, keeping contractions, hyphenated terms, and numbers together."""
    if not text:
        return 0
    pattern = re.compile(
        r"\b(?:\d+(?:[.,]\d+)*|[\w]+(?:['’][\w]+)*(?:-[\w]+(?:['’][\w]+)*)*)\b",
        re.UNICODE,
    )
    return len(pattern.findall(str(text)))


def count_chinese_chars(text):
    """Keep the Chinese app's existing non-whitespace character count behavior."""
    if not text:
        return 0
    return len(re.sub(r"\s", "", text))


def build_chapter_prompt(sandwich, prompts, reference_literatures, *, locale, correction_note=None):
    """Assemble the actual chapter-generation prompt for one supported locale."""
    if locale not in {"en", "zh"}:
        raise ValueError(f"Unsupported prompt locale: {locale}")

    node_wc = sandwich.get("current_word_count", 0)
    style_prompt = prompts.get("style_prompt", "") or (
        "学术严谨、客观陈述、逻辑清晰、用词准确。"
        if locale == "zh"
        else "Academically rigorous, objective, logically clear, and precise in wording."
    )
    reference_literatures = reference_literatures or []

    if locale == "zh":
        wc_constraint = (
            f"本章目标字数：{node_wc}字（请尽量贴合此篇幅，正负偏差允许在50%以内）。"
            if node_wc
            else "本章未指定字数，请依据全局字数规划合理控制篇幅。"
        )
        if reference_literatures:
            ref_lines = []
            for index, literature in enumerate(reference_literatures, 1):
                title = literature.get("title", "未命名")
                findings = str(literature.get("analysis", {}).get("key_findings", ""))[:200]
                ref_lines.append(f"[{index}] {title} —— 核心发现：{findings}")
            ref_section = (
                "【本章可引用文献（仅限以下文献，禁止引用任何未列出的来源）】\n"
                + "\n".join(ref_lines)
                + "\n\n【引用规则】\n"
                "1. 正文中所有引用外部文献的观点、数据或结论，必须在句末用 [编号] 形式标注，如 [1][2]。\n"
                "2. 只能引用上方列出的文献，编号必须与之对应，禁止编造文献、数据与结论。\n"
                "3. 若某数据/结论无对应文献支撑，则必须明确写出【数据来源：本章实验/估算】或直接省略，严禁虚构。"
            )
        else:
            ref_section = "【本章未绑定参考文献】禁止编造任何文献引用。涉及他人观点、数据时必须如实标注来源或注明无法核实。"
        sections = [
            f"【全局研究课题】\n{sandwich.get('global_topic', '')}",
            f"【预期总字数】\n{sandwich.get('target_word_count', 5000)}字",
            f"【全局逻辑大纲】\n{sandwich.get('global_outline', '')}",
            "【上游已写记忆 / 未写章节提纲】\n"
            f"{sandwich.get('upstream', '')}\n"
            "请利用上游已写内容减少重复论述，并保持此前已经确立的事实、概念和术语一致。",
            f"【当前章节】\n《{sandwich.get('current_title', '')}》（内容约束：{sandwich.get('current_desc', '')}）\n{wc_constraint}",
            "【下游已写内容 / 未写章节边界】\n"
            f"{sandwich.get('downstream', '')}\n"
            "下游已写内容用于保持一致并避免矛盾或重复；未写章节只作为写作边界，不要把后续章节的内容提前写入本章。",
            ref_section,
            f"【风格要求】\n{style_prompt}",
            "【排版格式硬性要求】\n"
            "1. 普通正文段落为纯文本，禁止使用任何 Markdown 标记（禁止 #、##、### 标题、**加粗**、*斜体*、- 列表、> 引用、--- 分隔线）。\n"
            "2. 需要小标题时，使用纯文本编号形式，如「一、」「1.」「（1）」等，不要加任何符号装饰。\n"
            "3. 每段独立成行，段与段之间用单个换行分隔，不要空行。\n"
            "4. 若正文确实需要展示表格数据，必须使用标准 Markdown 表格格式；单元格内禁止嵌套 Markdown，禁止使用中文全角竖线｜。\n"
            "5. 表格前后各空一行与正文分隔。",
        ]
        if correction_note:
            sections.append(
                "【篇幅纠偏反馈】\n"
                f"{correction_note}\n请据此调整篇幅，其他内容可适当保留。"
            )
        return "\n\n".join(sections)

    wc_constraint = (
        f"This chapter's target length: {node_wc} words (match it as closely as possible; a +/-50% deviation is acceptable)."
        if node_wc
        else "No target length is assigned to this chapter; control the length according to the global word plan."
    )
    if reference_literatures:
        ref_lines = []
        for index, literature in enumerate(reference_literatures, 1):
            title = literature.get("title", "Untitled")
            findings = str(literature.get("analysis", {}).get("key_findings", ""))[:200]
            ref_lines.append(f"[{index}] {title} - Key findings: {findings}")
        ref_section = (
            "[CITABLE REFERENCES FOR THIS CHAPTER - only the sources below may be cited]\n"
            + "\n".join(ref_lines)
            + "\n\n[CITATION RULES]\n"
            "1. Mark each claim, datum, or conclusion from external literature with [n], e.g. [1][2].\n"
            "2. Cite only the references listed above and do not fabricate references, data, or conclusions.\n"
            "3. If a claim lacks a supporting reference, identify it as an experiment/estimate or omit it."
        )
    else:
        ref_section = (
            "[NO REFERENCES BOUND TO THIS CHAPTER] Do not fabricate citations. When discussing others' views or data, "
            "state the source truthfully or note that it cannot be verified."
        )
    sections = [
        f"[GLOBAL RESEARCH TOPIC]\n{sandwich.get('global_topic', '')}",
        f"[TARGET TOTAL WORD COUNT]\n{sandwich.get('target_word_count', 5000)} words",
        f"[GLOBAL LOGIC OUTLINE]\n{sandwich.get('global_outline', '')}",
        "[UPSTREAM WRITTEN MEMORY / OUTLINE]\n"
        f"{sandwich.get('upstream', '')}\n"
        "Use upstream written material to avoid repeating earlier points and to keep established facts, concepts, and terminology consistent.",
        f"[CURRENT CHAPTER]\n{sandwich.get('current_title', '')} (content constraints: {sandwich.get('current_desc', '')})\n{wc_constraint}",
        "[DOWNSTREAM WRITTEN CONTENT / BOUNDARY]\n"
        f"{sandwich.get('downstream', '')}\n"
        "Use downstream written material to stay consistent and avoid contradictions or repetition. Treat unwritten downstream sections only as boundaries; do not draft their planned content here.",
        ref_section,
        f"[STYLE REQUIREMENTS]\n{style_prompt}",
        "[FORMATTING REQUIREMENTS]\n"
        "1. Body paragraphs must be plain text. Do not use Markdown headings, bold, italics, lists, quotes, or horizontal rules.\n"
        "2. Use plain-text numbering for subheadings, such as 1., 1.1, or (1).\n"
        "3. Put each paragraph on its own line, with a single newline between paragraphs and no blank lines.\n"
        "4. If a table is necessary, use a standard Markdown table with no Markdown nested inside cells.\n"
        "5. Leave one blank line before and after tables.",
    ]
    if correction_note:
        sections.append(
            "[LENGTH CORRECTION FEEDBACK]\n"
            f"{correction_note}\nPlease adjust the length and retain relevant content."
        )
    return "\n\n".join(sections)
