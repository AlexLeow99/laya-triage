# Laya Triage v1.0.0

A local console that turns free text into **typed decisions** using the
[Laya](https://huggingface.co/convaiinnovations/laya) decision model. Paste a
ticket, email or comment; get a category, a probability, and a set of signals you
define — all on your own machine, in ~60 ms.

Trilingual UI (中文 / English / Bahasa Melayu) · light & dark theme · every
question configurable · import/export Excel & CSV.

![dark theme](https://github.com/AlexLeow99/laya-triage/raw/main/docs/screenshot-dark.png)

---

## ⚠️ Reference tool, not an autopilot

Please read this before wiring it into anything automatic. Measured on
purpose-built test sets, with the shipped configuration:

| Input language | Picks the right option | The two methods agree |
|---|---|---|
| English | 10 / 13 (77 %) | 9 / 13 |
| Chinese | 12 / 17 (71 %) | 15 / 17 |
| Malay | 8 / 13 (62 %) | 7 / 13 |

Three failure modes, all reproduced:

1. **Hypersensitive to wording.** Rewording one category description flips
   verdicts on identical input. In Chinese this happened at **0.975 vs 0.977
   reported confidence** — a confident wrong answer. In English the effect
   reproduces but only on cases the model was already unsure about (0.401).
2. **The cross-check can agree on a wrong answer.** The app asks each category
   two ways and flags whether they agree; two of the three Chinese failures above
   were flagged "agree", at 0.99 confidence.
3. **Out-of-scope input gets forced into a category.** Give it a weather report
   and it may confidently return a business category.

**Always confirm before acting.** Validate on your own labelled data first.

---

## Install — Windows, no prerequisites

1. Download **`laya-triage-v1.0.0.zip`** from the assets below and extract it anywhere
2. Double-click **`install.bat`**
3. Double-click **`start.bat`** — your browser opens at <http://127.0.0.1:8100>

**Python is not required.** If you don't have a 3.10+ interpreter, `install.bat`
downloads a portable one (~21 MB) into `.bootstrap\` — no system install, no PATH
changes, no admin rights. **Delete the folder to uninstall.**

| Download size | |
|---|---|
| Portable Python (only if you have none) | ~21 MB |
| PyTorch — NVIDIA GPU detected | ~2.5–3 GB |
| PyTorch — CPU fallback | ~250 MB |
| Model weights (first start only) | ~1.5 GB |

**Linux / macOS:** `python3 install.py` then `./start.sh`.
(The zero-Python bootstrap is Windows-only; you need Python 3.10+ on these platforms.)

An NVIDIA GPU is optional but roughly 15× faster: **~60 ms** per request on a 4 GB
laptop GPU, versus ~200–460 ms on CPU.

---

## What's in the box

| | |
|---|---|
| `app/` | the application — FastAPI backend plus four static frontend files |
| `install.bat` · `install.py` · `bootstrap.ps1` | installer, including the zero-Python bootstrap |
| `start.bat` · `start.sh` | launcher — opens the browser itself, once the port is listening |
| `使用说明.md` | end-user guide (Chinese) |
| `app/分类维护说明.md` | configuration & maintenance guide (Chinese) |
| `LICENSE` | MIT |

Source code, English documentation and light-theme screenshot:
**[the repository](https://github.com/AlexLeow99/laya-triage)**

## Verify your download

```
SHA256  5A3F27D9E87E62657C3D40B4F510EEE4E347F9467CFEB2AE22D771B7FEED8E48
```

The archive is packaged from the tagged tree, so line endings follow
`.gitattributes`: `*.bat` and `*.ps1` are CRLF, everything else is LF.

Also attached as `SHA256SUMS.txt`. On Windows:

```powershell
Get-FileHash .\laya-triage-v1.0.0.zip -Algorithm SHA256
```

## Requirements

- Windows 10/11, or Linux/macOS with Python 3.10+
- ~4 GB free disk for the app plus model weights
- No internet after the initial install — everything runs offline

## Credits

Decision model: [Laya](https://huggingface.co/convaiinnovations/laya) by Convai
Innovations (Apache-2.0); weights are downloaded at runtime, not bundled here.
Built with FastAPI, Uvicorn, openpyxl and uv. Licensed MIT.
