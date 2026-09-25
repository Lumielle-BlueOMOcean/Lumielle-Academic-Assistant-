"""Validate structured chart data and render it with trusted matplotlib code."""

from decimal import Decimal, InvalidOperation
import json
import math
import os
import re
import struct

from writing_support import LLMOutputError, require_valid_llm_output


ALLOWED_CHART_TYPES = frozenset({"bar", "line", "scatter", "pie"})
MAX_SERIES = 10
MAX_DATA_POINTS = 500
MAX_TEXT_LENGTH = 300
MAX_RESPONSE_LENGTH = 100_000
MAX_CHART_PIXELS = 20_000_000
MAX_CHART_DIMENSION = 20_000

_NUMBER_PATTERN = re.compile(
    r"(?<![\w.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?%?"
    r"|(?<![\w.])[-+]?\.\d+(?:[eE][-+]?\d+)?%?"
)
_ILLUSTRATIVE_EN_MARKER = r"(?:illustrative|hypothetical|example|sample|demonstration|demo)"
_ILLUSTRATIVE_ZH_MARKER = r"(?:示例|假设|演示|示意)"
_DATA_EN_NOUN = r"(?:data|values?|numbers?)"
_DATA_ZH_NOUN = r"(?:数据|数值|数字)"
_EN_ILLUSTRATIVE_NEGATION = re.compile(
    rf"\b(?:do\s+not|don't|does\s+not|doesn't|did\s+not|didn't|never|no|without|avoid|not)\b"
    rf".{{0,40}}?\b{_ILLUSTRATIVE_EN_MARKER}\b.{{0,20}}?\b{_DATA_EN_NOUN}\b",
    re.IGNORECASE,
)
_ZH_ILLUSTRATIVE_NEGATION = re.compile(
    rf"(?:不要|不需要|不用|不使用|不采用|不生成|不提供|不允许|别(?:用|使用|采用|生成|提供)?|"
    rf"请勿(?:用|使用|采用|生成|提供)?|禁止(?:用|使用|采用|生成|提供)?|避免(?:用|使用|采用|生成|提供)?|"
    rf"不得(?:用|使用|采用|生成|提供)?|不能(?:用|使用|采用|生成|提供)?|不可(?:用|使用|采用|生成|提供)?)"
    rf"[^，,。.!！？?；;\n]{{0,10}}?{_ILLUSTRATIVE_ZH_MARKER}"
    rf"[^，,。.!！？?；;\n]{{0,8}}?{_DATA_ZH_NOUN}",
    re.IGNORECASE,
)
_EN_ILLUSTRATIVE_PERMISSION = re.compile(
    rf"\b(?:(?:please\s+)?(?:use|generate|create|make\s+up|invent|provide|supply)|"
    rf"(?:you\s+)?(?:may|can)\s+(?:use|generate|create|make\s+up|invent|provide|supply))\b"
    rf".{{0,40}}?\b{_ILLUSTRATIVE_EN_MARKER}\b.{{0,20}}?\b{_DATA_EN_NOUN}\b",
    re.IGNORECASE,
)
_EN_ILLUSTRATIVE_ACCEPTANCE = re.compile(
    rf"\b{_ILLUSTRATIVE_EN_MARKER}\s+\b{_DATA_EN_NOUN}\b\s+"
    r"(?:(?:is|are)\s+)?(?:fine|okay|acceptable|allowed|permitted)\b",
    re.IGNORECASE,
)
_ZH_ILLUSTRATIVE_PERMISSION = re.compile(
    rf"(?:可以|可|允许|请(?:你)?|自行)?\s*(?:自行)?(?:使用|采用|用|生成|提供)"
    rf"\s*[一二三四五六七八九十几若干些个组套份批只]{{0,6}}"
    rf"{_ILLUSTRATIVE_ZH_MARKER}(?:的)?\s*{_DATA_ZH_NOUN}",
    re.IGNORECASE,
)
_ZH_ILLUSTRATIVE_ACCEPTANCE = re.compile(
    rf"^\s*{_ILLUSTRATIVE_ZH_MARKER}{_DATA_ZH_NOUN}"
    r"(?:即可|就可以|就行|也行|就好)\s*$",
    re.IGNORECASE,
)
_VALUE_CUE_PATTERN = re.compile(
    r"\b(?:data\s+values?|values?|scores?|rates?|counts?|measurements?|mean(?:\s+value)?|average(?:\s+(?:score|value))?|results?|score|value)\b|数据值|数值|数据|得分|比例|均值|平均值|数量|结果",
    re.IGNORECASE,
)


