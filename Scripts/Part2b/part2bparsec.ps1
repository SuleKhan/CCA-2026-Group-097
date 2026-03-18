# Run-PARSEC-Dynamic.ps1
param (
    [Parameter(Mandatory=$true)]
    [string]$Job,            # e.g., blackscholes, barnes, etc.
    
    [Parameter(Mandatory=$true)]
    [int]$Threads,           # number of threads
    
    [string]$Date = $(Get-Date -Format "yyyy-MM-dd_HH-mm")  # default to current date/time
)

# Paths
$JobYaml = ".\parsec-benchmarks\part2b\parsec-$Job.yaml"
$OutputDir = ".\Logs\Part2b\parsec-$Job"

# Ensure output directory exists
if (-not (Test-Path $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir -Force
}

# Compose thread/date label for log file
$ThreadLabel = "$Threads-thread-$Date"
$LogFile = Join-Path $OutputDir "parsec-$Job-$ThreadLabel.txt"

Write-Host "Running PARSEC job: $Job"
Write-Host "Threads: $Threads"
Write-Host "Log file: $LogFile"

# Create job
kubectl create -f $JobYaml

# Wait for pods to start
Write-Host "Waiting for job to finish..."
do {
    $status = kubectl get job parsec-$Job -o jsonpath="{.status.succeeded}" 
    if ($status -ne "1") {
        Write-Host "Job not finished yet, waiting 5 seconds..."
        Start-Sleep -Seconds 5
    }
} while ($status -ne "1")
Write-Host "Job completed!"

# Fetch pod names
# $pods = kubectl get pods --selector=job-name=parsec-$Job --output=jsonpath="{.items[*].metadata.name}"
# Write-Host "Found pods: $pods"

# Save logs
# foreach ($pod in $pods.Split(' ')) {
#     Write-Host "Fetching logs from pod $pod..."
#     kubectl logs $pod | Out-File $LogFile -Encoding utf8
# }

kubectl logs $(kubectl get pods --selector=job-name=parsec-$Job --output=jsonpath='{.items[*].metadata.name}') > $LogFile

# Delete the job
Write-Host "Deleting job $Job..."
kubectl delete jobs parsec-$Job

Write-Host "PARSEC job $Job completed. Logs saved to $LogFile"

Import-Module BurntToast
New-BurntToastNotification -Text "PARSEC Job Finished", "Logs are ready!"
[console]::beep(1000, 500)