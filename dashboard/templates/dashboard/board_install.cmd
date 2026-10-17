{% autoescape off %}@echo off
rem Enjoy English: interactive board setup. Double-click once on the board - after that it opens by itself.
rem Below the marker line is PowerShell: it is saved to %LOCALAPPDATA%\EnjoyBoard\board.ps1 and runs at sign-in
set "EE_SELF=%~f0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$t = [IO.File]::ReadAllText($env:EE_SELF); $p = Join-Path $env:TEMP 'EnjoyBoard-setup.ps1'; [IO.File]::WriteAllText($p, $t.Substring($t.IndexOf('#EE' + '-SCRIPT')), (New-Object Text.UTF8Encoding $true)); & $p -Install -Source $p"
if errorlevel 1 pause
exit /b
#EE-SCRIPT
# Enjoy English — интерактивная доска (dashboard/boards.py, board_install).
#   -Install (из скачанного файла): автозапуск при входе в Windows, ярлык на рабочем столе, сразу открывает доску.
#   Без параметров (при включении): держит страницу доски открытой в отдельном окне Chrome — открывает
#   снова, если окно закрыли или страница не загрузилась (сеть ещё не поднялась, сервер перезапускался),
#   не даёт компьютеру уснуть и выводит окно доски вперёд, когда учитель подключился или что-то показал
#   (страница доски дописывает к заголовку « #N»).
param([switch]$Install, [string]$Source = '')

$BoardUrl = '{{ url }}'
$BoardName = '{{ name }}'
$Dir = Join-Path $env:LOCALAPPDATA 'EnjoyBoard'
$LauncherPath = Join-Path $Dir 'board.ps1'
$ProfileDir = Join-Path $Dir 'chrome'
$LogFile = Join-Path $Dir 'board.log'
$TitleRe = 'Enjoy English(?: #(\d+))?$'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$BrowserArgs = @(
    ('--user-data-dir="' + $ProfileDir + '"'), ('--app=' + $BoardUrl), '--start-fullscreen',
    '--autoplay-policy=no-user-gesture-required', '--disable-popup-blocking', '--no-first-run', '--no-default-browser-check',
    '--hide-crash-restore-bubble', '--disable-session-crashed-bubble', '--noerrdialogs', '--disable-pinch',
    '--overscroll-history-navigation=0', '--disable-features=Translate',
    '--disable-background-timer-throttling', '--disable-renderer-backgrounding', '--disable-backgrounding-occluded-windows'
)

$WinApi = @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

public static class EEWin {
    delegate bool EnumProc(IntPtr h, IntPtr l);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc f, IntPtr l);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] static extern bool IsIconic(IntPtr h);
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr h, int cmd);
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] static extern bool BringWindowToTop(IntPtr h);
    [DllImport("user32.dll")] static extern void SwitchToThisWindow(IntPtr h, bool alt);
    [DllImport("user32.dll")] static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
    [DllImport("kernel32.dll")] static extern uint SetThreadExecutionState(uint flags);

    // Видимые окна этих процессов: "handle|заголовок"
    public static string[] Windows(int[] pids) {
        List<string> found = new List<string>();
        EnumWindows(delegate (IntPtr h, IntPtr l) {
            uint pid;
            GetWindowThreadProcessId(h, out pid);
            if (Array.IndexOf(pids, (int)pid) >= 0 && IsWindowVisible(h)) {
                StringBuilder title = new StringBuilder(512);
                GetWindowText(h, title, title.Capacity);
                found.Add(h.ToInt64().ToString() + "|" + title.ToString());
            }
            return true;
        }, IntPtr.Zero);
        return found.ToArray();
    }

    // Вперёд, даже если сейчас открыта другая программа: Windows пускает вперёд после нажатия клавиши (Alt)
    public static void Raise(IntPtr h) {
        if (IsIconic(h)) ShowWindow(h, 9);
        if (GetForegroundWindow() == h) return;
        keybd_event(0x12, 0, 0, UIntPtr.Zero);
        keybd_event(0x12, 0, 2, UIntPtr.Zero);
        BringWindowToTop(h);
        SetForegroundWindow(h);
        if (GetForegroundWindow() != h) SwitchToThisWindow(h, true);
    }

    public static void Minimize(IntPtr h) { ShowWindow(h, 6); }

    // Не засыпать и не гасить экран, пока работает запуск доски
    public static void KeepAwake() { SetThreadExecutionState(0x80000003u); }
}
'@

function Write-Log([string]$Text) {
    try {
        if ((Test-Path -LiteralPath $LogFile) -and (Get-Item -LiteralPath $LogFile).Length -gt 262144) { Remove-Item -LiteralPath $LogFile -Force }
        Add-Content -LiteralPath $LogFile -Value ((Get-Date -Format 's') + ' ' + $Text) -Encoding UTF8 -ErrorAction Stop
    } catch { }
}

