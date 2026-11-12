# Builds and checks every robot. Called by .github/workflows/build-exe.yml on a
# Windows machine; change THIS file (not the workflow) to add or change a build.
#
#   1. install, 2. run all tests, 3. build each exe, 4. start each exe once with
#   --selftest (it opens its window, checks itself, closes, and writes a report).

$ErrorActionPreference = "Stop"
$ext = if ($IsWindows -or $env:OS -eq "Windows_NT") { ".exe" } else { "" }

function Step([string]$What, [scriptblock]$Block) {
    Write-Host "`n== $What"
    & $Block
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)" }
}

Step "Install" { python -m pip install --quiet -r requirements.txt pyinstaller pillow pytest }
Step "Tests" { python -m pytest -q tests }
Step "Self-check excel_reader" { python excel_reader.py }
Step "Self-check stake_rules" { python stake_rules.py }
Step "Self-check utils" { python utils.py }

# name, entry script, extra PyInstaller options, self-test report file
$builds = @(
    @{ Name = "InbetComboTicketBot"; Entry = "dashboard.py"; Report = "selftest.txt";
       Options = @("--collect-submodules", "qrcode", "--hidden-import", "PIL._tkinter_finder") },
    @{ Name = "Scraper8888"; Entry = "scraper_8888/scraper_8888_ui.py"; Report = "selftest_8888.txt";
       Options = @("--paths", "scraper_8888", "--collect-all", "playwright") }
)

foreach ($b in $builds) {
    Step "Build $($b.Name)$ext" {
        python -m PyInstaller --noconfirm --clean --onefile --windowed --name $b.Name @($b.Options) $b.Entry
    }
}

foreach ($b in $builds) {
    $exe = Join-Path "dist" "$($b.Name)$ext"
    Write-Host "`n== Self-test $($b.Name)$ext"
    $p = Start-Process -FilePath $exe -ArgumentList "--selftest" -Wait -PassThru
    $report = Join-Path "dist" $b.Report
    if (-not (Test-Path $report)) { throw "$($b.Name): no self-test report written" }
    Write-Host (Get-Content $report)
    if ($p.ExitCode -ne 0) { throw "$($b.Name) self-test failed" }
}
Write-Host "`nAll robots built and checked."
