$ErrorActionPreference = "Stop"

$configPath = Join-Path $env:USERPROFILE ".codex\config.toml"
$serverName = "codebase-memory-mcp"
$exePath = "C:\Users\zjxqm\AppData\Local\Programs\codebase-memory-mcp\codebase-memory-mcp.exe"

if (-not (Test-Path -LiteralPath $configPath)) {
    throw "Codex config not found: $configPath"
}

if (-not (Test-Path -LiteralPath $exePath)) {
    throw "codebase-memory-mcp executable not found: $exePath"
}

$block = @"
[mcp_servers.$serverName]
type = "stdio"
command = '$exePath'
args = []
enabled = true
startup_timeout_sec = 30
"@

$content = Get-Content -Raw -LiteralPath $configPath
$sectionPattern = "(?ms)^\[mcp_servers\.$([regex]::Escape($serverName))\]\r?\n.*?(?=^\[|\z)"

if ($content -match $sectionPattern) {
    $content = [regex]::Replace($content, $sectionPattern, ($block + "`r`n"), 1)
} elseif ($content -match "(?m)^\[mcp_servers\]\s*$") {
    $content = [regex]::Replace(
        $content,
        "(?m)^\[mcp_servers\]\s*\r?\n",
        { param($match) $match.Value + "`r`n" + $block + "`r`n`r`n" },
        1
    )
} else {
    throw "Config does not contain a [mcp_servers] table: $configPath"
}

$backupPath = "$configPath.bak-codebase-memory-mcp-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
Copy-Item -LiteralPath $configPath -Destination $backupPath -Force

$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($configPath, $content, $utf8NoBom)

Write-Host "Registered $serverName in $configPath"
Write-Host "Backup written to $backupPath"
