# Installs a self-contained environment on another Windows PC; no shared .pth files.
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
if(Test-Path -LiteralPath 'config\local.json'){throw '已有本机配置，避免覆盖。迁移电脑时请使用不含 local.json 的源码压缩包。'}
$base=(Get-Command python -ErrorAction Stop).Source
& $base -c "import sys; assert sys.version_info[:2]==(3,11), 'Please install Python 3.11 first'"
if($LASTEXITCODE -ne 0){throw '需要 Python 3.11'}
& $base -m venv runtime
if($LASTEXITCODE -ne 0){throw '创建 Python 环境失败'}
$python=Join-Path $PSScriptRoot 'runtime\Scripts\python.exe'
& $python -m pip install --upgrade pip
if($LASTEXITCODE -ne 0){throw 'pip 安装失败'}
& $python -m pip install torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cu124
if($LASTEXITCODE -ne 0){throw 'CUDA PyTorch 安装失败'}
& $python -m pip install -r requirements.txt
if($LASTEXITCODE -ne 0){throw '依赖安装失败'}
& $python tools\download_models.py
if($LASTEXITCODE -ne 0){throw '模型权重下载或 SHA256 校验失败'}
& $python -c "import torch; assert torch.cuda.is_available(), 'NVIDIA CUDA GPU unavailable'; print(torch.cuda.get_device_name())"
if($LASTEXITCODE -ne 0){throw '未找到可用的 NVIDIA GPU，请检查显卡驱动'}
& (Join-Path $PSScriptRoot 'CreateShortcut.ps1')
Write-Output '安装完成，双击桌面 FPGA 建模工作台。首次下载与安装需要额外时间和磁盘空间。'
