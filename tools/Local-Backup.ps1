[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Save', 'Verify', 'Restore')][string]$Action,
    [Parameter(Mandatory)][string]$Source,
    [string]$Destination,
    [string]$ExpectedUserName
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not [System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform([System.Runtime.InteropServices.OSPlatform]::Windows)) {
    throw 'Windows DPAPI is required.'
}
Add-Type -AssemblyName System.Security
if ($ExpectedUserName) {
    $currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    try {
        if ($currentIdentity.Name -ne $ExpectedUserName) { throw 'Current Windows account does not match ExpectedUserName.' }
    } finally { $currentIdentity.Dispose() }
}

function Clear-Bytes([byte[]]$Bytes) {
    if ($null -ne $Bytes) { [Array]::Clear($Bytes, 0, $Bytes.Length) }
}

function Protect-Directory([string]$Path) {
    [IO.Directory]::CreateDirectory($Path) | Out-Null
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    try {
        $existing = Get-Acl -LiteralPath $Path
        $sidType = [Security.Principal.SecurityIdentifier]
        $rules = @($existing.GetAccessRules($true, $true, $sidType))
        $expectedSids = @($identity.User.Value, 'S-1-5-18')
        $inheritance = [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
        $safe = $existing.AreAccessRulesProtected -and $existing.GetOwner($sidType).Value -eq $identity.User.Value -and $rules.Count -eq 2
        $seen = @{}
        foreach ($rule in $rules) {
            $sid = $rule.IdentityReference.Value
            if ($sid -notin $expectedSids -or $seen.ContainsKey($sid) -or
                $rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
                $rule.FileSystemRights -ne [Security.AccessControl.FileSystemRights]::FullControl -or
                $rule.InheritanceFlags -ne $inheritance -or
                $rule.PropagationFlags -ne [Security.AccessControl.PropagationFlags]::None -or $rule.IsInherited) {
                $safe = $false
            }
            $seen[$sid] = $true
        }
        if ($safe -and $seen.ContainsKey($identity.User.Value) -and $seen.ContainsKey('S-1-5-18')) { return }
        $acl = [Security.AccessControl.DirectorySecurity]::new()
        $acl.SetAccessRuleProtection($true, $false)
        $acl.SetOwner($identity.User)
        $inheritance = [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
        foreach ($sid in @($identity.User, [Security.Principal.SecurityIdentifier]::new('S-1-5-18'))) {
            $rule = [Security.AccessControl.FileSystemAccessRule]::new(
                $sid, [Security.AccessControl.FileSystemRights]::FullControl,
                $inheritance, [Security.AccessControl.PropagationFlags]::None,
                [Security.AccessControl.AccessControlType]::Allow)
            $acl.AddAccessRule($rule)
        }
        Set-Acl -LiteralPath $Path -AclObject $acl
    } finally { $identity.Dispose() }
}

function Read-Payload([string]$Path) {
    $cipher = $plain = $data = $buffer = $null
    $stream = $reader = $null
    try {
        $cipher = [IO.File]::ReadAllBytes($Path)
        $plain = [Security.Cryptography.ProtectedData]::Unprotect(
            $cipher, $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)
        $stream = [IO.MemoryStream]::new($plain, $false)
        $reader = [IO.BinaryReader]::new($stream, [Text.Encoding]::UTF8, $true)
        if ($reader.ReadString() -ne 'BiDsy.LocalBackup' -or $reader.ReadInt32() -ne 1) {
            throw 'Unsupported backup format or version.'
        }
        $name = $reader.ReadString()
        if ([string]::IsNullOrWhiteSpace($name) -or [IO.Path]::GetFileName($name) -ne $name) {
            throw 'Invalid original filename.'
        }
        $length = $reader.ReadInt64()
        if ($length -lt 0 -or $length -gt [int]::MaxValue -or $stream.Length - $stream.Position -ne $length) {
            throw 'Invalid backup payload length.'
        }
        $data = $reader.ReadBytes([int]$length)
        $result = [pscustomobject]@{ Version = 1; OriginalName = $name; Bytes = $data }
        $data = $null # Ownership passes to caller, which must clear Bytes.
        return $result
    } finally {
        if ($null -ne $reader) { $reader.Dispose() }
        if ($null -ne $stream) { $stream.Dispose() }
        Clear-Bytes $plain
        Clear-Bytes $cipher
        Clear-Bytes $data
    }
}

function Equal-Bytes([byte[]]$Left, [byte[]]$Right) {
    if ($Left.Length -ne $Right.Length) { return $false }
    for ($i = 0; $i -lt $Left.Length; $i++) {
        if ($Left[$i] -ne $Right[$i]) { return $false }
    }
    return $true
}

$sourcePath = [IO.Path]::GetFullPath($Source)
if (-not [IO.File]::Exists($sourcePath)) { throw 'Source file does not exist.' }
$sourceBytes = $payloadBytes = $cipherBytes = $null
$payload = $stream = $writer = $output = $null
$createdPath = $null
$completed = $false
try {
    if ($Action -eq 'Save') {
        # Destination is an output directory; the filename is always unique.
        $directory = if ($Destination) { [IO.Path]::GetFullPath($Destination) } else {
            [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../.private-backups'))
        }
        Protect-Directory $directory
        $sourceBytes = [IO.File]::ReadAllBytes($sourcePath)
        $stream = [IO.MemoryStream]::new()
        $writer = [IO.BinaryWriter]::new($stream, [Text.Encoding]::UTF8, $true)
        $writer.Write('BiDsy.LocalBackup')
        $writer.Write([int]1)
        $writer.Write([IO.Path]::GetFileName($sourcePath))
        $writer.Write([long]$sourceBytes.Length)
        $writer.Write($sourceBytes)
        $writer.Flush()
        $payloadBytes = $stream.ToArray()
        $cipherBytes = [Security.Cryptography.ProtectedData]::Protect(
            $payloadBytes, $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)
        $candidatePath = Join-Path $directory ('backup-{0}-{1}.bidsy-dpapi' -f [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ'), [Guid]::NewGuid().ToString('N'))
        $output = [IO.File]::Open($candidatePath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        $createdPath = $candidatePath
        $output.Write($cipherBytes, 0, $cipherBytes.Length)
        $output.Dispose(); $output = $null
        $payload = Read-Payload $createdPath
        if ($payload.OriginalName -ne [IO.Path]::GetFileName($sourcePath) -or -not (Equal-Bytes $sourceBytes $payload.Bytes)) {
            throw 'Backup roundtrip verification failed.'
        }
        $completed = $true
        [pscustomobject]@{ Action = 'Save'; Path = $createdPath; Verified = $true; Version = 1 }
    } else {
        if ($Action -eq 'Restore' -and [string]::IsNullOrWhiteSpace($Destination)) {
            throw 'Restore requires an explicit destination filename.'
        }
        $payload = Read-Payload $sourcePath
        if ($Action -eq 'Verify') {
            if ($Destination) { throw 'Verify does not accept Destination.' }
            [pscustomobject]@{ Action = 'Verify'; Verified = $true; Version = $payload.Version; OriginalName = $payload.OriginalName; Length = $payload.Bytes.Length }
        } else {
            $restorePath = [IO.Path]::GetFullPath($Destination)
            # CreateNew is atomic and never overwrites an existing file.
            $output = [IO.File]::Open($restorePath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
            $createdPath = $restorePath
            $output.Write($payload.Bytes, 0, $payload.Bytes.Length)
            $output.Dispose(); $output = $null
            $sourceBytes = [IO.File]::ReadAllBytes($restorePath)
            if (-not (Equal-Bytes $sourceBytes $payload.Bytes)) { throw 'Restore verification failed.' }
            $completed = $true
            [pscustomobject]@{ Action = 'Restore'; Path = $restorePath; Verified = $true }
        }
    }
} finally {
    if ($null -ne $output) { $output.Dispose() }
    if ($null -ne $writer) { $writer.Dispose() }
    if ($null -ne $stream) {
        Clear-Bytes $stream.GetBuffer()
        $stream.Dispose()
    }
    Clear-Bytes $sourceBytes
    Clear-Bytes $payloadBytes
    Clear-Bytes $cipherBytes
    if ($null -ne $payload) { Clear-Bytes $payload.Bytes }
    if (-not $completed -and $null -ne $createdPath -and [IO.File]::Exists($createdPath)) {
        [IO.File]::Delete($createdPath)
    }
}