function Find-Browser {
    $paths = @(
        ($env:ProgramFiles + '\Google\Chrome\Application\chrome.exe'),
        (${env:ProgramFiles(x86)} + '\Google\Chrome\Application\chrome.exe'),
        ($env:LOCALAPPDATA + '\Google\Chrome\Application\chrome.exe')
    )
    foreach ($key in @('HKCU:\Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe',
                       'HKLM:\Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe')) {
        try { $paths += (Get-ItemProperty -LiteralPath $key -ErrorAction Stop).'(default)' } catch { }
    }
    # Нет Chrome — Microsoft Edge (тот же движок, есть в каждой Windows)
    $paths += @((${env:ProgramFiles(x86)} + '\Microsoft\Edge\Application\msedge.exe'), ($env:ProgramFiles + '\Microsoft\Edge\Application\msedge.exe'))
    foreach ($p in $paths) {
        if ($p -and (Test-Path -LiteralPath $p)) { return $p }
    }
    return $null
}

# Процессы Chrome с профилем доски (у обычного Chrome на этом компьютере профиль другой — его не трогаем)
function Get-BoardPids([string]$Marker = $ProfileDir) {
    try {
        Get-CimInstance Win32_Process -Filter "Name='chrome.exe' OR Name='msedge.exe'" -ErrorAction Stop |
            Where-Object { $_.CommandLine -and $_.CommandLine.IndexOf($Marker, [StringComparison]::OrdinalIgnoreCase) -ge 0 } |
            ForEach-Object { [int]$_.ProcessId }
    } catch { }
}

function Stop-Board([string]$Marker = $ProfileDir) {
    foreach ($p in @(Get-BoardPids $Marker)) { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue }
}

