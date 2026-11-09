# Сжать диск Docker Desktop (docker_data.vhdx) и вернуть место Windows.
# Нужен, если внутри Docker место уже освобождено (docker system df), а файл vhdx остался большим.
# Запуск: PowerShell «от имени администратора» → powershell -ExecutionPolicy Bypass -File bench\compact-docker-disk.ps1
# Останавливает Docker Desktop и WSL на время сжатия (несколько минут), затем запускает Docker снова.

$ErrorActionPreference = "Stop"
$vhd = Join-Path $env:LOCALAPPDATA "Docker\wsl\disk\docker_data.vhdx"
if (-not (Test-Path $vhd)) { throw "Не найден $vhd" }
$before = (Get-Item $vhd).Length / 1GB

Get-Process -Name "Docker Desktop", "com.docker.backend", "com.docker.build" -ErrorAction SilentlyContinue |
    Stop-Process -Force -Confirm:$false
Start-Sleep -Seconds 3
wsl --shutdown
Start-Sleep -Seconds 5

Optimize-VHD -Path $vhd -Mode Full

$after = (Get-Item $vhd).Length / 1GB
"docker_data.vhdx: {0:N1} GB -> {1:N1} GB" -f $before, $after
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
