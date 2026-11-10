<#
Gap Court live runs, for the operator's own PowerShell window.

Each session first runs `gapcourt preflight` (which Python, numpy, anthropic, git; it names the install line if something is missing).
Before a live session, set ANTHROPIC_API_KEY in this window yourself
(a hidden-prompt helper, scripts\walter_key_window.ps1, is kept out of the public export). Never put the key in a file or a chat. This script only checks that
the variable is set; it never prints, stores or sets it. Every call is checked against the per-run cap and the total cap in
config/run_config.json before it is sent; a cap stops the session.

Run from the repository root. Two commands cover everything:

  .\scripts\walter_live_runs.ps1 -Session smoke   # builds the KB if missing, probe if not yet done, S-03 once plus dev cases
                                                  # PC01, PC03, PC11 (unscored), then refreshes the report draft
  .\scripts\walter_live_runs.ps1 -Session full    # AFTER the label freeze: dev cases, held-out once, S-01 x3 and S-02,
                                                  # then `gapcourt score` and the report draft. Refused without evals/FREEZE.json.

Single steps, if you want them:
  -Session probe     tiny calls that confirm the accepted request settings
  -Session dev       every dev case
  -Session heldout   held-out cases once; refused without the freeze
  -Session repeats   S-01 three times and S-02 once; refused without the freeze
  -Session report    rebuild offline facts and the report draft from what is already saved (no calls)
  -Session status    USD spent so far and exactly what the cost caps do and do not hold to; no calls

The report draft (docs/REPORT_DRAFT_5PAGE.md) fills itself from the saved runs after every live session: each number is tagged
[measured], [offline] or [pending-live]. Add -Rehearsal to any session to run it offline with the labelled test double: no key, no
network, the output is stamped TEST DOUBLE and is never counted by gapcourt score or the report. Rehearsal output goes to runs_rehearsal.
Keep the console output of a session; it contains no key.
#>
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('probe', 'smoke', 'dev', 'heldout', 'repeats', 'full', 'report', 'status')]
    [string]$Session,
    [switch]$Rehearsal,
    [string]$Kb = 'build/kb',
    [string]$Labels = 'evals/labels_v1.jsonl',
    [string]$Freeze = 'evals/FREEZE.json',
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$env:PYTHONPATH = Join-Path $repo 'src'
$env:PYTHONUTF8 = '1'
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$runsRoot = if ($Rehearsal) { 'runs_rehearsal' } else { 'runs' }
$reportOut = if ($Rehearsal) { 'runs_rehearsal/REPORT_DRAFT_REHEARSAL.md' } else { 'docs/REPORT_DRAFT_5PAGE.md' }
$extra = @('--runs-root', $runsRoot, '--freeze', $Freeze)
if ($Rehearsal) { $extra += '--test-double'; Write-Host 'REHEARSAL: test double, no key, no network, not a result' }

# Run Python and return its exit code. Under Windows PowerShell 5.1 a native command's stderr line becomes an error
# record, which with $ErrorActionPreference = 'Stop' would end the script (for instance at the cap-reached message when the
# output is piped to Tee-Object). So stderr is folded into plain text here and the exit code is checked by the caller.
function Invoke-Py([string[]]$PyArgs) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Python @PyArgs 2>&1 | ForEach-Object {
            if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.Exception.Message } else { "$_" }
        } | Out-Host
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $old
    }
}

function Show-Cost([switch]$Full) {
    $a = @('-m', 'gapcourt', 'cost', '--runs-root', 'runs')
    if (-not $Full) { $a += '--short' }
    [void](Invoke-Py $a)
}

function Invoke-Gc([string[]]$GcArgs) {
    Write-Host ('> gapcourt ' + ($GcArgs -join ' '))
    $code = Invoke-Py (@('-m', 'gapcourt') + $GcArgs)
    if (-not $Rehearsal) { Show-Cost }
    if ($code -eq 3) { Write-Host 'A cost cap was reached; the session stops here. Nothing past the cap was sent.'; exit 3 }
    if ($code -ne 0) { Write-Host "gapcourt exited with code $code; the session stops here."; exit $code }
}

function Next-RunDir([string]$Base) {
    $i = 1
    while (Test-Path (Join-Path $Base "r$i")) { $i++ }
    return (Join-Path $Base "r$i")
}

function Update-Report {
    # Refresh the report draft from the saved runs. Never fails the session: the runs are already saved.
    try {
        if (-not $Rehearsal) { [void](Invoke-Py @('scripts/offline_facts.py')) }
        $res = if (Test-Path 'results/heldout/results.json') { 'results/heldout' } else { 'results/none' }
        $c = Invoke-Py @('-m', 'gapcourt', 'report-fill', '--runs', $runsRoot, '--kb', $Kb, '--results', $res, '--out', $reportOut)
        if ($c -ne 0) { Write-Host "report-fill exited with code $c. The runs are saved; run -Session report later." }
    } catch {
        Write-Host "report-fill did not run: $($_.Exception.Message). The runs are saved; run -Session report later."
    }
}

