<#
  Laya 内容分诊台 —— 没有 Python 时的自举脚本（Windows）

  由 install.bat 在「检测不到 Python」或「Python 版本太旧」时调用。
  做三件事：
    1. 下载 uv（单文件 exe，校验 SHA256）
    2. 用 uv 下载一个便携版 Python
    3. 用它跑正常的 install.py

  特点：全程不往系统里装任何东西，也不改 PATH。
        下载的 uv 和 Python 都放在本文件夹的 .bootstrap\ 里，
        删掉整个文件夹就干净了。

  已知行为：uv 下载的 Python 自带 ensurepip，所以 install.py 里用
  标准库 venv 建环境的逻辑可以直接复用，不需要特殊分支。
#>
$ErrorActionPreference = 'Stop'

$HERE = Split-Path -Parent $MyInvocation.MyCommand.Path
$BOOT = Join-Path $HERE '.bootstrap'

# 固定版本 + 校验哈希：避免"最新版"哪天变了导致行为不可复现
$UV_VERSION = '0.12.18'
$UV_SHA256  = 'A3974903D5C1B2E9D9D8B5D5E90188C853760F82E3755F72825E350CE04BAC28'
$PY_VERSION = '3.12'

function Say($msg) { Write-Host $msg }

<#
  删掉一棵目录树，尽量删干净。
  为什么需要它：uv 会用软链接把 cpython-3.12 → cpython-3.12.14，
  如果这棵树被复制过（软链接被展开成真实目录），或者上次下载被中断过，
  uv 再次安装会报 "directory is not empty"。
  Remove-Item 处理软链接有时不干净，所以再兜一层 cmd rmdir。
#>
function Remove-Tree($path) {
    if (-not (Test-Path $path)) { return }
    Remove-Item $path -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path $path) {
        & cmd /c "rmdir /s /q `"$path`"" 2>$null | Out-Null
    }
}

Say ""
Say "  没有检测到可用的 Python，将自动下载一个便携版。"
Say "  不会安装到系统里，也不会修改 PATH —— 全部放在本文件夹的 .bootstrap\ 内。"
Say ""

New-Item -ItemType Directory -Force -Path $BOOT | Out-Null
$uvExe = Join-Path $BOOT 'uv.exe'

# ---------------------------------------------------------------- 1. 取 uv
if (-not (Test-Path $uvExe)) {
    $url = "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-x86_64-pc-windows-msvc.zip"
    $zip = Join-Path $BOOT 'uv.zip'
    Say "  [1/3] 下载引导程序（约 17 MB）…"
    try {
        Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
    } catch {
        throw "下载失败，请检查网络连接后重试。`n  原始错误：$($_.Exception.Message)"
    }
    Expand-Archive -Path $zip -DestinationPath $BOOT -Force
    Remove-Item $zip -Force -ErrorAction SilentlyContinue
    $found = Get-ChildItem $BOOT -Recurse -Filter 'uv.exe' |
             Where-Object { $_.FullName -ne $uvExe } | Select-Object -First 1
    if (-not $found) { throw "解压后找不到 uv.exe" }
    Move-Item $found.FullName $uvExe -Force
} else {
    Say "  [1/3] 引导程序已存在，跳过下载"
}

# 哈希校验放在 if 外面：无论是新下载的还是上次留下的，都要校验
$actual = (Get-FileHash $uvExe -Algorithm SHA256).Hash
if ($actual -ne $UV_SHA256) {
    Remove-Item $uvExe -Force -ErrorAction SilentlyContinue
    throw @"
引导程序校验失败（SHA256 不匹配），已删除。
  期望：$UV_SHA256
  实际：$actual
可能是下载被中断或被篡改。请重试；若反复失败请把这段发给管理员。
"@
}
Say "        ✓ 校验通过"

# ---------------------------------------------------------------- 2. 取 Python
# 全部放进本文件夹，避免污染系统、也避免和用户已有的 Python 打架
$env:UV_PYTHON_INSTALL_DIR = Join-Path $BOOT 'pythons'
$env:UV_CACHE_DIR          = Join-Path $BOOT 'cache'

Say "  [2/3] 下载 Python $PY_VERSION（约 21 MB）…"
& $uvExe python install $PY_VERSION

if ($LASTEXITCODE -ne 0) {
    # 上次下载中断、或这棵目录树被复制过（软链接被展开成真实目录）时，
    # uv 会报 "directory is not empty"。清掉重来一次。
    Say "        首次未成功，清理残留后重试一次…"
    Remove-Tree (Join-Path $BOOT 'pythons')
    & $uvExe python install $PY_VERSION
}

if ($LASTEXITCODE -ne 0) {
    throw @"
Python 下载失败。
请先手动删除这个文件夹，然后重新运行 install.bat：
  $BOOT
"@
}
Say "        ✓ 下载完成"

$py = (& $uvExe python find --managed-python $PY_VERSION 2>$null |
       Where-Object { $_ -match 'python\.exe$' } | Select-Object -Last 1)
if (-not $py -or -not (Test-Path $py)) { throw "找不到刚装好的 Python" }

# 防御性检查：必须是 .bootstrap 里的那个。
# 踩过的坑：uv python find 会从当前目录往上找 .venv，如果用户把本包解压到
# 任何一个上层已有 .venv 的地方，它就会返回那个无关的 Python。
# --managed-python 已经能挡住，这里再断言一次，免得将来 uv 行为变化后
# 悄悄用错解释器（那会导致"装的依赖在 A 处、跑的应用在 B 处"这种怪问题）。
$bootFull = (Resolve-Path $BOOT).Path
$pyFull = (Resolve-Path $py).Path
if (-not $pyFull.StartsWith($bootFull, [StringComparison]::OrdinalIgnoreCase)) {
    throw @"
取到的 Python 不在本包的 .bootstrap 目录内，已中止以免用错解释器。
  取到：$pyFull
  期望：$bootFull 下面
请删除 .bootstrap 后重试；若反复出现请把这段发给管理员。
"@
}
Say "        ✓ Python 就绪：$py"

# ---------------------------------------------------------------- 3. 跑正常安装流程
Say "  [3/3] 开始安装依赖…"
Say ""
& $py (Join-Path $HERE 'install.py') @args
exit $LASTEXITCODE
