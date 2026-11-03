param([switch]$NoBrowser,[switch]$NoDialog)
$ErrorActionPreference='Stop'
$root=$PSScriptRoot
$launchLog=Join-Path $root 'data\launcher.log'
$mutex=$null
$ownsMutex=$false
$failed=$false
function Write-LaunchLog([string]$Message){
    try {
        New-Item -ItemType Directory -Path (Split-Path -Parent $launchLog) -Force|Out-Null
        Add-Content -LiteralPath $launchLog -Value ((Get-Date -Format o)+' '+$Message) -Encoding UTF8
    } catch {}
}
function Get-StudioHealth([string]$Base){
    try {
        $s=Invoke-RestMethod -Uri ($Base+'/api/health') -TimeoutSec 2
        if(($s.app -in @('FPGA 建模工作台','FPGA AI 建模工作台')) -and $s.ready -and $s.root -eq $root){return $s}
    } catch {}
    return $null
}
try {
    $hasher=[Security.Cryptography.SHA256]::Create()
    try {$key=([BitConverter]::ToString($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($root.ToLowerInvariant())))).Replace('-','')}
    finally {$hasher.Dispose()}
    $mutex=[Threading.Mutex]::new($false,('Local\FPGA_AI_Studio_'+$key))
    try {$ownsMutex=$mutex.WaitOne(90000)}catch [Threading.AbandonedMutexException]{$ownsMutex=$true}
    if(-not $ownsMutex){throw '另一个启动过程尚未结束，请稍后重试。'}
    $cfg=Get-Content -LiteralPath (Join-Path $root 'config\default.json') -Raw -Encoding UTF8|ConvertFrom-Json
    $local=Join-Path $root 'config\local.json'
    if(Test-Path -LiteralPath $local){
        $override=Get-Content -LiteralPath $local -Raw -Encoding UTF8|ConvertFrom-Json
        foreach($p in $override.PSObject.Properties){$cfg|Add-Member -NotePropertyName $p.Name -NotePropertyValue $p.Value -Force}
    }
    $python=$cfg.python
    if(-not [IO.Path]::IsPathRooted($python)){$python=Join-Path $root $python}
    if(-not (Test-Path -LiteralPath $python)){throw '缺少 Python 运行环境，请先运行 Install.ps1；本机版本也可检查 config\local.json。'}
    $data=$cfg.data_dir
    if(-not [IO.Path]::IsPathRooted($data)){$data=Join-Path $root $data}
    New-Item -ItemType Directory -Path $data -Force|Out-Null
    $launchLog=Join-Path $data 'launcher.log'
    $base='http://127.0.0.1:'+$cfg.port
    $health=Get-StudioHealth $base
    if(-not $health){
        Write-LaunchLog ('PREFLIGHT '+$python)
        # Isolated mode verifies packages are in this runtime, not an app-specific user site.
        $probeInfo=[Diagnostics.ProcessStartInfo]::new()
        $probeInfo.FileName=$python
        $probeInfo.Arguments='-I "'+(Join-Path $root 'tools\check_startup.py')+'"'
        $probeInfo.WorkingDirectory=$root
        $probeInfo.UseShellExecute=$false
        $probeInfo.CreateNoWindow=$true
        $probeInfo.RedirectStandardOutput=$true
        $probeInfo.RedirectStandardError=$true
        $probe=[Diagnostics.Process]::Start($probeInfo)
        try {
            $outTask=$probe.StandardOutput.ReadToEndAsync()
            $errTask=$probe.StandardError.ReadToEndAsync()
            if(-not $probe.WaitForExit(30000)){$probe.Kill();throw '启动依赖检查超过 30 秒，请查看启动日志。'}
            $probeText=$outTask.Result+$errTask.Result
            Write-LaunchLog $probeText.Trim()
            if($probe.ExitCode -ne 0){throw ('启动依赖检查失败：'+$probeText.Trim()+"`n请使用安装了 requirements-startup.txt 的独立环境，不要依赖用户目录中的包。")}
        } finally {$probe.Dispose()}
        $env:PYTHONIOENCODING='utf-8'
        $argsLine='-E -s -u "'+(Join-Path $root 'server.py')+'"'
        $process=Start-Process -FilePath $python -ArgumentList $argsLine -WorkingDirectory $root -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $data 'server.log') -RedirectStandardError (Join-Path $data 'server_error.log')
        Write-LaunchLog ('STARTED pid='+$process.Id)
        $deadline=[DateTime]::UtcNow.AddSeconds(60)
        while([DateTime]::UtcNow -lt $deadline){
            $health=Get-StudioHealth $base
            if($health){break}
            $process.Refresh()
            if($process.HasExited){
                $errorLog=Join-Path $data 'server_error.log'
                $details=(Get-Content -LiteralPath $errorLog -Tail 12 -Encoding UTF8 -ErrorAction SilentlyContinue)-join "`n"
                throw ("工作台启动失败：`n"+$details+"`n日志："+$errorLog)
            }
            Start-Sleep -Milliseconds 250
        }
        if(-not $health){throw ('工作台尚未就绪，请查看 '+(Join-Path $data 'server_error.log')+'；稍后再次打开可重用已启动服务。')}
    } else {
        Write-LaunchLog ('REUSED pid='+$health.pid)
        try {
            $page=Invoke-WebRequest -Uri ($base+'/') -UseBasicParsing -TimeoutSec 3
            $match=[regex]::Match($page.Content,'name="studio-token" content="([^"]+)"')
            if($match.Success){Invoke-RestMethod -Uri ($base+'/api/check') -Method Post -Headers @{'X-Studio-Token'=$match.Groups[1].Value} -ContentType 'application/json' -Body '{}' -TimeoutSec 5|Out-Null}
        } catch {Write-LaunchLog ('CHECK_REFRESH_WARNING '+$_.Exception.Message)}
    }
    Write-LaunchLog ('READY '+$base+' pid='+$health.pid)
    if(-not $NoBrowser){
        $edge=Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'
        if(Test-Path -LiteralPath $edge){Start-Process -FilePath $edge -ArgumentList ('--app='+$base),'--new-window'}
        else {Start-Process $base}
    }
    Write-Output ('STUDIO_READY '+$base+' pid='+$health.pid)
} catch {
    $failed=$true
    $message=$_.Exception.Message
    Write-LaunchLog ('FAILED '+$message)
    if($NoDialog){[Console]::Error.WriteLine($message)}
    else {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show(($message+"`n启动日志："+$launchLog),'FPGA 建模工作台启动失败','OK','Error')|Out-Null
    }
} finally {
    if($ownsMutex){$mutex.ReleaseMutex()}
    if($mutex){$mutex.Dispose()}
}
if($failed){exit 1}
