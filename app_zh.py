# -*- coding: utf-8 -*-
"""
Lumielle Academic Assistant —— 学术论文写作助手
七大板块 + 全局侧边栏控制（研究课题 / 预期字数）
数据全部持久化于 data/ 目录下的 JSON 文件。
"""

# -*- coding: utf-8 -*-
# ============================================================
#  Lumielle Academic Assistant
#  开发者：Lumielle
#  仅供个人学习使用，禁止倒卖或用于商业牟利。
# ============================================================

import streamlit as st
from version import __version__
from document_support import is_document_parse_error, parse_document_in_chunks
from chart_support import chart_result_message, generate_chart_image
from writing_support import (
    LLMOutputError,
    build_chapter_prompt as assemble_chapter_prompt,
    collect_global_reference_registry,
    count_chinese_chars,
    record_generated_draft,
    reference_ids_for_node,
    remap_local_citations,
    require_valid_llm_output,
)
import json
import os
import time
import uuid
import requests
import PyPDF2
import docx
import re
import io
import platform
import subprocess
import pandas as pd
import numpy as np
import xml.etree.ElementTree as ET
from openai import OpenAI
from docx.shared import Pt, RGBColor, Inches
from docx.oxml.ns import qn
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import Image as _PILImage
# 放宽 PIL 解压炸弹检查上限（默认 178,956,970 像素）。本地单机工具允许最大 5 亿像素，
# 避免合法大图/长图被误伤；真正的超大异常图会在图表生成校验环节被拦截。
_PILImage.MAX_IMAGE_PIXELS = 500_000_000

# ============================================================
# 1. 基础环境与目录构建
# ============================================================
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
TEMPLATE_DIR = os.path.join(DATA_DIR, "templates")
RAW_DIR = os.path.join(DATA_DIR, "raw_files")
CHART_DIR = os.path.join(DATA_DIR, "charts")
IMAGE_DIR = os.path.join(DATA_DIR, "images")
MODEL_DIR = os.path.join(BASE_DIR, "models", "aigc_detector")

for directory in [DATA_DIR, TEMPLATE_DIR, RAW_DIR, CHART_DIR, IMAGE_DIR, MODEL_DIR]:
    os.makedirs(directory, exist_ok=True)

default_files = {
    "llm_profiles.json": [{
        "id": "default", "name": "DeepSeek 默认配置",
        "base_url": "https://api.deepseek.com/v1", "api_key": "", "model": "deepseek-chat"
    }],
    "literatures.json": [],
    "logic_tree.json": [],
    "drafts.json": {},
    "draft_reference_maps.json": {},
    "drafts_summary.json": {},
    "drafts_charts.json": {},
    "drafts_images.json": {},
    "research_base.json": {
        "content": {"modules": []},
        "background": {"modules": []},
        "requirements": {"modules": []}
    },
    "gap_report.json": {"content": ""},
    "literature_quality.json": {"content": ""},
    "full_logic_review.json": {"content": ""},
    "prompts.json": {
        "global_topic": "",
        "target_word_count": 5000,
        "style_prompt": "【任务定位】\n你是学术论文正文撰写引擎。以下规则为最高优先级约束，任何情况下不得违反。\n一、文风总则\n1. 学术严谨、客观克制，但行文自然流畅，禁止AI模板腔。\n2. 每章应有清晰的论证主线，段落之间逻辑递进，避免碎片化堆砌。\n二、句式要求\n1. 长短句交错：长句（30字以上）与短句（15字以内）穿插使用，禁止连续三个以上同长度句子。\n2. 允许并鼓励使用破折号、插入语、括号补充说明，模拟人类写作习惯。\n3. 段落开头禁止重复同一句式（如连续使用\"首先……其次……最后……\"）。\n三、段落结构\n1. 段落长度控制在80-200字，单段不超过250字。\n2. 相邻段落结构不得雷同，禁止连续多段均为\"观点+举例+小结\"模板。\n3. 论点与论据之间用逻辑关系连接，禁止用套话连接。\n四、禁用表达（硬性）\n以下词汇与句式禁止出现：\n1. 套路连接词：综上所述、总之、总而言之、首先、其次、再次、最后、此外、另外、值得注意的是、不难发现、由此可见、毋庸置疑、众所周知、显而易见。\n2. AI高频词：赋能、抓手、闭环、深耕、维度、层面、场景化、落地、痛点、破局、助力、护航。\n3. 空话套话：随着……的发展、在当今……背景下、具有重要意义、发挥着重要作用。\n4. 机械对仗：禁止刻意使用排比句、对偶句。\n五、论据要求\n1. 每个论断必须给出依据：数据、文献、案例或逻辑推演，禁止空泛断言。\n2. 使用具体数字时必须标明来源或注明估算依据。\n3. 涉及他人观点必须标注引用编号[1][2]。\n六、表格输出规范（硬性）\n1. 当正文需要展示表格数据（对比、统计、清单、流程）时，必须使用标准Markdown表格。\n2. 标准格式：第一行为表头（各列以|分隔，行首行尾均有|）；第二行为分隔行（|---|，列数与表头一致）；后续为数据行。\n3. 单元格内禁止嵌套任何Markdown标记（禁止**、*、[]、链接等）。\n4. 禁止使用中文全角竖线｜。\n5. 表格前后各空一行与正文分隔。\n6. 除表格外，正文禁止使用任何Markdown标记（#、*、-、>、---等一律禁止）。\n七、输出格式\n1. 纯文本输出，无标题标记、无列表符号。\n2. 需要小标题时用「一、」「1.」「（1）」等纯文本编号。\n3. 每段独立成行，段间单个换行分隔，不要空行。",
        "format_prompt": "一、页面设置\n1. 纸张：A4，上下页边距2.54cm，左右页边距3.17cm。\n2. 页码：页面底端居中，宋体五号。\n二、段落格式\n1. 正文首行缩进2字符。\n2. 行距1.5倍。\n3. 段前段后各6磅。\n4. 正文两端对齐。\n三、字体规范\n1. 正文：宋体，小四（12pt）。\n2. 一级标题：黑体，三号（16pt），居中，加粗。\n3. 二级标题：黑体，四号（14pt），左对齐，加粗。\n4. 三级标题：黑体，小四（12pt），左对齐，加粗。\n5. 图表标题：宋体，五号（10.5pt），加粗。\n6. 标题与正文之间空一行。\n四、表格规范\n1. 表格采用三线表：顶线1.5磅、底线1.5磅、表头下细线0.75磅。\n2. 表标题居中置于表上方，格式如\"表1-1 标题文字\"。\n3. 表内文字：宋体五号（10.5pt）。\n五、图片规范\n1. 图片标题居中置于图下方，格式如\"图1-1 标题文字\"。\n2. 图片宽度不超过正文宽度。\n3. 图片与正文之间留一行间距。\n六、章节编号\n1. 一级标题编号：第X章 或 1、2、3。\n2. 二级标题编号：1.1、1.2 或 一、（一）。\n3. 全文编号体系必须统一，禁止混用。",
        "aigc_rewrite_prompt": "【任务】\n请将以下学术文本重写为人类风格学术写作，消除大模型机械感。重写必须保持内容完整。\n一、内容保真（硬性）\n1. 保留原文全部论点、论据、数据、结论，禁止增删实质内容。\n2. 引用编号[1][2]必须原样保留。\n3. 专业术语不得随意替换。\n二、句式改造（硬性）\n1. 大量使用被动语态（如\"数据被验证后……\"代替\"我们验证了数据……\"）。\n2. 将动词名词化（如\"对……进行分析\"代替\"分析……\"）。\n3. 改变原句主干结构，禁止仅保留原句骨架换词。\n4. 强制破坏工整排比与对称句。\n5. 长短句交错，允许自然断句、插入语、破折号。\n三、禁用表达（硬性）\n禁止出现：综上所述、总之、总而言之、显然、此外、另外、至关重要、不言而喻、值得注意的是、不难看出、通过……分析、随着……的深入、在……背景下、具有重要意义。\n四、词汇多样性\n1. 同一概念在一段内不得以相同句式重复表达。\n2. 连接词使用多样化，避免全文只用一两种。\n五、输出格式\n1. 输出纯文本。\n2. 禁止任何Markdown标记（#、*、-、>、---）。\n3. 表格数据除外：若原文含表格，须保持标准Markdown表格格式。\n4. 直接输出重写后正文，不要任何前缀、说明、解释。",
        "aigc_detect_prompt": "【任务】\n你是AIGC文本检测专家。判断以下文本是否由AI生成。\n一、输出要求（硬性）\n1. 严格只输出一个JSON对象，格式如下：\n{\"ai_probability\": 0到100的整数, \"judgment\": \"AI生成\"或\"人类撰写\"或\"疑似混合\", \"reasons\": \"判断理由，不超过50字\"}\n2. ai_probability：0-100整数，数值越大越可能AI生成。\n3. judgment：三选一，必须使用指定值。\n4. reasons：简明扼要，给出1-2条关键依据。\n5. 禁止输出JSON以外的任何内容：禁止代码块标记、禁止解释、禁止思考过程、禁止前后缀文字。\n二、判断要点（参考）\n1. 句式是否过于工整、排比密集。\n2. 是否滥用套路连接词（综上所述、首先其次、此外）。\n3. 表达是否空洞、缺乏具体论据。\n4. 用词是否机械重复。\n三、输入\n待检测文本：{text}",
        "aigc_detect_engine": "llm"
    }
}

def load_json_file(filename, default_val):
    """安全读取本地 JSON 文件，带损坏回滚机制"""
    path = os.path.join(DATA_DIR, filename)
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(default_val, f, ensure_ascii=False, indent=2)
    return default_val

def save_json_file(filename, data):
    """安全写入本地 JSON 文件"""
    path = os.path.join(DATA_DIR, filename)
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        st.error(f"写入异常 ({filename}): {e}")
        return False

for fname, dval in default_files.items():
    load_json_file(fname, dval)

# ============================================================
# 2. 本地 AIGC 检测引擎
# ============================================================
@st.cache_resource
def load_local_aigc_detector():
    if not os.path.exists(MODEL_DIR) or not os.listdir(MODEL_DIR):
        return None
    try:
        from transformers import pipeline
        detector = pipeline("text-classification", model=MODEL_DIR, device=-1)
        return detector
    except Exception as e:
        st.sidebar.error(f"本地模型加载失败: {str(e)}")
        return None

def detect_aigc_prob(text: str) -> dict:
    """对文本进行 AI 概率预测，内置截断与标签兼容"""
    if not text or len(text.strip()) < 10:
        return {"score": 0.0, "label": "Too short", "raw": "文本过短，无法检测"}

    detector = load_local_aigc_detector()
    if detector is None:
        return {"score": 0.0, "label": "No Model", "raw": "未检测到本地模型，请按指南进行下载配置。"}

    try:
        res = detector(text, truncation=True, max_length=512)
        if isinstance(res, list) and len(res) > 0:
            item = res[0]
            label = str(item.get('label', '')).upper()
            score = float(item.get('score', 0.0))
            if "AI" in label or "FAKE" in label or "LABEL_1" in label or label == "1":
                final_prob = score
            elif "HUMAN" in label or "LABEL_0" in label or label == "0":
                final_prob = 1.0 - score
            else:
                final_prob = score if score > 0.5 else (1.0 - score)
            return {
                "score": round(final_prob * 100, 2),
                "label": label,
                "raw": f"模型原始判定为 [{label}], 原始置信度 {round(score*100, 2)}%"
            }
    except Exception as e:
        return {"score": 0.0, "label": "Error", "raw": f"推理异常: {str(e)}"}

    return {"score": 0.0, "label": "Unknown", "raw": "未能解析推理结果"}

def llm_detect_aigc(text: str, profile_id=None) -> dict:
    """通过 LLM 提示词进行 AIGC 检测，解析返回的 JSON 概率。
    profile_id 指定专用检测 LLM；为 None 时使用当前激活配置。"""
    prompts = load_json_file("prompts.json", {})
    # 若未显式指定，读取持久化的检测专用配置
    if profile_id is None:
        profile_id = prompts.get("aigc_detect_llm_id", None)
    detect_prompt = prompts.get("aigc_detect_prompt", "")
    if not detect_prompt.strip():
        detect_prompt = "你是AIGC文本检测专家。请判断以下文本是否由AI生成。严格只输出JSON，格式为：{\"ai_probability\": 0到100的整数, \"judgment\": \"AI生成/人类撰写/疑似混合\", \"reasons\": \"简要判断理由\"}。不要输出任何其他内容。\n\n待检测文本：\n"
    # 拼接待检测文本：优先 {text} 占位符，其次提示词含「待检测文本」字样则直接追加，否则补全标准后缀
    if "{text}" in detect_prompt:
        prompt = detect_prompt.replace("{text}", text)
    elif "待检测文本" in detect_prompt:
        prompt = detect_prompt + text
    else:
        prompt = detect_prompt + "\n\n待检测文本：\n" + text
    res = dispatch_llm_call(prompt, system_prompt="你是严格的AIGC文本检测专家，只输出JSON。", max_tokens=400, temp=0.2, profile_id=profile_id)
    try:
        parsed = re.search(r'(\{.*\})', res, re.DOTALL)
        if parsed:
            data = json.loads(parsed.group(1))
            prob = float(data.get("ai_probability", 50))
            judgment = str(data.get("judgment", "未知"))
            reasons = str(data.get("reasons", ""))
            return {
                "score": round(min(max(prob, 0), 100), 2),
                "label": judgment,
                "raw": f"LLM 判定：{judgment}（AI概率 {prob:.0f}%）。理由：{reasons}"
            }
    except Exception as e:
        return {"score": 0.0, "label": "Error", "raw": f"LLM 检测解析失败: {e}"}
    return {"score": 0.0, "label": "Unknown", "raw": f"未能解析 LLM 检测结果：{res[:200]}"}


def get_detect_engine():
    """读取当前 AIGC 检测引擎设置"""
    prompts = load_json_file("prompts.json", {})
    return prompts.get("aigc_detect_engine", "llm")


def set_detect_engine(engine):
    """保存 AIGC 检测引擎设置"""
    prompts = load_json_file("prompts.json", {})
    prompts["aigc_detect_engine"] = engine
    save_json_file("prompts.json", prompts)


def get_detect_llm_id():
    """读取 AIGC 检测专用 LLM 配置 ID（None = 跟随激活配置）"""
    prompts = load_json_file("prompts.json", {})
    return prompts.get("aigc_detect_llm_id", None)


def set_detect_llm_id(profile_id):
    """保存 AIGC 检测专用 LLM 配置 ID"""
    prompts = load_json_file("prompts.json", {})
    prompts["aigc_detect_llm_id"] = profile_id
    save_json_file("prompts.json", prompts)


def detect_aigc(text: str) -> dict:
    """统一 AIGC 检测入口：按用户选择的引擎分流（本地模型 / LLM 提示词）"""
    engine = get_detect_engine()
    if engine == "local":
        return detect_aigc_prob(text)
    return llm_detect_aigc(text)


# ============================================================
# 3. LLM 核心调用层（多配置支持）
# ============================================================
def call_llm_api(prompt, system_prompt, max_tokens, api_key, base_url, model_name, temp=0.7, json_mode=False):
    """底层 API 调用，包含重试机制。
    json_mode=True 时启用 response_format=json_object 强制模型输出合法 JSON，
    若服务商不支持该参数则自动降级为普通模式重试。"""
    if not api_key:
        return "API 密钥缺失，请在[基础环境配置]中补全。"
    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        req_params = {"model": model_name, "messages": messages, "temperature": temp}
        if max_tokens:
            req_params["max_tokens"] = max_tokens
        if json_mode:
            req_params["response_format"] = {"type": "json_object"}

        for attempt in range(3):
            try:
                res = client.chat.completions.create(**req_params)
                msg = res.choices[0].message
                content = msg.content
                # 注意：思考过程（reasoning_content）绝不是最终答案，禁止作为兜底返回。
                # 若 content 为空（推理模型思考占满预算等情况），视为本次调用无效并重试。
                # 思考链一旦混入正文，去味/压缩/审查等结果都会出现大量思考痕迹。
                if not content or not content.strip():
                    if attempt < 2:
                        time.sleep(1)
                        continue
                    return ""
                return content
            except Exception as e:
                # JSON 模式不支持时降级为普通模式重试
                if json_mode and ("response_format" in req_params) and attempt == 0:
                    req_params.pop("response_format", None)
                    continue
                if attempt < 2:
                    time.sleep(2)
                else:
                    return f"API 连续调用失败: {e}"
    except Exception as e:
        return f"OpenAI 客户端构造异常: {e}"

def get_all_profiles():
    return load_json_file("llm_profiles.json", [])

def get_active_profile():
    profiles = get_all_profiles()
    active_id = st.session_state.get("active_llm_id", None)
    if not active_id and profiles:
        active_id = profiles[0]["id"]
        st.session_state["active_llm_id"] = active_id
    for p in profiles:
        if p["id"] == active_id:
            return p
    return profiles[0] if profiles else None

def dispatch_llm_call(prompt, system_prompt=None, max_tokens=None, temp=0.7, profile_id=None, json_mode=False):
    """路由分发器。指定 profile_id 可调特定模型；否则使用当前激活配置。"""
    profiles = get_all_profiles()
    if not profiles:
        return "未配置任何 LLM，请先在基础配置中添加。"
    prof = None
    if profile_id:
        prof = next((p for p in profiles if p["id"] == profile_id), None)
    if not prof:
        prof = get_active_profile()
    if not prof:
        return "未找到有效的 LLM 配置。"
    return call_llm_api(
        prompt=prompt,
        system_prompt=system_prompt,
        max_tokens=max_tokens,
        api_key=prof.get("api_key", ""),
        base_url=prof.get("base_url", ""),
        model_name=prof.get("model", ""),
        temp=temp,
        json_mode=json_mode
    )

# ============================================================
# 4. 树状结构工具函数
# ============================================================
def flatten_tree_nodes(nodes, depth=0, res=None):
    """递归展平树状节点，带深度标记"""
    if res is None:
        res = []
    for node in nodes:
        res.append((node, depth))
        if "children" in node and node["children"]:
            flatten_tree_nodes(node["children"], depth + 1, res)
    return res

def get_node_by_id(nodes, node_id):
    for node, depth in flatten_tree_nodes(nodes):
        if node.get("id") == node_id:
            return node
    return None

def get_ordered_node_ids(nodes):
    """返回树的前序遍历节点 id 列表"""
    return [n.get("id") for n, _ in flatten_tree_nodes(nodes)]

def tree_to_compact_outline(nodes, depth=0, res=None):
    """把整棵逻辑树压缩成紧凑大纲文本，供 LLM 全局感知"""
    if res is None:
        res = []
    for node in nodes:
        prefix = "  " * depth
        own = "【本人实验/方法】" if node.get("is_own_experiment") else ""
        wc = node.get("word_count", "")
        wc_str = f"(约{wc}字)" if wc else ""
        res.append(f"{prefix}- {own}{node.get('title', '')}{wc_str}: {node.get('desc', '')}")
        if "children" in node and node["children"]:
            tree_to_compact_outline(node["children"], depth + 1, res)
    return res

def generate_mermaid_td(nodes, parent_id=None):
    """竖向思维导图 (graph TD)，节点 ID 经过净化处理"""
    lines = []
    for n in nodes:
        safe_nid = "N_" + n.get("id", str(uuid.uuid4())).replace("-", "")
        title = (n.get("title", "未命名") or "未命名").replace('"', '').replace("\n", " ")
        wc = n.get("word_count", "")
        own_flag = "🧪" if n.get("is_own_experiment") else ""
        label = f"{own_flag}{title}"
        if wc:
            label += f" ({wc}字)"
        lines.append(f'    {safe_nid}["{label}"]')
        if parent_id:
            lines.append(f'    {parent_id} --> {safe_nid}')
        if "children" in n and n["children"]:
            lines.extend(generate_mermaid_td(n["children"], safe_nid))
    return lines

