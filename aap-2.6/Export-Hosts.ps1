<#
.SYNOPSIS
    Dump every host in AAP 2.6 to CSV, with the inventory and organization
    each host belongs to.

.DESCRIPTION
    Pages through the controller /hosts/ and /organizations/ APIs and writes
    one row per host. Run as a system administrator or auditor to get
    everything. Works on Windows PowerShell 5.1 and PowerShell 7+.

    Token: create it in the platform gateway (User > API tokens, or POST /api/gateway/v1/tokens/).

    Hosts in constructed inventories are copies of their source hosts, so they
    appear twice. Filter on inventory_kind if you don't want them.

.EXAMPLE
    $env:AAP_HOST  = 'https://aap.example.com'
    $env:AAP_TOKEN = 'xxxxxxxx'
    .\Export-Hosts.ps1 -OutFile .\hosts.csv

.EXAMPLE
    # Basic auth, self-signed cert
    .\Export-Hosts.ps1 -AapHost https://aap.lab -Credential (Get-Credential) -SkipCertificateCheck
#>
[CmdletBinding()]
param(
    [string]$AapHost = $env:AAP_HOST,
    [string]$Token = $env:AAP_TOKEN,
    [pscredential]$Credential,
    [string]$ApiPrefix = '/api/controller/v2',
    [int]$PageSize = 200,
    [string]$OutFile = '.\hosts.csv',
    [switch]$SkipCertificateCheck
)

$ErrorActionPreference = 'Stop'

if (-not $AapHost) { throw 'Set -AapHost or $env:AAP_HOST.' }
$base = $AapHost.TrimEnd('/')

# --- Auth header -----------------------------------------------------------
if ($Token) {
    $headers = @{ Authorization = "Bearer $Token" }
} else {
    if (-not $Credential) { $Credential = Get-Credential -Message "AAP credentials for $base" }
    $pair = '{0}:{1}' -f $Credential.UserName, $Credential.GetNetworkCredential().Password
    $headers = @{ Authorization = 'Basic ' + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($pair)) }
}

# --- TLS handling (PS 7 has a switch; 5.1 needs the global callback) -------
$irmExtra = @{}
if ($PSVersionTable.PSVersion.Major -ge 6) {
    if ($SkipCertificateCheck) { $irmExtra.SkipCertificateCheck = $true }
} else {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    if ($SkipCertificateCheck) {
        [Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
    }
}

function Get-AllPages([string]$Url) {
    $all = New-Object System.Collections.Generic.List[object]
    while ($Url) {
        $resp = Invoke-RestMethod -Uri $Url -Headers $headers -Method Get @irmExtra
        foreach ($r in $resp.results) { $all.Add($r) }
        Write-Progress -Activity 'Fetching' -Status "$($all.Count) / $($resp.count)"
        if ($resp.next) {
            $Url = if ($resp.next -match '^https?://') { $resp.next } else { "$base$($resp.next)" }
        } else {
            $Url = $null
        }
    }
    Write-Progress -Activity 'Fetching' -Completed
    return ,$all
}

# --- Organizations (id -> name) --------------------------------------------
$orgNames = @{}
foreach ($o in (Get-AllPages "$base$ApiPrefix/organizations/?page_size=$PageSize")) {
    $orgNames[[int]$o.id] = $o.name
}

# --- Hosts -----------------------------------------------------------------
$hosts = Get-AllPages "$base$ApiPrefix/hosts/?page_size=$PageSize&order_by=inventory__name,name"

$rows = foreach ($h in $hosts) {
    $inv   = $h.summary_fields.inventory
    $job   = $h.summary_fields.last_job
    $orgId = $inv.organization_id
    $kind  = if ($inv.kind) { $inv.kind } else { 'standard' }
    [pscustomobject]@{
        host_id             = $h.id
        host_name           = $h.name
        enabled             = $h.enabled
        inventory_id        = $h.inventory
        inventory_name      = $inv.name
        inventory_kind      = $kind
        organization_id     = $orgId
        organization        = if ($null -ne $orgId) { $orgNames[[int]$orgId] } else { '' }
        description         = $h.description
        instance_id         = $h.instance_id
        has_active_failures = $h.has_active_failures
        last_job_id         = $job.id
        last_job_status     = $job.status
        last_job_finished   = $job.finished
        created             = $h.created
        modified            = $h.modified
    }
}

$rows | Export-Csv -Path $OutFile -NoTypeInformation -Encoding UTF8
$invCount = ($rows | Select-Object -ExpandProperty inventory_id -Unique | Measure-Object).Count
Write-Host "Wrote $($hosts.Count) hosts across $invCount inventories to $OutFile"
