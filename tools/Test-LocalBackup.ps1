[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script = Join-Path $PSScriptRoot 'Local-Backup.ps1'
$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$testRoot = Join-Path $tempRoot ('bidsy-backup-test-' + [Guid]::NewGuid().ToString('N'))
$bytes = $restoredBytes = $null
try {
    [IO.Directory]::CreateDirectory($testRoot) | Out-Null
    $source = Join-Path $testRoot 'synthetic.bin'
    $restore = Join-Path $testRoot 'restored.bin'
    $bytes = [byte[]]::new(4096)
    [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    [IO.File]::WriteAllBytes($source, $bytes)
    $saved = & $script -Action Save -Source $source -Destination (Join-Path $testRoot 'encrypted')
    if (-not $saved.Verified -or -not [IO.File]::Exists($source)) { throw 'Save failed or source disappeared.' }
    $backupDirectory = Join-Path $testRoot 'encrypted'
    $aclBefore = (Get-Acl -LiteralPath $backupDirectory).Sddl
    $savedAgain = & $script -Action Save -Source $source -Destination $backupDirectory
    if (-not $savedAgain.Verified -or $savedAgain.Path -eq $saved.Path -or
        (Get-Acl -LiteralPath $backupDirectory).Sddl -ne $aclBefore) { throw 'Repeated Save or ACL idempotence failed.' }
    $verified = & $script -Action Verify -Source $saved.Path
    if (-not $verified.Verified -or $verified.OriginalName -ne 'synthetic.bin' -or $verified.Length -ne $bytes.Length) { throw 'Verify failed.' }
    $result = & $script -Action Restore -Source $saved.Path -Destination $restore
    $restoredBytes = [IO.File]::ReadAllBytes($restore)
    if (-not $result.Verified -or [Convert]::ToBase64String($bytes) -ne [Convert]::ToBase64String($restoredBytes)) { throw 'Restore differs.' }
    $blocked = $false
    try { & $script -Action Restore -Source $saved.Path -Destination $restore | Out-Null } catch { $blocked = $true }
    if (-not $blocked -or -not [IO.File]::Exists($restore)) { throw 'No-overwrite protection failed.' }
    $blocked = $false
    try { & $script -Action Restore -Source $saved.Path | Out-Null } catch { $blocked = $true }
    if (-not $blocked) { throw 'Missing destination was accepted.' }
    Write-Output 'PASS: Save roundtrip, repeated Save/ACL idempotence, Verify, Restore bytes, no-overwrite, explicit destination, source retained.'
} finally {
    if ($null -ne $bytes) { [Array]::Clear($bytes, 0, $bytes.Length) }
    if ($null -ne $restoredBytes) { [Array]::Clear($restoredBytes, 0, $restoredBytes.Length) }
    $resolved = [IO.Path]::GetFullPath($testRoot)
    if ($resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -and [IO.Path]::GetFileName($resolved) -match '^bidsy-backup-test-[a-f0-9]{32}$') {
        if (Test-Path -LiteralPath $resolved) { Remove-Item -LiteralPath $resolved -Recurse -Force }
    } else { throw 'Refusing cleanup outside own temporary directory.' }
}