# ============================================================
# 5. 究极双向上下文三明治（完全重构版）
# ============================================================
def build_context_sandwich(current_node_id, max_chars=20000):
    """
    重构后的上下文三明治（全量版）：
    - 全局逻辑大纲：整棵逻辑树实时压缩，作为最顶层罗盘
    - 上游记忆：当前节点之前【已写】章节的完整记忆全量带上（不截断），未写章节只给大纲标题占位
    - 下游已写：全量提供完整记忆（用于回头修改中间章节时保持与下游一致，防冲突）
    - 下游未写：只给轻量大纲边界（防剧透/越界）
    - 兜底保护：总字符超 max_chars 时按「离当前节点远近」取舍（已写记忆优先保留）
    """
    prompts = load_json_file("prompts.json", {})
    global_topic = prompts.get("global_topic", "未设定课题")
    target_wc = prompts.get("target_word_count", 5000)

    tree = load_json_file("logic_tree.json", [])
    flat = flatten_tree_nodes(tree)

    global_outline = "\n".join(tree_to_compact_outline(tree))

    upstream_written = []      # 上游已写章节的完整记忆
    upstream_outline = []      # 上游未写章节的大纲占位（仅标题）
    downstream_written = []    # 下游已写章节的完整记忆
    downstream_boundary = []   # 下游未写章节的大纲边界（仅标题）
    current_node_title = ""
    current_node_desc = ""
    current_node = {"word_count": 0}
    current_depth = -1
    hit_current = False
    drafts_summary = load_json_file("drafts_summary.json", {})
    current_children_ids = set()

    for node, depth in flat:
        nid = node.get("id")
        title = node.get("title", "")

        if nid == current_node_id:
            hit_current = True
            current_node_title = title
            current_node_desc = node.get("desc", "")
            current_node = node
            current_depth = depth
            # 记录当前节点自己的直系子节点 id（仅用于跳过自身子节点）
            current_children_ids = {c.get("id") for c in (node.get("children") or []) if isinstance(c, dict)}
            continue

        if not hit_current:
            if nid in drafts_summary and drafts_summary[nid]:
                upstream_written.append(f"- 【上游已写·{title}】{purge_thinking_text(str(drafts_summary[nid]))}")
            else:
                upstream_outline.append(title)
        else:
            # 只跳过当前节点【自己的】直系子节点（当前节点为父节点时才可能触发；叶子节点无子节点，不误伤其他深层节点）
            if nid in current_children_ids:
                continue
            if nid in drafts_summary and drafts_summary[nid]:
                # 下游已写：完整记忆，防冲突（回头修改中间章节时 LLM 必须知晓下游已写内容）
                downstream_written.append(f"- 【下游已写·{title}】{purge_thinking_text(str(drafts_summary[nid]))}")
            else:
                # 下游未写：轻量边界，防剧透/越界
                downstream_boundary.append(title)

    # ---- 上游拼接：已写全量 + 未写占位合并为一行（不挤占窗口） ----
    up_parts = list(upstream_written)
    if upstream_outline:
        up_parts.append(f"- 【上游其余大纲章节（未写）】{'、'.join(upstream_outline[:30])}")
    up_str = "\n".join(up_parts) if up_parts else "无（当前为首个已写章节）"

    # ---- 下游拼接：已写全量 + 未写边界（轻量） ----
    down_parts = []
    if downstream_written:
        # 修改场景提示：下游已有内容，当前章修改必须与之保持一致
        down_parts.append("【⚠️ 下游已有已写章节，修改/生成当前章时必须与下列下游内容保持一致，禁止与之矛盾或重复】")
        down_parts.extend(downstream_written)
    if downstream_boundary:
        down_parts.append(f"- 【下游未写章节边界（防剧透）】{'、'.join(downstream_boundary[:30])}")
    down_str = "\n".join(down_parts) if down_parts else "无（当前为最终章节）"

    # ---- 兜底保护：超长论文时按离当前节点远近取舍 ----
    if len(up_str) + len(down_str) > max_chars:
        # 预算分配：上游 60%、下游 40%
        up_budget = int(max_chars * 0.6)
        down_budget = int(max_chars * 0.4)
        if len(up_str) > up_budget:
            # 保留尾部（最近的）已写记忆，并加截断说明
            lines = up_str.split("\n")
            keep = []
            used = 0
            for ln in reversed(lines):
                if used + len(ln) > up_budget:
                    break
                keep.insert(0, ln)
                used += len(ln)
            keep.insert(0, f"【上下文已按预算截断，保留最近 {len(keep)-1} 条上游记忆，完整上游共 {len(lines)} 条】")
            up_str = "\n".join(keep)
        if len(down_str) > down_budget:
            lines = down_str.split("\n")
            keep = []
            used = 0
            for ln in lines:
                if used + len(ln) > down_budget:
                    break
                keep.append(ln)
                used += len(ln)
            keep.insert(0, f"【下游已按预算截断，保留前 {len(keep)-1} 条下游信息，完整下游共 {len(lines)} 条】")
            down_str = "\n".join(keep)

    return {
        "global_topic": global_topic,
        "target_word_count": target_wc,
        "global_outline": global_outline,
        "current_title": current_node_title,
        "current_desc": current_node_desc,
        "current_word_count": current_node.get("word_count", 0) or 0,
        "current_refs": current_node.get("references", []) or [],
        "upstream": up_str,
        "downstream": down_str
    }

# ============================================================
# 6. 全局侧边栏（研究课题 + 预期字数 + 导航）
# ============================================================
def render_global_sidebar():
    """侧边栏：全局研究课题、预期字数、当前激活模型、导航"""
    st.sidebar.title("🎓 Lumielle Academic Assistant")
    st.sidebar.markdown("---")
    st.sidebar.subheader("🌍 全局研究设置")

    prompts = load_json_file("prompts.json", {})
    global_topic = st.sidebar.text_area(
        "研究课题 / 核心论点",
        value=prompts.get("global_topic", ""),
        height=100,
        key="global_topic_input"
    )
    target_wc = st.sidebar.number_input(
        "预期总字数",
        min_value=100, max_value=200000, step=500,
        value=int(prompts.get("target_word_count", 5000)),
        key="global_wc_input"
    )
    prompts["global_topic"] = global_topic
    prompts["target_word_count"] = int(target_wc)
    save_json_file("prompts.json", prompts)

    active = get_active_profile()
    if active:
        st.sidebar.caption(f"⚡ 激活模型：{active.get('name','')} · {active.get('model','')}")
    else:
        st.sidebar.caption("⚠️ 未配置 LLM 模型")

    st.sidebar.markdown("---")
    nav_choice = st.sidebar.radio("核心工作流选择", [
        "1. LLM 配置与交叉讨论",
        "2. 科研基座",
        "3. 文献处理",
        "4. 逻辑链路",
        "5. 正文写作",
        "6. AIGC 检测与去味",
        "7. 提示词配置"
    ])

    # ================= 全局数据管理（清除） =================
    st.sidebar.markdown("---")
    with st.sidebar.expander("🗑️ 数据管理（全局清除）", expanded=False):
        st.caption("选择性清除或清空项目数据（不可恢复，请谨慎操作）")
        clear_map = {
            "drafts.json": "📝 正文草稿",
            "drafts_summary.json": "🧠 章节记忆池",
            "drafts_charts.json": "📊 图表记录",
            "drafts_images.json": "🖼️ 图片记录",
            "literatures.json": "📚 文献库",
            "logic_tree.json": "🔗 逻辑大纲",
            "research_base.json": "🧪 科研基座",
            "gap_report.json": "📋 缺口报告",
            "literature_quality.json": "🔍 文献质量报告",
            "full_logic_review.json": "🧠 逻辑审查报告"
        }
        sel_names = st.multiselect("选择要清除的数据类型", list(clear_map.values()), key="clear_sel")
        c_files = st.checkbox("🗑️ 同时删除图表/图片物理文件", key="clear_files")
        c_confirm = st.checkbox("⚠️ 我确认清除（不可恢复）", key="clear_confirm")
        if st.button("💣 执行清除", disabled=not c_confirm, type="primary"):
            sel_files = [k for k, v in clear_map.items() if v in sel_names]
            if not sel_files:
                st.sidebar.warning("请至少选择一项数据类型。")
            else:
                for fname in sel_files:
                    if fname in default_files:
                        save_json_file(fname, default_files[fname])
                    else:
                        load_json_file(fname, {})
                if c_files:
                    for d in [CHART_DIR, IMAGE_DIR]:
                        for fn in os.listdir(d):
                            try:
                                os.remove(os.path.join(d, fn))
                            except Exception:
                                pass
                st.sidebar.success(f"已清除 {len(sel_files)} 项数据！")
                st.rerun()

    # ================= 版权署名（固定显示） =================
    st.sidebar.markdown("---")
    st.sidebar.markdown(
        "**🎓 Lumielle Academic Assistant**  \n"
        "开发者：Lumielle  \n"
        "交流反馈：QQ 群聊 **1029688024**  \n"
        "仅供个人学习使用，禁止倒卖牟利。"
    )
    st.sidebar.caption(f"v{__version__}")

    return nav_choice


# ============================================================
# 7. 板块一：LLM 配置与多模型交叉讨论
# ============================================================
def fetch_models_from_base_url(base_url, api_key):
    """通过 OpenAI 兼容的 /models 端点抓取可用模型列表。
    返回 (成功标志, 模型列表 或 错误信息)"""
    if not base_url or not base_url.strip():
        return False, "请先填写 Base URL。"
    # 处理 base_url 结尾的斜杠与 /v1 后缀，确保拼出 /models
    url = base_url.strip().rstrip("/")
    if not url.endswith("/models"):
        url = url + "/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            models = data.get("data", []) if isinstance(data, dict) else []
            names = []
            for m in models:
                mid = m.get("id") if isinstance(m, dict) else str(m)
                if mid:
                    names.append(str(mid))
            if names:
                return True, sorted(names)
            return False, "接口返回成功但未找到模型列表（data 为空）。"
        return False, f"请求失败 (HTTP {resp.status_code})：{resp.text[:200]}"
    except requests.exceptions.Timeout:
        return False, "请求超时，请检查网络或 Base URL 是否正确。"
    except Exception as e:
        return False, f"请求异常: {e}"


def llm_config_tab():
    st.subheader("⚙️ LLM 配置矩阵")
    st.caption("💡 DeepSeek 开放平台：https://platform.deepseek.com （申请 API Key 后在此配置）")
    profiles = get_all_profiles()
    active = get_active_profile()
    if active:
        st.info(f"当前激活：**{active.get('name','')}**  ·  `{active.get('model','')}`")

    # ---- 新增配置 ----
    with st.expander("➕ 新增 LLM 配置", expanded=False):
        with st.form("new_llm_form"):
            c1, c2 = st.columns(2)
            with c1:
                n_name = st.text_input("配置名称", value="新模型配置")
                n_base = st.text_input("Base URL", value="https://api.deepseek.com/v1", key="n_base_url")
            with c2:
                n_key = st.text_input("API Key", type="password", key="n_api_key")
            # 自动拉取模型列表
            c_fetch, c_hint = st.columns([1, 3])
            with c_fetch:
                fetch_clicked = st.form_submit_button("🔍 拉取模型列表")
            if fetch_clicked:
                ok, result = fetch_models_from_base_url(n_base, n_key)
                if ok:
                    st.session_state["fetched_models"] = result
                    st.session_state["fetch_base"] = n_base
                    st.success(f"获取到 {len(result)} 个模型！请在下拉框选择。")
                else:
                    st.session_state.pop("fetched_models", None)
                    st.error(f"模型列表获取失败：{result}")
            # 模型选择：完全走下拉（未拉取时禁用提示）
            if st.session_state.get("fetched_models") and st.session_state.get("fetch_base") == n_base:
                st.selectbox(
                    "选择模型名称（自动获取）：",
                    st.session_state["fetched_models"],
                    key="n_model_select",
                    help="从该服务商 API 拉取的可用模型列表"
                )
            else:
                st.selectbox(
                    "选择模型名称：",
                    ["（请先点击上方「拉取模型列表」）"],
                    key="n_model_empty",
                    disabled=True,
                    help="填写 Base URL 与 API Key 后点击「拉取模型列表」，从下拉框选择模型"
                )
            submitted = st.form_submit_button("💾 保存新增配置")
            if submitted:
                if not n_key:
                    st.warning("API Key 不能为空")
                elif not st.session_state.get("n_model_select"):
                    st.warning("请先点击「🔍 拉取模型列表」获取模型，并从下拉框选择。")
                else:
                    profiles.append({
                        "id": str(uuid.uuid4()), "name": n_name,
                        "base_url": n_base, "api_key": n_key, "model": st.session_state["n_model_select"]
                    })
                    save_json_file("llm_profiles.json", profiles)
                    st.session_state.pop("fetched_models", None)
                    st.success("配置已新增！")
                    st.rerun()

    st.markdown("---")
    # ---- 配置列表（编辑 / 激活 / 删除） ----
    for idx, prof in enumerate(profiles):
        with st.container(border=True):
            is_active = prof["id"] == st.session_state.get("active_llm_id")
            badge = "✅ **当前激活**" if is_active else ""
            st.markdown(f"### {prof.get('name','未命名')} {badge}")
            st.caption(f"Base URL：`{prof.get('base_url','')}` ｜ Model：`{prof.get('model','')}` ｜ Key：{'已填写' if prof.get('api_key') else '未填写'}")

            col_a, col_b = st.columns([1, 5])
            with col_a:
                if not is_active:
                    if st.button("⚡ 激活", key=f"act_{prof['id']}"):
                        st.session_state["active_llm_id"] = prof["id"]
                        st.rerun()
            with col_b:
                with st.expander("✏️ 编辑 / 🗑️ 删除", expanded=False):
                    e1, e2 = st.columns(2)
                    with e1:
                        e_name = st.text_input("名称", value=prof.get("name", ""), key=f"en_{prof['id']}")
                        e_base = st.text_input("Base URL", value=prof.get("base_url", ""), key=f"eb_{prof['id']}")
                    with e2:
                        e_key = st.text_input("API Key", value=prof.get("api_key", ""), type="password", key=f"ek_{prof['id']}")
                    st.caption(f"当前模型：`{prof.get('model','')}`")
                    # 自动拉取模型列表（模型完全走下拉）
                    if st.button("🔍 从 Base URL 拉取模型列表", key=f"efetch_{prof['id']}"):
                        ok, result = fetch_models_from_base_url(e_base, e_key)
                        if ok:
                            st.session_state[f"edit_models_{prof['id']}"] = result
                            st.session_state[f"edit_fetch_base_{prof['id']}"] = e_base
                            st.success(f"获取到 {len(result)} 个模型！请在下拉框选择。")
                        else:
                            st.session_state.pop(f"edit_models_{prof['id']}", None)
                            st.error(f"模型列表获取失败：{result}")
                    if st.session_state.get(f"edit_models_{prof['id']}") and st.session_state.get(f"edit_fetch_base_{prof['id']}") == e_base:
                        e_model = st.selectbox(
                            "选择模型名称（自动获取）：",
                            st.session_state[f"edit_models_{prof['id']}"],
                            index=0,
                            key=f"em_select_{prof['id']}",
                            help="从该服务商 API 拉取的可用模型列表"
                        )
                    else:
                        st.selectbox(
                            "选择模型名称：",
                            ["（请先点击上方「拉取模型列表」）"],
                            key=f"em_empty_{prof['id']}",
                            disabled=True,
                            help="点击「从 Base URL 拉取模型列表」后从下拉框选择模型"
                        )
                    c_ed, c_del = st.columns(2)
                    with c_ed:
                        if st.button("💾 保存修改", key=f"esave_{prof['id']}"):
                            chosen_model = st.session_state.get(f"em_select_{prof['id']}", "")
                            if not chosen_model:
                                st.warning("请先点击「🔍 从 Base URL 拉取模型列表」并从下拉框选择模型。")
                            else:
                                profiles[idx] = {
                                    "id": prof["id"], "name": e_name,
                                    "base_url": e_base, "api_key": e_key, "model": chosen_model
                                }
                                save_json_file("llm_profiles.json", profiles)
                                st.session_state.pop(f"edit_models_{prof['id']}", None)
                                st.success("已保存！")
                                st.rerun()
                    with c_del:
                        if st.button("🗑️ 删除此配置", key=f"edel_{prof['id']}"):
                            profiles.pop(idx)
                            if st.session_state.get("active_llm_id") == prof["id"]:
                                st.session_state["active_llm_id"] = profiles[0]["id"] if profiles else None
                            save_json_file("llm_profiles.json", profiles)
                            st.rerun()


def multi_model_discussion_tab():
    st.subheader("🤝 多模型交叉讨论")
    

    profiles = get_all_profiles()
    if not profiles:
        st.warning("请先在「LLM 配置」中添加至少一个模型。")
        return

    name_map = {p["id"]: f"{p.get('name','')} ({p.get('model','')})" for p in profiles}
    selected_ids = st.multiselect(
        "选择参与讨论的模型（建议 2 个以上）",
        list(name_map.keys()),
        format_func=lambda x: name_map[x],
        default=[profiles[0]["id"]]
    )

    question = st.text_area("输入需要交叉讨论的复杂问题 / 研究难题：", height=120)

    if st.button("🚀 开始交叉讨论"):
        if not question.strip():
            st.warning("请先输入问题。")
        elif len(selected_ids) < 1:
            st.warning("请至少选择一个模型。")
        else:
            answers = {}
            bar = st.progress(0)
            for i, pid in enumerate(selected_ids):
                with st.spinner(f"💭 {name_map[pid]} 独立思考中..."):
                    answers[pid] = dispatch_llm_call(
                        question,
                        system_prompt="你是资深学术专家。请独立、严谨、结构化地回答以下问题，不要提及自己是 AI。",
                        max_tokens=2000,
                        profile_id=pid
                    )
                bar.progress((i + 1) / len(selected_ids))
            st.session_state["discussion_answers"] = answers
            st.success("各模型回答完毕！")

    # 展示各模型回答
    if "discussion_answers" in st.session_state and st.session_state["discussion_answers"]:
        answers = st.session_state["discussion_answers"]
        for pid in selected_ids:
            if pid in answers:
                with st.expander(f"💬 {name_map[pid]} 的回答", expanded=False):
                    st.markdown(answers[pid])

        if st.button("🏛️ 汇总为最终共识"):
            summary_prompt = f"以下是多个大模型对同一个问题的独立回答。请作为仲裁者，综合各方观点，去粗取精，输出一份结构清晰、逻辑严密的最终共识答案，并在末尾标注各模型间存在的主要分歧点。\n\n问题：{question}\n\n"
            for pid in selected_ids:
                if pid in answers:
                    summary_prompt += f"\n--- {name_map[pid]} ---\n{answers[pid]}\n"
            with st.spinner("仲裁模型整合各方观点中..."):
                final = dispatch_llm_call(
                    summary_prompt,
                    system_prompt="你是严谨的学术仲裁者，擅长整合多方观点形成共识结论。",
                    max_tokens=2500
                )
            st.session_state["discussion_final"] = final

        if "discussion_final" in st.session_state:
            st.markdown("### 🏛️ 最终共识")
            st.markdown(st.session_state["discussion_final"])