function Test-Server {
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $BoardUrl -Method Head -TimeoutSec 10 | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Wait-Server([int]$Seconds) {
    $until = (Get-Date).AddSeconds($Seconds)
    while (-not (Test-Server)) {
        if ((Get-Date) -gt $until) { Write-Log 'server is not answering, opening anyway'; return }
        Start-Sleep -Seconds 3
    }
}

function Start-Board {
    Wait-Server 120
    $browser = Find-Browser
    if (-not $browser) { Write-Log 'Chrome not found'; Start-Sleep -Seconds 30; return }
    # Chrome выключили кнопкой или он упал — без этого он покажет «Восстановить страницы?»
    $prefs = Join-Path $ProfileDir 'Default\Preferences'
    if (Test-Path -LiteralPath $prefs) {
        try {
            $text = [IO.File]::ReadAllText($prefs)
            $fixed = $text.Replace('"exit_type":"Crashed"', '"exit_type":"Normal"').Replace('"exited_cleanly":false', '"exited_cleanly":true')
            if ($fixed -ne $text) { [IO.File]::WriteAllText($prefs, $fixed) }
        } catch { }
    }
    Write-Log ('open ' + $browser)
    Start-Process -FilePath $browser -ArgumentList $BrowserArgs | Out-Null
}

function Find-BoardWindow {
    if ($script:Pids.Count -eq 0) { return $null }
    foreach ($row in [EEWin]::Windows([int[]]$script:Pids)) {
        $i = $row.IndexOf('|')
        $m = [regex]::Match($row.Substring($i + 1), $TitleRe)
        if ($m.Success) {
            $seq = 0
            if ($m.Groups[1].Success) { $seq = [int]$m.Groups[1].Value }
            return New-Object PSObject -Property @{ Handle = [IntPtr][long]$row.Substring(0, $i); Seq = $seq }
        }
    }
    return $null
}

function Start-Launcher {
    Add-Type -TypeDefinition $WinApi
    $created = $false
    $script:Mutex = New-Object System.Threading.Mutex($true, 'Local\EnjoyBoardLauncher', [ref]$created)
    $script:Pids = @(Get-BoardPids)
    if (-not $created) {
        # Уже работает (нажали ярлык на рабочем столе) — просто показать доску
        $w = Find-BoardWindow
        if ($w) { [EEWin]::Raise($w.Handle) }
        return
    }
    [EEWin]::KeepAwake()
    Write-Log 'launcher started'
    if ($script:Pids.Count -eq 0) { Start-Board; $script:Pids = @(Get-BoardPids) }
    $lastSeen = Get-Date
    $lastSeq = -1
    $wasOpen = $false
    $hide = $false
    while ($true) {
        try {
            $w = Find-BoardWindow
            if ($w) {
                $lastSeen = Get-Date
                if ($hide) { [EEWin]::Minimize($w.Handle); $hide = $false }
                if ($lastSeq -ge 0 -and $w.Seq -gt 0 -and $w.Seq -ne $lastSeq) { [EEWin]::Raise($w.Handle) }
                $lastSeq = $w.Seq
                $wasOpen = $true
            } else {
                $script:Pids = @(Get-BoardPids)
                if ($script:Pids.Count -eq 0) {
                    # Окно доски закрыли (или Chrome упал) — открываем снова. Закрыл человек — окно ждёт
                    # свёрнутым в панели задач и само выйдет вперёд, когда учитель подключится
                    Write-Log 'board window closed, opening again'
                    $hide = $wasOpen
                    $wasOpen = $false
                    $lastSeq = -1
                    Start-Board
                    $script:Pids = @(Get-BoardPids)
                    $lastSeen = Get-Date
                } elseif (((Get-Date) - $lastSeen).TotalSeconds -gt 45) {
                    # Chrome открыт, а страницы доски нет (при включении не было сети, ошибка сервера) — заново
                    if (Test-Server) {
                        Write-Log 'board page is not open, restarting'
                        Stop-Board
                        Start-Sleep -Seconds 2
                        $lastSeq = -1
                        Start-Board
                        $script:Pids = @(Get-BoardPids)
                    }
                    $lastSeen = Get-Date
                }
            }
        } catch {
            Write-Log ('error: ' + $_.Exception.Message)
            Start-Sleep -Seconds 5
        }
        Start-Sleep -Milliseconds 1500
    }
}

function Install-Board {
    $ErrorActionPreference = 'Stop'
    Write-Host ''
    Write-Host ('  Enjoy English: настраиваем доску «' + $BoardName + '»') -ForegroundColor Cyan
    $browser = Find-Browser
    if (-not $browser) { throw 'не нашли Google Chrome. Установите Chrome и запустите этот файл ещё раз.' }
    New-Item -ItemType Directory -Force -Path $Dir | Out-Null

    # Ставим повторно (новая ссылка, другая доска) — прежний запуск и его окно закрываем
    Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -and $_.CommandLine.IndexOf($LauncherPath, [StringComparison]::OrdinalIgnoreCase) -ge 0 } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Stop-Board
    Stop-Board 'C:\EnjoyBoard'                  # окно от ярлыка прошлой версии
    Copy-Item -LiteralPath $Source -Destination $LauncherPath -Force

    $shell = New-Object -ComObject WScript.Shell
    $startup = [Environment]::GetFolderPath('Startup')
    # Ярлык прошлой версии (Chrome с профилем C:\EnjoyBoard) больше не нужен — иначе откроются два окна
    foreach ($f in @(Get-ChildItem -LiteralPath $startup -Filter '*.lnk' -ErrorAction SilentlyContinue)) {
        try {
            if ($shell.CreateShortcut($f.FullName).Arguments -like '*C:\EnjoyBoard*') { Remove-Item -LiteralPath $f.FullName -Force }
        } catch { }
    }
    $ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $run = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $LauncherPath + '"'
    $places = @((Join-Path $startup 'Enjoy English board.lnk'))
    try { $places += (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Доска Enjoy English.lnk') } catch { }
    foreach ($place in $places) {
        try {
            $lnk = $shell.CreateShortcut($place)
            $lnk.TargetPath = $ps
            $lnk.Arguments = $run
            $lnk.WorkingDirectory = $Dir
            $lnk.WindowStyle = 7
            $lnk.IconLocation = $browser + ',0'
            $lnk.Description = 'Enjoy English board'
            $lnk.Save()
        } catch {
            if ($place -like ($startup + '*')) { throw }
        }
    }
    Start-Process -FilePath $ps -ArgumentList $run -WindowStyle Hidden

    Write-Host ''
    Write-Host '  Готово! Через несколько секунд на доске откроется страница доски.' -ForegroundColor Green
    Write-Host '  Дальше она открывается сама при каждом включении и всегда готова к подключению.'
    Write-Host '  Если окно доски закрыть, оно свернётся в панель задач и само выйдет вперёд, когда учитель'
    Write-Host '  подключится. Открыть вручную — ярлык «Доска Enjoy English» на рабочем столе.'
    Write-Host ''
    Write-Host '  Это окно закроется само.'
    Start-Sleep -Seconds 12
}

if ($Install) {
    try {
        Install-Board
    } catch {
        Write-Host ''
        Write-Host ('  Не получилось: ' + $_.Exception.Message) -ForegroundColor Red
        Read-Host '  Нажмите Enter, чтобы закрыть' | Out-Null
        exit 1
    }
} else {
    try { Start-Launcher } catch { Write-Log ('stopped: ' + $_.Exception.Message); exit 1 }
}
{% endautoescape %}
