# Windows PowerShell 5.1 WinRT bridge. One JSON request and response per line.
$ErrorActionPreference = 'Stop'
$utf8 = [Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager,Windows.Media.Control,ContentType=WindowsRuntime]
$null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties,Windows.Media.Control,ContentType=WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStreamWithContentType,Windows.Storage.Streams,ContentType=WindowsRuntime]

$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethodDefinition -and $_.GetParameters().Length -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1
function Await($operation, [Type]$type) {
    $task = $asTask.MakeGenericMethod($type).Invoke($null, @($operation))
    if (!$task.Wait(2100)) { throw 'WinRT 操作超时。' }
    return $task.Result
}

function GetSeekRange($time) {
    $min = $time.StartTime.Ticks
    $max = $time.EndTime.Ticks
    if ($time.MaxSeekTime.Ticks -gt $time.MinSeekTime.Ticks) {
        $min = [Math]::Max($min, $time.MinSeekTime.Ticks)
        $max = [Math]::Min($max, $time.MaxSeekTime.Ticks)
    }
    return @{ min=$min; max=$max }
}
function Describe($session, [bool]$artwork, $knownArt) {
    $playback = $session.GetPlaybackInfo()
    $c = $playback.Controls
    $time = $session.GetTimelineProperties()
    $range = GetSeekRange $time
    $length = [Math]::Max(0, ($time.EndTime - $time.StartTime).TotalMilliseconds)
    $position = ($time.Position - $time.StartTime).TotalMilliseconds
    if ($length -gt 0 -and $playback.PlaybackStatus.ToString() -eq 'Playing' -and $time.LastUpdatedTime.Year -gt 2000) {
        $rate = if ($null -eq $playback.PlaybackRate) { 1 } else { $playback.PlaybackRate }
        $position += [Math]::Max(0, ([DateTimeOffset]::UtcNow - $time.LastUpdatedTime).TotalMilliseconds) * $rate
    }
    $result = @{ source=$session.SourceAppUserModelId; status=$playback.PlaybackStatus.ToString();
        position=[long][Math]::Max(0,[Math]::Min($length,$position)); duration=[long]$length;
        capabilities=@{ play=$c.IsPlayEnabled; pause=$c.IsPauseEnabled; toggle=$c.IsPlayPauseToggleEnabled;
            next=$c.IsNextEnabled; previous=$c.IsPreviousEnabled; seek=($c.IsPlaybackPositionEnabled -and $range.max -gt $range.min) } }
    if ($null -ne $knownArt -or $artwork) {
        try {
            $item = Await ($session.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])
            $result.title = $item.Title
            $result.artist = $item.Artist
            $result.album = $item.AlbumTitle
            $result.metadata = $true
            if (!$artwork) { return $result }
            if ($item.Thumbnail -and $knownArt.Count -eq 3 -and
                $knownArt[0] -ceq $item.Title -and $knownArt[1] -ceq $item.Artist -and $knownArt[2] -ceq $item.AlbumTitle) {
                return $result
            }
            $result.art = ''
            if ($item.Thumbnail) {
                $raw = Await ($item.Thumbnail.OpenReadAsync()) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
                $readStream = [System.IO.WindowsRuntimeStreamExtensions].GetMethods() | Where-Object {
                    $_.Name -eq 'AsStreamForRead' -and $_.GetParameters().Length -eq 1
                } | Select-Object -First 1
                $stream = $readStream.Invoke($null, @($raw))
                try {
                    $buffer = [byte[]]::new(1572865)
                    $read = 0
                    while ($read -lt $buffer.Length) {
                        $count = $stream.Read($buffer,$read,$buffer.Length-$read)
                        if ($count -eq 0) { break }
                        $read += $count
                    }
                    if ($read -gt 0 -and $read -le 1572864) { $result.art = [Convert]::ToBase64String($buffer,0,$read) }
                } finally { $stream.Dispose() }
            }
        } catch { $result.metadataError = $_.Exception.Message }
    }
    return $result
}

function PlatformFor([string]$identity, $platforms) {
    if (!$identity) { return }
    $segments = ($identity -split '[/\\]')[-1].ToLowerInvariant() -split '[.!_\-]'
    foreach ($platform in $platforms.PSObject.Properties) {
        if (@($platform.Value | Where-Object { $_ -in $segments }).Count) { return $platform.Name }
    }
}

function ClientInventory($platforms, $labels) {
    $start = @(Get-StartApps -ErrorAction Stop)
    $running = @(Get-Process | ForEach-Object { PlatformFor $_.ProcessName $platforms })
    $uninstall = @(Get-ItemProperty -Path @(
        'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
    ) -ErrorAction SilentlyContinue | Where-Object DisplayName)
    $apps = @{}
    foreach ($platform in $platforms.PSObject.Properties) {
        $key = $platform.Name
        $label = $labels.$key
        $entries = @($start | Where-Object { (PlatformFor $_.AppID $platforms) -eq $key })
        $launch = @($entries | Where-Object {
            $_.AppID -match '^[A-Za-z0-9._-]+![A-Za-z0-9._-]+$' -or
            ($_.AppID -like '*.exe' -and [IO.Path]::IsPathRooted($_.AppID) -and (Test-Path -LiteralPath $_.AppID -PathType Leaf))
        } | Select-Object -First 1)
        $registered = $entries.Count -gt 0 -or @($uninstall | Where-Object {
            ($_.DisplayName -replace '\s','') -like (($label -replace '\s','')+'*')
        }).Count -gt 0
        $apps[$key] = @{ installed=$registered -or ($key -in $running); running=$key -in $running;
            launch=if ($launch.Count) { $launch[0].AppID } else { '' } }
    }
    return $apps
}