def ast_reverse_tab():
    st.subheader("📄 文稿逆向工程（AST 拆解）")
    
    up_doc = st.file_uploader("上传已有的 .docx 文档提取大纲", type=["docx"], key="ast_up")
    if up_doc:
        try:
            doc = docx.Document(io.BytesIO(up_doc.read()))
            extracted_text = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
            st.info(f"成功读取文档，纯文本字符数: {len(extracted_text)}")
            if st.button("🚀 执行逆向拆解并写入逻辑树"):
                with st.spinner("AI 逆向构建抽象语法树 (AST) 中..."):
                    prompt = (
                        "请将以下文档内容逆向解析为标准 JSON 的逻辑大纲树。"
                        "每个节点包含 id, title, desc, word_count, chart_instruction, image_suggestion, is_own_experiment, references(空数组), children。"
                        "必须直接输出纯 JSON 数组，禁止任何 Markdown 标记和解释：\n"
                        + extracted_text[:4000]
                    )
                    res = dispatch_llm_call(prompt, system_prompt="你是一个极简主义的结构解析器。", max_tokens=3000)
                    parsed = re.search(r'(\[.*\]|\{.*\})', res, re.DOTALL)
                    if parsed:
                        tree_json = json.loads(parsed.group(1))
                        save_json_file("logic_tree.json", tree_json if isinstance(tree_json, list) else [tree_json])
                        st.success("逆向工程完成！大纲已写入【逻辑链路】板块。")
                        st.rerun()
                    else:
                        st.error("未能从大模型返回中解析出合法的 JSON 大纲结构。")
        except Exception as e:
            st.error(f"文档逆向解析遭遇错误: {e}")


def module1_llm():
    st.header("🔧 LLM 配置与多模型交叉讨论")
    tabs = st.tabs(["LLM 配置矩阵", "多模型交叉讨论", "文稿逆向工程 (AST)"])
    with tabs[0]:
        llm_config_tab()
    with tabs[1]:
        multi_model_discussion_tab()
    with tabs[2]:
        ast_reverse_tab()

# ============================================================
# 8. 板块二：科研基座（研究内容 / 研究背景 / 研究要求）
# ============================================================
def parse_uploaded_doc_to_text(uploaded_file):
    """把上传的 PDF/Word/txt 解析为纯文本"""
    name = uploaded_file.name
    raw = uploaded_file.read()
    try:
        if name.endswith(".pdf"):
            reader = PyPDF2.PdfReader(io.BytesIO(raw))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            if not text.strip():
                return "解析失败: PDF 未能提取到文字内容（可能是扫描件/图片型 PDF），请转换为 Word 或文本文件后重试。"
            return text
        elif name.endswith(".docx"):
            doc = docx.Document(io.BytesIO(raw))
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        else:
            return raw.decode("utf-8", errors="ignore")
    except Exception as e:
        return f"解析失败: {e}"


# 中英文键名归一化映射（v4flash 可能输出中文键名或变体键名）
_MODULE_KEY_ALIASES = {
    "title": ["title", "标题", "模块标题", "名称", "name", "heading", "模块"],
    "type": ["type", "类型", "模块类型", "kind"],
    "content": ["content", "内容", "正文", "text", "文本"],
    "columns": ["columns", "列", "列名", "表头", "headers", "cols"],
    "data": ["data", "数据", "表格数据", "rows", "rows_data"],
}


def _norm_key(k):
    """把任意键名归一化为标准键名（title/type/content/columns/data），未识别返回原键"""
    if not isinstance(k, str):
        return k
    k_l = k.strip().lower()
    for std, aliases in _MODULE_KEY_ALIASES.items():
        if k_l in aliases or k.strip() in aliases:
            return std
    return k


def _normalize_module_dict(m):
    """把 LLM 输出的模块 dict 归一化为标准结构（中文键名→英文键名）"""
    if not isinstance(m, dict):
        return None
    # 键名归一化
    norm = {}
    for k, v in m.items():
        norm[_norm_key(k)] = v
    # 如果存在嵌套的"结构/子模块"列表（v4flash 偶发输出树形结构），展开为多个模块
    for nest_key in ("structure", "children", "modules", "items", "sections", "结构", "子模块", "章节", "模块列表", "子结构"):
        if isinstance(norm.get(nest_key), list) and norm[nest_key]:
            nested = []
            for x in norm[nest_key]:
                nx = _normalize_module_dict(x)
                if isinstance(nx, list):
                    nested.extend(nx)
                elif nx is not None:
                    nested.append(nx)
            if not nested:
                continue
            # 顶层若含实质内容（content/type/columns/data）则保留，否则视为纯容器丢弃
            has_own_content = bool(norm.get("content") or norm.get("type") or norm.get("columns") or norm.get("data"))
            if has_own_content:
                clean_top = {k: v for k, v in norm.items() if k != nest_key}
                return [clean_top] + nested
            return nested  # 返回列表表示"展开"
    return norm


def _extract_json_array(res):
    """从 LLM 返回中稳健提取 JSON 数组：
    1. 剥离 Markdown 代码块围栏（```json ... ```）
    2. 支持 {"modules": [...]} 包装对象
    3. 支持纯数组
    4. 逐字符定位真正的 JSON 起始/结束，避免被解释文字干扰
    5. 元素自动做中文键名归一化，兼容嵌套结构
    返回 list 或 None（解析失败时）"""
    if not res or not res.strip():
        return None
    # 剥离 Markdown 围栏
    cleaned = re.sub(r"```(?:json)?\s*", "", res)
    cleaned = re.sub(r"\s*```", "", cleaned).strip()

    # 尝试多个候选 JSON 片段（首个 {/[ 到最后一个 }/]）
    candidates = []
    try:
        candidates.append(cleaned)
    except Exception:
        pass
    start = min([i for i in (cleaned.find('['), cleaned.find('{')) if i >= 0], default=-1)
    if start >= 0:
        end = max(cleaned.rfind(']'), cleaned.rfind('}'))
        if end > start:
            candidates.append(cleaned[start:end + 1])

    for frag in candidates:
        try:
            data = json.loads(frag)
        except Exception:
            continue
        # 直接是数组
        if isinstance(data, list):
            out = []
            for x in data:
                nx = _normalize_module_dict(x)
                if isinstance(nx, list):  # 嵌套展开
                    out.extend(nx)
                elif nx is not None:
                    out.append(nx)
            if out:
                return out
        # 包装对象：modules/结构/items 等键里是数组
        if isinstance(data, dict):
            for wrap_key in ("modules", "structure", "items", "sections", "data", "结果", "模块", "内容", "list", "子模块", "章节", "模块列表"):
                v = data.get(wrap_key)
                if isinstance(v, list):
                    out = []
                    for x in v:
                        nx = _normalize_module_dict(x)
                        if isinstance(nx, list):
                            out.extend(nx)
                        elif nx is not None:
                            out.append(nx)
                    if out:
                        return out
    return None


def _log_parse_failure(doc_text, res):
    """把解析失败的原始返回记录到日志，便于定位"""
    try:
        log_path = os.path.join(DATA_DIR, "parse_fail_log.json")
        log = load_json_file("parse_fail_log.json", {"entries": []})
        entries = log.setdefault("entries", [])
        entries.append({
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "doc_len": len(doc_text or ""),
            "llm_res_len": len(res or ""),
            "llm_res_head": (res or "")[:800]
        })
        # 只保留最近 20 条
        log["entries"] = entries[-20:]
        save_json_file("parse_fail_log.json", log)
    except Exception:
        pass


def ai_parse_doc_to_modules(doc_text, section_desc):
    """按顺序解析全部文档分块，只有整体成功后才返回模块。"""
    def normalize_modules(parsed_modules):
        cleaned = []
        for module in parsed_modules:
            if not isinstance(module, dict):
                continue
            module_type = str(module.get("type", "text")).strip().lower()
            if module_type == "table":
                data = module.get("data") or [[]]
                if not isinstance(data, list) or not data:
                    data = [[]]
                columns = module.get("columns") or [f"列{i + 1}" for i in range(len(data[0]))]
                cleaned.append({
                    "id": str(uuid.uuid4()), "type": "table",
                    "title": str(module.get("title", "表格模块")),
                    "columns": [str(column) for column in columns], "data": data,
                })
            else:
                cleaned.append({
                    "id": str(uuid.uuid4()), "type": "text",
                    "title": str(module.get("title", "文本模块")),
                    "content": str(module.get("content", "")),
                })
        return cleaned

    return parse_document_in_chunks(
        doc_text,
        section_desc,
        dispatch_llm_call,
        parse_response=_extract_json_array,
        normalize_modules=normalize_modules,
        locale="zh",
        on_parse_failure=_log_parse_failure,
    )


def get_research_context_text():
    """汇总研究课题 + 科研基座全部内容，供各模块使用"""
    prompts = load_json_file("prompts.json", {})
    rb = load_json_file("research_base.json", {"content": {"modules": []}, "background": {"modules": []}, "requirements": {"modules": []}})
    parts = [f"研究课题：{prompts.get('global_topic','')}"]
    labels = {"content": "研究内容（数据与方法）", "background": "研究背景", "requirements": "研究要求"}
    for key, label in labels.items():
        mods = rb.get(key, {}).get("modules", [])
        if mods:
            lines = [f"[{label}]"]
            for m in mods:
                title = m.get("title", "")
                if m.get("type") == "table":
                    lines.append(f"- {title}: 表格数据 {m.get('columns', [])} -> {m.get('data', [])[:5]}")
                else:
                    lines.append(f"- {title}: {str(m.get('content',''))[:200]}")
            parts.append("\n".join(lines))
    return "\n\n".join(parts)


def render_research_section(section_key, section_title, section_desc):
    """渲染科研基座的单一板块（研究内容/背景/要求）"""
    st.subheader(section_title)
    st.caption(section_desc)

    rb = load_json_file("research_base.json", {"content": {"modules": []}, "background": {"modules": []}, "requirements": {"modules": []}})
    section = rb.setdefault(section_key, {"modules": []})
    modules = section.get("modules", [])

    # ---- 新增模块 ----
    with st.expander("➕ 新增模块（文本 / 表格）", expanded=False):
        with st.form(f"add_mod_{section_key}"):
            m_type = st.radio("模块类型", ["text", "table"], format_func=lambda x: "📝 文本模块" if x == "text" else "📊 表格模块", horizontal=True)
            m_title = st.text_input("模块标题", value="新模块")
            if m_type == "text":
                m_content = st.text_area("文本内容", height=120)
            else:
                t_rows = st.number_input("初始行数", min_value=1, max_value=50, value=3, key=f"tr_{section_key}")
                t_cols = st.number_input("初始列数", min_value=1, max_value=20, value=3, key=f"tc_{section_key}")
                m_content = None
            if st.form_submit_button("💾 添加模块"):
                if m_type == "text":
                    modules.append({"id": str(uuid.uuid4()), "type": "text", "title": m_title, "content": m_content})
                else:
                    data = [["" for _ in range(int(t_cols))] for _ in range(int(t_rows))]
                    columns = [f"列{i+1}" for i in range(int(t_cols))]
                    modules.append({"id": str(uuid.uuid4()), "type": "table", "title": m_title, "columns": columns, "data": data})
                save_json_file("research_base.json", rb)
                st.success("模块已添加！")
                st.rerun()

    # ---- 文档导入自动解析 ----
    with st.expander("📥 导入文档自动解析为模块", expanded=False):
        up_doc = st.file_uploader(f"上传文档（自动解析为{section_title}模块）", type=["pdf", "docx", "txt"], key=f"up_{section_key}")
        if up_doc:
            if st.button("🔍 解析并生成模块", key=f"parse_{section_key}"):
                with st.spinner("AI 正在完整解析文档..."):
                    doc_text = parse_uploaded_doc_to_text(up_doc)
                    if is_document_parse_error(doc_text):
                        st.error(doc_text)
                    else:
                        if len(doc_text.strip()) < 50:
                            st.warning("文档内容较短，将尝试解析已提取的文字。")
                        parse_result = ai_parse_doc_to_modules(doc_text, section_desc)
                        if parse_result["status"] == "ok":
                            new_modules = parse_result["modules"]
                            modules.extend(new_modules)
                            save_json_file("research_base.json", rb)
                            if parse_result["chunks_total"] > 1:
                                st.success(f"完整文档已按顺序解析为 {parse_result['chunks_total']} 个部分，新增 {len(new_modules)} 个模块。")
                            else:
                                st.success(f"解析完成，新增 {len(new_modules)} 个模块！")
                            st.rerun()
                        else:
                            st.error(parse_result["message"])

    st.markdown("---")
    # ---- 已有模块渲染 ----
    if not modules:
        st.info("该板块暂无模块，请手动新增或导入文档解析。")
        return

    for idx, mod in enumerate(modules):
        with st.container(border=True):
            c_head, c_del = st.columns([6, 1])
            with c_head:
                st.markdown(f"**{mod.get('title','未命名模块')}**  `({mod.get('type')})`")
            with c_del:
                if st.button("🗑️", key=f"delmod_{section_key}_{idx}"):
                    modules.pop(idx)
                    save_json_file("research_base.json", rb)
                    st.rerun()

            if mod.get("type") == "text":
                mod["title"] = st.text_input("模块标题", value=mod.get("title", ""), key=f"mt_{section_key}_{idx}")
                mod["content"] = st.text_area("内容", value=mod.get("content", ""), height=100, key=f"mc_{section_key}_{idx}")
            else:
                mod["title"] = st.text_input("模块标题", value=mod.get("title", ""), key=f"mtt_{section_key}_{idx}")
                columns = mod.get("columns", [])
                data = mod.get("data", [[""]])
                # 列数自定义（该版本 data_editor 不支持 num_columns 动态，用按钮增删列）
                c_acol, c_dcol = st.columns(2)
                with c_acol:
                    if st.button("➕ 加一列", key=f"addcol_{section_key}_{idx}"):
                        columns.append(f"列{len(columns)+1}")
                        for row in data:
                            row.append("")
                        mod["columns"] = columns
                        mod["data"] = data
                        save_json_file("research_base.json", rb)
                        st.rerun()
                with c_dcol:
                    if st.button("➖ 减一列", key=f"delcol_{section_key}_{idx}"):
                        if len(columns) > 1:
                            columns.pop()
                            for row in data:
                                if row:
                                    row.pop()
                        mod["columns"] = columns
                        mod["data"] = data
                        save_json_file("research_base.json", rb)
                        st.rerun()
                try:
                    df = pd.DataFrame(data, columns=columns if len(columns) == len(data[0]) else None)
                except Exception:
                    df = pd.DataFrame(data)
                edited = st.data_editor(
                    df, num_rows="dynamic",
                    use_container_width=True, key=f"mte_{section_key}_{idx}"
                )
                mod["columns"] = [str(c) for c in edited.columns]
                mod["data"] = edited.astype(str).values.tolist()

    if st.button("💾 保存科研基座更改", type="primary", key=f"save_{section_key}"):
        save_json_file("research_base.json", rb)
        st.success("科研基座已保存！")


def module2_research_base():
    st.header("🧪 科研基座")
    
    tabs = st.tabs([
        "📌 研究内容（数据与方法）",
        "🌱 研究背景",
        "📋 研究要求（任务书等）"
    ])
    with tabs[0]:
        render_research_section("content", "研究内容（数据与方法）", "你的实验数据、研究方法、技术路线等核心内容")
    with tabs[1]:
        render_research_section("background", "研究背景", "领域背景、文献综述、研究动机等")
    with tabs[2]:
        render_research_section("requirements", "研究要求", "导师任务书、学校格式要求、对研究方法与角度的硬性约束")


# ============================================================
# 9. 板块三：文献处理
# ============================================================
def build_literature_rating_prompt(title, text):
    """构造文献评级分类 prompt"""
    context = get_research_context_text()
    prompt = (
        "你是文献情报分析专家。请结合研究者的研究背景，对以下文献进行评级与分类。\n\n"
        f"【研究上下文】\n{context[:2000]}\n\n"
        "【待分析文献】\n"
        f"标题：{title}\n内容：{text[:3000]}\n\n"
        "严格只输出 JSON（不要 Markdown）：\n"
        '{"rating": 1到5的整数（5=高度相关且至关重要，1=相关性极低）, '
        '"category": "研究背景/研究方法/理论基础/数据来源/案例分析/其他", '
        '"key_findings": "核心发现，100字内", '
        '"quality_assessment": "文献质量评估：严谨性、时效性、相关性，100字内", '
        '"summary": "文献摘要，150字内"}'
    )
    return prompt


def open_file_with_default_app(file_path):
    """调用系统默认文档查看工具（WPS / Office / 预览等）直接打开本地文件。
    适用于本地部署场景（后端与文件同机），返回 (是否成功, 提示信息)"""
    if not file_path or not os.path.exists(file_path):
        return False, "文件不存在或已被移动。"
    try:
        sys_name = platform.system()
        if sys_name == "Darwin":
            subprocess.Popen(["open", file_path])
        elif sys_name == "Windows":
            os.startfile(file_path)  # type: ignore
        else:
            subprocess.Popen(["xdg-open", file_path])
        return True, f"已调用本地应用打开：{os.path.basename(file_path)}"
    except Exception as e:
        return False, f"打开失败: {e}"


