<div align="center">

# Laya Triage

**Turn free text into typed decisions — locally, in ~60 ms.**

Paste a ticket, email or comment. Get a category, a calibrated probability, and a
set of signals you define — from a 322 M-parameter decision model running on your
own machine.

[中文说明](README.zh-CN.md) · [End-user guide (Chinese)](使用说明.md) ·
[Config & maintenance guide (Chinese)](app/分类维护说明.md)

Light · Dark — both follow your system theme by default

<img src="docs/screenshot-light.png" width="49%" alt="Light theme">
<img src="docs/screenshot-dark.png" width="49%" alt="Dark theme">

</div>

---

## ⚠️ Read this first

**This is a reference tool, not an autopilot.** Do not wire its output into
automated actions. That is not a hedge — it is what the measurements say.

Measured with the shipped configuration, on purpose-built test sets:

| Input language | Picks the right option | The two methods agree |
|---|---|---|
| English | 10 / 13 (77 %) | 9 / 13 |
| Chinese | 12 / 17 (71 %) | 15 / 17 |
| Malay | 8 / 13 (62 %) | 7 / 13 |

Three concrete failure modes, all reproduced:

1. **It is hypersensitive to wording, and how badly depends on the language.**
   Rewording a category description flips verdicts on identical input:

   - **Chinese** (where the model is weakest) — this is the severe case. Two
     descriptions meaning the same thing —
     `程序错误、服务中断或集成问题` vs `故障报修，程序报错或服务中断`
     ("software errors, outages or integration problems" vs "fault reports,
     program errors or service interruptions") — flipped the verdict, at
     **0.975 vs 0.977 reported confidence**. A confident wrong answer.
   - **English** — the effect reproduces, but weakly: rewording two category
     descriptions flipped 1 of 15 test cases, and that case was one the model was
     already unsure about (0.401 confidence). Accuracy did not move (12/15 both ways).

   So in Chinese the percentage can be high and still be wrong. In English it is a
   passable signal for *this* model — but you cannot tell which you are looking at
   from the number alone. **Calibrate on your own data before trusting it.**
2. **Both methods can agree on a wrong answer.** The app asks each category two
   ways (one multiple-choice question, plus one yes/no per category) and flags
   whether they agree. Two of the three Chinese failures above were flagged
   "agree" — at 0.99 confidence.
3. **Out-of-scope input gets forced into a category.** Give it a weather report
   and it may confidently return a business category.

**What this means in practice:** always confirm before acting. If you need
automation, validate on your own labelled data first, and expect to write your
own rules — the model gives you a signal, not a decision.

---

## Quick start

**Windows — nothing needs to be installed first.** No Python, no admin rights.

```
1. Download this repo (Code → Download ZIP, or git clone)
2. Double-click  install.bat
3. Double-click  start.bat
```

The browser opens by itself at <http://127.0.0.1:8100>.

`install.bat` will, in order:

| Step | What happens |
|---|---|
| Python | Uses yours if it is 3.10+. **Otherwise downloads a portable one (~21 MB) into `.bootstrap\`** — no system install, no PATH changes |
| PyTorch | Detects an NVIDIA GPU and picks the matching build (~2.5–3 GB), or falls back to CPU (~250 MB) |
| Dependencies | `laya[serve]`, `requests`, `openpyxl` |
| Model weights | Downloaded from Hugging Face on first start (~1.5 GB), then cached |

Nothing is installed system-wide. Delete the folder to uninstall.

**Linux / macOS:** `python3 install.py` then `./start.sh`.

---

## Features

- **Three interface languages** — 中文 / English / Bahasa Melayu. Switching the UI
  language changes every label and result; it is independent of the input language.
- **Light / dark theme** — follows your system by default, overridable.
- **Categories are just the choices of a question.** Add, rename, disable, reorder.
  The `other` option is locked so out-of-scope content always has somewhere to land.
- **Every question is editable.** Not just the categories — the question wording,
  the display name, the type (`choice` / `score` / `noul`), the options, the levels.
  Add your own questions. No retraining, ever.
- **Question preview** — see the exact list of questions that will be sent to the
  model before you trust it.
- **Import / export** — `.xlsx`, `.xlsm`, `.csv`, `.tsv`, format auto-detected.
  The Excel export has a styled header, column widths and frozen panes.
- **Everything in one forward pass** — all questions are answered together, so
  adding questions costs almost nothing in latency.
- **Fully local** — your text never leaves the machine. Only the one-time model
  download touches the network.

---

## Configuring it

All configuration lives in `app/categories.json`, and everything in it is
editable from the **Questions & choices** tab in the UI.

```jsonc
{
  "version": 2,
  "category_question": {
    "name":          { "en": "Category", "zh": "分类", "ms": "Kategori" },
    "instructions":  { "en": "What is this text mainly about?",
                       "zh": "这段内容主要属于哪一类？" },
    "noul_template": { "en": "Does this text concern {desc}?" }
  },
  "categories": [                       // = the choices of the category question
    { "key": "billing", "enabled": true,
      "en": { "name": "Billing",   "desc": "refunds, duplicate charges, invoices" },
      "zh": { "name": "财务/计费", "desc": "退款、重复扣款、账单、发票" },
      "ms": { "name": "Bil",       "desc": "bayaran balik" } }
  ],
  "questions": [                        // your own extra questions
    { "key": "urgency", "enabled": true, "type": "score",
      "name":         { "en": "Urgency", "zh": "紧急程度" },
      "instructions": { "en": "How urgent is this text?",
                        "zh": "这段内容的紧急程度如何？" },
      "criteria": [ { "en": "not urgent", "zh": "不急" },
                    { "en": "normal",     "zh": "一般" },
                    { "en": "critical",   "zh": "很急" } ] }
  ]
}
```

*(Abridged — Chinese values are kept alongside so you can see the shape. The
[Chinese README](README.zh-CN.md) shows the same structure Chinese-first.)*

**Two texts per option.** `name` is what humans see in the UI; `desc` is what the
model reads. Leave `desc` empty and it falls back to `name` — so filling one is enough.

**You do not need to maintain three languages.** The app asks questions in Chinese
for Chinese input and English for everything else, so both must exist for best
results — but **Malay question text is never sent to the model** and can be omitted
if you only want it for display. See
[the measurements](app/分类维护说明.md) *(Chinese)* before dropping a language entirely.

**Change the wording and you change the answers.** The UI warns about this. Re-test
with your own content after any edit — there is no way for the app to know whether
a rephrasing helped or hurt.

---

## Architecture

```
Browser (vanilla JS, no build step)
  │  POST /api/analyze
  ▼
FastAPI (single process, single file: app/server.py)
  │
  ├─ language detection (pure Python, < 1 ms)   →  Chinese or English question set
  ├─ one forward pass through Laya               →  all answers at once
  └─ cross-check: choice pick vs. yes/no decomposition
  ▼
Laya multilingual checkpoint (mmBERT-base, 322 M, Apache-2.0)
```

- **No build step, no bundler, no framework.** The frontend is four static files.
- **No database.** Config is one JSON file, hot-reloaded on change.
- **The model stays resident** — ~1.6 GB VRAM, ~60 ms per request on a 4 GB laptop GPU.
- **Graceful degradation** — falls back to CPU on low VRAM, keeps the last working
  config if you save something invalid, and never opens a browser onto a dead port.

Design notes and the experiments behind every claim are in
[`app/分类维护说明.md`](app/分类维护说明.md) *(Chinese)* and inline in `app/server.py`.

---

## Credits

- **[Laya](https://huggingface.co/convaiinnovations/laya)** by Convai Innovations —
  the decision model, Apache-2.0. Weights are downloaded at runtime, not redistributed here.
- **[uv](https://github.com/astral-sh/uv)** — bootstraps a portable Python when none exists.
- **[FastAPI](https://fastapi.tiangolo.com/)** / **[Uvicorn](https://www.uvicorn.org/)** — the HTTP layer.
- **[openpyxl](https://openpyxl.readthedocs.io/)** — Excel import/export.

The **Choices** interaction (options laid out A / B / C with a live count) is
inspired by the [Jev AI playground](https://jev-ai.net/#playground).

## How this was built

Developed by the repo owner in collaboration with **deepseek-v4-flash** (DeepSeek)
as the AI coding agent.

The human side drove every product decision — what the tool is for, which
languages matter, what counts as acceptable accuracy, how failures should surface
to a non-technical user — and did all the testing against real content. The
measured limits documented at the top of this README are not boilerplate: they
came out of that process and are the reason the app is deliberately framed as a
reference tool rather than an autopilot.

Not affiliated with, sponsored by, or endorsed by DeepSeek.

## License

[MIT](LICENSE). Laya itself is Apache-2.0; see Credits.
