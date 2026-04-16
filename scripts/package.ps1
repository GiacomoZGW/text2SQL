$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

Push-Location (Join-Path $Root "frontend")
if (-not (Test-Path "node_modules")) {
  npm install
}
npm run package
Pop-Location

Write-Host ""
Write-Host "完成。静态资源: $Root\dist_package\frontend"
Write-Host "一体启动: 确保已 build，然后在项目根执行: python api/main.py  访问 http://127.0.0.1:8000/"
