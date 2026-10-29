# setup_env.ps1 —— 下载 Phira 与 Phichain（走 ghfast 镜像，已验证可用）
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$root = "$env:USERPROFILE\Apps"
New-Item -ItemType Directory -Force -Path $root | Out-Null

function Get-GhAsset([string]$url, [string]$dest) {
  foreach ($m in @("https://ghfast.top/$url","https://gh-proxy.com/$url","https://ghproxy.net/$url")) {
    try {
      Write-Host "下载: $m"
      if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
        & curl.exe -L --fail -sS --retry 2 --connect-timeout 15 --max-time 900 -o $dest $m
        if ($LASTEXITCODE -ne 0) { throw "curl exit $LASTEXITCODE" }
      } else {
        Invoke-WebRequest -Uri $m -OutFile $dest -UseBasicParsing -TimeoutSec 900
      }
      if ((Get-Item $dest).Length -gt 20MB) { return }
    } catch { Write-Host "失败: $($_.Exception.Message)"; }
  }
  throw "全部镜像失败，请手动下载: $url"
}

# Phira（音游 + 内置编辑器）
$phiraZip = "$root\Phira.zip"
$phira = "$root\Phira\app\phira-main.exe"
if (-not (Test-Path $phira)) {
  Get-GhAsset "https://github.com/TeamFlos/phira/releases/download/v0.8.2/Phira-windows-x86_64-v0.8.2.zip" $phiraZip
  Expand-Archive -LiteralPath $phiraZip -DestinationPath "$root\Phira\app" -Force
}

# Phichain（RPE 制谱器 + 格式转换器）
$pcZip = "$root\Phichain.zip"
$pcDir = "$root\Phichain\app\phichain-v0.5.0-x86_64-pc-windows-msvc"
if (-not (Test-Path "$pcDir\phichain.exe")) {
  Get-GhAsset "https://github.com/Ivan-1F/phichain/releases/download/v0.5.0/phichain-v0.5.0-x86_64-pc-windows-msvc.zip" $pcZip
  Expand-Archive -LiteralPath $pcZip -DestinationPath "$root\Phichain\app" -Force
}

# 桌面快捷方式
$ws = New-Object -ComObject WScript.Shell
foreach ($item in @(
  @{ Name = 'Phira.lnk';            Target = $phira;                         Work = (Split-Path $phira) },
  @{ Name = 'Phichain 制谱器.lnk';  Target = "$pcDir\phichain.exe";           Work = $pcDir }
)) {
  $lnk = $ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) $item.Name))
  $lnk.TargetPath = $item.Target; $lnk.WorkingDirectory = $item.Work
  $lnk.IconLocation = $item.Target + ',0'; $lnk.Save()
}

Write-Host "`n完成："
Write-Host "  Phira    : $phira"
Write-Host "  Phichain : $pcDir\phichain.exe"
Write-Host "  转换器   : $pcDir\phichain-converter.exe"