class ChartSpecError(ValueError):
    """Raised when model-produced JSON is not a safe, supported chart spec."""


class ChartRenderError(RuntimeError):
    """Raised when a validated chart cannot be rendered as a bounded PNG."""


def is_illustrative_request(instruction):
    """Return whether a clause explicitly permits made-up illustrative data."""
    if not isinstance(instruction, str):
        return False
    # Clause-local handling lets a later explicit permission stand on its own,
    # while negation takes priority over any permission in the same clause.
    clauses = re.split(r"[.!?;；,，。！？\n]+|\bbut\b|\bhowever\b|但是|不过|但", instruction, flags=re.I)
    for clause in clauses:
        if _EN_ILLUSTRATIVE_NEGATION.search(clause) or _ZH_ILLUSTRATIVE_NEGATION.search(clause):
            continue
        if (
            _EN_ILLUSTRATIVE_PERMISSION.search(clause)
            or _EN_ILLUSTRATIVE_ACCEPTANCE.search(clause)
            or _ZH_ILLUSTRATIVE_PERMISSION.search(clause)
            or _ZH_ILLUSTRATIVE_ACCEPTANCE.search(clause)
        ):
            return True
    return False


def _source_numbers(instruction):
    values = set()
    if not isinstance(instruction, str):
        return values, False

    non_year_matches = []
    explicit_value_cue = False
    matches = list(_NUMBER_PATTERN.finditer(instruction))
    for match in matches:
        token = match.group(0)
        normalized = token.rstrip("%").replace(",", "")
        try:
            value = Decimal(normalized)
        except InvalidOperation:
            continue
        if not value.is_finite():
            continue
        values.add(value)

        prefix = instruction[max(0, match.start() - 48):match.start()]
        suffix = instruction[match.end():match.end() + 12]
        is_assigned = bool(re.search(r"[^,;\n]{1,40}[=:]\s*$", prefix))
        is_percent = token.endswith("%") or bool(re.match(r"\s*(?:percent|percentage)\b", suffix, re.I))
        has_cue = False
        for cue in _VALUE_CUE_PATTERN.finditer(prefix):
            trailing_text = prefix[cue.end():]
            if re.fullmatch(r"\s*(?:(?:are|is|was|were|of|equals?|equal to)|为|是|等于|[:=])?\s*", trailing_text, re.I):
                has_cue = True
                break
        if is_assigned or is_percent or has_cue:
            explicit_value_cue = True

        is_year = value == value.to_integral_value() and Decimal(1800) <= value <= Decimal(2200)
        if not is_year or is_assigned or is_percent or has_cue or re.match(r"\s*(?:%|percent|percentage|年|年度)", suffix, re.I):
            non_year_matches.append(match)

    # Two unlabeled numeric values count as a simple list only when they are adjacent
    # data items; numbers embedded in instructions such as "3D, 2 series" do not.
    has_numeric_pair = False
    for first, second in zip(non_year_matches, non_year_matches[1:]):
        separator = instruction[first.end():second.start()]
        if re.fullmatch(r"[\s,;/|&+()\[\]{}]*(?:(?:and|与|和)[\s,;/|&+()\[\]{}]*)?", separator, re.I):
            has_numeric_pair = True
            break

    # A lone year/range or a numeric chart setting is not a usable data series.
    has_usable_data = explicit_value_cue or has_numeric_pair
    return values, has_usable_data


