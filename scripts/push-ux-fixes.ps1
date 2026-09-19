#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Package UX-fix overlays into one archive, upload once, optionally apply on the Pi.

.DESCRIPTION
  Stages overlay files under their /tmp names, builds a single .tgz, uploads that
  one file (one scp password). With -Apply, also runs extract + apply over one ssh -t
  (ssh + sudo passwords only — not once per file).

.EXAMPLE
  .\scripts\push-ux-fixes.ps1
  .\scripts\push-ux-fixes.ps1 -Apply
#>
param(
  [Parameter(Mandatory = $false)]
  [string]$PiUserHost = "adam@192.168.0.225",

  [Parameter(Mandatory = $false)]
  [switch]$Apply
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$files = @(
  @{ Local = "VERSION"; Remote = "stonepi-VERSION" },
  @{ Local = "packages/stonepi_auth/stonepi_auth/http.py"; Remote = "sp-http.py" },
  @{ Local = "packages/stonepi_auth/stonepi_auth/__init__.py"; Remote = "sp-auth-init.py" },
  @{ Local = "apps/dashboard/app/config.py"; Remote = "dash-config.py" },
  @{ Local = "apps/dashboard/app/routes.py"; Remote = "dash-routes.py" },
  @{ Local = "apps/dashboard/app/services.py"; Remote = "dash-services.py" },
  @{ Local = "apps/dashboard/app/templates/base.html"; Remote = "dash-base.html" },
  @{ Local = "apps/dashboard/app/templates/overview.html"; Remote = "dash-overview.html" },
  @{ Local = "apps/dashboard/app/templates/settings.html"; Remote = "dash-settings.html" },
  @{ Local = "apps/dashboard/app/templates/_household_users.html"; Remote = "dash-household-users.html" },
  @{ Local = "apps/dashboard/app/templates/_theme_boot.html"; Remote = "dash-theme-boot.html" },
  @{ Local = "apps/dashboard/app/templates/home.html"; Remote = "dash-home.html" },
  @{ Local = "apps/dashboard/app/templates/users.html"; Remote = "dash-users.html" },
  @{ Local = "apps/dashboard/app/static/js/app.js"; Remote = "dash-app.js" },
  @{ Local = "apps/dashboard/app/static/css/app.css"; Remote = "dash-app.css" },
  @{ Local = "apps/dashboard/app/__init__.py"; Remote = "dash-init.py" },
  @{ Local = "apps/auth/app/templates/login.html"; Remote = "auth-login.html" },
  @{ Local = "apps/auth/app/templates/status.html"; Remote = "auth-status.html" },
  @{ Local = "apps/auth/app/templates/_theme_boot.html"; Remote = "auth-theme-boot.html" },
  @{ Local = "apps/auth/app/routes.py"; Remote = "auth-routes.py" },
  @{ Local = "apps/auth/app/__init__.py"; Remote = "auth-init.py" },
  @{ Local = "apps/auth/app/users.py"; Remote = "auth-users.py" },
  @{ Local = "apps/newscast/app/auth.py"; Remote = "news-auth.py" },
  @{ Local = "apps/newscast/app/routers/ui.py"; Remote = "news-ui.py" },
  @{ Local = "apps/newscast/app/templates/base.html"; Remote = "news-base.html" },
  @{ Local = "apps/newscast/app/templates/login.html"; Remote = "news-login.html" },
  @{ Local = "apps/newscast/app/templates/settings.html"; Remote = "news-settings.html" },
  @{ Local = "apps/newscast/app/templates/_theme_boot.html"; Remote = "news-theme-boot.html" },
  @{ Local = "apps/newscast/app/static/js/app.js"; Remote = "news-app.js" },
  @{ Local = "apps/fileserve/app/auth.py"; Remote = "files-auth.py" },
  @{ Local = "apps/fileserve/app/services/pages.py"; Remote = "files-pages.py" },
  @{ Local = "apps/fileserve/app/main.py"; Remote = "files-main.py" },
  @{ Local = "apps/fileserve/app/templates/base.html"; Remote = "files-base.html" },
  @{ Local = "apps/fileserve/app/templates/pages.html"; Remote = "files-pages.html" },
  @{ Local = "apps/fileserve/app/templates/browse.html"; Remote = "files-browse.html" },
  @{ Local = "apps/fileserve/app/templates/login.html"; Remote = "files-login.html" },
  @{ Local = "apps/fileserve/app/templates/settings.html"; Remote = "files-settings.html" },
  @{ Local = "apps/fileserve/app/templates/_theme_boot.html"; Remote = "files-theme-boot.html" },
  @{ Local = "apps/fileserve/app/static/js/app.js"; Remote = "files-app.js" },
  @{ Local = "apps/eventtrakr/app/services/auth.py"; Remote = "events-auth.py" },
  @{ Local = "apps/eventtrakr/app/routes/settings.py"; Remote = "events-settings-routes.py" },
  @{ Local = "apps/eventtrakr/app/templates/base.html"; Remote = "events-base.html" },
  @{ Local = "apps/eventtrakr/app/templates/login.html"; Remote = "events-login.html" },
  @{ Local = "apps/eventtrakr/app/templates/settings.html"; Remote = "events-settings.html" },
  @{ Local = "apps/eventtrakr/app/templates/_theme_boot.html"; Remote = "events-theme-boot.html" },
  @{ Local = "apps/eventtrakr/app/static/js/app.js"; Remote = "events-app.js" },
  @{ Local = "apps/pinboard/app/routes.py"; Remote = "pin-routes.py" },
  @{ Local = "apps/pinboard/app/templates/base.html"; Remote = "pin-base.html" },
  @{ Local = "apps/pinboard/app/templates/_theme_boot.html"; Remote = "pin-theme-boot.html" },
  @{ Local = "apps/pinboard/app/static/js/app.js"; Remote = "pin-app.js" },
  @{ Local = "apps/studio/app/routes.py"; Remote = "studio-routes.py" },
  @{ Local = "apps/studio/app/templates/base.html"; Remote = "studio-base.html" },
  @{ Local = "apps/studio/app/templates/_theme_boot.html"; Remote = "studio-theme-boot.html" },
  @{ Local = "apps/studio/app/static/js/app.js"; Remote = "studio-app.js" },
  @{ Local = "scripts/apply-ux-fixes-on-pi.sh"; Remote = "apply-ux-fixes-on-pi.sh" }
)

$stage = Join-Path $env:TEMP ("stonepi-ux-overlay-" + [guid]::NewGuid().ToString("n"))
New-Item -ItemType Directory -Path $stage | Out-Null
try {
  foreach ($item in $files) {
    $src = Join-Path $Root $item.Local
    if (-not (Test-Path $src)) { throw "Missing $($item.Local)" }
    $dest = Join-Path $stage $item.Remote
    if ($item.Local -like "*.sh") {
      $text = [IO.File]::ReadAllText($src) -replace "`r`n", "`n" -replace "`r", "`n"
      [IO.File]::WriteAllText($dest, $text)
    } else {
      Copy-Item -LiteralPath $src -Destination $dest -Force
    }
  }

  $archive = Join-Path $env:TEMP "stonepi-ux-overlay.tgz"
  if (Test-Path $archive) { Remove-Item -LiteralPath $archive -Force }

  # ustar avoids LIBARCHIVE extended headers that make GNU tar on the Pi exit 2.
  & tar.exe --format=ustar -czf $archive -C $stage .
  if ($LASTEXITCODE -ne 0) { throw "tar failed with exit $LASTEXITCODE" }

  $sizeKb = [math]::Round((Get-Item $archive).Length / 1KB, 1)
  Write-Host "Packed $($files.Count) files -> $archive ($sizeKb KB)"

  Write-Host "Uploading one archive (one scp password)..."
  & scp.exe $archive "${PiUserHost}:/tmp/stonepi-ux-overlay.tgz"
  if ($LASTEXITCODE -ne 0) { throw "scp failed with exit $LASTEXITCODE" }

  if ($Apply) {
    Write-Host "Extracting and applying (ssh + sudo passwords)..."
    # Ignore unknown-header warnings from mixed tar implementations; fail only if apply script missing.
    $remote = @'
set -euo pipefail
cd /tmp
tar -xzf /tmp/stonepi-ux-overlay.tgz -C /tmp --no-same-owner 2>/tmp/stonepi-ux-tar.err || true
if [[ ! -f /tmp/apply-ux-fixes-on-pi.sh ]]; then
  echo "Extract failed — apply script missing. tar notes:" >&2
  cat /tmp/stonepi-ux-tar.err >&2 || true
  ls -la /tmp/stonepi-ux-overlay.tgz /tmp/dash-*.py /tmp/auth-*.py 2>&1 | head -40 >&2 || true
  exit 1
fi
# Strip CRLF if the script was edited on Windows
sed -i 's/\r$//' /tmp/apply-ux-fixes-on-pi.sh
chmod +x /tmp/apply-ux-fixes-on-pi.sh
sudo bash /tmp/apply-ux-fixes-on-pi.sh
'@
    $remote = $remote -replace "`r`n", "`n" -replace "`r", "`n"
    & ssh.exe -t $PiUserHost $remote
    if ($LASTEXITCODE -ne 0) { throw "Remote apply failed with exit $LASTEXITCODE" }
    Write-Host "Applied. Hard-refresh http://stonepi.home/"
  }
  else {
    Write-Host ""
    Write-Host "Uploaded. Apply with one SSH session:"
    Write-Host "  ssh -t $PiUserHost `"tar -xzf /tmp/stonepi-ux-overlay.tgz -C /tmp --no-same-owner; sed -i 's/\r`$//' /tmp/apply-ux-fixes-on-pi.sh; sudo bash /tmp/apply-ux-fixes-on-pi.sh`""
    Write-Host ""
    Write-Host "Or:  .\scripts\push-ux-fixes.ps1 -Apply"
  }
}
finally {
  if (Test-Path $stage) { Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue }
}
