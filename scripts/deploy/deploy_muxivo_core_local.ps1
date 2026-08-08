param(
    [string]$HostName = '192.168.1.142',
    [int]$Port = 22,
    [string]$User = 'minecraft',
    [string]$SshPassword = $env:MUXIVO_CORE_SSH_PASSWORD,
    [string]$RootPassword = $env:MUXIVO_CORE_ROOT_PASSWORD,
    [string]$Archive = (Join-Path $env:TEMP 'muxivo-core-release.tar.gz'),
    [string]$ModelArchive = (Join-Path $env:TEMP 'muxivo-core-model.tar.gz'),
    [string]$RemoteArchive = '/tmp/muxivo-core-release.tar.gz',
    [string]$RemoteModelArchive = '/tmp/muxivo-core-model.tar.gz',
    [string]$RemoteDeployScript = '/tmp/muxivo_core_deploy.sh',
    [string]$Services = 'muxivo-core.service'
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($SshPassword)) {
    throw 'SshPassword is required. Pass -SshPassword or set MUXIVO_CORE_SSH_PASSWORD.'
}

if ([string]::IsNullOrWhiteSpace($RootPassword)) {
    throw 'RootPassword is required. Pass -RootPassword or set MUXIVO_CORE_ROOT_PASSWORD.'
}

$deployScript = Join-Path $PSScriptRoot 'muxivo_core_deploy.sh'
if (-not (Test-Path -LiteralPath $deployScript)) {
    throw "Deploy script was not found: $deployScript"
}

& (Join-Path $PSScriptRoot 'build_muxivo_core_release.ps1') -Archive $Archive -ModelArchive $ModelArchive

pscp.exe -batch -P $Port -pw $SshPassword $Archive "${User}@${HostName}:$RemoteArchive"
pscp.exe -batch -P $Port -pw $SshPassword $ModelArchive "${User}@${HostName}:$RemoteModelArchive"
pscp.exe -batch -P $Port -pw $SshPassword $deployScript "${User}@${HostName}:$RemoteDeployScript"

$remoteCommand = "chmod +x $RemoteDeployScript; printf '%s\n' '$RootPassword' | su root -c 'SERVICES=""$Services"" ARCHIVE=$RemoteArchive MODEL_ARCHIVE=$RemoteModelArchive APP_DIR=/opt/muxivo-core $RemoteDeployScript'"
plink.exe -batch -ssh "${User}@${HostName}" -P $Port -pw $SshPassword $remoteCommand

$statusCommand = "systemctl is-active $Services"
plink.exe -batch -ssh "${User}@${HostName}" -P $Port -pw $SshPassword $statusCommand
