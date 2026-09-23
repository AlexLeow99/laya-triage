#!/usr/bin/env python3
"""Laya 内容分诊台 —— 安装脚本

只依赖 Python 标准库，因此可以在装任何第三方依赖之前运行。
由 install.bat 调用；Linux / macOS 也可以直接 `python3 install.py`。

它做四件事:
  1. 检查 Python 版本（需要 3.10 或更新）
  2. 在本目录下创建 .venv 虚拟环境
  3. 检测有没有 NVIDIA 显卡，选对应的 PyTorch 版本装上
  4. 装上 laya[serve] 和 requests

用法:
    python install.py                # 自动检测显卡
    python install.py --cpu          # 强制装 CPU 版 PyTorch（约 250MB）
    python install.py --cuda cu126   # 指定 CUDA 版本（cu126 / cu130）

环境变量 LAYA_TORCH 效果同 --cpu / --cuda。
"""
import os
import subprocess
import sys
import venv

HERE = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(HERE, ".venv")
IS_WIN = os.name == "nt"
MIN_PY = (3, 10)

# PyTorch 官方轮子索引。cu126 兼容的显卡驱动范围最广（约 525+），
# cu130 需要更新的驱动（约 580+），cpu 版体积最小。
TORCH_INDEX = {
    "cpu": "https://download.pytorch.org/whl/cpu",
    "cu126": "https://download.pytorch.org/whl/cu126",
    "cu130": "https://download.pytorch.org/whl/cu130",
}
DEFAULT_CUDA = "cu126"

TOTAL_STEPS = 4


def say(msg=""):
    print(msg, flush=True)


def step(n, msg):
    say(f"\n[{n}/{TOTAL_STEPS}] {msg}")


def venv_python():
    return os.path.join(VENV_DIR, "Scripts" if IS_WIN else "bin",
                        "python.exe" if IS_WIN else "python")


def run(cmd, **kw):
    say("    $ " + " ".join(cmd))
    return subprocess.call(cmd, **kw)


def has_nvidia_gpu():
    """用 nvidia-smi 判断有没有 NVIDIA 显卡。"""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total",
                              "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=15)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip().splitlines()[0].strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def parse_args(argv):
    """返回 (torch_key or None, 显式指定的 cuda 名)"""
    force = os.environ.get("LAYA_TORCH", "").strip().lower()
    cuda = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--cpu":
            force = "cpu"
        elif a == "--cuda" and i + 1 < len(argv):
            cuda = argv[i + 1].strip()
            force = cuda
            i += 1
        elif a in ("-h", "--help"):
            say(__doc__)
            sys.exit(0)
        i += 1
    return force or None, cuda


def main():
    say("=" * 62)
    say("  Laya 内容分诊台 —— 安装")
    say("=" * 62)

    force, cuda = parse_args(sys.argv[1:])

    # ---------------------------------------------------------------- 1
    step(1, "检查 Python 版本")
    if sys.version_info < MIN_PY:
        say(f"    [X] 当前 Python {sys.version.split()[0]}，需要 "
            f"{MIN_PY[0]}.{MIN_PY[1]} 或更新。")
        say("        请到 https://www.python.org/downloads/ 安装新版本。")
        return 1
    say(f"    [OK] Python {sys.version.split()[0]}  ({sys.executable})")

    # ---------------------------------------------------------------- 2
    step(2, "创建虚拟环境 .venv")
    if os.path.exists(venv_python()):
        say("    [OK] 已存在，跳过。想重装就先删掉 .venv 目录。")
    else:
        try:
            venv.EnvBuilder(with_pip=True, clear=False).create(VENV_DIR)
        except Exception as exc:                              # noqa: BLE001
            say(f"    [X] 创建失败：{exc}")
            return 1
        say(f"    [OK] 已创建 {VENV_DIR}")

    py = venv_python()
    if not os.path.exists(py):
        say(f"    [X] 找不到 {py}")
        return 1

    say("    升级 pip …")
    run([py, "-m", "pip", "install", "--upgrade", "--quiet", "pip"])

    # ---------------------------------------------------------------- 3
    step(3, "安装 PyTorch（这一步下载量最大）")
    gpu = has_nvidia_gpu()
    if force in TORCH_INDEX:
        key, why = force, "手动指定"
    elif force:
        key, why = force, "手动指定"
    elif gpu:
        key, why = DEFAULT_CUDA, f"检测到显卡：{gpu}"
    else:
        key, why = "cpu", "没有检测到 NVIDIA 显卡"

    if key not in TORCH_INDEX:
        say(f"    [X] 不认识的版本 {key}，可选：{', '.join(TORCH_INDEX)}")
        return 1

    say(f"    选择 {key} 版本（{why}）")
    if key == "cpu":
        say("    提示：CPU 版单次分析约 200-460 毫秒；显卡版约 30-70 毫秒。")
    else:
        say("    提示：这一步会下载约 2.5-3 GB，请耐心等待。")
    say("")

    if run([py, "-m", "pip", "install", "torch",
            "--index-url", TORCH_INDEX[key]]) != 0:
        say("\n    [X] PyTorch 安装失败。")
        say(f"        可以先试 CPU 版：python install.py --cpu")
        return 1

    # ---------------------------------------------------------------- 4
    step(4, "安装 Laya 及其余依赖")
    if run([py, "-m", "pip", "install", "laya[serve]", "requests", "openpyxl"]) != 0:
        say("\n    [X] 安装失败，请把上面的报错发给我。")
        return 1

    # 验证
    say("")
    rc = run([py, "-c",
              "import torch, laya;"
              "print('    [OK] torch', torch.__version__,"
              "' CUDA 可用:', torch.cuda.is_available());"
              "print('    [OK] laya', laya.__version__)"])
    if rc != 0:
        say("    [X] 装完了但导入失败，请把上面的报错发给我。")
        return 1

    say("")
    say("=" * 62)
    say("  安装完成")
    say("=" * 62)
    say("")
    say("  启动方式：双击 start.bat")
    say("  浏览器打开：http://127.0.0.1:8100")
    say("")
    say("  第一次启动会从 HuggingFace 下载模型权重（约 1.5 GB），")
    say("  需要几分钟。之后启动只需几秒。")
    say("")
    say("  分类怎么改：见 app\\分类维护说明.md")
    say("  遇到问题：见 使用说明.md 的「常见问题」")
    say("")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        say("\n已取消。")
        sys.exit(130)