$inputReader = [IO.StreamReader]::new([Console]::OpenStandardInput(), $utf8)
while ($null -ne ($line = $inputReader.ReadLine())) {
    try {
        $req = ConvertFrom-Json -InputObject $line
        if ($req.action -eq 'inventory') {
            $value = ClientInventory $req.identities $req.labels
        } else {
        $manager = Await ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
        $sessions = @($manager.GetSessions() | Where-Object { $_ -and $_.SourceAppUserModelId })
        switch ($req.action) {
            'list' { $value = @($sessions | ForEach-Object { $_.SourceAppUserModelId }); break }
            'snapshot' {
                $value = @{ sessions=@(); current=''; inventory=$null }
                $current = $manager.GetCurrentSession()
                if ($current) { $value.current = $current.SourceAppUserModelId }
                foreach ($session in $sessions) {
                    $source = $session.SourceAppUserModelId
                    $key = PlatformFor $source $req.identities
                    $entry = @{ source=$source; platform=$key }
                    if ($key) {
                        try {
                            $cached = @()
                            if ($req.known -and $req.known.PSObject.Properties[$source]) { $cached = $req.known.PSObject.Properties[$source].Value }
                            $entry.media = Describe $session ($key -in $req.artwork) $cached
                        } catch { $entry.error = $_.Exception.Message }
                    }
                    $value.sessions += $entry
                }
                if ($req.netease_window -and !($value.sessions | Where-Object { $_.platform -eq 'netease' -and $_.media })) {
                    $processes = @(Get-Process -Name cloudmusic -ErrorAction SilentlyContinue)
                    $windows = @($processes | Where-Object { $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle })
                    $uptime = [long][Environment]::TickCount -band 4294967295L
                    if ($windows.Count -eq 1) {
                        $value.netease_window = @{pid=$windows[0].Id; title=$windows[0].MainWindowTitle; uptime=$uptime}
                    } elseif ($req.netease_pid -and ($processes.Id -contains $req.netease_pid)) {
                        $value.netease_window = @{pid=$req.netease_pid; title=''; uptime=$uptime}
                    }
                }
                if ($req.inventory) {
                    try { $value.inventory = ClientInventory $req.identities $req.labels }
                    catch { $value.inventory_error = '无法读取 Windows 客户端登记。' }
                }
                break
            }
            default {
                $found = @($sessions | Where-Object { $_.SourceAppUserModelId -ceq $req.source })
                if ($found.Count -ne 1) { throw '指定媒体会话已关闭或来源有歧义。' }
                $session = $found[0]
                if ($req.action -eq 'read') { $value = Describe $session ([bool]$req.metadata) $req.known_art; break }
                $controls = $session.GetPlaybackInfo().Controls
                $op = switch ($req.action) {
                    'play' { if ($controls.IsPlayEnabled) { $session.TryPlayAsync() } }
                    'pause' { if ($controls.IsPauseEnabled) { $session.TryPauseAsync() } }
                    'toggle' { if ($controls.IsPlayPauseToggleEnabled) { $session.TryTogglePlayPauseAsync() } }
                    'next' { if ($controls.IsNextEnabled) { $session.TrySkipNextAsync() } }
                    'previous' { if ($controls.IsPreviousEnabled) { $session.TrySkipPreviousAsync() } }
                    'seek' {
                        if (!$controls.IsPlaybackPositionEnabled) { throw '当前 Windows 媒体会话未开放进度跳转。' }
                        $time = $session.GetTimelineProperties()
                        $range = GetSeekRange $time
                        $ms = 0L
                        if (![long]::TryParse([string]$req.position, [ref]$ms) -or
                            $ms -lt 0 -or $ms -gt ($time.EndTime - $time.StartTime).TotalMilliseconds) {
                            throw '目标进度超出当前曲目的时长。'
                        }
                        $requested = [long]($time.StartTime.Ticks + $ms * 10000L)
                        if ($range.max -le $range.min -or $requested -lt $range.min -or $requested -gt $range.max) {
                            throw '目标进度超出播放器允许的跳转范围。'
                        }
                        $session.TryChangePlaybackPositionAsync($requested)
                    }
                    default { throw '未知控制命令。' }
                }
                if (!$op -or !(Await $op ([bool]))) {
                    if ($req.action -eq 'seek') { throw '客户端拒绝了进度跳转，播放位置未确认。' }
                    throw '客户端未接受该操作，请检查当前播放状态。'
                }
                $value = $true
            }
        }
        }
        $reply = @{ ok=$true; value=$value }
    } catch { $reply = @{ ok=$false; error=$_.Exception.Message } }
    [Console]::WriteLine((ConvertTo-Json -InputObject $reply -Compress -Depth 6))
}
