"""Laya 内容分诊台 — 本地 Web 应用（人工参考工具）

分类由同目录的 categories.json 决定，可在**网页端「分类管理」**里增删改，
也可以用 Excel 编辑 CSV 后导入。本文件不需要使用者修改。

语言支持:
  · 界面/输出语言: 中文 / English / Bahasa Melayu，由前端选择
  · 送进模型的问题语言: 中文内容用中文问题，其余（含马来文）用英文问题。
    依据实测: 马来文输入 + 英文问题 3/4，+ 马来文问题仅 2/4；
    中文输入 + 中文问题明显优于英文问题。语言识别用 laya.lang.analyse。

其他设计依据（均为本机实测结论）:
  1. 单检查点 multilingual。4GB 显存经不起常驻两个检查点。
  2. noul 问题【不要】加 criteria 标签，实测加了会让判定翻转。
  3. 判定对类别描述的【措辞】极度敏感，对错置信度几乎相同（0.975 vs 0.977）。
     因此本应用只给参考信号，不给"可否自动处置"的结论。
  4. 必须保留 other 类别，否则域外内容会被自信地硬塞进某个类别。

启动:  D:\\laya\\app\\start.bat
"""
import csv
import io
import json
import math
import os
import re
import threading
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Tuple

os.environ.setdefault("USE_TF", "0")

import uvicorn
from fastapi import Body, FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import laya
from laya.lang import analyse as lang_analyse

# --------------------------------------------------------------------------- 配置
HOST = os.environ.get("APP_HOST", "127.0.0.1")
PORT = int(os.environ.get("APP_PORT", "8100"))
CHECKPOINT = "convaiinnovations/laya"
SUBFOLDER = "multilingual"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_PATH = os.path.join(BASE_DIR, "categories.json")
CSV_PATH = os.path.join(BASE_DIR, "categories.csv")
STATIC_DIR = os.path.join(BASE_DIR, "static")

UI_LANGS = ("zh", "en", "ms")          # 界面语言
MAX_ENABLED_WARN = 12

# 注意: 不能用 str.isalnum() —— 它对中文字符也返回 True，会放过中文标识。
KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

# 模型问题的语言: 中文内容走 zh，其余走 en（马来文实测英文问题更准）
def q_lang_for(script: Optional[str]) -> str:
    return "zh" if script == "han" else "en"


# --------------------------------------------------------------------------- 分类存储
_lock = threading.Lock()
_cfg: Dict = {
    "categories": [],          # [{key, enabled, zh:{name,desc}, en:{...}, ms:{...}}]
    "questions": None,         # {category_question, questions}；None 表示用默认值
    "errors": [],
    "warnings": [],
    "mtime": None,
    "loaded_at": None,
    "ok": False,
}


def _questions_of(cfg: Dict) -> Dict:
    """取问题配置；没有就用默认值。

    注意必须惰性解析：DEFAULT_* 定义在 _cfg 之后，
    在模块级引用会直接 NameError。
    """
    q = cfg.get("questions")
    if isinstance(q, dict) and q.get("questions") is not None:
        return q
    return {"category_question": DEFAULT_CATEGORY_QUESTION,
            "questions": DEFAULT_EXTRA_QUESTIONS}


def _blank_entry(key: str = "") -> Dict:
    return {"key": key, "enabled": True,
            **{lg: {"name": "", "desc": ""} for lg in UI_LANGS}}


# 「other」是锁定分类：强制存在、强制启用、标识不可改。
# 理由：它承接"不属于任何预设分类"的内容。一旦缺失或被停用，域外内容会被
# 硬塞进某个分类里，而且看起来还很"确定"——这是最难发现的失效模式。
# 网页端会把它渲染成只读，服务端再兜一层自动修复，任何客户端都破坏不了它。
LOCKED_KEY = "other"
LOCKED_ENTRY = {
    "key": LOCKED_KEY,
    "enabled": True,
    "zh": {"name": "其他", "desc": "以上都不是的其他内容"},
    "en": {"name": "Other", "desc": "none of the above"},
    "ms": {"name": "Lain-lain", "desc": "tiada satu pun di atas"},
}
MSG_LOCKED_RESTORED = ("「other」分类是固定的，不能删除，已自动恢复，"
                       "并保留在配置中。")
MSG_LOCKED_REENABLED = "「other」分类是固定的，不能停用，已自动重新启用。"
MSG_LOCKED_REFILLED = "「other」分类是固定的，空白项已自动填回默认内容。"


# ---------------------------------------------------------------- 问题配置
# 原来这些问题写死在 build_questions() 里。现在搬成配置的初值，
# 使用者可以在网页端「问题设置」里改措辞、改档位、改显示名称、启停、增删。
#
# 三种问题类型（Laya 原生支持）：
#   choice  多选一  -> criteria 是一组选项，每项要有 key 和三语文字
#   score   有序打分 -> criteria 是一组档位（有序），只给文字
#   noul    是/否    -> 不给 criteria（实测加了中文标签反而会判错，见 README）
#
# 分类那个问题是特殊的：它的选项来自 categories，所以只需要配措辞，且不允许停用。

DEFAULT_CATEGORY_QUESTION = {
    "enabled": True,                       # 固定启用，不可停用
    "name": {"zh": "分类", "en": "Category", "ms": "Kategori"},
    "instructions": {
        "zh": "这段内容主要属于哪一类？",
        "en": "What is this text mainly about?",
        "ms": "Teks ini terutamanya tentang apa?",
    },
    # 拆分问法（每个分类问一个是非题）里 {desc} 会被替换成该分类的描述
    "noul_template": {
        "zh": "这段内容是否属于「{desc}」？",
        "en": "Does this text concern {desc}?",
        "ms": "Adakah teks ini mengenai {desc}?",
    },
}