def has_usable_numeric_data(instruction):
    """Reject requests that contain only a year range or chart dimensions, not values."""
    return _source_numbers(instruction)[1]


def _text(value, field, *, allow_empty=False, limit=MAX_TEXT_LENGTH):
    if not isinstance(value, str):
        raise ChartSpecError(f"{field} must be text.")
    value = value.strip()
    if (not value and not allow_empty) or len(value) > limit:
        raise ChartSpecError(f"{field} must be non-empty text of at most {limit} characters.")
    if any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise ChartSpecError(f"{field} contains unsupported control characters.")
    return value


def _check_keys(value, allowed, required, field):
    if not isinstance(value, dict):
        raise ChartSpecError(f"{field} must be an object.")
    keys = set(value)
    unknown = keys - set(allowed)
    missing = set(required) - keys
    if unknown:
        raise ChartSpecError(f"{field} contains unsupported fields: {', '.join(sorted(map(str, unknown)))}.")
    if missing:
        raise ChartSpecError(f"{field} is missing required fields: {', '.join(sorted(missing))}.")


def _number(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ChartSpecError(f"{field} must be a finite number.")
    try:
        if not math.isfinite(float(value)):
            raise ChartSpecError(f"{field} must be a finite number.")
        return Decimal(str(value))
    except (OverflowError, ValueError, InvalidOperation):
        raise ChartSpecError(f"{field} must be a finite number.") from None


def _text_array(values, field, *, min_items=1):
    if not isinstance(values, list) or not min_items <= len(values) <= MAX_DATA_POINTS:
        raise ChartSpecError(f"{field} must contain between {min_items} and {MAX_DATA_POINTS} items.")
    return [_text(item, f"{field}[{index}]", limit=160) for index, item in enumerate(values)]


def _numeric_label_values(labels):
    """Return numeric axis labels as values that require source provenance."""
    numeric_values = []
    for label in labels:
        match = _NUMBER_PATTERN.fullmatch(label)
        if match:
            token = match.group(0).rstrip("%").replace(",", "")
            try:
                numeric_values.append(Decimal(token))
            except InvalidOperation:
                continue
    return numeric_values


def _number_array(values, field, *, min_items=1):
    if not isinstance(values, list) or not min_items <= len(values) <= MAX_DATA_POINTS:
        raise ChartSpecError(f"{field} must contain between {min_items} and {MAX_DATA_POINTS} numbers.")
    parsed = [_number(item, f"{field}[{index}]") for index, item in enumerate(values)]
    return values, parsed


def _validate_series(series, *, kind, expected_length=None):
    if not isinstance(series, list) or not 1 <= len(series) <= MAX_SERIES:
        raise ChartSpecError(f"series must contain between 1 and {MAX_SERIES} items.")
    clean_series = []
    source_values = []
    point_count = 0
    for index, item in enumerate(series):
        field = f"series[{index}]"
        if kind == "scatter":
            _check_keys(item, {"name", "x", "y"}, {"name", "x", "y"}, field)
            x, x_decimals = _number_array(item["x"], f"{field}.x")
            y, y_decimals = _number_array(item["y"], f"{field}.y")
            if len(x) != len(y):
                raise ChartSpecError(f"{field}.x and {field}.y must have the same length.")
            clean_series.append({"name": _text(item["name"], f"{field}.name", limit=120), "x": x, "y": y})
            source_values.extend(x_decimals)
            source_values.extend(y_decimals)
            point_count += len(x)
        else:
            _check_keys(item, {"name", "values"}, {"name", "values"}, field)
            values, decimals = _number_array(item["values"], f"{field}.values")
            if expected_length is not None and len(values) != expected_length:
                raise ChartSpecError(f"{field}.values length must match the chart labels.")
            clean_series.append({"name": _text(item["name"], f"{field}.name", limit=120), "values": values})
            source_values.extend(decimals)
            point_count += len(values)
        if point_count > MAX_DATA_POINTS:
            raise ChartSpecError(f"A chart may contain at most {MAX_DATA_POINTS} data points.")
    return clean_series, source_values, point_count


def validate_chart_spec(spec, *, instruction=None, locale="en", allow_illustrative=None):
    """Validate and return a small normalized ChartSpec; reject all unknown fields."""
    if allow_illustrative is None:
        allow_illustrative = is_illustrative_request(instruction)
    if not isinstance(spec, dict):
        raise ChartSpecError("The response root must be an object.")

    status = spec.get("status")
    if status == "needs_data":
        _check_keys(spec, {"status", "message"}, {"status", "message"}, "response")
        return {"status": "needs_data", "message": _text(spec["message"], "message", limit=500)}
    if status != "ok":
        raise ChartSpecError("status must be 'ok' or 'needs_data'.")

    chart_type = spec.get("chart_type")
    if not isinstance(chart_type, str) or chart_type not in ALLOWED_CHART_TYPES:
        raise ChartSpecError("chart_type must be bar, line, scatter, or pie.")

    common = {"status", "chart_type", "title", "data_note"}
    if chart_type == "pie":
        allowed = common | {"labels", "values"}
        required = {"status", "chart_type", "title", "labels", "values"}
    elif chart_type == "bar":
        allowed = common | {"x_label", "y_label", "categories", "series"}
        required = {"status", "chart_type", "title", "x_label", "y_label", "categories", "series"}
    elif chart_type == "line":
        allowed = common | {"x_label", "y_label", "x", "series"}
        required = {"status", "chart_type", "title", "x_label", "y_label", "x", "series"}
    else:
        allowed = common | {"x_label", "y_label", "series"}
        required = {"status", "chart_type", "title", "x_label", "y_label", "series"}
    _check_keys(spec, allowed, required, "chart spec")

    data_note = None
    if allow_illustrative:
        data_note = "示例数据（假设）" if locale == "zh" else "Illustrative data"
    elif "data_note" in spec:
        raise ChartSpecError("data_note is allowed only for explicitly illustrative requests.")

    clean = {
        "status": "ok",
        "chart_type": chart_type,
        "title": _text(spec["title"], "title", allow_empty=True),
    }
    if data_note:
        clean["data_note"] = data_note

    source_values = []
    point_count = 0
    if chart_type == "pie":
        labels = _text_array(spec["labels"], "labels")
        values, decimals = _number_array(spec["values"], "values")
        if len(labels) != len(values):
            raise ChartSpecError("labels and values must have the same length.")
        if any(value < 0 for value in decimals):
            raise ChartSpecError("pie values must be zero or greater.")
        if not any(value > 0 for value in decimals):
            raise ChartSpecError("pie values must have a total greater than zero.")
        clean.update({"labels": labels, "values": values})
        source_values = decimals
        point_count = len(values)
    elif chart_type == "bar":
        categories = _text_array(spec["categories"], "categories")
        series, source_values, point_count = _validate_series(
            spec["series"], kind="values", expected_length=len(categories)
        )
        clean.update({"x_label": _text(spec["x_label"], "x_label", allow_empty=True),
                      "y_label": _text(spec["y_label"], "y_label", allow_empty=True),
                      "categories": categories, "series": series})
    elif chart_type == "line":
        x = _text_array(spec["x"], "x")
        series, source_values, point_count = _validate_series(
            spec["series"], kind="values", expected_length=len(x)
        )
        source_values.extend(_numeric_label_values(x))
        clean.update({"x_label": _text(spec["x_label"], "x_label", allow_empty=True),
                      "y_label": _text(spec["y_label"], "y_label", allow_empty=True),
                      "x": x, "series": series})
    else:
        series, source_values, point_count = _validate_series(spec["series"], kind="scatter")
        clean.update({"x_label": _text(spec["x_label"], "x_label", allow_empty=True),
                      "y_label": _text(spec["y_label"], "y_label", allow_empty=True),
                      "series": series})

    if point_count > MAX_DATA_POINTS:
        raise ChartSpecError(f"A chart may contain at most {MAX_DATA_POINTS} data points.")
    if instruction is not None and not allow_illustrative:
        source_numbers, has_data = _source_numbers(instruction)
        if not has_data:
            raise ChartSpecError("No usable numerical data was supplied; return status='needs_data'.")
        if any(value not in source_numbers for value in source_values):
            raise ChartSpecError("Every plotted number must be explicitly present in the user's request.")
    return clean


def parse_chart_spec(response, *, instruction=None, locale="en", allow_illustrative=None):
    """Parse JSON and validate it before it reaches the renderer."""
    if not isinstance(response, str):
        raise ChartSpecError("The model response must be JSON text.")
    if len(response) > MAX_RESPONSE_LENGTH:
        raise ChartSpecError("The model response is too large.")
    try:
        spec = json.loads(response)
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ChartSpecError(f"Invalid JSON: {exc.__class__.__name__}.") from None
    return validate_chart_spec(
        spec,
        instruction=instruction,
        locale=locale,
        allow_illustrative=allow_illustrative,
    )


def build_chart_spec_prompt(instruction, *, previous_error=None, locale="en", allow_illustrative=None):
    """Build an internal, localized prompt that requests data only, never code."""
    allow_illustrative = is_illustrative_request(instruction) if allow_illustrative is None else allow_illustrative
    if locale == "zh":
        rules = (
            "你是学术图表数据整理助手。只返回一个 JSON 对象，不要代码块、解释或其他文字。"
            "只允许 chart_type 为 bar、line、scatter、pie。用用户明确提供的数值；不得编造或推测事实性数据。"
            "若缺少绘图所需数值，返回 {\"status\":\"needs_data\",\"message\":\"...\"}。"
            "只有用户明确要求示例、假设、演示或示意数据时才可生成示意数值；此时 status 为 ok，并设置 data_note。"
            "不要输出 Python、表达式、文件路径、matplotlib 配置或额外字段。"
            "bar 使用 categories 和 series[{name,values}]；line 使用 x 和 series[{name,values}]；"
            "scatter 使用 series[{name,x,y}]；pie 使用 labels 和 values。所有数值必须是 JSON 数字。"
        )
        retry = f"上一份 JSON 无效：{previous_error}\n请修正并返回完整 JSON 对象。" if previous_error else ""
        request = f"用户绘图要求：\n{instruction}"
    else:
        rules = (
            "You organize academic chart data. Return exactly one JSON object, with no Markdown or commentary. "
            "Allowed chart_type values are bar, line, scatter, and pie. Use numerical values explicitly supplied by the user; "
            "never invent or infer factual statistics. If usable values are missing, return "
            '{"status":"needs_data","message":"..."}. Only generate illustrative values when the user explicitly requests '
            "example, illustrative, hypothetical, or sample data; then use status=ok and set data_note. "
            "Never return Python, expressions, file paths, matplotlib settings, or extra fields. "
            "bar uses categories and series[{name,values}]; line uses x and series[{name,values}]; "
            "scatter uses series[{name,x,y}]; pie uses labels and values. All plotted values must be JSON numbers."
        )
        retry = f"The previous JSON was invalid: {previous_error}\nReturn a complete corrected JSON object." if previous_error else ""
        request = f"User chart request:\n{instruction}"
    if allow_illustrative:
        if locale == "zh":
            rules += "本次用户已明确允许示意数据，图表中必须标注为示例数据（假设）。"
        else:
            rules += " The user explicitly permits illustrative data; the chart must be labeled as illustrative."
    return "\n\n".join(part for part in (rules, retry, request) if part)


def chart_result_message(status, locale="en"):
    if status == "needs_data":
        return (
            "还需要一些数据才能生成图表。请直接在绘图要求中写出需要比较的数值，例如：A=35、B=42、C=28。"
            if locale == "zh" else
            "More data is needed to draw this chart. Please include the values to compare, for example: A=35, B=42, C=28."
        )
    return (
        "暂时无法生成这张图，请补充或简化绘图要求后重试。"
        if locale == "zh" else
        "This chart could not be generated right now. Please simplify the request and try again."
    )


def chart_png_is_valid(path, *, max_pixels=MAX_CHART_PIXELS, max_dimension=MAX_CHART_DIMENSION):
    """Validate the PNG signature, IHDR dimensions, and bounded pixel count."""
    try:
        if not os.path.isfile(path) or os.path.getsize(path) < 33:
            return False
        with open(path, "rb") as stream:
            header = stream.read(33)
        if header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
            return False
        width, height = struct.unpack(">II", header[16:24])
        return (
            width > 0 and height > 0
            and width <= max_dimension and height <= max_dimension
            and width * height <= max_pixels
        )
    except (OSError, struct.error, TypeError):
        return False


def _display_text(value):
    # Matplotlib mathtext is not needed in labels; escape its delimiter so arbitrary
    # user/model strings remain literal display text.
    return value.replace("$", r"\$")


def _font_settings():
    from matplotlib import font_manager

    preferred = ("Arial Unicode MS", "SimHei", "PingFang SC")
    installed = {font.name for font in font_manager.fontManager.ttflist}
    available = [name for name in preferred if name in installed]
    if "DejaVu Sans" not in available:
        available.append("DejaVu Sans")
    return {"font.sans-serif": available, "axes.unicode_minus": False}


def render_chart_spec(spec, output_path):
    """Render a validated chart with fixed, trusted matplotlib code."""
    illustrative_notes = {"Illustrative data", "示例数据（假设）"}
    allow_illustrative = isinstance(spec, dict) and spec.get("data_note") in illustrative_notes
    clean = validate_chart_spec(spec, allow_illustrative=allow_illustrative)
    if clean["status"] != "ok":
        raise ChartRenderError("A needs_data result cannot be rendered.")
    if not isinstance(output_path, (str, os.PathLike)) or os.fspath(output_path).lower().endswith(".png") is False:
        raise ChartRenderError("The renderer requires a PNG output path.")
    output_path = os.fspath(output_path)
    if os.path.exists(output_path):
        raise ChartRenderError("The renderer will not overwrite an existing chart.")

    import matplotlib
    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt
    import numpy as np

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    figure = None
    try:
        with matplotlib.rc_context(_font_settings()):
            figure, axis = plt.subplots(figsize=(10, 6))
            chart_type = clean["chart_type"]
            title = _display_text(clean["title"])
            note = clean.get("data_note")
            if chart_type == "bar":
                categories = [_display_text(value) for value in clean["categories"]]
                positions = np.arange(len(categories), dtype=float)
                width = 0.8 / len(clean["series"])
                for index, series in enumerate(clean["series"]):
                    offset = (index - (len(clean["series"]) - 1) / 2) * width
                    axis.bar(positions + offset, series["values"], width=width,
                             label=_display_text(series["name"]))
                axis.set_xticks(positions)
                axis.set_xticklabels(categories, rotation=35 if len(categories) > 8 else 0, ha="right" if len(categories) > 8 else "center")
                axis.set_xlabel(_display_text(clean["x_label"]))
                axis.set_ylabel(_display_text(clean["y_label"]))
            elif chart_type == "line":
                x_values = [_display_text(value) for value in clean["x"]]
                for series in clean["series"]:
                    axis.plot(x_values, series["values"], marker="o", label=_display_text(series["name"]))
                axis.set_xlabel(_display_text(clean["x_label"]))
                axis.set_ylabel(_display_text(clean["y_label"]))
                if len(x_values) > 8:
                    axis.tick_params(axis="x", labelrotation=35)
            elif chart_type == "scatter":
                for series in clean["series"]:
                    axis.scatter(series["x"], series["y"], label=_display_text(series["name"]), alpha=0.8)
                axis.set_xlabel(_display_text(clean["x_label"]))
                axis.set_ylabel(_display_text(clean["y_label"]))
            else:
                values = clean["values"]
                scale = max(values)
                scaled_values = [value / scale for value in values]
                axis.pie(scaled_values, labels=[_display_text(label) for label in clean["labels"]],
                         autopct="%1.1f%%", startangle=90)
                axis.axis("equal")

            if chart_type != "pie":
                axis.set_title(title)
                axis.grid(axis="y", linestyle="--", alpha=0.3)
                axis.set_axisbelow(True)
                axis.legend()
            else:
                axis.set_title(title)
            if note:
                figure.text(0.5, 0.015, _display_text(note), ha="center", va="bottom", fontsize=9)
                figure.tight_layout(rect=(0, 0.045, 1, 1))
            else:
                figure.tight_layout()
            # Keep the raster dimensions tied to the fixed 10x6 inch figure;
            # bbox_inches='tight' can expand the image to fit adversarially long labels.
            figure.savefig(output_path, format="png", dpi=150)
    except Exception as exc:
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except OSError:
                pass
        raise ChartRenderError(f"Chart rendering failed: {type(exc).__name__}.") from exc
    finally:
        if figure is not None:
            plt.close(figure)

    if not chart_png_is_valid(output_path):
        try:
            os.remove(output_path)
        except OSError:
            pass
        raise ChartRenderError("The renderer did not produce a valid bounded PNG.")
    return output_path


def generate_chart_image(instruction, output_path, llm_call, *, locale="en", max_attempts=3):
    """Request, validate, and render a chart without persisting application records."""
    allow_illustrative = is_illustrative_request(instruction)
    if not allow_illustrative and not has_usable_numeric_data(instruction):
        return {"status": "needs_data", "message": chart_result_message("needs_data", locale)}
    try:
        attempts = min(3, max(1, int(max_attempts)))
    except (TypeError, ValueError, OverflowError):
        attempts = 3

    previous_error = None
    for attempt in range(attempts):
        prompt = build_chart_spec_prompt(
            instruction,
            previous_error=previous_error,
            locale=locale,
            allow_illustrative=allow_illustrative,
        )
        if locale == "zh":
            system_prompt = "你只返回符合要求的合法 JSON 图表数据，不生成或执行代码。"
        else:
            system_prompt = "Return only valid JSON chart data. Never generate or execute code."
        try:
            response = require_valid_llm_output(llm_call(
                prompt,
                system_prompt=system_prompt,
                max_tokens=6000,
                temp=0.3 if previous_error else 0.7,
                json_mode=True,
            ))
        except LLMOutputError as exc:
            return {"status": "error", "message": chart_result_message("error", locale), "debug": str(exc)}
        except Exception as exc:
            return {"status": "error", "message": chart_result_message("error", locale), "debug": type(exc).__name__}

        try:
            spec = parse_chart_spec(
                response,
                instruction=instruction,
                locale=locale,
                allow_illustrative=allow_illustrative,
            )
        except ChartSpecError as exc:
            previous_error = str(exc)[:240]
            continue

        if spec["status"] == "needs_data":
            return {"status": "needs_data", "message": chart_result_message("needs_data", locale)}
        try:
            render_chart_spec(spec, output_path)
        except (ChartRenderError, OSError, ValueError) as exc:
            return {"status": "error", "message": chart_result_message("error", locale), "debug": str(exc)[:240]}
        return {"status": "ok", "path": output_path, "spec": spec}

    return {
        "status": "error",
        "message": chart_result_message("error", locale),
        "debug": previous_error,
    }
