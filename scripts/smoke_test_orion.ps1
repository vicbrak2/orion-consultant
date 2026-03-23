param(
    [string]$BaseUrl = "http://localhost:8100",
    [string]$DockerN8nContainer = "n8n",
    [string]$N8nOrionUrl = "http://orion-consultant:8100",
    [switch]$SkipN8nChecks
)

$ErrorActionPreference = "Stop"

function Invoke-JsonPost {
    param(
        [string]$Url,
        [hashtable]$Body
    )

    return Invoke-RestMethod `
        -Method Post `
        -Uri $Url `
        -ContentType "application/json" `
        -Body ($Body | ConvertTo-Json)
}

Write-Host "== Orion health =="
$health = Invoke-RestMethod -Method Get -Uri "$BaseUrl/health"
$health | ConvertTo-Json -Depth 6

$healthyPayload = @{
    symbol = "Step Index"
    direction = "BUY"
    entry_price = 5432.10
    stop_loss = 5400.00
    take_profit = 5500.00
    equity = 1000.0
    balance = 1050.0
    current_volatility = 120.5
    trend_h1 = "bullish"
    trend_h4 = "bullish"
}

$riskyPayload = @{
    symbol = "Step Index"
    direction = "BUY"
    entry_price = 5432.10
    stop_loss = 5300.00
    take_profit = 5500.00
    equity = 800.0
    balance = 1000.0
    current_volatility = 250.0
    trend_h1 = "bearish"
    trend_h4 = "bearish"
}

Write-Host "== Committee approve case =="
$approveVerdict = Invoke-JsonPost -Url "$BaseUrl/api/v1/consult" -Body $healthyPayload
$approveVerdict | ConvertTo-Json -Depth 8

Write-Host "== Committee reject case =="
$rejectVerdict = Invoke-JsonPost -Url "$BaseUrl/api/v1/consult" -Body $riskyPayload
$rejectVerdict | ConvertTo-Json -Depth 8

Write-Host "== Pattern expert =="
$patternVerdict = Invoke-JsonPost -Url "$BaseUrl/api/v1/consult/pattern_expert" -Body $healthyPayload
$patternVerdict | ConvertTo-Json -Depth 6

Write-Host "== MCP initialize =="
$mcpInitBody = @{
    jsonrpc = "2.0"
    id = "1"
    method = "initialize"
    params = @{
        protocolVersion = "2025-03-26"
        capabilities = @{}
        clientInfo = @{
            name = "orion-smoke-test"
            version = "1.0.0"
        }
    }
}

$mcpPayloadFile = [System.IO.Path]::GetTempFileName()
($mcpInitBody | ConvertTo-Json -Depth 8) | Set-Content -Path $mcpPayloadFile -Encoding utf8

try {
    curl.exe -sS `
        -X POST `
        "$BaseUrl/mcp/" `
        -H "Content-Type: application/json" `
        -H "Accept: application/json, text/event-stream" `
        --data-binary "@$mcpPayloadFile"
}
finally {
    Remove-Item $mcpPayloadFile -ErrorAction SilentlyContinue
}

if (-not $SkipN8nChecks) {
    Write-Host "== n8n container reachability =="
    docker exec $DockerN8nContainer node -e "fetch('$N8nOrionUrl/health').then(async r => { console.log(r.status); console.log(await r.text()); }).catch(e => { console.error(e.message); process.exit(1); })"
    docker exec $DockerN8nContainer node -e "const http = require('http'); const req = http.request({ hostname: 'orion-consultant', port: 8100, path: '/mcp/', method: 'POST', headers: { 'content-type': 'application/json', 'accept': 'application/json, text/event-stream' } }, res => { console.log(res.statusCode); res.setEncoding('utf8'); res.on('data', chunk => process.stdout.write(chunk)); }); req.on('error', err => { console.error(err.message); process.exit(1); }); req.write(JSON.stringify({ jsonrpc: '2.0', id: '1', method: 'initialize', params: { protocolVersion: '2025-03-26', capabilities: {}, clientInfo: { name: 'n8n-smoke', version: '1.0.0' } } })); req.end();"
}