def module3_literature():
    st.header("📚 文献处理")
    literatures = load_json_file("literatures.json", [])

    # ---- 批量导入 ----
    st.subheader("📥 批量导入与智能评级分类")
    
    up_files = st.file_uploader("批量上传 PDF / Word 文献（可多选）", type=["pdf", "docx"], accept_multiple_files=True, key="lit_batch_up")

    if up_files and st.button("🚀 开始批量导入并智能解析"):
        if not get_active_profile() or not get_active_profile().get("api_key"):
            st.error("请先在板块一配置 LLM API Key。")
        else:
            bar = st.progress(0)
            added = 0
            for i, uf in enumerate(up_files):
                with st.spinner(f"解析《{uf.name}》..."):
                    text = parse_uploaded_doc_to_text(uf)
                    if is_document_parse_error(text):
                        st.warning(f"{uf.name}: {text}")
                        bar.progress((i + 1) / len(up_files))
                        continue
                    prompt = build_literature_rating_prompt(uf.name, text)
                    res = dispatch_llm_call(prompt, system_prompt="你只输出合法 JSON，不输出任何其他内容。", max_tokens=1000)
                    parsed = re.search(r'(\{.*\})', res, re.DOTALL)
                    analysis = {"rating": 3, "category": "其他", "key_findings": "", "quality_assessment": "", "summary": ""}
                    if parsed:
                        try:
                            analysis.update(json.loads(parsed.group(1)))
                        except Exception:
                            pass
                    lit_id = str(uuid.uuid4())
                    # 保存原文件副本到 raw_files（供本地查看器/下载）
                    file_path = ""
                    try:
                        uf.seek(0)
                        raw_bytes = uf.read()
                        file_name = f"{lit_id}_{uf.name}"
                        file_path = os.path.join(RAW_DIR, file_name)
                        with open(file_path, "wb") as rf:
                            rf.write(raw_bytes)
                    except Exception as e:
                        st.warning(f"原文件保存失败: {e}")
                    literatures.append({
                        "id": lit_id,
                        "title": uf.name,
                        "summary": text[:500] + ("..." if len(text) > 500 else ""),
                        "source": "Local Upload",
                        "link": "",
                        "file_path": file_path,
                        "important": False,
                        "rating": int(analysis.get("rating", 3)),
                        "category": analysis.get("category", "其他"),
                        "analysis": analysis
                    })
                    added += 1
                bar.progress((i + 1) / len(up_files))
            save_json_file("literatures.json", literatures)
            st.success(f"批量导入完成！新增 {added} 篇文献。")
            st.rerun()

    st.markdown("---")

    # ---- 文献列表 ----
    st.subheader("📖 已入库文献库")
    if not literatures:
        st.info("文献库为空，请先批量导入。")
    else:
        # 统计概览
        cats = {}
        for lit in literatures:
            cats[lit.get("category", "其他")] = cats.get(lit.get("category", "其他"), 0) + 1
        st.caption("分类统计：" + " ｜ ".join([f"{k} × {v}" for k, v in cats.items()]))

        for idx, lit in enumerate(literatures):
            with st.container(border=True):
                stars = "⭐" * int(lit.get("rating", 0))
                star_prefix = "⭐⭐⭐ " if lit.get("important", False) else ""
                c_head, c_star, c_del = st.columns([6, 1, 1])
                with c_head:
                    st.markdown(f"#### {star_prefix}{lit['title']}")
                    st.markdown(f"**评级**: {stars} ｜ **分类**: `{lit.get('category','其他')}`")
                    with st.expander("👁️ 查看 LLM 文献总结与原文", expanded=False):
                        st.markdown(f"**核心发现**: {lit.get('analysis',{}).get('key_findings','-')}")
                        st.markdown(f"**质量评估**: {lit.get('analysis',{}).get('quality_assessment','-')}")
                        st.markdown(f"**文献摘要**: {lit.get('analysis',{}).get('summary', lit['summary'][:200])}")
                        # 本地文件查看：直接调用系统默认文档查看工具打开
                        fp = lit.get("file_path", "")
                        if fp and os.path.exists(fp):
                            fname = os.path.basename(fp)
                            c_open, c_dl = st.columns(2)
                            with c_open:
                                if st.button("📂 用本地应用打开", key=f"litopen_{lit['id']}"):
                                    ok, msg = open_file_with_default_app(fp)
                                    if ok:
                                        st.success(msg)
                                    else:
                                        st.error(msg)
                            with c_dl:
                                # 保留下载按钮作为备选
                                with open(fp, "rb") as fh:
                                    file_bytes = fh.read()
                                st.download_button(
                                    "📥 下载副本",
                                    data=file_bytes,
                                    file_name=fname,
                                    mime="application/octet-stream",
                                    key=f"litfile_{lit['id']}"
                                )
                            
                        else:
                            st.caption("（未保存原文件，仅存解析文本）")
                with c_star:
                    if st.button("🌟", key=f"litstar_{lit['id']}"):
                        lit['important'] = not lit.get('important', False)
                        save_json_file("literatures.json", literatures)
                        st.rerun()
                with c_del:
                    if st.button("🗑️", key=f"litdel_{lit['id']}"):
                        # 同时删除原文件副本
                        fp = lit.get("file_path", "")
                        if fp and os.path.exists(fp):
                            try:
                                os.remove(fp)
                            except Exception:
                                pass
                        literatures.pop(idx)
                        save_json_file("literatures.json", literatures)
                        st.rerun()

    st.markdown("---")

    # ---- 文献质量评估与补足方向 ----
    st.subheader("🔍 文献质量评估与补足方向")
    if st.button("📋 执行整体质量评估与缺口分析"):
        if not literatures:
            st.warning("文献库为空，无法评估。请先导入文献。")
        else:
            with st.spinner("AI 全景扫描文献库，评估质量并寻找补足方向..."):
                lit_summary = "\n".join([
                    f"- [{l.get('category','其他')}][{'⭐'*int(l.get('rating',0))}] {l['title']}: {l.get('analysis',{}).get('key_findings','')[:100]}"
                    for l in literatures
                ])
                context = get_research_context_text()
                prompt = (
                    f"你是极端严厉的学术评审委员。基于以下研究上下文和文献库，完成两件事：\n"
                    f"1. 评估当前文献库的整体质量（覆盖度、均衡性、权威性）\n"
                    f"2. 明确指出缺失的文献方向，并给出可补足的文献检索建议（具体关键词/研究方向）\n\n"
                    f"【输出要求（必须严格遵守）】\n"
                    f"- 直接输出评审结论本身，禁止输出任何思考过程、分析步骤、推理链或解说性开场白。\n"
                    f"- 禁止出现「好的」「我来分析」「首先」「接下来」「综上所述」等过程性、总结性套话。\n"
                    f"- 使用简洁的条目式结论（如「一、整体质量」「二、缺失方向与补足建议」），每项直接给出判断与理由。\n\n"
                    f"【研究上下文】\n{context[:1500]}\n\n"
                    f"【文献库清单】\n{lit_summary[:3000]}"
                )
                res = dispatch_llm_call(prompt, system_prompt="你是一位极端严厉、挑剔的学术评审委员。", max_tokens=4000)
                # 结果实时显示，不依赖事后读文件
                if not res or not res.strip():
                    st.error("LLM 返回了空内容，请检查板块一的 LLM 配置是否正常。")
                elif res.startswith("API 密钥缺失") or res.startswith("API 连续调用失败") or res.startswith("OpenAI 客户端构造异常") or res.startswith("未配置"):
                    st.error(f"LLM 调用失败：{res}")
                else:
                    save_json_file("literature_quality.json", {"content": res})
                    st.success("质量评估完成！")
                    with st.expander("📋 文献质量评估报告", expanded=True):
                        st.markdown(purge_thinking_text(res))
                    st.rerun()

    q_rep = load_json_file("literature_quality.json", {"content": ""})
    if q_rep.get("content"):
        with st.expander("📋 最近一次文献质量评估报告", expanded=True):
            st.markdown(purge_thinking_text(q_rep["content"]))

# ============================================================
# 10. 板块四：逻辑链路
# ============================================================
def extract_first_nonempty_json_array(res, require_title=True):
    """从 LLM 返回中稳健提取第一个【非空】JSON 数组。
    处理 LLM 常见的多段输出：空数组[]、解释文字、多个数组拼接等。
    使用 json.JSONDecoder.raw_decode 逐位置尝试，保证只取完整合法的第一个非空数组。
    require_title=True 时要求数组至少含一个带 title 字段的 dict 节点，
    避免把思考过程中的文献编号数组（如 [4,10,17]）误当成大纲。"""
    if not res or not res.strip():
        return None
    # 剥离 Markdown 围栏
    cleaned = re.sub(r"```(?:json)?\s*", "", res)
    cleaned = re.sub(r"\s*```", "", cleaned).strip()
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(cleaned):
        ch = cleaned[idx]
        if ch == '[':
            try:
                obj, end = decoder.raw_decode(cleaned, idx)
                if isinstance(obj, list) and obj:
                    if require_title:
                        # 必须是"大纲节点"数组：至少一个元素是含 title 的 dict
                        if any(isinstance(x, dict) and x.get("title") for x in obj):
                            return obj
                        # 文献编号数组等非大纲数据：跳过，继续找下一个
                        idx = end
                        continue
                    return obj
                # 空数组或非数组对象：跳过，继续找下一个
                idx = end
                continue
            except json.JSONDecodeError:
                idx += 1
                continue
        idx += 1
    return None


def remove_node_from_tree(nodes, nid):
    """递归删除树中的指定节点"""
    for j, n in enumerate(nodes):
        if n.get("id") == nid:
            nodes.pop(j)
            return True
        if "children" in n and n["children"]:
            if remove_node_from_tree(n["children"], nid):
                return True
    return False


def build_literature_catalog(literatures, max_len=150):
    """把文献库转为带索引的清单文本，供 LLM 选择引用（索引从1开始）"""
    if not literatures:
        return "（文献库为空）"
    lines = []
    for i, lit in enumerate(literatures):
        title = lit.get("title", "未命名")
        cat = lit.get("category", "其他")
        rating = "⭐" * int(lit.get("rating", 0))
        findings = str(lit.get("analysis", {}).get("key_findings", ""))[:max_len]
        lines.append(f"[{i+1}] {title}（{cat} {rating}）{findings}")
    return "\n".join(lines)


def resolve_reference_indices(raw_refs, literatures):
    """把 LLM 输出的文献索引号解析为文献 ID 列表（非法索引自动跳过）"""
    ids = []
    if not raw_refs:
        return ids
    if isinstance(raw_refs, dict):
        raw_refs = raw_refs.get("references", [])
    if not isinstance(raw_refs, list):
        raw_refs = [raw_refs]
    for r in raw_refs:
        try:
            idx = int(r) - 1
            if 0 <= idx < len(literatures):
                ids.append(literatures[idx].get("id"))
        except (ValueError, TypeError):
            # 尝试按标题匹配
            for lit in literatures:
                if str(r) and str(r) in lit.get("title", ""):
                    ids.append(lit.get("id"))
                    break
    # 去重保序
    seen = set()
    return [x for x in ids if not (x in seen or seen.add(x))]


def get_literatures_by_ids(ids):
    """根据文献 ID 列表取文献对象，按 ID 顺序返回"""
    literatures = load_json_file("literatures.json", [])
    lit_map = {l.get("id"): l for l in literatures}
    return [lit_map[i] for i in ids if i in lit_map]


def refs_to_display(ids):
    """把文献 ID 列表转成可读的标题字符串"""
    lits = get_literatures_by_ids(ids)
    if not lits:
        return "（未绑定文献）"
    return " ｜ ".join([f"[{i+1}]{l.get('title','')}" for i, l in enumerate(lits)])


def render_logic_tree_editor(tree, nodes):
    """递归渲染可折叠的大纲节点编辑器：全部收起时仅见一级标题，展开后可编辑/新增/删除，逐层嵌套"""
    for node in nodes:
        nid = node.get("id")
        title = node.get("title", "未命名")
        own = "🧪 " if node.get("is_own_experiment") else ""
        wc = node.get("word_count", 0)
        wc_str = f"（{wc}字）" if wc else ""
        children = node.get("children") or []

        with st.expander(f"{own}{title}{wc_str}", expanded=False):
            # ---- 编辑本节点 ----
            with st.form(f"logic_edit_{nid}"):
                e_title = st.text_input("章节标题", value=node.get("title", ""), key=f"lt_{nid}")
                c1, c2 = st.columns(2)
                with c1:
                    e_wc = st.number_input("字数分配", min_value=0, max_value=100000, step=100,
                                           value=int(node.get("word_count", 0) or 0), key=f"lw_{nid}")
                    e_own = st.checkbox("🧪 本人实验/方法/数据相关章节",
                                        value=node.get("is_own_experiment", False), key=f"lo_{nid}")
                with c2:
                    e_desc = st.text_area("章节大致内容", value=node.get("desc", ""), height=90, key=f"ld_{nid}")
                e_chart = st.text_input("📊 图表绘制要求和指令", value=node.get("chart_instruction", ""), key=f"lc_{nid}")
                e_image = st.text_input("🖼️ 图片挂载建议和内容", value=node.get("image_suggestion", ""), key=f"li_{nid}")
                # 文献绑定选择
                st.caption("📚 本章参考文献（从文献库中选择）")
                all_lits = load_json_file("literatures.json", [])
                lit_name_map = {l.get("id"): f"[{l.get('category','其他')}] {l.get('title','')}" for l in all_lits}
                e_refs = st.multiselect(
                    "选择本章参考的文献",
                    list(lit_name_map.keys()),
                    default=[r for r in node.get("references", []) if r in lit_name_map],
                    format_func=lambda x: lit_name_map.get(x, x),
                    key=f"lref_{nid}"
                )
                if st.form_submit_button("💾 保存节点修改"):
                    node["title"] = e_title
                    node["word_count"] = int(e_wc)
                    node["desc"] = e_desc
                    node["chart_instruction"] = e_chart
                    node["image_suggestion"] = e_image
                    node["is_own_experiment"] = bool(e_own)
                    node["references"] = e_refs
                    save_json_file("logic_tree.json", tree)
                    st.success("节点已保存！")
                    st.rerun()

            # ---- 新增 / 删除 ----
            c_add, c_del = st.columns(2)
            with c_add:
                with st.expander("➕ 新增子节点", expanded=False):
                    new_title = st.text_input("新子节点标题", key=f"newt_{nid}")
                    if st.button("添加为子节点", key=f"addc_{nid}"):
                        node.setdefault("children", []).append({
                            "id": str(uuid.uuid4()), "title": new_title or "新节点", "desc": "",
                            "word_count": 0, "chart_instruction": "", "image_suggestion": "",
                            "is_own_experiment": False, "references": [], "children": []
                        })
                        save_json_file("logic_tree.json", tree)
                        st.rerun()
            with c_del:
                if st.button("🗑️ 删除本节点（含子节点）", key=f"deln_{nid}"):
                    if remove_node_from_tree(tree, nid):
                        save_json_file("logic_tree.json", tree)
                        st.rerun()

            # ---- 递归渲染子节点 ----
            if children:
                st.markdown("---")
                st.caption("📂 子节点（可继续展开）")
                render_logic_tree_editor(tree, children)


def module4_logic():
    st.header("🔗 逻辑链路")
    prompts = load_json_file("prompts.json", {})
    tree = load_json_file("logic_tree.json", [])

    # ---- 生成大纲 ----
    st.subheader("🤖 生成逻辑大纲")
    
    if st.button("🚀 基于科研基座与文献生成逻辑大纲", type="primary"):
        context = get_research_context_text()
        literatures = load_json_file("literatures.json", [])
        lit_catalog = build_literature_catalog(literatures)

        prompt = (
            "你是资深学术规划专家。请基于以下信息生成严密、完整的学术论文逻辑大纲。\n\n"
            f"【研究课题】{prompts.get('global_topic','（未设定）')}\n"
            f"【预期总字数】{prompts.get('target_word_count', 5000)}字\n\n"
            f"【科研基座】\n{context}\n\n"
            f"【已入库文献目录】\n{lit_catalog}\n\n"
            "要求：\n"
            '1. 输出 JSON 数组，每个节点结构为：{"id": "唯一id", "title": "章节标题", "desc": "章节大致内容", '
            '"word_count": 预估字数, "chart_instruction": "本章需绘制的图表要求(无则空字符串)", '
            '"image_suggestion": "本章建议挂载的图片内容(无则空字符串)", "is_own_experiment": false, '
            '"references": [文献目录中的索引号数组, 该章节应参考的文献, 如 [1,3,5], 无则空数组], "children": []}\n'
            "2. 所有章节 word_count 之和约等于预期总字数\n"
            "3. 涉及研究者本人实验/方法/数据的章节，is_own_experiment 设为 true\n"
            "4. 嵌套深度不超过 3 层\n"
            "5. 每个节点的 references 只能从上方文献目录中选取，禁止凭空编造文献\n"
            "6. 【字数规则】word_count 只分配给最底层的叶子节点（即没有 children 的节点）；有子节点的父节点 word_count 填 0，其字数由子节点之和体现，严禁父节点与子节点重复分配字数\n"
            "7. 所有叶子节点的 word_count 之和约等于预期总字数\n"
            "只输出纯 JSON 数组，禁止 Markdown 代码块，禁止任何解释。"
        )
        target_wc = int(prompts.get("target_word_count", 5000) or 5000)
        outline = None
        last_res = ""
        for attempt in range(3):
            with st.spinner(f"AI 正在深度规划逻辑大纲...（第{attempt+1}次尝试）"):
                res = dispatch_llm_call(prompt, system_prompt="你是一位拥有十年经验的期刊主编，只输出合法 JSON。", max_tokens=16000, json_mode=True)
                last_res = res
                # LLM 调用层面报错直接提示
                if res.startswith("API 密钥缺失") or res.startswith("API 连续调用失败") or res.startswith("OpenAI 客户端构造异常") or res.startswith("未配置"):
                    st.error(f"LLM 调用失败：{res}")
                    break
                tree = extract_first_nonempty_json_array(res)
                if tree is None:
                    continue
                # 顶层过滤 + 引用解析 + 字段规范化
                tree = [t for t in tree if isinstance(t, dict)]
                def resolve_refs(nodes):
                    for n in nodes:
                        if not isinstance(n, dict):
                            continue
                        raw = n.get("references", [])
                        if raw is None:
                            raw = []
                        if not isinstance(raw, list):
                            raw = [raw]
                        n["references"] = resolve_reference_indices(raw, literatures)
                        kids = n.get("children", [])
                        if isinstance(kids, list):
                            n["children"] = [c for c in kids if isinstance(c, dict)]
                            resolve_refs(n["children"])
                def normalize_tree(nodes):
                    for n in nodes:
                        if not isinstance(n, dict):
                            continue
                        own = n.get("is_own_experiment")
                        if isinstance(own, str):
                            n["is_own_experiment"] = own.strip().lower() in ("true", "1", "yes", "是")
                        else:
                            n["is_own_experiment"] = bool(own)
                        wc = n.get("word_count", 0)
                        if wc in (None, ""):
                            n["word_count"] = 0
                        else:
                            try:
                                n["word_count"] = int(str(wc).replace(",", "").replace("字", "").strip())
                            except (ValueError, TypeError):
                                n["word_count"] = 0
                        kids = n.get("children", [])
                        if isinstance(kids, list):
                            normalize_tree([k for k in kids if isinstance(k, dict)])
                resolve_refs(tree)
                normalize_tree(tree)
                # 字数归一化：父节点 word_count = 子节点之和（仅展示），字数只归属叶子节点
                def normalize_wc(nodes):
                    for n in nodes:
                        if not isinstance(n, dict):
                            continue
                        kids = [c for c in n.get("children", []) if isinstance(c, dict)]
                        if kids:
                            normalize_wc(kids)
                            n["word_count"] = sum(c.get("word_count", 0) or 0 for c in kids)
                normalize_wc(tree)
                # 字数校验：只统计叶子节点总字数，避免父子重复计数
                def sum_leaf_wc(nodes):
                    s = 0
                    for n in nodes:
                        if not isinstance(n, dict):
                            continue
                        kids = [c for c in n.get("children", []) if isinstance(c, dict)]
                        if kids:
                            s += sum_leaf_wc(kids)
                        else:
                            s += n.get("word_count", 0) or 0
                    return s
                total = sum_leaf_wc(tree)
                if total >= max(target_wc * 0.7, 1000):
                    outline = tree
                    break
                # 字数不达标：记录并重试
        if outline is not None:
            save_json_file("logic_tree.json", outline)
            st.success(f"逻辑大纲生成成功！共 {len(outline)} 个顶层章节，叶子节点规划总字数约 {sum_leaf_wc(outline)} 字。")
            st.rerun()
        else:
            res = last_res
            if res and not (res.startswith("API 密钥缺失") or res.startswith("API 连续调用失败") or res.startswith("OpenAI 客户端构造异常") or res.startswith("未配置")):
                # 智能识别：检测返回是否为 YAML/文本思考草稿
                if ("word_count:" in res) or ("- title:" in res) or ("title:" in res and "id:" in res):
                    st.error("未能解析出 JSON 大纲：当前模型返回的是 YAML 文本草稿而非 JSON（推理型模型特性）。建议在板块一将模型切换为 deepseek-chat 等常规对话模型后重试。")
                elif "未能从模型输出中解析" not in res:
                    st.error("生成的逻辑大纲字数远低于预期目标，多次尝试后仍未达标。可稍后重试，或检查板块一的目标字数设置。")
                with st.expander("查看 LLM 原始返回（调试用）", expanded=False):
                    st.code(res[:3000])

    st.markdown("---")

    # ---- 思维导图（横向排版） ----
    st.subheader("🌳 思维导图")
    if tree:
        mermaid_lines = ["graph LR"] + generate_mermaid_td(tree)
        mermaid_code = "\n".join(mermaid_lines)
        # 加大字体与节点间距，保证图片尺寸较大方便查看
        mermaid_code = "%%{init: {'theme': 'default', 'themeVariables': {'fontSize': '18px'}, 'flowchart': {'nodeSpacing': 40, 'rankSpacing': 60}}}%%\n" + mermaid_code
        st.markdown(f"```mermaid\n{mermaid_code}\n```")
        with st.expander("查看 Mermaid 源码", expanded=False):
            st.code(mermaid_code, language="mermaid")
    else:
        st.info("暂无大纲数据，请先生成逻辑大纲。")

    st.markdown("---")

    # ---- 节点编辑器（递归可折叠树形） ----
    st.subheader("✏️ 大纲节点编辑器")
    
    if not tree:
        st.warning("请先在上方生成逻辑大纲。")
    else:
        render_logic_tree_editor(tree, tree)


