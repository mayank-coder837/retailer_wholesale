# M0.6 acceptance on a clean Windows machine with admin rights (CI windows-latest): the real installer, start to
# finish. Install silently, set up as main PC through the installed app, check services, health, sign-up and a
# backup, install again over it (the update path), uninstall silently, check services are gone and data stays.
#
#   powershell -File apps/desktop/scripts/installer-smoke.ps1 -Setup apps/desktop/release/<setup>.exe
param([Parameter(Mandatory = $true)][string]$Setup)

$ErrorActionPreference = 'Stop'
$product = Get-Content (Join-Path $PSScriptRoot '..\..\..\packages\config\product.json') -Raw | ConvertFrom-Json
$prefix = $product.servicePrefix
$installDir = Join-Path $env:ProgramFiles $product.name
$exe = Join-Path $installDir "$($product.name).exe"
$dataDir = Join-Path $env:ProgramData $prefix

function Step($message) { Write-Host "== $message" }
function Check($condition, $message) {
  if (-not $condition) { throw "FAIL: $message" }
  Write-Host "ok - $message"
}
function Run($file, $arguments) {
  $p = Start-Process -FilePath $file -ArgumentList $arguments -Wait -PassThru
  return $p.ExitCode
}
function Health {
  $config = Get-Content (Join-Path $dataDir 'config.json') -Raw | ConvertFrom-Json
  $base = "http://127.0.0.1:$($config.apiPort)"
  for ($i = 0; $i -lt 60; $i++) {
    try { return @{ base = $base; body = (Invoke-RestMethod "$base/health/db" -TimeoutSec 5) } } catch { Start-Sleep -Seconds 2 }
  }
  throw "FAIL: the API never answered at $base"
}

if (Get-Service "$prefix-*" -ErrorAction SilentlyContinue) { throw "services already installed here; refusing to touch them" }
if (Test-Path $dataDir) { throw "$dataDir already exists; refusing to touch it" }

try {
  Step 'install silently'
  Check ((Run $Setup '/S') -eq 0) 'installer exit code 0'
  Check (Test-Path $exe) "app installed at $exe"
  Check (Test-Path (Join-Path $installDir 'resources\runtime\runtime.json')) 'bundled runtime shipped'

  Step 'set up as main PC through the installed app (what the first-run button runs after the UAC prompt)'
  $json = '{"command":"setup","serverName":"CI main PC"}'
  $request = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json)).TrimEnd('=').Replace('+', '-').Replace('/', '_')
  $code = Run $exe "--runtime-cli=$request"
  if ($code -ne 0) { Get-Content (Join-Path $dataDir 'logs\runtime.log') -Tail 40 | Write-Host }
  Check ($code -eq 0) 'setup exit code 0'
  foreach ($name in "$prefix-PostgreSQL", "$prefix-API") {
    $service = Get-Service $name
    Check ($service.Status -eq 'Running' -and $service.StartType -eq 'Automatic') "$name running, starts with Windows"
  }
  $health = Health
  Check ($health.body.server_version -like '17.*') "API healthy on PostgreSQL $($health.body.server_version)"
  $page = Invoke-WebRequest "$($health.base)/" -UseBasicParsing
  Check ($page.Content -match "<title>$([regex]::Escape($product.name))</title>") 'the API serves the app'

  Step 'sign up and back up through the installed services'
  $signup = @{
    email = 'owner@ci.ae'; password = 'correct horse battery'; full_name = 'CI Owner'; tenant_name = 'CI Trading'
    business = @{ legal_name_en = 'CI Trading LLC'; legal_name_ar = 'شركة الاختبار' }
    head_office = @{ code = 'HO'; name_en = 'Deira'; name_ar = 'ديرة'; emirate = 'dubai' }
    client = 'desktop'
  } | ConvertTo-Json -Depth 4
  $tokens = Invoke-RestMethod "$($health.base)/auth/signup" -Method Post -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($signup))
  Check ([bool]$tokens.access_token) 'sign-up'
  $backup = Invoke-RestMethod "$($health.base)/system/backups" -Method Post -Headers @{ Authorization = "Bearer $($tokens.access_token)" }
  Check (Test-Path (Join-Path $dataDir "backups\$($backup.name)")) "backup $($backup.name) written"

  Step 'install again over it (the update path)'
  Check ((Run $Setup '/S') -eq 0) 'second install exit code 0'
  $after = Health
  Check ($after.body.database -eq 'ok') 'API healthy after the update'
  Check ((Get-Content (Join-Path $dataDir 'logs\runtime.log') -Raw) -match 'upgrade: done') 'installer ran the upgrade step'
  $login = @{ email = 'owner@ci.ae'; password = 'correct horse battery'; client = 'desktop' } | ConvertTo-Json
  $again = Invoke-RestMethod "$($after.base)/auth/login" -Method Post -ContentType 'application/json' -Body $login
  Check ([bool]$again.access_token) 'data survived the update: the owner still logs in'

  Step 'uninstall silently'
  Check ((Run (Join-Path $installDir "Uninstall $($product.name).exe") '/S') -eq 0) 'uninstaller exit code 0'
  Start-Sleep -Seconds 5
  Check (-not (Get-Service "$prefix-*" -ErrorAction SilentlyContinue)) 'services removed'
  Check (-not (Test-Path $exe)) 'app removed'
  Check (Test-Path (Join-Path $dataDir 'pgdata\PG_VERSION')) 'database kept (silent uninstall never deletes data)'
  Write-Host 'ALL OK'
}
catch {
  if (Test-Path (Join-Path $dataDir 'logs')) {
    Get-ChildItem (Join-Path $dataDir 'logs') -File | ForEach-Object { Write-Host "== $($_.Name)"; Get-Content $_.FullName -Tail 60 | Write-Host }
  }
  throw
}
