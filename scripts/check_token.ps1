param(
    [string]$DataDir = "",
    [string]$Pattern = '://[^/\s]+@'
)
# ASCII only on purpose. Windows PowerShell 5.1 on a zh-CN system reads a .ps1
# without BOM as ANSI/GBK, so Chinese comments can swallow the following byte and
# silently corrupt the regex literal. Observed effect: the script printed nothing
# even on a repo that really contained credentials -- a false negative on a
# security self-check, which is worse than having no check at all.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\check_token.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\check_token.ps1 -DataDir "C:\path\to\data"

if ($DataDir -eq "") {
    $candidates = @()
    $scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
    $appDir = Split-Path -Parent $scriptRoot
    $candidates += (Join-Path $appDir "data")
    $candidates += (Join-Path $env:LOCALAPPDATA "qingdan")
} else {
    $candidates = @($DataDir)
}

$found = 0
foreach ($dir in $candidates) {
    if (-not (Test-Path $dir)) { continue }
    Write-Output ("SCAN " + $dir)
    $files = Get-ChildItem -Recurse -Force -File $dir -ErrorAction SilentlyContinue
    foreach ($f in $files) {
        # Only text-ish files git actually writes URLs into; skip object store and db blobs
        $rel = $f.FullName.Substring($dir.Length) -replace '\\', '/'
        if ($rel -notmatch '\.git/(config|logs|FETCH_HEAD|HEAD)' -and $rel -notmatch '\.sqlite$' -and $rel -notmatch 'http\.json$') {
            continue
        }
        $hits = Select-String -LiteralPath $f.FullName -Pattern $Pattern -ErrorAction SilentlyContinue
        foreach ($h in $hits) {
            $found = $found + 1
            Write-Output ("HIT " + $h.Path + ":" + $h.LineNumber)
        }
    }
}

if ($found -eq 0) {
    Write-Output "CLEAN no credential-bearing URL found in the locations above"
} else {
    Write-Output ("RESULT " + $found + " hit(s): revoke that token on Gitee, then rewrite the lines without the user:password@ part")
}