function Ensure-Kb {
    if (-not (Test-Path (Join-Path $Kb 'KB_MANIFEST.json'))) {
        if (-not (Test-Path 'corpus_local/uae')) {
            Write-Host 'No UAE texts in corpus_local/uae. From a fresh clone run, in this order:'
            Write-Host '  python -m gapcourt kb-fetch        (downloads each raw file from its official address and checks its hash)'
            Write-Host '  python -m gapcourt prepare --rebuild-all'
            Write-Host 'Entries kb-fetch marks manual need a browser step (it prints it).'
            exit 2
        }
        Write-Host "No KB at $Kb; building it from the corpus (lexical embedder)."
        $c = Invoke-Py @('-m', 'gapcourt', 'build-kb', '--root', 'corpus', '--root', 'corpus_local', '--out', $Kb)
        if ($c -ne 0) { Write-Host 'build-kb failed; run `gapcourt kb-fetch` then `gapcourt prepare --rebuild-all` first.'; exit 2 }
    }
}

function Require-Freeze {
    if (-not (Test-Path $Freeze) -or -not (Test-Path $Labels)) {
        Write-Host "No freeze record at $Freeze or no approved label file at $Labels. Freeze the labels first:"
        Write-Host '  python -m gapcourt freeze --labels evals/labels_v1.jsonl --kb build/kb --scenario scenarios/S-01.json --scenario scenarios/S-02.json --reviewer "<your name>" --attest-independent'
        Write-Host '  (review each label from its source passage, without looking at model answers, before you run this)'
        exit 2
    }
}

if ($Session -eq 'status') { Show-Cost -Full; exit 0 }
if ($Session -eq 'report') { Update-Report; exit 0 }

# What this Python can do, before any folder is written or any call is made (names the install line if something is missing).
$pre = @('-m', 'gapcourt', 'preflight'); if ($Rehearsal) { $pre += '--test-double' }
if ((Invoke-Py $pre) -ne 0) { exit 2 }

if (-not $Rehearsal -and -not $env:ANTHROPIC_API_KEY) {
    Write-Host 'ANTHROPIC_API_KEY is not set in this window. Set it yourself first (never in a file), or add -Rehearsal.'
    exit 2
}
if ($Session -ne 'probe') { Ensure-Kb }
$devLabels = if (Test-Path $Labels) { $Labels } else { 'evals/pilot_cases_DRAFT.jsonl' }

function Run-Probe {
    Invoke-Gc (@('probe-model') + $extra)
}
function Run-Smoke {
    # Smoke uses only an unscored scenario (S-03) and designated dev cases, so no held-out case or scored
    # scenario is seen before the freeze.
    Invoke-Gc (@('run-scenario', '--kb', $Kb, '--scenario', 'scenarios/S-03.json',
                 '--out', "$runsRoot/smoke/S-03-$stamp") + $extra)
    Invoke-Gc (@('run-cases', '--kb', $Kb, '--labels', $devLabels, '--split', 'dev',
                 '--case', 'PC01', '--case', 'PC03', '--case', 'PC11', '--out', "$runsRoot/smoke/dev3-$stamp") + $extra)
}
function Run-Dev {
    Invoke-Gc (@('run-cases', '--kb', $Kb, '--labels', $devLabels, '--split', 'dev', '--out', "$runsRoot/dev/$stamp") + $extra)
    # S-04 is a development demonstration of one revision: not frozen, run once, reported separately
    Invoke-Gc (@('run-scenario', '--kb', $Kb, '--scenario', 'scenarios/S-04.json', '--out', (Next-RunDir "$runsRoot/demo/S-04")) + $extra)
}
function Run-Heldout {
    Require-Freeze
    Invoke-Gc (@('run-cases', '--kb', $Kb, '--labels', $Labels, '--split', 'heldout', '--out', "$runsRoot/heldout/$stamp") + $extra)
}
function Run-Repeats {
    Require-Freeze
    foreach ($n in 1..3) {
        Invoke-Gc (@('run-scenario', '--kb', $Kb, '--scenario', 'scenarios/S-01.json', '--out', (Next-RunDir "$runsRoot/S-01")) + $extra)
    }
    Invoke-Gc (@('run-scenario', '--kb', $Kb, '--scenario', 'scenarios/S-02.json', '--out', (Next-RunDir "$runsRoot/S-02")) + $extra)
}
function Run-Score {
    if ($Rehearsal) { Write-Host 'REHEARSAL: scoring skipped (test-double runs are never counted).'; return }
    Invoke-Gc @('score', '--kb', $Kb, '--labels', $Labels, '--runs', $runsRoot, '--split', 'heldout', '--out', 'results/heldout', '--freeze', $Freeze)
}

switch ($Session) {
    'probe' { Run-Probe }
    'smoke' {
        # The probe runs again unless a real probe on record accepted a configuration (a failed probe also leaves a file).
        if ($Rehearsal -or (Invoke-Py @('-m', 'gapcourt', 'probe-status', '--runs-root', 'runs')) -ne 0) { Run-Probe }
        Run-Smoke
        Update-Report
    }
    'dev' { Run-Dev; Update-Report }
    'heldout' { Run-Heldout; Update-Report }
    'repeats' { Run-Repeats; Update-Report }
    'full' {
        Require-Freeze
        Run-Dev
        Run-Heldout
        Run-Repeats
        Run-Score
        Update-Report
    }
}
Write-Host 'Session complete. Keep each digest.json separately from its log.jsonl.'
if (-not $Rehearsal) { Write-Host "Report draft: $reportOut (numbers tagged measured / offline / pending-live)." }