# ============================================================
# 11. 板块五：正文写作
# ============================================================
def generate_chart_for_node(node_id, instruction, max_attempts=3):
    """生成经过验证的图表并在成功后保存兼容记录。"""
    safe_node_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(node_id))[:64] or "chapter"
    chart_path = os.path.join(
        CHART_DIR, f"chart_{safe_node_id}_{int(time.time())}_{uuid.uuid4().hex[:8]}.png"
    )
    result = generate_chart_image(
        instruction,
        chart_path,
        dispatch_llm_call,
        locale="zh",
        max_attempts=max_attempts,
    )
    if result["status"] != "ok":
        return result

    charts = load_json_file("drafts_charts.json", {})
    if not isinstance(charts, dict):
        try:
            os.remove(chart_path)
        except OSError:
            pass
        return {"status": "error", "message": chart_result_message("error", "zh")}
    charts.setdefault(str(node_id), []).append({
        "path": chart_path,
        "instruction": instruction,
        "chart_type": result["spec"]["chart_type"],
        "data_note": result["spec"].get("data_note", ""),
    })
    if not save_json_file("drafts_charts.json", charts):
        try:
            os.remove(chart_path)
        except OSError:
            pass
        return {"status": "error", "message": chart_result_message("error", "zh")}
    return result



def build_chapter_prompt(sandwich, prompts, correction_note=None):
    """Build the Chinese chapter prompt with its bound reference metadata."""
    reference_literatures = get_literatures_by_ids(sandwich.get("current_refs", []) or [])
    return assemble_chapter_prompt(
        sandwich,
        prompts,
        reference_literatures,
        locale="zh",
        correction_note=correction_note,
    )


def generate_chapter_with_correction(sandwich, prompts, max_attempts=3, tolerance=0.5):
    """自动纠偏生成：生成后统计字数，偏差超过 tolerance 则带反馈重写，最多 max_attempts 次"""
    target = int(sandwich.get("current_word_count", 0) or 0)
    correction_note = None
    last_res = ""
    for attempt in range(max_attempts):
        res = require_valid_llm_output(
            dispatch_llm_call(build_chapter_prompt(sandwich, prompts, correction_note), max_tokens=4000)
        )
        last_res = res
        if target <= 0:
            return res, None  # 未分配字数，不纠偏
        actual = count_chinese_chars(res)
        deviation = abs(actual - target) / target
        if deviation <= tolerance:
            return res, {"attempts": attempt + 1, "actual": actual, "target": target, "deviation": deviation}
        correction_note = (
            f"上一轮生成约 {actual} 字，目标 {target} 字，偏差 {deviation:.0%} 超过允许范围（{tolerance:.0%}）。"
            f"第 {attempt + 1}/{max_attempts} 次重写，请调整篇幅：过短则补充论证与细节，过长则精简冗余。"
        )
    return last_res, {"attempts": max_attempts, "actual": count_chinese_chars(last_res), "target": target, "deviation": abs(count_chinese_chars(last_res) - target) / target if target else None}


def compress_memory(text):
    """把章节文本压缩为严格 150-200 字核心记忆，带长度校验与一次自动重试"""
    if not text or not text.strip():
        return ""
    prompt = (
        "请将以下文本压缩为**严格150-200个汉字**的核心记忆。"
        "要求：保留关键论点、核心数据、研究方法与重要结论；"
        "语言连贯成段，不要分点罗列；不要输出任何前缀、引号或解释，只输出记忆正文本身。\n\n"
        f"原文：\n{text[:3000]}"
    )
    res = require_valid_llm_output(dispatch_llm_call(prompt, max_tokens=500))
    res = res.strip().strip('"').strip("“”").strip()
    # 净化思考痕迹与提示词残渣（推理型模型可能把压缩指令本身混进输出）
    res = require_valid_llm_output(purge_thinking_text(res))
    # 长度校验：目标150-200字，宽容到120-280，不合格自动重试一次
    if not (120 <= len(res) <= 280):
        retry = require_valid_llm_output(dispatch_llm_call(
            f"上一轮压缩不符合字数要求（当前约{len(res)}字）。请重新输出，严格控制为150-200个汉字，只输出记忆正文本身：\n{text[:3000]}",
            max_tokens=500
        ))
        retry = retry.strip().strip('"').strip("“”").strip()
        retry = require_valid_llm_output(purge_thinking_text(retry))
        if 120 <= len(retry) <= 280:
            return retry
        return res if res else retry
    return res


def render_tree_nav(tree, nodes, sel_key="writing_selected_id", _drafts=None):
    """递归渲染可折叠的章节导航树：父章节仅作为容器（展开子章节），点击叶子节点按钮选中目标章节。
    已写章节（drafts.json 中有非空内容）会显示 ✅ 标记；父节点标题显示子树已写进度 (已写/总数)。"""
    if _drafts is None:
        _drafts = load_json_file("drafts.json", {})
    cur = st.session_state.get(sel_key)

    def _is_written(nid):
        txt = _drafts.get(nid, "")
        return bool(txt and txt.strip())

    def _count_written(sub_nodes):
        """统计子树中已写的叶子节点数，返回 (已写数, 叶子总数)"""
        nw, nt = 0, 0
        for sn in sub_nodes:
            sk = sn.get("children") or []
            if sk:
                sw, stt = _count_written(sk)
                nw += sw
                nt += stt
            else:
                nt += 1
                if _is_written(sn.get("id")):
                    nw += 1
        return nw, nt

    for node in nodes:
        nid = node.get("id")
        title = node.get("title", "未命名")
        own = "🧪 " if node.get("is_own_experiment") else ""
        children = node.get("children") or []
        if children:
            # 父节点：仅作为展开容器，标题显示子树已写进度
            nw, nt = _count_written(children)
            prog = f" ({nw}/{nt})" if nt else ""
            with st.expander(f"📂 {own}{title}{prog}", expanded=False, key=f"navexp_{sel_key}_{nid}"):
                render_tree_nav(tree, children, sel_key=sel_key, _drafts=_drafts)
        else:
            # 选中节点高亮 + 已写标记
            mark = "🟢 " if nid == cur else ""
            written = "✅ " if _is_written(nid) else ""
            if st.button(f"{mark}{written}{own}{title}", key=f"navsel_{sel_key}_{nid}", use_container_width=True):
                st.session_state[sel_key] = nid
                st.rerun()


def render_single_chapter_editor(tree):
    """单章编辑区（左侧可折叠层级导航）"""
    st.subheader("✍️ 单章写作与编辑")
    col_l, col_r = st.columns([1, 3])
    with col_l:
        st.caption("📑 章节导航")
        with st.container(height=600, border=True):
            render_tree_nav(tree, tree)
    sel_id = st.session_state.get("writing_selected_id")
    with col_r:
        node = get_node_by_id(tree, sel_id)
        if not node:
            st.info("请在左侧选择一个章节。")
            return
        st.subheader(node.get("title", ""))
        if node.get("is_own_experiment"):
            st.markdown("🧪 **本章为本人实验/方法/数据章节**")

        sandwich = build_context_sandwich(sel_id)
        with st.expander("👁️ 全局上下文", expanded=False):
            st.markdown(f"**【全局课题】**：{sandwich['global_topic']}")
            st.markdown(f"**【预期总字数】**：{sandwich['target_word_count']}")
            st.markdown(f"**【全局逻辑大纲】**")
            st.code(sandwich['global_outline'], language=None)
            st.markdown(f"**【上游实时记忆】**：\n{sandwich['upstream']}")
            st.markdown(f"**【下游已写/大纲边界（避免冲突）】**：\n{sandwich['downstream']}")
            st.markdown(f"**【本章绑定文献】**：{refs_to_display(sandwich['current_refs'])}")

        prompts = load_json_file("prompts.json", {})
        drafts = load_json_file("drafts.json", {})
        content_ver = st.session_state.get(f"content_ver_{sel_id}", 0)
        current_text = drafts.get(sel_id, "")
        new_text = st.text_area("正文编辑区（支持 Markdown）", value=current_text, height=400, key=f"draft_{sel_id}_v{content_ver}")
        for level, message in st.session_state.pop(f"writing_notice_{sel_id}", []):
            getattr(st, level)(message)

        c_save, c_gen = st.columns(2)
        with c_save:
            if st.button("💾 手动保存并凝练记忆", key=f"save_{sel_id}"):
                drafts[sel_id] = new_text
                save_json_file("drafts.json", drafts)
                reference_maps = load_json_file("draft_reference_maps.json", {})
                if sel_id not in reference_maps:
                    bound_literatures = get_literatures_by_ids(node.get("references", []) or [])
                    reference_maps[sel_id] = [lit.get("id") for lit in bound_literatures if lit.get("id") is not None]
                save_json_file("draft_reference_maps.json", reference_maps)
                notices = [("success", "正文已保存。")]
                try:
                    with st.spinner("正在压缩章节记忆 (150-200字)..."):
                        sum_res = compress_memory(new_text)
                    ds = load_json_file("drafts_summary.json", {})
                    ds[sel_id] = sum_res
                    save_json_file("drafts_summary.json", ds)
                    notices.append(("success", f"记忆池已更新（本章记忆 {len(sum_res)} 字）。"))
                except LLMOutputError as exc:
                    notices.append(("warning", f"正文已保存，但记忆凝练失败；之后可再次保存重试。({exc})"))
                st.session_state[f"writing_notice_{sel_id}"] = notices
                st.session_state[f"content_ver_{sel_id}"] = content_ver + 1
                st.rerun()
        with c_gen:
            if st.button("🤖 AI 生成本章", type="primary", key=f"gen_{sel_id}"):
                _kids = [c for c in (node.get("children") or []) if isinstance(c, dict)]
                if _kids:
                    st.warning("该章节为父节点（章节容器），请对其叶子子节点分别生成正文，父节点不单独生成。")
                else:
                    with st.spinner("AI 生成中（自动纠偏）..."):
                        try:
                            res, wc_info = generate_chapter_with_correction(sandwich, prompts)
                        except LLMOutputError as exc:
                            st.error(f"章节生成失败，请检查模型/API 配置后重试。（{exc}）")
                        else:
                            reference_maps = load_json_file("draft_reference_maps.json", {})
                            bound_literatures = get_literatures_by_ids(sandwich.get("current_refs", []) or [])
                            record_generated_draft(
                                drafts,
                                reference_maps,
                                sel_id,
                                res,
                                [lit.get("id") for lit in bound_literatures if lit.get("id") is not None],
                            )
                            save_json_file("drafts.json", drafts)
                            save_json_file("draft_reference_maps.json", reference_maps)
                            if wc_info:
                                generated_message = f"本章生成完毕！实际 {wc_info['actual']}字/目标{wc_info['target']}字（纠偏{wc_info['attempts']}次）。"
                            else:
                                generated_message = f"本章生成完毕！实际 {count_chinese_chars(res)}字（未分配目标字数）。"
                            notices = [("success", generated_message)]
                            try:
                                sum_res = compress_memory(res)
                            except LLMOutputError as exc:
                                notices.append(("warning", f"正文已保存，但记忆凝练失败；之后可再次保存重试。({exc})"))
                            else:
                                ds = load_json_file("drafts_summary.json", {})
                                ds[sel_id] = sum_res
                                save_json_file("drafts_summary.json", ds)
                                notices.append(("success", f"记忆池已更新（本章记忆 {len(sum_res)} 字）。"))
                            st.session_state[f"writing_notice_{sel_id}"] = notices
                            st.session_state[f"content_ver_{sel_id}"] = content_ver + 1
                            st.rerun()

        # ---- 图表 ----
        st.markdown("---")
        st.markdown("### 📊 本章图表")
        chart_prompt = st.text_input(
            "图表绘制要求",
            value=node.get("chart_instruction", ""),
            help="例如：绘制柱状图比较 A=35、B=42、C=28。",
            key=f"chartin_{sel_id}",
        )
        if st.button("🎨 生成图表并挂载本章", key=f"genchart_{sel_id}"):
            if not chart_prompt.strip():
                st.warning("请输入图表绘制要求。")
            else:
                with st.spinner("AI 正在准备图表..."):
                    result = generate_chart_for_node(sel_id, chart_prompt)
                    if result["status"] == "ok":
                        st.success("图表生成并挂载成功！")
                        st.rerun()
                    elif result["status"] == "needs_data":
                        st.info(result["message"])
                    else:
                        st.error(result["message"])

        dc = load_json_file("drafts_charts.json", {})
        charts = dc.get(sel_id, [])
        if charts:
            st.markdown("**已挂载图表：**")
            for ci, ch in enumerate(charts):
                if os.path.exists(ch.get("path", "")):
                    # 不自动加图题/序号，图题留给用户在文档中自行编辑调整
                    st.image(ch["path"])
                    if st.button("🗑️ 删除此图表", key=f"delchart_{sel_id}_{ci}"):
                        dc[sel_id].pop(ci)
                        save_json_file("drafts_charts.json", dc)
                        st.rerun()

        # ---- 图片挂载 ----
        st.markdown("### 🖼️ 本章图片挂载")
        with st.expander("📤 上传图片并附解释", expanded=False):
            up_img = st.file_uploader("上传图片", type=["png", "jpg", "jpeg"], key=f"imgu_{sel_id}")
            img_caption = st.text_input("图片解释内容（供 LLM 理解）", key=f"imgc_{sel_id}")
            if up_img and st.button("📌 挂载图片", key=f"imgmount_{sel_id}"):
                img_path = os.path.join(IMAGE_DIR, f"{sel_id}_{int(time.time())}.png")
                with open(img_path, "wb") as f:
                    f.write(up_img.getbuffer())
                di = load_json_file("drafts_images.json", {})
                di.setdefault(sel_id, []).append({"path": img_path, "caption": img_caption})
                save_json_file("drafts_images.json", di)
                st.success("图片已挂载！")
                st.rerun()

        di = load_json_file("drafts_images.json", {})
        imgs = di.get(sel_id, [])
        if imgs:
            st.markdown("**已挂载图片：**")
            for ii, im in enumerate(imgs):
                if os.path.exists(im.get("path", "")):
                    st.image(im["path"], caption=im.get("caption", "") or None)
                    if st.button("🗑️ 删除此图片", key=f"delimg_{sel_id}_{ii}"):
                        di[sel_id].pop(ii)
                        save_json_file("drafts_images.json", di)
                        st.rerun()

def _batch_select_all(prefix, all_key):
    """全选/取消全选某个队列的所有叶子章节（父节点为章节容器，不参与正文/图表生成队列）"""
    val = st.session_state.get(all_key, False)
    tree = load_json_file("logic_tree.json", [])
    for node, _ in flatten_tree_nodes(tree):
        nid = node.get("id")
        kids = [c for c in (node.get("children") or []) if isinstance(c, dict)]
        if kids:
            # 父节点：正文队列不勾选；图表队列也不勾选（图表挂在叶子节点）
            st.session_state[f"bchk_{prefix}_{nid}"] = False
        else:
            st.session_state[f"bchk_{prefix}_{nid}"] = val