DEFAULT_EXTRA_QUESTIONS = [
    {
        "key": "urgency", "enabled": True, "type": "score",
        "name": {"zh": "紧急程度", "en": "Urgency", "ms": "Tahap segera"},
        "instructions": {
            "zh": "这段内容的紧急程度如何？",
            "en": "How urgent is this text?",
            "ms": "Sejauh mana kecemasan teks ini?",
        },
        "criteria": [
            {"zh": "不急，可以放着", "en": "not urgent", "ms": "tidak mendesak"},
            {"zh": "一般，正常排队处理", "en": "normal queue", "ms": "baris biasa"},
            {"zh": "较急，需要尽快处理", "en": "fairly urgent, handle soon",
             "ms": "agak mendesak, urus segera"},
            {"zh": "非常紧急，有明确截止期限或已阻断业务",
             "en": "very urgent: a deadline or something already broken",
             "ms": "sangat mendesak: ada tarikh akhir atau sesuatu sudah rosak"},
        ],
    },
    {
        "key": "negative", "enabled": True, "type": "noul",
        "name": {"zh": "负面情绪", "en": "Negative tone", "ms": "Nada negatif"},
        "instructions": {
            "zh": "这段内容是否表达了不满或负面情绪？",
            "en": "Does this text express dissatisfaction or a negative tone?",
            "ms": "Adakah teks ini menyatakan ketidakpuasan hati atau nada negatif?",
        },
        "criteria": [],
    },
    {
        "key": "needs_human", "enabled": True, "type": "noul",
        "name": {"zh": "需人工介入", "en": "Needs a human", "ms": "Perlu manusia"},
        "instructions": {
            "zh": "这段内容是否需要人工尽快介入？",
            "en": "Does this text need a human to step in soon?",
            "ms": "Perlukah manusia campur tangan segera untuk teks ini?",
        },
        "criteria": [],
    },
    {
        "key": "harmful", "enabled": True, "type": "noul",
        "name": {"zh": "有害内容", "en": "Harmful content", "ms": "Kandungan berbahaya"},
        "instructions": {
            "zh": "这段内容是否包含威胁、辱骂骚扰或违法信息？",
            "en": "Does this text contain threats, harassment or illegal content?",
            "ms": "Adakah teks ini mengandungi ancaman, gangguan atau kandungan haram?",
        },
        "criteria": [],
    },
]

QTYPE_SET = ("choice", "score", "noul")
MAX_EXTRA_QUESTIONS = 12
# 这些 key 会和内部生成的 key 撞车，不允许使用者占用
RESERVED_QKEYS = {"category"}


def _langs_dict(**kw) -> Dict:
    return {lg: kw.get(lg, "") for lg in UI_LANGS}


def validate_questions(raw: Optional[Dict], cats: List[Dict]
                       ) -> Tuple[Dict, List[str], List[str]]:
    """校验问题配置。返回 (规范化后的配置, errors, warnings)。"""
    errors: List[str] = []
    warnings: List[str] = []
    raw = raw if isinstance(raw, dict) else {}

    # ---- 分类问题：措辞可改，但不允许停用（它产出本应用的主判定）
    cq_raw = raw.get("category_question") or {}
    cq = {
        "enabled": True,
        "name": _clean_langs(cq_raw.get("name"), DEFAULT_CATEGORY_QUESTION["name"]),
        "instructions": _clean_langs(cq_raw.get("instructions"),
                                     DEFAULT_CATEGORY_QUESTION["instructions"]),
        "noul_template": _clean_langs(cq_raw.get("noul_template"),
                                      DEFAULT_CATEGORY_QUESTION["noul_template"]),
    }
    if "{desc}" not in cq["noul_template"].get("zh", "") + \
                       cq["noul_template"].get("en", "") + \
                       cq["noul_template"].get("ms", ""):
        warnings.append("分类拆分的问法里没有 {desc} 占位符，"
                        "所有分类会被问成同一个问题。")

    # ---- 附加问题
    extra_raw = raw.get("questions")
    if extra_raw is None:
        extra_raw = DEFAULT_EXTRA_QUESTIONS
    if not isinstance(extra_raw, list):
        errors.append("questions 必须是一个列表。")
        return {"category_question": cq, "questions": []}, errors, warnings

    seen: set = set()
    out: List[Dict] = []
    for i, item in enumerate(extra_raw, start=1):
        if not isinstance(item, dict):
            errors.append(f"第 {i} 个附加问题格式不对。")
            continue
        key = str(item.get("key", "")).strip()
        if not key:
            errors.append(f"第 {i} 个附加问题：必须填「标识」。")
            continue
        if not KEY_RE.match(key):
            errors.append(f"附加问题的标识「{key}」不合法：只能用英文字母开头，"
                          f"后面跟英文字母、数字或下划线。")
            continue
        if key in RESERVED_QKEYS or key.startswith("is_"):
            errors.append(f"附加问题的标识不能叫「{key}」"
                          f"（category 和 is_ 开头的名字是内部保留的）。")
            continue
        if key in seen:
            errors.append(f"附加问题的标识「{key}」重复了。")
            continue
        seen.add(key)

        qtype = str(item.get("type", "noul")).strip().lower()
        if qtype not in QTYPE_SET:
            errors.append(f"附加问题「{key}」的类型「{qtype}」不认识，"
                          f"只能是 choice / score / noul。")
            continue

        q = {
            "key": key,
            "enabled": bool(item.get("enabled", True)),
            "type": qtype,
            "name": _clean_langs(item.get("name"), {"zh": key, "en": key, "ms": key}),
            "instructions": _clean_langs(item.get("instructions"), None),
            "criteria": [],
        }
        if not _any_text(q["instructions"]):
            errors.append(f"附加问题「{key}」至少要填一种语言的「提问」。")
            continue

        crit = item.get("criteria") or []
        if qtype == "noul":
            if crit:
                warnings.append(f"附加问题「{key}」是是非题，已忽略你填的选项"
                                f"（实测给 noul 加选项文字反而会判错）。")
        elif qtype == "score":
            levels = [_clean_langs(c, None) for c in crit if isinstance(c, dict)]
            levels = [c for c in levels if _any_text(c)]
            if len(levels) < 2:
                errors.append(f"附加问题「{key}」是打分题，至少要 2 个档位。")
                continue
            q["criteria"] = levels
        else:                                   # choice —— 与分类问题共用同一个校验器
            opts, oerrors, owarns = _validate_options(crit, f"附加问题「{key}」的")
            errors += oerrors
            warnings += owarns
            if oerrors:
                continue
            if len(opts) < 2:
                errors.append(f"附加问题「{key}」是多选题，至少要 2 个有效选项。")
                continue
            q["criteria"] = opts
        out.append(q)

    if errors:
        return {"category_question": cq, "questions": []}, errors, warnings

    n_enabled = len([q for q in out if q["enabled"]])
    if n_enabled > MAX_EXTRA_QUESTIONS:
        warnings.append(f"启用了 {n_enabled} 个附加问题，超过 {MAX_EXTRA_QUESTIONS} 个后"
                        f"每个问题分到的 token 预算变少，准确率会下降。")
    return {"category_question": cq, "questions": out}, errors, warnings


