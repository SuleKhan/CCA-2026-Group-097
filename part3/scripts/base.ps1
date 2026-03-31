# ========================================
# Part 3 Automation Script - PowerShell
# ========================================

# Exit on errors
$ErrorActionPreference = "Stop"

Write-Host "Starting Part 3 experiment (dynamic nodeSelector & CPU overrides)" -ForegroundColor Cyan

# -----------------------------
# CONFIGURATION
# -----------------------------
$NODE_A_LABEL = "node-a-8core"    # e.g., node-a-8core-dcvv
$NODE_B_LABEL = "node-b-4core"    # e.g., node-b-4core-kcnn

# Batch job YAMLs
$HEAVY_JOBS = @(
    "../parsec-benchmarks/part2a/parsec-blackscholes.yaml",
    "../parsec-benchmarks/part2a/parsec-radix.yaml",
    "../parsec-benchmarks/part2a/parsec-canneal.yaml"
)
$LIGHT_JOBS = @(
    "../parsec-benchmarks/part2a/parsec-freqmine.yaml",
    "../parsec-benchmarks/part2a/parsec-streamcluster.yaml",
    "../parsec-benchmarks/part2a/parsec-barnes.yaml",
    "../parsec-benchmarks/part2a/parsec-vips.yaml"
)

# Results directory
$resultsDir = ".\results\"
if (-not (Test-Path $resultsDir)) {
    New-Item -ItemType Directory -Path $resultsDir | Out-Null
}

# -----------------------------
# 0. Clean any old jobs/pods
# -----------------------------
kubectl delete pods --all
kubectl delete jobs --all

# -----------------------------
# 1. Start Memcached dynamically
# -----------------------------
Write-Host "Starting Memcached on Node A..."

# Set CPU values dynamically
$cpuRequest = "2"
$cpuLimit = "3"

# Build YAML with CPU values
$yaml = @"
apiVersion: v1
kind: Pod
metadata:
  name: some-memcached
  labels:
    name: some-memcached
spec:
  containers:
    - image: anakli/memcached:t1
      name: memcached
      imagePullPolicy: Always
      command: ["/bin/sh"]
      args: ["-c", "taskset -c 0 ./memcached -t 1 -u memcache"]
      resources:
        requests:
          cpu: "$cpuRequest"
        limits:
          cpu: "$cpuLimit"
  nodeSelector:
    cca-project-nodetype: "$NODE_A_LABEL"
"@

# Save to file (optional)
$yamlPath = ".\some-memcached.yaml"
$yaml | Set-Content $yamlPath

# Apply it
kubectl apply -f $yamlPath

Write-Host "Waiting 30s for Memcached to stabilize..."
Start-Sleep -Seconds 30

# -----------------------------
# 2. Start mcperf manually
# -----------------------------
Write-Host "Start mcperf now on client agents (30K QPS)"
Read-Host "Press Enter when mcperf is running..."

# -----------------------------
# 3. Launch batch jobs dynamically with CPU & Node selector
# -----------------------------
function Launch-Job {
    param(
        [string]$YamlFile,
        [string]$NodeLabel,
        [string]$CPURequest,
        [string]$CPULimit
    )

    # Load the template YAML
    $baseYaml = Get-Content $YamlFile -Raw
    $yamlObj = ConvertFrom-Yaml $baseYaml

    # Extract job name
    $jobName = $yamlObj.metadata.name
    Write-Host "Launching job $jobName on node $NodeLabel with CPU $CPURequest-$CPULimit"

    # Build YAML with dynamic values
    $yaml = @"
apiVersion: batch/v1
kind: Job
metadata:
  name: $jobName
spec:
  template:
    metadata:
      labels:
        name: $jobName
    spec:
      containers:
        - name: benchmark
          image: $($yamlObj.spec.template.spec.containers[0].image)
          imagePullPolicy: Always
          command: $($yamlObj.spec.template.spec.containers[0].command | ConvertTo-Json)
          args: $($yamlObj.spec.template.spec.containers[0].args | ConvertTo-Json)
          resources:
            requests:
              cpu: "$CPURequest"
            limits:
              cpu: "$CPULimit"
      nodeSelector:
        cca-project-nodetype: "$NodeLabel"
      restartPolicy: Never
"@

    # Save to temp file
    $tempYamlPath = ".\temp-job-$jobName.yaml"
    $yaml | Set-Content $tempYamlPath

    # Apply it
    kubectl apply -f $tempYamlPath

    # Clean up temp file
    Remove-Item $tempYamlPath
}

Write-Host "Launching batch jobs in waves..."

# Wave 1
Launch-Job $HEAVY_JOBS[0] $NODE_A_LABEL 2 3
Launch-Job $LIGHT_JOBS[0] $NODE_B_LABEL 1 2
Start-Sleep -Seconds 60

# Wave 2
Launch-Job $HEAVY_JOBS[1] $NODE_A_LABEL 2 3
Launch-Job $LIGHT_JOBS[1] $NODE_B_LABEL 1 2
Launch-Job $LIGHT_JOBS[2] $NODE_B_LABEL 1 2
Start-Sleep -Seconds 60

# Wave 3
Launch-Job $HEAVY_JOBS[2] $NODE_A_LABEL 2 3
Launch-Job $LIGHT_JOBS[3] $NODE_B_LABEL 1 2

# -----------------------------
# 4. Wait for all jobs to complete
# -----------------------------
Write-Host "Waiting for all jobs to complete..."
kubectl wait --for=condition=complete job --all --timeout=3600s
Write-Host "All jobs completed!" -ForegroundColor Green

# -----------------------------
# 5. Collect results
# -----------------------------
$resultsFile = Join-Path $resultsDir ("results_{0}.txt" -f (Get-Date -Format "yyyyMMdd_HHmmss"))
kubectl get pods -o json > "$resultsFile.json"
Write-Host "Processing results..."
python3 get_time.py "$resultsFile.json" > "$resultsFile"

# Clean up
kubectl delete pods --all
kubectl delete jobs --all

# -----------------------------
# 6. Done alert
# -----------------------------
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.MessageBox]::Show("Part 3 experiment completed!", "Done")

# Play beep
[console]::beep(800, 500)
[console]::beep(1000, 500)

Write-Host "Results saved to $resultsFile"