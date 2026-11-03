$ErrorActionPreference='Stop'
$desktop=[Environment]::GetFolderPath('Desktop')
$shortcut=Join-Path $desktop 'FPGA 建模工作台.lnk'
$shell=New-Object -ComObject WScript.Shell
$link=$shell.CreateShortcut($shortcut)
$link.TargetPath=Join-Path $env:WINDIR 'System32\wscript.exe'
$link.Arguments='"'+(Join-Path $PSScriptRoot 'Launch.vbs')+'"'
$link.WorkingDirectory=$PSScriptRoot
$link.IconLocation=(Join-Path $env:WINDIR 'System32\shell32.dll')+',14'
$link.Description='FPGA 建模工作台：照片接收、连通测试与本地 GPU 建模'
$link.Save()
Write-Output $shortcut