def _clean_langs(src: Any, default: Optional[Dict]) -> Dict:
    """把 {zh,en,ms} 结构规整成三语字典；空值用 default 兜底。"""
    out = {}
    src = src if isinstance(src, dict) else {}
    for lg in UI_LANGS:
        v = src.get(lg)
        if v is None or not str(v).strip():
            v = (default or {}).get(lg, "")
        out[lg] = str(v).strip()
    return out


def _any_text(d: Dict) -> bool:
    return any((d or {}).get(lg) for lg in UI_LANGS)


def _qtext(d: Dict, lang: str) -> str:
    """取某语言的文字，空了按 lang -> zh -> en -> ms 兜底。"""
    for lg in (lang, "zh", "en", "ms"):
        v = (d or {}).get(lg)
        if v:
            return v
    return ""


# ---------------------------------------------------------------- 选项：统一模块
# 分类问题的选项（= 分类）和 choice 问题的选项，本来是两套代码：
# 一套存在顶层 categories、一套存在 questions[i].criteria，校验和渲染各写一遍。
# 现在统一成同一个结构、同一个校验器、同一套前端编辑器。
#
# 统一后的选项结构（三语都带「名称」和「描述」）：
#   {"key": "refund", "enabled": true,
#    "zh": {"name": "退款/撤销重复扣款", "desc": "退款、或要求撤销重复扣款"},
#    "en": {...}, "ms": {...}}
#
# 「名称」给人看（结果卡片、CSV），「描述」给模型看。描述没填就用名称兜底，
# 所以只填一个也能用。

def _opt_text(opt: Dict, lang: str) -> str:
    """选项送给模型的文字：优先「描述」，没填就用「名称」。"""
    for field in ("desc", "name"):
        for lg in (lang, "zh", "en", "ms"):
            v = (opt.get(lg) or {}).get(field)
            if v:
                return v
    return opt.get("key", "")


def _opt_names(opt: Dict) -> Dict[str, str]:
    """选项给人看的显示名（每种语言都取好）：优先「名称」，没填就用「描述」。"""
    out = {}
    for lg in UI_LANGS:
        v = ""
        for field in ("name", "desc"):
            for l2 in (lg, "zh", "en", "ms"):
                v = (opt.get(l2) or {}).get(field)
                if v:
                    break
            if v:
                break
        out[lg] = v or opt.get("key", "")
    return out


def _validate_options(raw: Any, where: str
                      ) -> Tuple[List[Dict], List[str], List[str]]:
    """校验一组选项。分类问题和 choice 问题共用这一个入口。

    兼容两种旧结构，读到就自动升级成统一结构：
      · 扁平标签（早期 choice 问题）：{"key":"o1","zh":"文字","en":"...","ms":"..."}
      · 分类结构（本来就是统一结构，无需升级）

    「other」这一项的存在性不在这里校验——它由 _enforce_locked 自动补齐。
    """
    errors: List[str] = []
    warnings: List[str] = []
    opts: List[Dict] = []
    keys: set = set()

    for i, item in enumerate(raw if isinstance(raw, list) else [], start=1):
        if not isinstance(item, dict):
            errors.append(f"{where}第 {i} 个选项格式不对。")
            continue
        key = str(item.get("key", "")).strip()
        if not key:
            errors.append(f"{where}第 {i} 个选项要填「标识」。")
            continue
        if not KEY_RE.match(key):
            errors.append(f"{where}选项标识「{key}」不合法：只能用英文字母开头，"
                          f"后面跟英文字母、数字或下划线。")
            continue
        if key in keys:
            errors.append(f"{where}选项标识「{key}」重复了，每一个必须唯一。")
            continue
        keys.add(key)

        opt = {"key": key, "enabled": bool(item.get("enabled", True)),
               **{lg: {"name": "", "desc": ""} for lg in UI_LANGS}}
        for lg in UI_LANGS:
            cell = item.get(lg)
            if isinstance(cell, dict):                 # 统一结构
                opt[lg]["name"] = str(cell.get("name", "")).strip()
                opt[lg]["desc"] = str(cell.get("desc", "")).strip()
            elif cell is not None and str(cell).strip():   # 旧扁平结构，升级
                opt[lg]["name"] = str(cell).strip()
        # 注意：不能用 _opt_text() 来判断"有没有填文字"——它最后会兜底返回 key，
        # 于是空选项也会被当成有内容。必须直接查名称/描述字段。
        if not any((opt[lg].get(f) or "").strip()
                   for lg in UI_LANGS for f in ("name", "desc")):
            errors.append(f"{where}选项「{key}」至少要填一种语言的名称或描述。")
            continue
        opts.append(opt)

    if errors:
        return [], errors, warnings
    return opts, errors, warnings