def render_batch_workbench():
    """批量生成工作台"""
    st.markdown("---")
    st.subheader("⚡ 批量生成工作台")
    
    prompts = load_json_file("prompts.json", {})
    tree = load_json_file("logic_tree.json", [])
    flat = flatten_tree_nodes(tree)
    ordered_ids = get_ordered_node_ids(tree)

    if not flat:
        st.warning("逻辑大纲为空，请先前往板块四生成。")
        return

    # 两个独立队列：正文生成队列 + 图表绘制队列，各自可全选
    c_all_text, c_all_chart = st.columns(2)
    with c_all_text:
        st.checkbox(
            "✅ 全选正文生成队列",
            key="batch_select_all_text",
            on_change=_batch_select_all,
            args=("text", "batch_select_all_text"),
            help="勾选后所有章节加入正文生成队列"
        )
    with c_all_chart:
        st.checkbox(
            "✅ 全选图表绘制队列",
            key="batch_select_all_chart",
            on_change=_batch_select_all,
            args=("chart", "batch_select_all_chart"),
            help="勾选后所有章节加入图表绘制队列"
        )

    # 每章节：两个独立勾选（正文队列/图表队列）+ 图表要求 + 图片上传
    selected_text = {}
    selected_chart = {}
    per_node_chart = {}
    per_node_img = {}
    # 提前读取各章节勾选状态，用于在折叠标题上显示队列标记
    sel_map_text = {}
    sel_map_chart = {}
    for node, _ in flat:
        nid = node.get("id")
        sel_map_text[nid] = st.session_state.get(f"bchk_text_{nid}", False)
        sel_map_chart[nid] = st.session_state.get(f"bchk_chart_{nid}", False)

    # 树状折叠渲染：父节点仅作展开容器（不显示勾选），叶子节点才可加入队列
    def _render_batch_level(nodes):
        for node in nodes:
            nid = node.get("id")
            kids = [c for c in (node.get("children") or []) if isinstance(c, dict)]
            own = "🧪 " if node.get("is_own_experiment") else ""
            wc = node.get("word_count", 0)
            wc_str = f" ({wc}字)" if wc else ""
            if kids:
                # 父节点：章节容器，仅折叠展示子节点，不参与正文/图表队列
                label = f"📂 {own}{node.get('title','')}{wc_str}"
                with st.expander(label, expanded=False, key=f"bexp_{nid}"):
                    _render_batch_level(kids)
            else:
                # 叶子节点：真正的内容实体，可加入正文/图表队列
                t_mark = "📝" if sel_map_text.get(nid) else "　"
                c_mark = "📊" if sel_map_chart.get(nid) else "　"
                label = f"{own}{node.get('title','')}{wc_str}  {t_mark}{c_mark}"
                with st.expander(label, expanded=False, key=f"beleaf_{nid}"):
                    cq1, cq2 = st.columns(2)
                    with cq1:
                        is_sel_text = st.checkbox("📝 加入正文生成队列", key=f"bchk_text_{nid}")
                    with cq2:
                        is_sel_chart = st.checkbox("📊 加入图表绘制队列", key=f"bchk_chart_{nid}")
                    selected_text[nid] = is_sel_text
                    selected_chart[nid] = is_sel_chart
                    st.markdown("**本章图表与图片配置**")
                    per_node_chart[nid] = st.text_input(
                        "📊 图表绘制要求",
                        value=node.get("chart_instruction", ""),
                        key=f"bchart_{nid}"
                    )
                    up_img = st.file_uploader("🖼️ 上传图片", type=["png", "jpg", "jpeg"], key=f"bimg_{nid}")
                    img_cap = st.text_input("图片解释内容", key=f"bcap_{nid}")
                    if up_img:
                        per_node_img[nid] = (up_img, img_cap)

    with st.container(border=True):
        _render_batch_level(tree)

    # ---- 批量生成正文 ----
    if st.button("📝 批量生成正文（逐章循环）", type="primary"):
        sel_ids = [nid for nid in ordered_ids if selected_text.get(nid)]
        if not sel_ids:
            st.warning("请先在正文生成队列中勾选至少一个章节。")
        else:
            # 过滤掉父节点（章节容器）：字数只归叶子节点，父节点不生成正文
            leaf_ids = []
            skipped = []
            for nid in sel_ids:
                node = get_node_by_id(tree, nid)
                kids = [c for c in (node.get("children") or []) if isinstance(c, dict)]
                if kids:
                    skipped.append(node.get("title", ""))
                else:
                    leaf_ids.append(nid)
            if not leaf_ids:
                st.warning("所选章节均为父节点（章节容器）。请勾选叶子节点（没有子节点的章节）生成正文。")
            else:
                drafts = load_json_file("drafts.json", {})
                ds = load_json_file("drafts_summary.json", {})
                reference_maps = load_json_file("draft_reference_maps.json", {})
                progress_bar = st.progress(0)
                status = st.empty()
                results_note = []
                failed_chapters = []
                memory_warnings = []
                for k, nid in enumerate(leaf_ids):
                    node = get_node_by_id(tree, nid)
                    status.info(f"📝 正在生成第 {k+1}/{len(leaf_ids)} 章：{node.get('title','')} ...")
                    sandwich = build_context_sandwich(nid)
                    try:
                        res, wc_info = generate_chapter_with_correction(sandwich, prompts)
                    except LLMOutputError as exc:
                        failed_chapters.append(f"{node.get('title','未命名')}: {exc}")
                        progress_bar.progress((k + 1) / len(leaf_ids))
                        continue
                    bound_literatures = get_literatures_by_ids(sandwich.get("current_refs", []) or [])
                    record_generated_draft(
                        drafts,
                        reference_maps,
                        nid,
                        res,
                        [lit.get("id") for lit in bound_literatures if lit.get("id") is not None],
                    )
                    save_json_file("drafts.json", drafts)
                    save_json_file("draft_reference_maps.json", reference_maps)
                    if wc_info:
                        results_note.append(f"{node.get('title','')}: {wc_info['actual']}字/目标{wc_info['target']}字（纠偏{wc_info['attempts']}次）")
                    else:
                        results_note.append(f"{node.get('title','')}: {count_chinese_chars(res)}字（无目标）")
                    try:
                        sum_res = compress_memory(res)
                    except LLMOutputError as exc:
                        memory_warnings.append(f"{node.get('title','未命名')}: {exc}")
                    else:
                        ds[nid] = sum_res
                        save_json_file("drafts_summary.json", ds)
                    progress_bar.progress((k + 1) / len(leaf_ids))
                msg = f"批量生成结束：成功 {len(results_note)} 章，失败 {len(failed_chapters)} 章。"
                if results_note:
                    msg += "\n" + "\n".join(results_note)
                if skipped:
                    msg += f"\n\n（已跳过 {len(skipped)} 个父节点容器：{'、'.join(skipped[:5])}）"
                if failed_chapters:
                    status.warning(msg)
                    st.error("生成失败的章节：\n" + "\n".join(failed_chapters))
                else:
                    status.success(msg)
                if memory_warnings:
                    st.warning("正文已保存，但以下章节的记忆凝练失败：\n" + "\n".join(memory_warnings))

    # ---- 批量生成图表（独立选项，走图表绘制队列） ----
    if st.button("📊 批量生成图表"):
        sel_ids = [nid for nid in ordered_ids if selected_chart.get(nid)]
        if not sel_ids:
            st.warning("请先在图表绘制队列中勾选至少一个章节。")
        else:
            progress_bar = st.progress(0)
            status = st.empty()
            ok_count = 0
            needs_data_chapters = []
            failed_chapters = []
            for k, nid in enumerate(sel_ids):
                node = get_node_by_id(tree, nid)
                instruction = per_node_chart.get(nid, "") or node.get("chart_instruction", "")
                if instruction.strip():
                    status.info(f"📊 正在为《{node.get('title','')}》生成图表 ...")
                    result = generate_chart_for_node(nid, instruction)
                    if result["status"] == "ok":
                        ok_count += 1
                    elif result["status"] == "needs_data":
                        needs_data_chapters.append(node.get("title", "未命名章节"))
                    else:
                        failed_chapters.append(node.get("title", "未命名章节"))
                progress_bar.progress((k + 1) / len(sel_ids))
            st.session_state["chart_batch_notice"] = {
                "ok_count": ok_count,
                "needs_data": needs_data_chapters,
                "failed": failed_chapters,
            }
            st.rerun()

    chart_batch_notice = st.session_state.pop("chart_batch_notice", None)
    if chart_batch_notice:
        st.success(f"✅ 图表批量生成完成，成功 {chart_batch_notice['ok_count']} 张。")
        if chart_batch_notice["needs_data"]:
            st.info(chart_result_message("needs_data", "zh") + " 章节：" + "、".join(chart_batch_notice["needs_data"]))
        if chart_batch_notice["failed"]:
            st.warning("以下章节图表生成失败：" + "、".join(chart_batch_notice["failed"]))

    # ---- 批量挂载图片 ----
    if per_node_img and st.button("🖼️ 批量挂载上传的图片"):
        di = load_json_file("drafts_images.json", {})
        for nid, (up_img, img_cap) in per_node_img.items():
            img_path = os.path.join(IMAGE_DIR, f"{nid}_{int(time.time())}.png")
            with open(img_path, "wb") as f:
                f.write(up_img.getbuffer())
            di.setdefault(nid, []).append({"path": img_path, "caption": img_cap})
        save_json_file("drafts_images.json", di)
        st.success("图片批量挂载完成！")
        st.rerun()

    # ---- 完成后直接生成文档（快捷入口） ----
    st.markdown("---")
    st.subheader("📄 完成后直接生成文档")
    
    tpl_names = list_templates()
    tpl_opts = ["（不使用模板，新建空白文档）"] + tpl_names
    tpl_sel = st.selectbox("排版模板（可选）", tpl_opts, key="quick_tpl")
    with st.expander("⚙️ 排版参数（默认：宋体/黑体、1.5倍行距、首行缩进2字符）", expanded=False):
        q_ls = st.number_input("行距", min_value=1.0, max_value=3.0, value=1.5, step=0.1, key="quick_ls")
        q_fi = st.number_input("首行缩进（字符数）", min_value=0, max_value=4, value=2, step=1, key="quick_fi")
        q_font = st.text_input("正文字体", value="宋体", key="quick_font")
        q_hfont = st.text_input("标题字体", value="黑体", key="quick_hfont")
        q_size = st.number_input("正文字号（磅）", min_value=9, max_value=22, value=12, step=1, key="quick_size")
        q_align = st.selectbox("对齐方式", ["justify", "left", "center", "right"], index=0, key="quick_align")
    if st.button("📥 立即生成 Word 文档", type="primary", key="quick_gen_doc"):
        if not flatten_tree_nodes(load_json_file("logic_tree.json", [])):
            st.warning("逻辑大纲为空，无法导出。请先在板块四生成大纲。")
        else:
            with st.spinner("Formatting Agent 正在装配终稿..."):
                params = {
                    "line_spacing": float(q_ls), "first_line_indent": int(q_fi),
                    "font_name": q_font, "font_size": int(q_size),
                    "heading_font": q_hfont, "alignment": q_align
                }
                bio = build_final_document(None if tpl_sel.startswith("（不使用") else tpl_sel, params)
                st.success("终稿装配完成！")
                st.download_button(
                    "📥 下载排版完成的 Word 终稿",
                    data=bio,
                    file_name="Refined_Scholar_Final.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    key="quick_dl"
                )


def _scan_structure_issues(tree, drafts, target_wc):
    """浅度审查-规则扫描：纯确定性检查，秒出结果，不依赖 LLM。
    返回 (issues列表, summary统计dict)"""
    import re
    issues = []
    flat = flatten_tree_nodes(tree)
    # 收集标题用于查重
    seen_titles = {}
    leaf_ids = []
    parent_ids = []
    for node, depth in flat:
        title = (node.get("title") or "").strip()
        nid = node.get("id")
        kids = [c for c in (node.get("children") or []) if isinstance(c, dict)]
        if kids:
            parent_ids.append((nid, title, depth))
        else:
            leaf_ids.append((nid, title, depth, node))
        # 标题查重
        if title:
            seen_titles.setdefault(title, []).append(nid)
    # 1) 重复标题
    for title, ids in seen_titles.items():
        if len(ids) > 1:
            issues.append({"level": "🟡", "where": f"{title}", "problem": f"标题重复出现于 {len(ids)} 个节点（{'、'.join(ids)}）"})
    # 2) 标题长度异常
    for nid, title, depth, node in leaf_ids:
        if title and len(title) < 4:
            issues.append({"level": "🟡", "where": f"{title}", "problem": "标题过短（<4字），可能无法准确概括章节内容"})
        elif title and len(title) > 30:
            issues.append({"level": "🟡", "where": f"{title[:15]}...", "problem": "标题过长（>30字），建议精简"})
    # 3) 空描述章节
    for node, depth in flat:
        desc = (node.get("desc") or "").strip()
        if not desc:
            issues.append({"level": "🔴", "where": f"{node.get('title','')}", "problem": "章节缺少内容描述（desc 为空），可能无法指导正文生成"})
    # 4) 叶子节点未分配字数
    for nid, title, depth, node in leaf_ids:
        wc = node.get("word_count", 0) or 0
        if wc <= 0:
            issues.append({"level": "🔴", "where": f"{title}", "problem": "叶子节点未分配字数（word_count=0），正文生成将无字数约束"})
    # 5) 字数健康度：叶子总字数 vs 目标
    leaf_total = sum((n.get("word_count", 0) or 0) for _, _, _, n in leaf_ids)
    if target_wc and leaf_total > 0:
        dev = (leaf_total - target_wc) / target_wc
        if abs(dev) > 0.5:
            issues.append({"level": "🔴", "where": "全局", "problem": f"叶子节点规划总字数 {leaf_total} 与目标 {target_wc} 偏差 {dev:.0%}（超过±50%容差）"})
    # 6) 单章字数超容差（有正文时）
    for nid, title, depth, node in leaf_ids:
        wc = node.get("word_count", 0) or 0
        text = drafts.get(nid, "")
        if wc > 0 and text:
            actual = count_chinese_chars(text)
            dev = abs(actual - wc) / wc
            if dev > 0.5:
                issues.append({"level": "🟡", "where": f"{title}", "problem": f"正文 {actual}字 与规划 {wc}字 偏差 {dev:.0%}（超±50%容差）"})
    # 7) 孤章节：叶子节点既无文献引用又无图表指令又无图片建议
    for nid, title, depth, node in leaf_ids:
        refs = node.get("references") or []
        chart = (node.get("chart_instruction") or "").strip()
        img = (node.get("image_suggestion") or "").strip()
        if not refs and not chart and not img:
            issues.append({"level": "🟢", "where": f"{title}", "problem": "孤章节：未绑定文献、无图表指令、无图片建议，内容支撑可能偏弱"})
    # 8) 图表引用但无图表指令：正文提到「如图」但大纲无 chart_instruction
    for nid, title, depth, node in leaf_ids:
        text = drafts.get(nid, "")
        chart = (node.get("chart_instruction") or "").strip()
        if text and not chart and re.search(r"[图表][\d一二三四五六七八九十]*[：:]?[\s]|如图|如表|下图|下表", text):
            issues.append({"level": "🟡", "where": f"{title}", "problem": "正文疑似引用了图表/表格，但大纲未配置图表绘制指令（chart_instruction 为空）"})

    # 统计摘要
    summary = {
        "total_nodes": len(flat),
        "parent_nodes": len(parent_ids),
        "leaf_nodes": len(leaf_ids),
        "leaf_total_wc": leaf_total,
        "target_wc": target_wc,
        "draft_count": sum(1 for nid, _, _, _ in leaf_ids if drafts.get(nid)),
        "red": sum(1 for i in issues if i["level"] == "🔴"),
        "yellow": sum(1 for i in issues if i["level"] == "🟡"),
        "green": sum(1 for i in issues if i["level"] == "🟢"),
    }
    return issues, summary


def _light_llm_scan(tree, drafts, prompts):
    """浅度审查-轻量 LLM 扫描：喂大纲+章节摘要，限制输出短清单。
    只抓明显问题：明显重复、明显跑题、术语混用。"""
    target_wc = int(prompts.get("target_word_count", 5000) or 5000)
    ds = load_json_file("drafts_summary.json", {})
    outline_lines = []
    for node, depth in flatten_tree_nodes(tree):
        indent = "  " * depth
        nid = node.get("id")
        summ = (ds.get(nid) or "")[:80]
        outline_lines.append(f"{indent}- {node.get('title','')}（{node.get('word_count',0) or 0}字）{('｜摘要:'+summ) if summ else ''}")
    outline_text = "\n".join(outline_lines)
    prompt = (
        f"研究课题：{prompts.get('global_topic','未设定')}（预期总字数 {target_wc}）\n"
        "以下是论文的章节大纲（含字数与摘要），请做一次【快速浅层扫描】，只找出最容易发现的明显问题：\n"
        "1. 明显重复：两章以上内容高度重合（仅标最明显的1-2处）\n"
        "2. 明显跑题：与全局课题明显偏离的章节（仅标偏离最明显的1-2处）\n"
        "3. 术语混用：关键术语（如数据资产/数据资源/工业知识）的明显前后不一（仅标最明显的1-2处）\n\n"
        "【输出要求】\n"
        "- 直接输出问题清单，禁止思考过程与解释性开场白。\n"
        "- 每条一行，格式：🔴/🟡 + 章节名 + 问题简述（40字内）。\n"
        "- 没有明显问题时输出「未发现明显问题」。\n"
        "- 总输出控制在 300 字以内，宁缺毋滥。\n\n"
        "【章节大纲】\n" + outline_text
    )
    res = dispatch_llm_call(prompt, system_prompt="你是一个快速扫描型学术审稿助理，只输出简短问题清单。", max_tokens=1000, temp=0.3)
    return purge_thinking_text(res)


def render_full_logic_review():
    """逻辑审查双模式：浅度审查（体检式快速扫描）+ 深度审查（分块逐章深挖）"""
    st.markdown("---")
    st.subheader("🧠 逻辑审查（浅度 / 深度）")
    

    tree = load_json_file("logic_tree.json", [])
    drafts = load_json_file("drafts.json", {})
    prompts = load_json_file("prompts.json", {})
    target_wc = int(prompts.get("target_word_count", 5000) or 5000)

    c_light, c_deep = st.columns(2)
    # ---- 浅度审查 ----
    with c_light:
        st.markdown("#### ⚡ 浅度审查（体检）")
        if st.button("🔎 执行浅度审查", key="light_review_btn"):
            if not flatten_tree_nodes(tree):
                st.warning("逻辑大纲为空，请先在板块四生成大纲。")
            else:
                with st.spinner("规则扫描中..."):
                    issues, summary = _scan_structure_issues(tree, drafts, target_wc)
                # 结果存入 session_state，保证重跑后仍然渲染
                st.session_state["light_issues"] = issues
                st.session_state["light_summary"] = summary
                st.session_state["light_llm_result"] = None
        # 渲染规则扫描结果（不依赖按钮即时返回值）
        if st.session_state.get("light_issues") is not None:
            issues = st.session_state["light_issues"]
            summary = st.session_state["light_summary"]
            with st.expander("📋 浅度审查报告（规则扫描）", expanded=True):
                st.markdown(
                    f"**节点概况**：共 {summary['total_nodes']} 节点（父 {summary['parent_nodes']} / 叶 {summary['leaf_nodes']}）｜"
                    f"叶子规划 {summary['leaf_total_wc']}字 / 目标 {summary['target_wc']}字｜已生成正文 {summary['draft_count']}/{summary['leaf_nodes']}"
                )
                if not issues:
                    st.success("✅ 规则扫描未发现明显问题。")
                else:
                    st.markdown(f"**发现 {len(issues)} 项**（🔴{summary['red']} 🟡{summary['yellow']} 🟢{summary['green']}）：")
                    for it in issues:
                        st.markdown(f"{it['level']} **{it['where']}**：{it['problem']}")
            # 轻量 LLM 扫描按钮 + 结果（同样用 session_state 稳定渲染）
            if st.button("🤖 轻量 LLM 扫描（重复/跑题/术语）", key="light_llm_btn"):
                with st.spinner("LLM 快速扫描中..."):
                    res = _light_llm_scan(tree, drafts, prompts)
                st.session_state["light_llm_result"] = purge_thinking_text(res)
            if st.session_state.get("light_llm_result"):
                st.markdown(st.session_state["light_llm_result"])

    # ---- 深度审查 ----
    with c_deep:
        st.markdown("#### 🧠 深度审查（逐章深挖）")
        
        if st.button("🔍 执行深度审查", key="deep_review_btn"):
            content_parts = []
            for node, depth in flatten_tree_nodes(tree):
                nid = node.get("id")
                text = drafts.get(nid, "")
                if text:
                    content_parts.append((node, depth, text))
            if not content_parts:
                st.warning("还没有已生成的正文，无法深度审查。请先生成至少一章正文。")
            else:
                with st.spinner("深度审查中：按顶层章节分块逐组审查..."):
                    top_groups = {}
                    for node, depth in flatten_tree_nodes(tree):
                        if depth == 0:
                            top_groups[node.get("id")] = node
                    block_reports = []
                    # 按顶层章节名分组
                    top_titles = [n.get("title", "") for n, d in flatten_tree_nodes(tree) if d == 0]
                    groups = {t: [] for t in top_titles}
                    # 简单按顺序归属：叶子节点挂在最近的顶层标题下
                    current_top = None
                    for node, depth in flatten_tree_nodes(tree):
                        if depth == 0:
                            current_top = node.get("title", "未分组")
                            groups.setdefault(current_top, [])
                        else:
                            nid = node.get("id")
                            text = drafts.get(nid, "")
                            if text and current_top:
                                groups[current_top].append(f"### {node.get('title','')}\n{text[:1200]}")
                    bar = st.progress(0)
                    total_blocks = len([g for g, parts in groups.items() if parts])
                    done = 0
                    for gtitle, parts in groups.items():
                        if not parts:
                            continue
                        block_prompt = (
                            "你是严格的学术逻辑审查官。请审查以下【一个章节块】的正文，重点检查：\n"
                            "1. 块内各小节逻辑是否连贯、有无断裂或跳跃\n"
                            "2. 论证是否完整、有无明显的重复表述或前后矛盾\n"
                            "3. 是否紧扣全局课题\n\n"
                            "【输出要求】\n"
                            "- 直接输出审查结论，禁止思考过程与解说性开场白。\n"
                            "- 简洁条目式，每条 = 问题 + 具体位置 + 修改建议（40字内）。\n"
                            "- 无明显问题时写「本节块未发现明显问题」。\n"
                            "- 总输出控制在 400 字以内。\n\n"
                            f"【章节块：{gtitle}】\n" + "\n".join(parts)
                        )
                        res = dispatch_llm_call(block_prompt, system_prompt="你是极端严谨的学术论文逻辑审查专家。", max_tokens=1500, temp=0.3)
                        cleaned = purge_thinking_text(res)
                        block_reports.append(f"### 📦 章节块：{gtitle}\n\n{cleaned}")
                        done += 1
                        bar.progress(done / total_blocks)
                # 汇总
                final_report = "## 深度逻辑审查汇总报告\n\n" + "\n\n".join(block_reports)
                save_json_file("full_logic_review.json", {"content": final_report})
                st.success(f"深度审查完成！共 {len(block_reports)} 个章节块。")
                with st.expander("📋 深度审查汇总报告", expanded=True):
                    st.markdown(final_report)

    # ---- 历史报告 ----
    review = load_json_file("full_logic_review.json", {"content": ""})
    if review.get("content"):
        st.markdown("---")
        with st.expander("📋 最近一次审查报告", expanded=True):
            st.markdown(purge_thinking_text(review["content"]))


