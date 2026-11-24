$ErrorActionPreference = 'Stop'

$repoUrl = 'https://github.com/S1nuso1d/LocalAssistantRefactor.git'
$sourcePath = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$publishPath = Join-Path $env:TEMP ('LocalAssistant-master-publish-' + [guid]::NewGuid().ToString('N'))

function Invoke-Git {
    param(
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string[]]$GitArguments
    )

    & git -C $WorkingDirectory @GitArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Git command failed (exit code $LASTEXITCODE): git -C `"$WorkingDirectory`" $($GitArguments -join ' ')"
    }
}

try {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw 'Git was not found. Install Git for Windows and reopen PowerShell.'
    }

    $python = Join-Path $sourcePath '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        throw 'The project virtual environment is missing. Run setup.bat before publishing.'
    }

    Write-Host 'Running Python compilation checks...' -ForegroundColor Cyan
    & $python -m compileall -q main.py assistant tests tools
    if ($LASTEXITCODE -ne 0) { throw 'Python compilation checks failed.' }

    Write-Host 'Running the complete test suite (including audio-runtime tests)...' -ForegroundColor Cyan
    $previousQtPlatform = $env:QT_QPA_PLATFORM
    $previousAudioTests = $env:LOCALASSISTANT_FULL_AUDIO_TESTS
    $env:QT_QPA_PLATFORM = 'offscreen'
    $env:LOCALASSISTANT_FULL_AUDIO_TESTS = '1'
    & $python -m unittest discover -s tests -v
    $testExitCode = $LASTEXITCODE
    if ($null -eq $previousAudioTests) {
        Remove-Item Env:\LOCALASSISTANT_FULL_AUDIO_TESTS -ErrorAction SilentlyContinue
    } else {
        $env:LOCALASSISTANT_FULL_AUDIO_TESTS = $previousAudioTests
    }
    if ($null -eq $previousQtPlatform) {
        Remove-Item Env:\QT_QPA_PLATFORM -ErrorAction SilentlyContinue
    } else {
        $env:QT_QPA_PLATFORM = $previousQtPlatform
    }
    if ($testExitCode -ne 0) { throw "Tests failed (exit code $testExitCode). Nothing was published." }

    Write-Host 'Cloning the current master branch into a temporary folder...' -ForegroundColor Cyan
    & git clone --branch master --single-branch $repoUrl $publishPath
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not clone master. Check internet access and GitHub authentication.'
    }

    Write-Host 'Copying the unified LocalAssistant source into the clean clone...' -ForegroundColor Cyan
    $copyArgs = @(
        $sourcePath,
        $publishPath,
        '/MIR', '/R:2', '/W:1', '/NFL', '/NDL', '/NP',
        '/XD', '.git', '.venv', 'venv', '__pycache__', '.pytest_cache', '.mypy_cache', 'build', 'dist',
        '/XF', '.env', '*.sqlite3', '*.db', '*.log', '*.log.*', '*.pyc', 'settings.json'
    )
    & robocopy @copyArgs
    $copyCode = $LASTEXITCODE
    if ($copyCode -ge 8) {
        throw "Robocopy failed with exit code $copyCode. The temporary clone was kept at: $publishPath"
    }

    Invoke-Git -WorkingDirectory $publishPath -GitArguments @('add', '-A')
    & git -C $publishPath diff --cached --quiet
    $diffCode = $LASTEXITCODE
    if ($diffCode -eq 0) {
        Write-Host 'No changes to publish: master already contains this exact source snapshot.' -ForegroundColor Green
        Remove-Item -LiteralPath $publishPath -Recurse -Force
        exit 0
    }
    if ($diffCode -ne 1) {
        throw "Could not inspect staged changes (exit code $diffCode). Temporary clone: $publishPath"
    }

    Invoke-Git -WorkingDirectory $publishPath -GitArguments @(
        'commit', '-m', 'Publish unified LocalAssistant r23.2'
    )

    Write-Host 'Pushing the unified release to origin/master (no force push)...' -ForegroundColor Cyan
    Invoke-Git -WorkingDirectory $publishPath -GitArguments @('push', 'origin', 'master')

    Write-Host ''
    Write-Host 'SUCCESS: unified LocalAssistant r23.2 has been pushed to GitHub master.' -ForegroundColor Green
    Write-Host 'You can now clone or pull the master branch on your computer.' -ForegroundColor Green
    Remove-Item -LiteralPath $publishPath -Recurse -Force
}
catch {
    Write-Host ''
    Write-Host ('PUBLISH FAILED: ' + $_.Exception.Message) -ForegroundColor Red
    if (Test-Path -LiteralPath $publishPath) {
        Write-Host ('The temporary clone was kept for diagnosis: ' + $publishPath) -ForegroundColor Yellow
    }
    Write-Host 'No force push was used; the existing master history was not overwritten.' -ForegroundColor Yellow
    exit 1
}