def _enforce_locked(out: List[Dict], warnings: List[str]) -> None:
    """就地保证锁定分类存在、启用、且文字不为空。"""
    locked = next((c for c in out if c["key"] == LOCKED_KEY), None)

    if locked is None:
        out.append({k: (dict(v) if isinstance(v, dict) else v)
                    for k, v in LOCKED_ENTRY.items()})
        warnings.append(MSG_LOCKED_RESTORED)
        return

    if not locked["enabled"]:
        locked["enabled"] = True
        warnings.append(MSG_LOCKED_REENABLED)

    # 文字可以被改写，但不允许被清空——空描述会让模型无法判断"以上都不是"
    refilled = False
    for lg in UI_LANGS:
        for field in ("name", "desc"):
            if not locked[lg].get(field):
                locked[lg][field] = LOCKED_ENTRY[lg][field]
                refilled = True
    if refilled:
        warnings.append(MSG_LOCKED_REFILLED)


def validate(raw: List[Dict]) -> Tuple[List[Dict], List[str], List[str]]:
    """校验并规范化分类列表（= 分类问题的选项）。

    选项本身的校验交给共用的 _validate_options()；这里只加分类特有的规则：
    至少 2 个、必须保留 other、数量上限。
    """
    out, errors, warnings = _validate_options(raw, "分类")
    if errors:
        return [], errors, warnings

    _enforce_locked(out, warnings)          # other 不可删除 / 不可停用

    enabled = [c for c in out if c["enabled"]]
    if len(enabled) < 2:
        errors.append(f"至少要启用 2 个分类，当前只有 {len(enabled)} 个。")
    if len(enabled) > MAX_ENABLED_WARN:
        warnings.append(f"启用了 {len(enabled)} 个分类，超过 {MAX_ENABLED_WARN} 个后"
                        f"每个分类分到的 token 预算变少，准确率会下降。")

    if errors:
        return [], errors, warnings

    descs = [_opt_text(c, "zh") for c in enabled]
    if len(set(descs)) != len(descs):
        warnings.append("有分类的文字完全相同，模型无法区分它们。")
    return out, errors, warnings


def load_from_disk(force: bool = False) -> Dict:
    """按 mtime 热加载 categories.json。校验失败时保留上一份可用配置。"""
    with _lock:
        try:
            mtime = os.path.getmtime(JSON_PATH)
        except OSError:
            mtime = None

        if not force and mtime is not None and mtime == _cfg["mtime"]:
            return _cfg

        if mtime is None:
            # 没有 json 但有 csv -> 自动迁移一次
            if os.path.exists(CSV_PATH):
                try:
                    cats, errs, warns = _csv_to_categories(open(CSV_PATH, encoding="utf-8-sig").read())
                    if not errs:
                        save_categories(cats)
                        return load_from_disk(force=True)
                except Exception:                             # noqa: BLE001
                    pass
            _cfg["errors"] = [f"找不到分类文件：{JSON_PATH}"]
            _cfg["ok"] = False
            return _cfg

        try:
            data = json.loads(open(JSON_PATH, encoding="utf-8").read())
        except json.JSONDecodeError as exc:
            _cfg["errors"] = [f"categories.json 不是合法的 JSON（第 {exc.lineno} 行）：{exc.msg}"]
            _cfg["ok"] = False
            _cfg["mtime"] = mtime
            return _cfg
        except OSError as exc:
            _cfg["errors"] = [f"读取 categories.json 失败：{exc}"]
            _cfg["ok"] = False
            return _cfg

        raw = data.get("categories") if isinstance(data, dict) else data
        raw_q = ({k: data.get(k) for k in ("category_question", "questions")}
                 if isinstance(data, dict) else {})
        cats, errors, warnings = validate(raw or [])
        qcfg, qerrors, qwarnings = validate_questions(raw_q, cats)
        errors = list(errors) + list(qerrors)
        warnings = list(warnings) + list(qwarnings)
        _cfg["mtime"] = mtime
        _cfg["warnings"] = warnings
        if errors:
            _cfg["errors"] = errors
            _cfg["ok"] = False            # 保留上一份可用配置
        else:
            _cfg["categories"] = cats
            _cfg["questions"] = qcfg
            _cfg["errors"] = []
            _cfg["ok"] = True
            _cfg["loaded_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        return _cfg


def save_categories(cats: List[Dict], questions: Optional[Dict] = None) -> None:
    """原子写入 categories.json（分类 + 问题配置）。"""
    if questions is None:
        with _lock:
            questions = _cfg.get("questions")
        if not questions:
            questions = {"category_question": DEFAULT_CATEGORY_QUESTION,
                         "questions": DEFAULT_EXTRA_QUESTIONS}
    payload = {"version": 2,
               "note": "分类与问题配置。可在网页端「分类管理」和「问题设置」里维护，"
                       "也可手工编辑本文件。",
               "categories": cats,
               "category_question": questions.get("category_question"),
               "questions": questions.get("questions", [])}
    tmp = JSON_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, JSON_PATH)
    with _lock:
        _cfg["mtime"] = None              # 强制下次重新加载
    load_from_disk(force=True)