def module5_writing():
    st.header("📝 正文写作")
    tree = load_json_file("logic_tree.json", [])
    flat = flatten_tree_nodes(tree)

    if not flat:
        st.warning("逻辑大纲为空，请先前往板块四生成或编辑大纲。")
        return

    # 初始化当前选中章节（防止导航状态失效）
    ordered_ids = get_ordered_node_ids(tree)
    current_sel = st.session_state.get("writing_selected_id")
    if current_sel not in ordered_ids:
        st.session_state["writing_selected_id"] = ordered_ids[0]

    render_single_chapter_editor(tree)
    render_batch_workbench()
    render_full_logic_review()


# ============================================================
# 12. 板块六：AIGC 检测与去味
# ============================================================
def adversarial_rewrite(text, rewrite_prompt, max_attempts=3):
    """对抗闭环去味：检测反馈驱动重写（检测→反馈原因→针对性修正→再检测）。
    每一轮检测的判定与理由都会注入下一轮重写 prompt，使重写不再是孤立套用提示词，
    而是针对检测报告点名的 AI 特征逐条精准修正。
    输出约束：禁止思考过程/解说性文字，结果经 purge_thinking_text 净化兜底，防止推理型模型漏出推理链。
    乱码防护：输入为乱码时直接返回原文，不做重写。"""
    if is_garbled_text(text):
        return text, False, 0, None
    curr_text = text
    success = False
    final_score = None
    feedback = None  # 上一轮检测报告（含判定与理由）
    # 硬性输出约束：直接给重写后正文，杜绝思考/解说/草稿
    output_rule = (
        "\n\n【输出硬性要求】\n"
        "1. 直接输出重写后的正文，开头即正文内容，禁止任何思考过程、推理链、解说、草稿或前缀。\n"
        "2. 禁止出现以下内容：我来重写、首先、让我们、好的、下面、我将、分析一下、这段文字、改写要点、说明 等过程性表述。\n"
        "3. 全文长度与原文相当，不得截断；禁止输出省略号+「（未完）」之类的中断标记。\n"
        "4. 输出即终稿，无任何注释或补充说明。"
    )
    for attempt in range(max_attempts):
        if attempt == 0:
            prompt = f"{rewrite_prompt}{output_rule}\n待处理文本：\n{curr_text}"
        else:
            # 第二轮起：注入上一轮检测报告，针对性修正被点名的 AI 特征
            prompt = (
                f"{rewrite_prompt}{output_rule}\n"
                f"\n【检测反馈（上一轮）】\n{feedback}\n"
                f"【修正指令】\n"
                "1. 逐一阅读上述检测反馈，针对其指出的每一个 AI 特征（如句式工整、排比密集、套路连接词、表达空洞、用词机械等）进行针对性修正。\n"
                "2. 不要只套用通用改写规则，必须解决检测反馈中点名的具体问题。\n"
                "3. 修正后通读全文，确保没有引入新的机械痕迹，保持内容与数据完整。\n\n"
                f"待处理文本：\n{curr_text}"
            )
        temp_val = 0.85 + (attempt * 0.1)
        # 根据原文长度动态调整输出预算：正文约原文1.2倍token + 思考余量
        out_tokens = max(8000, int(len(text) * 1.8) + 4000)
        rewritten = require_valid_llm_output(dispatch_llm_call(
            prompt,
            system_prompt="你是极其克制的顶级学术校对员，只输出重写后的正文，绝不输出任何思考过程。",
            max_tokens=out_tokens,
            temp=temp_val
        ))
        # 清理后再次验证，避免空结果进入检测或保存流程。
        rewritten = require_valid_llm_output(purge_thinking_text(rewritten))
        score_dict = detect_aigc(rewritten)
        final_score = score_dict["score"]
        if score_dict["score"] < 40.0:
            success = True
            return rewritten, True, attempt + 1, final_score
        # 未达标：把检测报告（含判断理由）留作下一轮重写反馈
        raw = score_dict.get("raw", "")
        feedback = raw if raw else f"AI概率 {score_dict['score']}%，判定：{score_dict.get('label', '未知')}"
        curr_text = rewritten
    return curr_text, False, max_attempts, final_score


# ============================================================
# 12.5 终稿导出与排版辅助函数
# ============================================================
def list_templates():
    """列出已上传的模板文件"""
    if not os.path.exists(TEMPLATE_DIR):
        return []
    return [f for f in os.listdir(TEMPLATE_DIR) if f.endswith(".docx")]


def save_uploaded_template(uploaded_file):
    """保存上传的模板到 templates 目录，返回文件名"""
    fname = uploaded_file.name
    if not fname.endswith(".docx"):
        return None, "模板必须是 .docx 文件"
    path = os.path.join(TEMPLATE_DIR, fname)
    with open(path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return fname, None


def remove_template(fname):
    """卸载（删除）指定模板"""
    path = os.path.join(TEMPLATE_DIR, fname)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def extract_format_params(format_prompt):
    """LLM 从自然语言格式要求中提取排版参数 JSON，带兜底"""
    defaults = {
        "line_spacing": 1.5,
        "first_line_indent": 2,
        "font_name": "宋体",
        "font_size": 12,
        "heading_font": "黑体",
        "alignment": "justify"
    }
    if not format_prompt or not format_prompt.strip():
        return defaults
    prompt = (
        "你是一个 Word 排版参数提取器。请分析用户的自然语言格式指令，严格只输出合法纯 JSON：\n"
        '{"line_spacing": 浮点数如1.5, "first_line_indent": 首行缩进字符数整数如2, '
        '"font_name": 正文字体名如"宋体", "font_size": 正文字号磅值整数如12, '
        '"heading_font": 标题字体名如"黑体", "alignment": "left/center/right/justify"}\n'
        "用户指令：\n" + format_prompt
    )
    res = dispatch_llm_call(prompt, system_prompt="严格遵守 JSON 协议，不可有废话。", max_tokens=300)
    try:
        parsed = re.search(r'(\{.*\})', res, re.DOTALL)
        if parsed:
            extracted = json.loads(parsed.group(1))
            for k in defaults:
                if k in extracted and extracted[k] is not None:
                    defaults[k] = extracted[k]
    except Exception:
        pass
    return defaults


def apply_paragraph_format(p, params):
    """把排版参数物理应用到段落上"""
    try:
        p.paragraph_format.line_spacing = float(params.get("line_spacing", 1.5))
        p.paragraph_format.first_line_indent = Inches(0.15 * int(params.get("first_line_indent", 2)))
        align_map = {"left": WD_PARAGRAPH_ALIGNMENT.LEFT, "center": WD_PARAGRAPH_ALIGNMENT.CENTER,
                     "right": WD_PARAGRAPH_ALIGNMENT.RIGHT, "justify": WD_PARAGRAPH_ALIGNMENT.JUSTIFY}
        p.alignment = align_map.get(str(params.get("alignment", "justify")), WD_PARAGRAPH_ALIGNMENT.JUSTIFY)
        for run in p.runs:
            run.font.name = params.get("font_name", "宋体")
            run._element.rPr.rFonts.set(qn("w:eastAsia"), params.get("font_name", "宋体"))
            run.font.size = Pt(float(params.get("font_size", 12)))
    except Exception:
        pass


def collect_references_for_export(tree, drafts=None, draft_reference_maps=None, literatures=None):
    """建立正文引用与参考文献表共同使用的全局编号注册表。"""
    if drafts is None:
        drafts = load_json_file("drafts.json", {})
    if draft_reference_maps is None:
        draft_reference_maps = load_json_file("draft_reference_maps.json", {})
    if literatures is None:
        literatures = load_json_file("literatures.json", [])
    return collect_global_reference_registry(tree, drafts, draft_reference_maps, literatures)


def is_garbled_text(text, threshold=0.05):
    """检测文本是否含乱码/编码错乱（mojibake）。乱码字符占比超过阈值判定为乱码。"""
    import re
    if not text or not text.strip():
        return False
    garbled_chars = re.compile(
        r"[\u2550-\u256c\ufffd\x00-\x08\x0b\x0c\x0e-\x1f]"
    )
    hits = len(garbled_chars.findall(text))
    return hits / max(len(text), 1) > threshold


def is_suspicious_rewrite(original, rewritten):
    """检测重写结果是否异常（跑题/膨胀/思考残留）。返回 (是否异常, 原因列表)"""
    import re
    reasons = []
    if not original or not rewritten:
        return True, ["原文或结果为空"]
    if len(rewritten) > len(original) * 3 and len(original) > 100:
        reasons.append(f"字数膨胀{len(rewritten)/len(original):.1f}倍")
    # 字数骤减：重写结果不足原文 30%（LLM 把重写做成摘要/被截断在开头）
    if len(rewritten) < len(original) * 0.3 and len(original) > 200:
        reasons.append(f"字数骤减为原文的{len(rewritten)/len(original):.0%}")
    if re.search(r"(Need transform|Let['’]?s inspect|Need decode|Need perhaps|manually decode|garbled|mojibake)", rewritten, re.I):
        reasons.append("含英文解码思考")
    if re.search(r"(我们只需要重写|我们需要理解任务|我们需要在最终输出|我来重写|改写要点|让我逐句)", rewritten):
        reasons.append("含思考痕迹")
    return len(reasons) > 0, reasons


def purge_thinking_text(text):
    """净化 LLM 输出中的思考痕迹与解说性套话，返回纯净结论。
    兜底清理：即使 prompt 已约束，推理型模型仍可能漏出思考过程。"""
    import re
    if not text:
        return text
    lines = text.split("\n")
    out = []
    # 压缩任务特有的指令残渣特征（如「我们需要的输出是严格150-200」）
    TASK_RESIDUE = re.compile(
        r"(我们(需要的输出|要求|需要|需要确保|需要从|需要计算|需要严格|需要写|需要把|需确保|需从|需计算|需严格)|"
        r"让我们(仔细)?(计数|数|计算|写|起草|尝试)|"
        r"(首先|然后|最后)?(计算|数|数一下|逐字|逐个)?(字数|汉字数|字数是|字：|汉字个数|标点|中文字符|字符数)|"
        r"(尝试|起草|先起草|撰写|写一段话|写一下|输出正文|最终输出|我们写|我们来写)(草稿|一段|一下|正文)?|"
        r"(注意|要求|需要)(不要|只|严格)|"
        r"不计标点|只数汉字|不算汉字|字数在|控制在)"
    )
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        # 整行都是过程性套话则跳过
        if re.fullmatch(r"(好的|好的，|好的！|嗯|嗯嗯|OK|Okay|好的呢|收到|明白|了解|没问题)[，。！!、\s]*", line):
            continue
        # 整行是压缩任务指令残渣则跳过
        if TASK_RESIDUE.search(line) and len(line) < 60:
            continue
        # 长行中文思考/任务复述整行删除（不依赖 TASK_RESIDUE，长句也删）
        if re.search(r"(我们只需要(重写|改写|理解|输出|给出|处理|压缩)|我们需要(理解|重写|改写|输出|在最终|完成)|我们要求(把|将)|我们(需要|必须)在最终输出)", line):
            continue
        # 英文思考/解码过程整行删除（mojibake 场景）
        if re.search(r"(Need (to )?(transform|decode|inspect|verify|perhaps|restore)|Let['’]?s (inspect|decode|verify|check)|manually decode|garbled (input|text)|maybe I should|looks like (GBK|UTF|CP437))", line, re.I):
            continue
        # 强特征思考行整行删除（基于原始行判断，这些词绝不会出现在学术正文里）
        if re.search(r"(我来重写|让我重写|我将重写|我来改写|让我改写|我将改写|我来处理|让我处理|"
                     r"逐句(处理|分析|修改|检查)|这段文字存在|这段文本存在|本段文字存在|这段文字的特点是|"
                     r"原文特点|原文存在|让我看看原文|我先看看原文|待我逐句|存在(排比|句式|机械|模板|AI)痕迹|"
                     r"存在(句式|排比)(过于|密集|工整))", line):
            continue
        # 重写标签前缀剥离：「重写后的正文：」等，保留后面的正文
        line = re.sub(r"^(重写|改写|处理|修改)后的(正文|文本|内容|文字)[：:]\s*", "", line)
        line = re.sub(r"^(正文|文本|内容|文字)(本身|内容)?[：:]\s*", "", line)
        # 行内混入压缩指令残渣（如「我们需要的输出是严格150-200个汉字的核心记忆」）整体剔除该片段
        line = re.sub(r"我们需要的输出是[^。；\n]*?。", "", line)
        line = re.sub(r"我们要求的输出是[^。；\n]*?。", "", line)
        line = re.sub(r"我们(要求|需要)把[^。；\n]*?正文。", "", line)
        line = re.sub(r"我们来写[：:][^。；\n]*", "", line)
        line = re.sub(r"我们先起草[^。；\n]*", "", line)
        line = re.sub(r"(计算|数|逐字|检查)一下字数[^。；\n]*", "", line)
        line = re.sub(r"（约\d+字[^）]*）", "", line)
        line = re.sub(r"约\d+字[？?]?$", "", line)
        line = re.sub(r"\d+字[？?]?$", "", line)
        # 行首过程性套话剥离
        line = re.sub(r"^(好的|好的，|好的！|嗯|嗯嗯|收到|明白|了解|没问题|好的呢)[，。！!、:：]?\s*", "", line)
        line = re.sub(r"^(我来(分析|看看|梳理|总结|检查|重写|改写|处理|修改)(一下)?|让我(分析|看看|梳理|总结|检查|重写|改写|处理|修改)(一下)?|下面我(来)?(分析|看看|梳理|总结|检查|重写|改写|处理|修改)(一下)?|我将(重写|改写|处理|修改)一下|以下是我(的)?(分析|总结|看法|重写结果))[，。！!、:：]?\s*", "", line)
        line = re.sub(r"^(首先|其次|再次|最后|接下来|总而言之|综上所述|总的来说|整体来看|总体而言|分析一下|逐句(处理|分析|修改)|这段文字|本段文字|原文(特点|存在|来看))[，。！!、:：]?\s*", "", line)
        # 解说标签整行删除：改写要点/重写说明/修改说明/处理说明/说明/主要针对/重点调整/对比/注：
        if re.match(r"^(改写要点|重写说明|修改说明|处理说明|修改要点|改写说明|说明|注|备注|主要针对|重点调整|对比(一下|结果)|处理思路|修改思路|重写思路|调整内容)[：:].*$", line):
            continue
        if re.fullmatch(r"(改写完毕|重写完毕|修改完毕|处理完毕|以上就是(重写|改写|修改)结果|以上是(重写|改写|修改)后的文本|已完成(重写|改写|修改))[。！!]?", line):
            continue
        # 截断残渣行：未完/待续/省略号结尾的占位
        if re.search(r"(未完待续|待续|此处省略|省略\d+字|内容过长|以下省略|已截断|（未完）|（待续）)", line):
            continue

        # 行内剥离重写/改写的思考引导（如"好的，我来重写这段文本。首先分析一下原文特点……"）
        line = re.sub(r"^(好的，?)?(我(来|将|要)?(重写|改写|处理|修改)(这段|该|此)?(文本|文字|内容|文章|段落|原文)?)[。，！!：:]?\s*", "", line)
        line = re.sub(r"^(让我(重写|改写|处理|修改)一下|我来(重写|改写|处理|修改)一下|下面(我)?(重写|改写|处理|修改)如下)[。，！!：:]?\s*", "", line)
        # 去掉行内残留的思考性引导（出现在行首的"我认为/我觉得"保留为观点表述，不删）
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            out.append(line)
    return "\n".join(out)


def is_md_table_line(line):
    """判断是否为 Markdown 表格行（以 | 开头且含 |）"""
    import re
    if not line.startswith("|"):
        return False
    if "|" not in line[1:]:
        return False
    # 排除分隔行 |---|---|（后续单独处理）
    return True


def clean_markdown_text(text):
    """清洗 LLM 正文中的 Markdown 痕迹，返回干净的纯文本行列表。
    Markdown 表格行原样保留（以 TABLE| 前缀标记），由装配阶段渲染为 Word 表格。"""
    import re
    lines = text.split("\n")
    out = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        # 表格行：原样保留并标记
        if is_md_table_line(line):
            out.append("TABLE|" + line)
            continue
        # 分隔线（---、***、___）直接跳过
        if re.fullmatch(r"[-*_]{3,}", line):
            continue
        # 标题标记：## / ### / # 开头（可能带空格），去掉 # 号保留文字
        m = re.match(r"^#{1,6}\s*(.*)$", line)
        if m:
            line = m.group(1).strip()
        # 引用标记 > 开头
        m = re.match(r"^>+\s*(.*)$", line)
        if m:
            line = m.group(1).strip()
        # 列表标记：-、*、+、1.、1)、（1） 开头
        m = re.match(r"^[-*+•]\s+(.*)$", line)
        if m:
            line = m.group(1).strip()
        else:
            m = re.match(r"^\d+[.、)]\s*(.*)$", line)
            if m:
                line = m.group(1).strip()
            else:
                m = re.match(r"^（\d+）\s*(.*)$", line)
                if m:
                    line = m.group(1).strip()
        # 去除行内 markdown：**加粗**、*斜体*、`代码`、[text](url)
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        line = re.sub(r"\*(.+?)\*", r"\1", line)
        line = re.sub(r"`(.+?)`", r"\1", line)
        line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)
        # 去除行内图片标记 ![]() 与残留链接括号
        line = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", line)
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            out.append(line)
    return out


def render_md_table(doc, table_lines, params):
    """把连续 Markdown 表格行渲染为 Word 表格"""
    import re
    rows = []
    for line in table_lines:
        if line.startswith("TABLE|"):
            line = line[len("TABLE|"):]
        line = line.strip()
        if not line:
            continue
        # 去掉首尾 | 后按 | 切分
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows.append(cells)
    if not rows:
        return
    # 过滤分隔行（如 |---|---|）
    data_rows = [r for r in rows if not all(re.fullmatch(r":?-{3,}:?", c.strip()) for c in r)]
    if not data_rows:
        return
    n_cols = max(len(r) for r in data_rows)
    table = doc.add_table(rows=len(data_rows), cols=n_cols)
    try:
        table.style = "Table Grid"
    except Exception:
        pass
    for ri, row in enumerate(data_rows):
        for ci in range(n_cols):
            cell_text = row[ci] if ci < len(row) else ""
            # 单元格内也可能有 markdown，做简单清理
            cell_text = re.sub(r"\*\*(.+?)\*\*", r"\1", cell_text)
            cell_text = re.sub(r"\*(.+?)\*", r"\1", cell_text)
            cell_text = re.sub(r"`(.+?)`", r"\1", cell_text)
            table.cell(ri, ci).text = cell_text.strip()


