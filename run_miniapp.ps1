Set-Location $PSScriptRoot
$env:MINIAPP_PORT = if ($env:MINIAPP_PORT) { $env:MINIAPP_PORT } else { "8080" }
python run_miniapp.py