# --------------------------------------------------------------------------- 表格互通
# CSV / TSV / XLSX 走同一条路：都先变成「行数据」，再交给同一个解析器。
# 这样加一种格式只需要写"怎么变成行"和"怎么从行变出来"，校验逻辑只有一份。
TABLE_HEADER = ["启用", "标识", "中文名称", "中文描述", "英文名称", "英文描述",
                "马来文名称", "马来文描述"]
LANG_COLS = {"zh": ("中文名称", "中文描述"), "en": ("英文名称", "英文描述"),
             "ms": ("马来文名称", "马来文描述")}

# openpyxl 是可选的：没装也不影响应用运行，只是 xlsx 导入导出会给出明确提示。
try:
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    HAVE_XLSX = True
except ImportError:                                          # noqa: BLE001
    openpyxl = None
    HAVE_XLSX = False

MSG_NO_XLSX = ("服务器没装 openpyxl，无法处理 Excel 文件。"
               "请在命令行执行：pip install openpyxl（或重新运行 install.bat）")


def _categories_to_rows(cats: List[Dict]) -> List[List[str]]:
    """分类 -> 行数据（表格的首行是表头）。"""
    rows = [list(TABLE_HEADER)]
    for c in cats:
        row = ["1" if c["enabled"] else "0", c["key"]]
        for lg in UI_LANGS:
            row += [c[lg]["name"], c[lg]["desc"]]
        rows.append(row)
    return rows


def _cell_to_text(v: Any) -> str:
    """Excel 单元格 -> 文本。数字型要特别处理：Excel 会把 1 存成 1.0。"""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _rows_to_categories(rows: List[List[Any]]) -> Tuple[List[Dict], List[str], List[str]]:
    """行数据 -> 分类。CSV / TSV / XLSX 共用这一个解析器。"""
    rows = [r for r in rows if any(_cell_to_text(c) for c in r)]
    if not rows:
        return [], ["表格是空的。"], []

    header = [_cell_to_text(c).lstrip("\ufeff") for c in rows[0]]
    # 兼容旧版 6 列表格（没有马来文那两列）
    if "马来文名称" not in header:
        header = header + ["马来文名称", "马来文描述"]
    missing = [c for c in TABLE_HEADER if c not in header]
    if missing:
        return [], [f"表格表头缺少列：{'、'.join(missing)}。"
                    f"第一行应为：{','.join(TABLE_HEADER)}"], []
    idx = {c: header.index(c) for c in TABLE_HEADER}

    cats: List[Dict] = []
    for row in rows[1:]:
        def cell(col: str, _row=row) -> str:
            i = idx[col]
            return _cell_to_text(_row[i]) if i < len(_row) else ""

        enabled_raw = cell("启用").lower()
        enabled = enabled_raw in {"1", "是", "y", "yes", "true", "t", "启用", "开", ""}
        key = cell("标识")
        if not key:
            continue
        entry = _blank_entry(key)
        entry["enabled"] = enabled
        for lg, (ncol, dcol) in LANG_COLS.items():
            entry[lg] = {"name": cell(ncol), "desc": cell(dcol)}
        cats.append(entry)
    return validate(cats)


def _sniff_delimiter(text: str) -> str:
    """判断是逗号分隔还是制表符分隔（Excel 另存为"文本(制表符分隔)"很常见）。"""
    head = "\n".join(text.splitlines()[:5])
    return "\t" if head.count("\t") > head.count(",") else ","


def _text_to_rows(text: str) -> List[List[str]]:
    # Excel 和我们的导出都会带 UTF-8 BOM，不清掉的话表头第一个单元格会变成
    # "\ufeff启用"，导致"表头缺少列：启用"的误报。
    text = text.lstrip("\ufeff")
    delim = _sniff_delimiter(text)
    sep = "\t" if delim == "\t" else ","
    return list(csv.reader(io.StringIO(text), delimiter=sep))