def build_final_document(template_name, params):
    """装配终稿 Word 文档，返回 BytesIO"""
    tree = load_json_file("logic_tree.json", [])
    drafts = load_json_file("drafts.json", {})
    draft_reference_maps = load_json_file("draft_reference_maps.json", {})
    drafts_charts = load_json_file("drafts_charts.json", {})
    drafts_images = load_json_file("drafts_images.json", {})
    ordered, global_number_by_id, lit_map = collect_references_for_export(
        tree, drafts, draft_reference_maps
    )

    # 依据模板：打开模板继承页边距/页眉/样式；否则新建空白文档
    doc = None
    if template_name:
        tpath = os.path.join(TEMPLATE_DIR, template_name)
        if os.path.exists(tpath):
            try:
                doc = docx.Document(tpath)
            except Exception:
                doc = docx.Document()
    if doc is None:
        doc = docx.Document()

    # 标题字体设置（模板模式下也生效）
    def set_heading_font(paragraph, level):
        try:
            for run in paragraph.runs:
                run.font.name = params.get("heading_font", "黑体")
                run._element.rPr.rFonts.set(qn("w:eastAsia"), params.get("heading_font", "黑体"))
                run.font.color.rgb = RGBColor(0, 0, 0)
        except Exception:
            pass

    # 遍历逻辑树装配
    for node, depth in flatten_tree_nodes(tree):
        nid = node.get("id")
        title = node.get("title", "")
        if title:
            h = doc.add_heading(title, level=min(depth + 1, 3))
            set_heading_font(h, depth + 1)

        text = drafts.get(nid, "")
        if text:
            local_reference_ids = reference_ids_for_node(node, draft_reference_maps)
            export_text = remap_local_citations(text, local_reference_ids, global_number_by_id)
            cleaned = clean_markdown_text(export_text)
            # 连续表格行聚合为 Word 表格
            buf = []
            for para in cleaned:
                if para.startswith("TABLE|"):
                    buf.append(para)
                else:
                    if buf:
                        render_md_table(doc, buf, params)
                        buf = []
                    p = doc.add_paragraph(para)
                    apply_paragraph_format(p, params)
            if buf:
                render_md_table(doc, buf, params)

        # 挂载图表
        for ch in drafts_charts.get(nid, []) or []:
            if os.path.exists(ch.get("path", "")):
                try:
                    doc.add_picture(ch["path"], width=Inches(5.5))
                    doc.paragraphs[-1].alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
                except Exception:
                    pass

        # 挂载图片
        for im in drafts_images.get(nid, []) or []:
            if os.path.exists(im.get("path", "")):
                try:
                    doc.add_picture(im["path"], width=Inches(5.0))
                    doc.paragraphs[-1].alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
                    if im.get("caption"):
                        cap = doc.add_paragraph(im["caption"])
                        cap.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
                        for run in cap.runs:
                            run.font.size = Pt(9)
                except Exception:
                    pass

    # 参考文献表
    if ordered:
        doc.add_page_break()
        doc.add_heading("参考文献", level=1)
        for rid in ordered:
            lit = lit_map[rid]
            title = lit.get("title", "未命名")
            cat = lit.get("category", "")
            findings = str(lit.get("analysis", {}).get("key_findings", ""))[:100]
            ref_p = doc.add_paragraph(f"[{global_number_by_id[rid]}] {title}（{cat}）")
            apply_paragraph_format(ref_p, {**params, "first_line_indent": 0})
            if findings:
                ref_p2 = doc.add_paragraph(f"    核心发现：{findings}")
                for run in ref_p2.runs:
                    run.font.size = Pt(10.5)

    bio = io.BytesIO()
    doc.save(bio)
    bio.seek(0)
    return bio


def module6_aigc():
    st.header("🛡️ AIGC 检测与去味")
    st.caption("受限于成本与部署难度，本工具的 AIGC 检测与去味结果仅供参考，无法完全模拟专业 AIGC 检测/降重网站。")
    prompts = load_json_file("prompts.json", {})
    rewrite_prompt = prompts.get("aigc_rewrite_prompt", "")
    tree = load_json_file("logic_tree.json", [])
    flat = flatten_tree_nodes(tree)
    drafts = load_json_file("drafts.json", {})

    # ---- 检测引擎选择（本地模型 / LLM 提示词） ----
    st.markdown("### 🔍 AIGC 检测引擎设置")
    cur_engine = get_detect_engine()
    engine_map = {"local": "本地模型（需挂载 HuggingFace 检测模型）", "llm": "LLM 提示词检测（无需本地模型，推荐公开部署）"}
    engine_choice = st.radio(
        "选择 AIGC 检测引擎：",
        ["local", "llm"],
        index=0 if cur_engine == "local" else 1,
        format_func=lambda x: engine_map[x],
        horizontal=True,
        key="aigc_engine"
    )
    if engine_choice != cur_engine:
        set_detect_engine(engine_choice)
        st.rerun()
    if engine_choice == "local":
        detector = load_local_aigc_detector()
        if detector is None:
            st.warning("⚠️ 当前选择本地模型，但未检测到本地模型文件（models/aigc_detector 为空）。检测将返回 0%。可切换到 LLM 检测。")
        else:
            st.success("✅ 本地模型已加载，使用本地推理引擎检测。")
    else:
        
        # ---- 检测专用 LLM 选择 ----
        all_profiles = get_all_profiles()
        if all_profiles:
            # 选项：跟随激活配置 + 所有已配置的 LLM
            detect_llm_id = get_detect_llm_id()
            name_map = {p["id"]: f"{p.get('name','')} ({p.get('model','')})" for p in all_profiles}
            # 校验持久化的 ID 是否仍有效
            if detect_llm_id not in name_map:
                detect_llm_id = None
            options = [None] + list(name_map.keys())
            fmt = lambda x: "⚡ 跟随当前激活配置（正文同款）" if x is None else f"🔍 {name_map[x]}"
            sel_detect_llm = st.selectbox(
                "选择用于 AIGC 检测的 LLM 配置：",
                options,
                index=0 if detect_llm_id is None else options.index(detect_llm_id),
                format_func=fmt,
                key="aigc_detect_llm_sel"
            )
            if sel_detect_llm != detect_llm_id:
                set_detect_llm_id(sel_detect_llm)
                st.rerun()
        else:
            st.warning("⚠️ 尚未配置任何 LLM，请先前往板块一添加。")

    st.markdown("---")

    tabs = st.tabs(["单章审查与去味", "全文审查与去味", "外部文档处理", "终稿导出与排版"])

    # ---- 单章 ----
    with tabs[0]:
        if not flat:
            st.warning("大纲为空。")
        else:
            # 树形导航：父节点仅作为展开容器，审查/去味只针对叶子节点
            ordered_ids = get_ordered_node_ids(tree)
            cur_sel = st.session_state.get("aigc_selected_id")
            if cur_sel not in ordered_ids:
                # 默认选中第一个叶子节点
                cur_sel = None
                for node, _ in flat:
                    if not (node.get("children") or []):
                        cur_sel = node.get("id")
                        break
                st.session_state["aigc_selected_id"] = cur_sel
            col_l, col_r = st.columns([1, 3])
            with col_l:
                st.caption("📑 章节导航")
                with st.container(height=500, border=True):
                    render_tree_nav(tree, tree, sel_key="aigc_selected_id")
            sel_id = st.session_state.get("aigc_selected_id")
            with col_r:
                # 大字体显示当前选中章节名
                sel_node = get_node_by_id(tree, sel_id)
                sel_title = sel_node.get("title", "未命名") if sel_node else "未命名"
                st.markdown(f"<div style='font-size:26px;font-weight:700;color:#1f3a5f;margin-bottom:8px;'>{sel_title}</div>", unsafe_allow_html=True)
                content_ver = st.session_state.get(f"content_ver_{sel_id}", 0)
                target_text = drafts.get(sel_id, "")
                edited_text = st.text_area("章节原生底稿", value=target_text, height=250, key=f"aigc_ta_{sel_id}_v{content_ver}")
                feedback = st.empty()
                # 显示上次去味反馈（持久化，rerun 后仍保留）
                if st.session_state.get(f"rew_fb_{sel_id}"):
                    fb_ok, fb_rounds, fb_score = st.session_state[f"rew_fb_{sel_id}"]
                    if fb_ok:
                        feedback.success(f"去味成功！第 {fb_rounds} 轮达标，AIGC 痕迹降至 {fb_score}%。")
                    else:
                        feedback.warning(f"已达最大轮次（3轮），最终 AIGC 概率 {fb_score}%。已保存最终努力结果。")

                c_det, c_rew = st.columns(2)
                with c_det:
                    if st.button("📊 检测", key=f"det_{sel_id}"):
                        drafts[sel_id] = edited_text
                        save_json_file("drafts.json", drafts)
                        st.session_state[f"content_ver_{sel_id}"] = content_ver + 1
                        with st.spinner("检测中..."):
                            res_dict = detect_aigc(edited_text)
                            feedback.info(f"**AIGC 疑似概率: {res_dict['score']}%**  \n{res_dict['raw']}")
                with c_rew:
                    if st.button("✨ 降重去味", key=f"rew_{sel_id}", type="primary"):
                        # 输入防护：乱码文本拒绝去味
                        if is_garbled_text(edited_text):
                            st.error("⚠️ 检测到当前章节文本为乱码（编码错乱），已中止去味。请先在板块五重新生成该章节或手动修正内容。")
                        else:
                            with st.spinner("检测去味中 (最多3轮)..."):
                                # 去味前自动备份当前章节（防破坏可恢复）
                                bak_d = load_json_file("drafts_rewrite_backup.json", {})
                                bak_d[sel_id] = drafts.get(sel_id, "")
                                save_json_file("drafts_rewrite_backup.json", bak_d)
                                try:
                                    rewritten, ok, rounds, score = adversarial_rewrite(edited_text, rewrite_prompt)
                                except LLMOutputError as exc:
                                    st.error(f"去味失败，原有正文已保留。请检查模型/API 配置后重试。（{exc}）")
                                else:
                                    # 输出防护：异常重写结果不覆盖原文
                                    abnormal, abn_reasons = is_suspicious_rewrite(edited_text, rewritten)
                                    if abnormal:
                                        drafts[sel_id] = edited_text
                                        save_json_file("drafts.json", drafts)
                                        st.warning(f"⚠️ 去味结果异常（{'、'.join(abn_reasons)}），已保留原文，未写入。")
                                        st.session_state[f"rew_fb_{sel_id}"] = (False, 0, "异常已拦截")
                                    else:
                                        drafts[sel_id] = rewritten
                                        save_json_file("drafts.json", drafts)
                                        st.session_state[f"rew_fb_{sel_id}"] = (ok, rounds, score)
                                    # 版本号+1 → rerun 后 text_area 以新 key 重新从 drafts 读取，全局同步
                                    st.session_state[f"content_ver_{sel_id}"] = content_ver + 1
                                    st.rerun()

    # ---- 全文 ----
    with tabs[1]:
        st.subheader("📖 全文 AIGC 审查与去味")
        if st.button("📊 全文 AIGC 体检（逐章打分）"):
            if not flat:
                st.warning("大纲为空。")
            else:
                rows = []
                bar = st.progress(0)
                for i, (node, depth) in enumerate(flat):
                    nid = node.get("id")
                    text = drafts.get(nid, "")
                    if text:
                        s = detect_aigc(text)
                        rows.append({"章节": node.get("title", ""), "AIGC概率": f"{s['score']}%", "原始判定": s["label"]})
                    bar.progress((i + 1) / len(flat))
                if rows:
                    st.table(pd.DataFrame(rows))
                else:
                    st.warning("还没有已生成的正文。")

        st.markdown("---")
        if st.button("✨ 全文批量去味（逐章对抗循环）"):
            if not flat:
                st.warning("大纲为空。")
            else:
                ordered = get_ordered_node_ids(tree)
                drafts = load_json_file("drafts.json", {})
                bar = st.progress(0)
                status = st.empty()
                ok_count = 0
                failed_rewrites = []
                for k, nid in enumerate(ordered):
                    node = get_node_by_id(tree, nid)
                    text = drafts.get(nid, "")
                    if text:
                        status.info(f"正在去味：{node.get('title','')} ...")
                        try:
                            rewritten, ok, _, _ = adversarial_rewrite(text, rewrite_prompt)
                        except LLMOutputError as exc:
                            failed_rewrites.append(f"{node.get('title','未命名')}: {exc}")
                        else:
                            drafts[nid] = rewritten
                            save_json_file("drafts.json", drafts)
                            if ok:
                                ok_count += 1
                    bar.progress((k + 1) / len(ordered))
                if failed_rewrites:
                    status.warning(f"全文去味结束：{ok_count} 章达标，{len(failed_rewrites)} 章失败。")
                    st.warning("以下章节的去味失败，原文已保留：\n" + "\n".join(failed_rewrites))
                else:
                    status.success(f"✅ 全文去味完成！达标章节 {ok_count} 篇。")

    # ---- 外部文档处理 ----
    with tabs[2]:
        st.subheader("外部文档处理")
        
        ext_file = st.file_uploader("装载需要脱水的 Word 文档", type=["docx"], key="ext_up2")
        if ext_file:
            doc = docx.Document(io.BytesIO(ext_file.read()))
            full_txt = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
            st.success(f"外部文档加载完毕，捕获纯净字符数: {len(full_txt)}")

            ext_feedback = st.empty()
            if st.button("全局初级体检"):
                res = detect_aigc(full_txt[:1500])
                ext_feedback.metric("首段 AI 判定概率", f"{res['score']}%")

            if st.button("🚀 执行分块流水线降重并出表", type="primary"):
                with st.spinner("分块对抗重构中，请勿关闭页面..."):
                    chunk_size = 1500
                    chunks = [full_txt[i:i + chunk_size] for i in range(0, len(full_txt), chunk_size)]
                    new_chunks = []
                    bar = st.progress(0)
                    for idx, chunk in enumerate(chunks):
                        chunk_prompt = f"{rewrite_prompt}\n待处理文本：\n{chunk}"
                        res = dispatch_llm_call(chunk_prompt, max_tokens=2500, temp=0.9)
                        new_chunks.append(res)
                        bar.progress((idx + 1) / len(chunks))

                    out_doc = docx.Document()
                    for nc in new_chunks:
                        out_doc.add_paragraph(nc)
                    out_io = io.BytesIO()
                    out_doc.save(out_io)
                    out_io.seek(0)
                    st.success("流水线清洗完成！")
                    st.download_button(
                        "📥 下载脱水降重后的安全终稿",
                        data=out_io,
                        file_name="AIGC_Cleaned_Document.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    )



    # ---- 终稿导出与排版 ----
    with tabs[3]:
        st.subheader("📥 终稿导出与排版")
        

        # ---- 模板管理 ----
        st.markdown("### 🗂️ 模板管理")
        templates = list_templates()
        if templates:
            st.caption("当前已上传模板：" + " ｜ ".join(templates))
        else:
            st.caption("当前无模板（将使用空白文档）")

        c_up, c_rm = st.columns(2)
        with c_up:
            with st.expander("📤 上传封面模板 (.docx)", expanded=False):
                tpl_file = st.file_uploader("选择模板文件", type=["docx"], key="tpl_up")
                if tpl_file:
                    if st.button("💾 保存模板", key="tpl_save"):
                        fname, err = save_uploaded_template(tpl_file)
                        if err:
                            st.error(err)
                        else:
                            st.success(f"模板《{fname}》已保存！")
                            st.rerun()
        with c_rm:
            if templates:
                with st.expander("🗑️ 卸载模板", expanded=False):
                    rm_name = st.selectbox("选择要卸载的模板", templates, key="tpl_rm_sel")
                    if st.button("🗑️ 确认卸载", key="tpl_rm"):
                        if remove_template(rm_name):
                            st.success(f"模板《{rm_name}》已卸载！")
                            st.rerun()

        st.markdown("---")

        # ---- 格式来源选择 ----
        st.markdown("### 🎛️ 格式来源设置")
        format_source = st.radio(
            "排版依据（模板 / 提示词）：",
            ["依据提示词 (Prompt)", "依据模板 (Template)", "模板 + 提示词结合"],
            index=0,
            help="提示词来自板块七的「文档生成格式提示词」；模板来自上方上传的 .docx。",
            key="format_source"
        )

        prompts = load_json_file("prompts.json", {})
        format_prompt = prompts.get("format_prompt", "")
        st.caption(f"当前格式提示词：{format_prompt[:120]}{'...' if len(format_prompt) > 120 else ''}")

        use_template = format_source in ["依据模板 (Template)", "模板 + 提示词结合"]
        use_prompt = format_source in ["依据提示词 (Prompt)", "模板 + 提示词结合"]
        if use_template and not templates:
            st.warning("⚠️ 你选择了依据模板，但当前没有已上传的模板，将降级为空白文档。")
            use_template = False
        tpl_name = None
        if use_template:
            tpl_name = st.selectbox("选择用于导出的模板", templates, key="tpl_export_sel")

        st.markdown("---")

        # ---- 导出 ----
        if st.button("📑 装配并导出终稿 Word", type="primary", key="export_btn"):
            if not flatten_tree_nodes(load_json_file("logic_tree.json", [])):
                st.warning("逻辑大纲为空，无法导出。请先在板块四生成大纲。")
            else:
                with st.spinner("Formatting Agent 正在装配终稿..."):
                    # 依据提示词：提取排版参数；否则用默认参数
                    if use_prompt:
                        params = extract_format_params(format_prompt)
                    else:
                        params = {"line_spacing": 1.5, "first_line_indent": 2, "font_name": "宋体",
                                  "font_size": 12, "heading_font": "黑体", "alignment": "justify"}
                    bio = build_final_document(tpl_name, params)
                    st.success("终稿装配完成！")
                    st.download_button(
                        "📥 下载排版完成的 Word 终稿",
                        data=bio,
                        file_name="Refined_Scholar_Final.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        key="export_dl"
                    )


# ============================================================
# 13. 板块七：提示词配置
# ============================================================
def module7_prompts():
    st.header("🎛️ 提示词配置")
    
    prompts = load_json_file("prompts.json", {})

    with st.form("prompts_form"):
        style_prompt = st.text_area(
            "📝 文章生成风格提示词",
            value=prompts.get("style_prompt", ""),
            height=120,
            help="控制 AI 生成正文的文风、语气、用词习惯"
        )
        format_prompt = st.text_area(
            "📄 文档生成格式提示词",
            value=prompts.get("format_prompt", ""),
            height=120,
            help="控制终稿 Word 的排版格式（缩进、行距、字体等）"
        )
        rewrite_prompt = st.text_area(
            "🛡️ AIGC 重写提示词",
            value=prompts.get("aigc_rewrite_prompt", ""),
            height=120,
            help="控制去味引擎的硬性重写规则"
        )
        detect_prompt = st.text_area(
            "🔍 AIGC 检测提示词",
            value=prompts.get("aigc_detect_prompt", ""),
            height=120,
            help="控制 LLM 检测引擎的判断规则。需让 LLM 输出含 ai_probability（0-100整数）的 JSON。待检测文本会自动拼接到提示词末尾"
        )
        if st.form_submit_button("💾 保存全部提示词"):
            prompts["style_prompt"] = style_prompt
            prompts["format_prompt"] = format_prompt
            prompts["aigc_rewrite_prompt"] = rewrite_prompt
            prompts["aigc_detect_prompt"] = detect_prompt
            save_json_file("prompts.json", prompts)
            st.success("提示词已保存，将在各模块中生效！")






# ============================================================
# 14. 主函数
# ============================================================
def main():
    # 启动版权横幅（终端可见，截图可留证）
    print("=" * 56)
    print("  Lumielle Academic Assistant")
    print("  开发者：Lumielle")
    print("  本工具仅供个人学习使用，禁止倒卖或商业牟利。")
    print("=" * 56)
    st.set_page_config(page_title="Lumielle Academic Assistant · 开发者 Lumielle", layout="wide", initial_sidebar_state="expanded")


    nav_choice = render_global_sidebar()

    if nav_choice == "1. LLM 配置与交叉讨论":
        module1_llm()
    elif nav_choice == "2. 科研基座":
        module2_research_base()
    elif nav_choice == "3. 文献处理":
        module3_literature()
    elif nav_choice == "4. 逻辑链路":
        module4_logic()
    elif nav_choice == "5. 正文写作":
        module5_writing()
    elif nav_choice == "6. AIGC 检测与去味":
        module6_aigc()
    elif nav_choice == "7. 提示词配置":
        module7_prompts()


if __name__ == "__main__":
    main()
