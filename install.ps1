# Installs PodVault for this user, from this checkout (Windows; install.sh on
# macOS / Linux). Run it again after `git pull` to update. No admin rights.
#
#   powershell -ExecutionPolicy Bypass -File install.ps1              install or update
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Uninstall   remove (settings stay)
#
# What goes where:
#   %LOCALAPPDATA%\Programs\PodVault    the program and its Python packages
#   Start menu -> PodVault              the shortcut
#   Settings -> Apps                    "PodVault", with Uninstall
#   %APPDATA%\PodVault                  settings and data (src/settings.py)
#
# Python 3.12+ is needed: winget install Python.Python.3.13
param([switch]$Uninstall)
$ErrorActionPreference = 'Stop'

$Src  = $PSScriptRoot
$Dest = Join-Path $env:LOCALAPPDATA 'Programs\PodVault'
$App  = Join-Path $Dest 'app'
$Venv = Join-Path $Dest 'venv'
$Lnk  = Join-Path ([Environment]::GetFolderPath('Programs')) 'PodVault.lnk'
$Key  = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\PodVault'

if ($Uninstall) {
    Remove-Item $Lnk, $Key -Recurse -Force -ErrorAction SilentlyContinue
    # the script may be running from inside $Dest: PowerShell has read it already
    Remove-Item $Dest -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host 'PodVault removed. Settings and data are kept in %APPDATA%\PodVault.'
    return
}

function Run($exe, [string[]]$arguments) {
    & $exe @arguments
    if ($LASTEXITCODE) { throw "$exe $($arguments -join ' ') failed ($LASTEXITCODE)" }
}

# --- Python 3.12+

$Py = $null
foreach ($c in @(@('py', '-3.13'), @('py', '-3.12'), @('py', '-3'), @('python'))) {
    if (-not (Get-Command $c[0] -ErrorAction SilentlyContinue)) { continue }
    $args_ = @($c | Select-Object -Skip 1) + @('-c', 'import sys; sys.exit(sys.version_info < (3, 12))')
    try { & $c[0] @args_ 2>$null } catch { continue }    # "py -3.13" without a 3.13 writes to stderr
    if ($LASTEXITCODE -eq 0) { $Py = $c; break }
}
if (-not $Py) { throw 'Python 3.12 or newer is needed: winget install Python.Python.3.13' }
$PyExe = $Py[0]
$PyArgs = @($Py | Select-Object -Skip 1)
Write-Host "Python: $(& $PyExe @PyArgs -c 'import sys; print(sys.executable, sys.version.split()[0])')"

if (-not (Test-Path (Join-Path $Src 'vendor\podsync\podsync\__init__.py'))) {
    Write-Host 'Fetching podsync (git submodule)...'
    Run 'git' @('-C', $Src, 'submodule', 'update', '--init')
}

# --- the program: a clean copy, so files deleted upstream go too

Write-Host "Copying the program to $App"
Remove-Item $App -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force (Join-Path $App 'vendor\podsync') | Out-Null
foreach ($item in 'Main.py', 'src', 'locale', 'packaging', 'requirements.txt', 'LICENSE', 'README.md', 'install.ps1') {
    Copy-Item (Join-Path $Src $item) $App -Recurse
}
Copy-Item (Join-Path $Src 'vendor\podsync\podsync') (Join-Path $App 'vendor\podsync') -Recurse
Get-ChildItem $App -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force

# --- its Python packages

$Python = Join-Path $Venv 'Scripts\python.exe'
$Want = (& $PyExe @PyArgs -c 'import sys; print(sys.executable)')
$Mark = Join-Path $Venv '.podvault'
if (-not (Test-Path $Mark) -or (Get-Content $Mark) -ne $Want) {
    Remove-Item $Venv -Recurse -Force -ErrorAction SilentlyContinue
    Run $PyExe ($PyArgs + @('-m', 'venv', $Venv))
    Set-Content $Mark $Want
}

Write-Host 'Installing Python packages...'
$lines = Get-Content (Join-Path $Src 'requirements.txt')
$cut = [array]::FindIndex($lines, [Predicate[string]]{ param($l) $l -like '# AI*' })
if ($cut -lt 0) { $cut = $lines.Count }
$Req = Join-Path $env:TEMP 'podvault-req.txt'
$Opt = Join-Path $env:TEMP 'podvault-opt.txt'
$lines[0..($cut - 1)] | Set-Content $Req
if ($cut -lt $lines.Count) { $lines[$cut..($lines.Count - 1)] | Set-Content $Opt } else { '' | Set-Content $Opt }
Run $Python @('-m', 'pip', 'install', '-q', '--upgrade', 'pip')
Run $Python @('-m', 'pip', 'install', '-q', '-r', $Req)
& $Python -m pip install -q -r $Opt
if ($LASTEXITCODE) { Write-Host '!! The AI playlist packages could not be installed; everything else works.' }
Remove-Item $Req, $Opt -ErrorAction SilentlyContinue

# --- the shortcut and the entry in Settings -> Apps

$Ico = Join-Path $Dest 'podvault.ico'
Run $Python @((Join-Path $App 'packaging\helper.py'), 'icon', $Ico)
$sh = (New-Object -ComObject WScript.Shell).CreateShortcut($Lnk)
$sh.TargetPath = Join-Path $Venv 'Scripts\pythonw.exe'
$sh.Arguments = '"' + (Join-Path $App 'Main.py') + '"'
$sh.WorkingDirectory = $App
$sh.IconLocation = $Ico
$sh.Description = 'PodVault: an iPod music library'
$sh.Save()

New-Item $Key -Force | Out-Null
$version = (& git -C $Src describe --tags --always 2>$null)
if (-not $version) { $version = 'dev' }
foreach ($p in @{
    DisplayName = 'PodVault'; DisplayVersion = "$version"; Publisher = 'PodVault'
    DisplayIcon = $Ico; InstallLocation = $Dest; NoModify = 1; NoRepair = 1
    UninstallString = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $App 'install.ps1')`" -Uninstall"
}.GetEnumerator()) {
    $type = if ($p.Value -is [int]) { 'DWord' } else { 'String' }
    New-ItemProperty $Key -Name $p.Key -Value $p.Value -PropertyType $type -Force | Out-Null
}

Run $Python @((Join-Path $App 'packaging\helper.py'), 'settings', (Join-Path $Src 'settings.json'))

Write-Host ''
Write-Host 'PodVault is installed: Start menu -> PodVault.'
Write-Host "  Settings: $(& $Python (Join-Path $App 'packaging\helper.py') where)"
Write-Host '  Update: git pull, then install.ps1 again. Remove: Settings -> Apps, or install.ps1 -Uninstall'