def _rows_to_csv(rows: List[List[str]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


def _categories_to_csv(cats: List[Dict]) -> str:
    return _rows_to_csv(_categories_to_rows(cats))


def _csv_to_categories(text: str) -> Tuple[List[Dict], List[str], List[str]]:
    return _rows_to_categories(_text_to_rows(text))


def _categories_to_xlsx(cats: List[Dict]) -> bytes:
    """生成真正的 .xlsx（带表头加粗、列宽、冻结首行），员工用 Excel 打开更好编辑。"""
    if not HAVE_XLSX:
        raise RuntimeError(MSG_NO_XLSX)
    rows = _categories_to_rows(cats)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "分类"
    for r in rows:
        ws.append(r)

    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="4A5568")
    for cell in ws[1]:
        cell.font = head_font
        cell.fill = head_fill
        cell.alignment = Alignment(vertical="center")
    widths = [7, 14, 16, 34, 16, 34, 16, 34]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    ws.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _xlsx_to_categories(data: bytes) -> Tuple[List[Dict], List[str], List[str]]:
    if not HAVE_XLSX:
        return [], [MSG_NO_XLSX], []
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    except Exception as exc:                                 # noqa: BLE001
        return [], [f"打不开这个 Excel 文件：{type(exc).__name__}: {exc}"], []
    try:
        ws = wb.worksheets[0]
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()
    return _rows_to_categories(rows)


def import_table(filename: str, data: bytes) -> Tuple[List[Dict], List[str], List[str]]:
    """按内容判断格式并解析。扩展名不可靠，所以优先后缀名、再退回看文件头。"""
    name = (filename or "").lower()
    if name.endswith(".xlsx") or name.endswith(".xlsm") or data[:2] == b"PK":
        return _xlsx_to_categories(data)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = data.decode("gbk")          # 中文 Excel 存的 CSV 可能是 GBK
        except UnicodeDecodeError:
            return [], ["文件编码无法识别。请另存为「CSV UTF-8」或 .xlsx 后重试。"], []
    return _csv_to_categories(text)


# --------------------------------------------------------------------------- 模型
_agent = None
_model_lock = threading.Lock()          # predict 不是线程安全的，串行化
_model_state = {"status": "loading", "error": None, "loaded_in": None}


def load_model() -> None:
    global _agent
    t0 = time.time()
    try:
        _agent = laya.load(CHECKPOINT, subfolder=SUBFOLDER)
        _model_state["status"] = "ready"
        _model_state["loaded_in"] = round(time.time() - t0, 1)
    except Exception as exc:                                  # noqa: BLE001
        _model_state["status"] = "error"
        _model_state["error"] = f"{type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------- 分析
STATE_KEY = "text"


def _desc_for(cat: Dict, lang: str) -> str:
    """分类给模型看的文字。现在与 choice 选项共用同一套取法（描述优先，名称兜底）。"""
    return _opt_text(cat, lang)


def build_questions(lang: str, cats: List[Dict], qcfg: Dict) -> Dict:
    """按配置构造问题。一次前向里问完所有问题。lang: 'zh' 或 'en'。"""
    q: Dict[str, Dict] = {}
    cq = qcfg.get("category_question") or DEFAULT_CATEGORY_QUESTION

    # --- 分类问题（选项来自 categories，措辞来自配置）
    q["category"] = {
        "type": "choice",
        "instructions": _qtext(cq["instructions"], lang),
        "criteria": {c["key"]: _desc_for(c, lang) for c in cats},
    }
    # --- 分类的 noul 拆分问法：模板里的 {desc} 换成该分类的描述
    tmpl = _qtext(cq["noul_template"], lang)
    for c in cats:
        q[f"is_{c['key']}"] = {
            "type": "noul",
            "instructions": tmpl.replace("{desc}", _desc_for(c, lang)),
        }

    # --- 使用者自定义的附加问题
    for item in qcfg.get("questions") or []:
        if not item.get("enabled"):
            continue
        key, qtype = item["key"], item["type"]
        spec: Dict = {"type": qtype, "instructions": _qtext(item["instructions"], lang)}
        if qtype == "choice":
            spec["criteria"] = {o["key"]: _opt_text(o, lang) for o in item["criteria"]}
        elif qtype == "score":
            spec["criteria"] = [_qtext(c, lang) for c in item["criteria"]]
        # noul 不加 criteria（实测加了中文标签反而判错）
        q[key] = spec
    return q


def normalise(probs: Dict[str, float]) -> Dict[str, float]:
    """把一组互相独立的二元概率转成互斥的类别分布 (logit + softmax)。"""
    logits = {}
    for k, p in probs.items():
        p = min(max(float(p), 1e-4), 1 - 1e-4)
        logits[k] = math.log(p / (1 - p))
    top = max(logits.values())
    exps = {k: math.exp(v - top) for k, v in logits.items()}
    total = sum(exps.values())
    return {k: v / total for k, v in exps.items()}


def analyse(text: str) -> Dict:
    if _agent is None:
        return {"ok": False, "error": "model_not_ready"}

    cfg = load_from_disk()
    cats = [c for c in cfg["categories"] if c["enabled"]]
    if not cats:
        return {"ok": False, "error": "config_invalid", "detail": cfg["errors"][:3]}
    qcfg = _questions_of(cfg)

    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "empty_input"}

    info = lang_analyse(text)
    script = info.get("script")
    lang = q_lang_for(script)

    t0 = time.time()
    with _model_lock:
        answers = _agent.predict({STATE_KEY: text},
                                 build_questions(lang, cats, qcfg))["answers"]
    elapsed = (time.time() - t0) * 1000

    names = {c["key"]: c for c in cats}
    known = set(names)

    choice = answers["category"]
    choice_pick = choice["choice"]
    if choice_pick not in known:
        choice_pick = max(choice["probabilities"], key=choice["probabilities"].get)
    choice_probs = {k: v for k, v in choice["probabilities"].items() if k in known}

    raw_noul = {k: answers[f"is_{k}"]["noul"] for k in known}
    noul_probs = normalise(raw_noul)
    noul_pick = max(noul_probs, key=noul_probs.get)
    agree = choice_pick == noul_pick

    ranked = sorted(choice_probs.items(), key=lambda x: -x[1])
    top1_k, top1_v = ranked[0]
    top2_v = ranked[1][1] if len(ranked) > 1 else 0.0

    if not agree:
        tier = "disagree"
    elif top1_k == "other":
        tier = "other"
    else:
        tier = "agree"

    def nm(k: str) -> Dict[str, str]:
        c = names[k]
        return {lg: (c[lg]["name"] or c["en"]["name"] or c["zh"]["name"] or k)
                for lg in UI_LANGS}

    # 附加问题：按配置逐个取结果，界面据此通用渲染（不再是写死的 4 个）。
    # 注意：这里把名称/档位/选项的【三语全部】返回，由前端按【界面语言】挑选。
    # 参考信号跟界面语言，不跟输入语言——否则界面选英文、输入中文时会出现
    # 「Urgency: 不急 / 一般 / 较急」这种名字和档位不同语言的混搭。
    extra: List[Dict] = []
    for item in qcfg.get("questions") or []:
        if not item.get("enabled"):
            continue
        key, qtype = item["key"], item["type"]
        ans = answers.get(key)
        if not isinstance(ans, dict):
            continue
        row: Dict = {
            "key": key,
            "type": qtype,
            # 每种语言都取好（空了按 lang -> zh -> en -> ms 兜底），前端直接用
            "names": {lg: _qtext(item["name"], lg) for lg in UI_LANGS},
        }
        if qtype == "score":
            row["score"] = round(ans.get("score", 0.0), 2)
            row["confidence"] = round(ans.get("confidence", 0.0), 3)
            row["levels"] = [{lg: _qtext(c, lg) for lg in UI_LANGS}
                             for c in item["criteria"]]
        elif qtype == "noul":
            row["value"] = round(ans.get("noul", 0.0), 3)
            row["confidence"] = round(ans.get("confidence", 0.0), 3)
        else:                                     # choice
            pick = ans.get("choice")
            okeys = {o["key"] for o in item["criteria"]}
            if pick not in okeys:
                pick = max(ans.get("probabilities", {key: 0}), key=ans["probabilities"].get)
            row["choice"] = pick
            row["confidence"] = round(ans.get("confidence", 0.0), 3)
            row["options"] = [
                {"key": o["key"],
                 "labels": _opt_names(o),
                 "p": round(ans.get("probabilities", {}).get(o["key"], 0.0), 4)}
                for o in item["criteria"]
            ]
        extra.append(row)

    return {
        "ok": True,
        "input_script": script,
        "question_lang": lang,
        "elapsed_ms": round(elapsed, 1),
        "verdict": {
            "tier": tier,
            "category": choice_pick,
            "names": nm(choice_pick),
            "confidence": round(top1_v, 3),
            "margin": round(top1_v - top2_v, 3),
            "agree": agree,
        },
        "distribution": [{"key": k, "names": nm(k), "p": round(v, 4)} for k, v in ranked],
        "method_check": {
            "agree": agree,
            "choice": {"key": choice_pick, "names": nm(choice_pick)},
            "noul": {"key": noul_pick, "names": nm(noul_pick),
                     "p": round(noul_probs[noul_pick], 4)},
            "noul_raw": {k: round(v, 4) for k, v in raw_noul.items()},
        },
        "extra": extra,
    }


