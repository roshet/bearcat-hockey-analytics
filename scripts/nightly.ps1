# The scheduled morning run (M7): Task Scheduler starts this; `bca nightly` does the work.
# Its log is data/logs/nightly-YYYY-MM-DD.log; each run is a row in pipeline_runs.
# Exit codes: 0 done (or nothing to deploy), 1 a step failed, 3 the canary tripped.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

# A run started on wake can beat Wi-Fi back (Nov 4 and 6: "getaddrinfo failed" in 15s of
# retries). Wait up to 3 minutes for the feed's name to resolve: a DNS lookup only, never a
# request to the feed. If it still fails, run anyway so the failure lands in the log.
$deadline = (Get-Date).AddMinutes(3)
while ((Get-Date) -lt $deadline) {
    try { [void][System.Net.Dns]::GetHostAddresses("lscluster.hockeytech.com"); break }
    catch { Start-Sleep -Seconds 5 }
}

& .\.venv\Scripts\python.exe -m bca.cli nightly
exit $LASTEXITCODE