# --------------------------------------------------------------------------- Web
@asynccontextmanager
async def lifespan(_app: FastAPI):
    load_from_disk(force=True)
    threading.Thread(target=load_model, daemon=True).start()
    yield


app = FastAPI(title="Laya Triage Console", lifespan=lifespan)


class AnalyzeIn(BaseModel):
    text: str


class SaveIn(BaseModel):
    categories: List[Dict]
    questions: Optional[Dict] = None      # {category_question, questions}


class ImportIn(BaseModel):
    filename: str = ""
    content_b64: Optional[str] = None     # 新方式：任意表格文件（xlsx 是二进制，必须 base64）
    csv: Optional[str] = None             # 兼容旧调用：直接传 CSV 文本


def _public(cfg: Dict) -> Dict:
    return {
        "ok": cfg["ok"],
        "errors": cfg["errors"],
        "warnings": cfg["warnings"],
        "loaded_at": cfg["loaded_at"],
        "path": JSON_PATH,
        "categories": cfg["categories"],
        "count": len([c for c in cfg["categories"] if c["enabled"]]),
        "questions": _questions_of(cfg),
        "langs": list(UI_LANGS),
    }


@app.get("/api/status")
def api_status():
    return {**_model_state, "app": APP_ID, "checkpoint": f"{CHECKPOINT}/{SUBFOLDER}"}


@app.get("/api/categories")
def api_categories():
    return _public(load_from_disk())


@app.put("/api/categories")
def api_save_categories(body: SaveIn):
    cats, errors, warnings = validate(body.categories)
    if errors:
        return JSONResponse({"ok": False, "errors": errors, "warnings": warnings}, status_code=400)

    # 没带 questions 就沿用当前生效的（比如只想改分类的场景）
    qraw = body.questions
    if qraw is None:
        qraw = _questions_of(load_from_disk())
    qcfg, qerrors, qwarnings = validate_questions(qraw, cats)
    warnings = list(warnings) + list(qwarnings)
    if qerrors:
        return JSONResponse({"ok": False, "errors": qerrors, "warnings": warnings},
                            status_code=400)

    try:
        save_categories(cats, qcfg)
    except OSError as exc:
        return JSONResponse({"ok": False, "errors": [f"保存失败：{exc}"]}, status_code=500)
    return {"ok": True, "errors": [], "warnings": warnings,
            "count": len([c for c in cats if c["enabled"]]),
            "question_count": len([q for q in qcfg["questions"] if q["enabled"]])}


@app.get("/api/questions/preview")
def api_questions_preview(qlang: str = "zh"):
    """预览「真正送给模型的问题」，不跑模型。

    用途：措辞对结果影响极大（见 README），改完问题后必须能确认
    自己的改动到底变成什么送到了模型面前。
    """
    lang = qlang if qlang in ("zh", "en") else "zh"
    cfg = load_from_disk()
    cats = [c for c in cfg["categories"] if c["enabled"]]
    qcfg = _questions_of(cfg)
    return {"ok": True, "question_lang": lang,
            "count": len(cats),
            "questions": build_questions(lang, cats, qcfg)}


@app.get("/api/config/export.json", response_class=PlainTextResponse)
def api_export_config():
    """导出完整配置（分类 + 问题），用于备份或搬到另一台机器。"""
    cfg = load_from_disk()
    qcfg = _questions_of(cfg)
    payload = {"version": 2,
               "categories": cfg["categories"],
               "category_question": qcfg.get("category_question"),
               "questions": qcfg.get("questions", [])}
    return PlainTextResponse(
        json.dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="laya-config.json"'})


@app.post("/api/categories/import")
def api_import_table(body: ImportIn):
    """解析表格文件 -> 返回分类列表供页面预览，不直接保存。

    支持 .xlsx / .xlsm（二进制，走 base64）以及 .csv / .tsv / 任何分隔文本。
    格式按扩展名 + 文件头判断，不靠调用方声明。
    """
    import base64

    if body.content_b64:
        try:
            data = base64.b64decode(body.content_b64)
        except Exception as exc:                             # noqa: BLE001
            return JSONResponse({"ok": False, "errors": [f"文件内容解析失败：{exc}"]},
                                status_code=400)
        cats, errors, warnings = import_table(body.filename, data)
    elif body.csv is not None:
        cats, errors, warnings = _csv_to_categories(body.csv)
    else:
        return JSONResponse({"ok": False, "errors": ["没有收到文件内容。"]}, status_code=400)

    if errors:
        return JSONResponse({"ok": False, "errors": errors}, status_code=400)
    return {"ok": True, "errors": [], "warnings": warnings, "categories": cats}


@app.get("/api/categories/export.csv", response_class=PlainTextResponse)
def api_export_csv():
    text = _categories_to_csv(load_from_disk()["categories"])
    return PlainTextResponse(
        "\ufeff" + text, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="categories.csv"'})


@app.get("/api/categories/export.xlsx")
def api_export_xlsx():
    if not HAVE_XLSX:
        return JSONResponse({"ok": False, "errors": [MSG_NO_XLSX]}, status_code=503)
    data = _categories_to_xlsx(load_from_disk()["categories"])
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="categories.xlsx"'})


@app.post("/api/analyze")
def api_analyze(body: AnalyzeIn):
    try:
        return JSONResponse(analyse(body.text))
    except Exception as exc:                                  # noqa: BLE001
        return JSONResponse({"ok": False, "error": "internal",
                             "detail": [f"{type(exc).__name__}: {exc}"]}, status_code=500)


@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as fh:
        return HTMLResponse(fh.read())


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# --------------------------------------------------------------------------- 启动
APP_ID = "laya-triage"          # 用来判断某个端口上跑的是不是本应用
OPEN_HOST = "127.0.0.1" if HOST in ("0.0.0.0", "::", "") else HOST


def _probe(timeout: float = 1.5) -> bool:
    """这个端口上是否已经有本应用在跑？（只有本应用会返回 APP_ID）"""
    import urllib.request
    try:
        with urllib.request.urlopen(
                f"http://{OPEN_HOST}:{PORT}/api/status", timeout=timeout) as resp:
            return APP_ID.encode() in resp.read(800)
    except Exception:                                          # noqa: BLE001
        return False


def _open_when_ready(timeout: float = 60.0) -> None:
    """等端口真的能响应了再开浏览器。

    不能一启动就开：uvicorn 绑定端口需要时间，抢在前面打开的话
    用户会看到一个「无法连接」的页面，以为装坏了。
    """
    import webbrowser
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _probe(0.8):
            try:
                webbrowser.open(f"http://{OPEN_HOST}:{PORT}/")
            except Exception:                                  # noqa: BLE001
                pass
            return
        time.sleep(0.4)


if __name__ == "__main__":
    url = f"http://{OPEN_HOST}:{PORT}/"
    no_browser = os.environ.get("LAYA_NO_BROWSER", "").strip().lower() in (
        "1", "true", "yes", "on")

    # 已经在运行了？别再起一个，直接把已有页面打开。
    # （避免用户重复双击 start.bat 时看到 "address already in use" 一头雾水）
    if _probe():
        print("本应用已经在运行，为你打开页面。")
        print(f"地址：{url}")
        print("（想重开一个，请先关掉之前那个窗口）")
        if not no_browser:
            try:
                import webbrowser
                webbrowser.open(url)
            except Exception:                                  # noqa: BLE001
                pass
        raise SystemExit(0)

    print("=" * 60)
    print("  内容分诊台 正在启动，浏览器会自动打开…")
    print(f"  页面地址：{url}")
    print("=" * 60)
    print("  如果浏览器没有自动弹出，请手动把上面的地址复制到浏览器。")
    print("  页面第一次打开会显示「正在加载模型…」，")
    print("  首次启动要从网上下载模型（约 1.5 GB），请耐心等几分钟；")
    print("  变成「模型就绪」就可以开始用了。")
    print()
    print("  停止运行：点这个窗口按 Ctrl+C，或直接关掉这个窗口。")
    print()

    if not no_browser:
        threading.Thread(target=_open_when_ready, daemon=True).start()

    try:
        uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
    except SystemExit as exc:
        if exc.code:
            print()
            print("=" * 60)
            print(f"  启动失败。最常见的原因是端口 {PORT} 已经被别的程序占用。")
            print()
            print("  怎么办：")
            print("   1) 是不是已经开着另一个本应用的窗口？")
            print("      找到那个（可能被最小化的）黑色窗口，关掉它再试。")
            print("   2) 或者换一个端口启动，在命令行里执行：")
            print(f"        set APP_PORT=8200")
            print(r"        .venv\Scripts\python.exe app\server.py")
            print("      然后浏览器打开 http://127.0.0.1:8200")
            print("=" * 60)
        raise
