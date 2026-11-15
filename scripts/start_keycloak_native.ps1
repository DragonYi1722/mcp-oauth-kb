# Start Keycloak from the standalone distribution (no Docker).
#
# Useful when container images cannot be pulled. Requires a JDK/JRE 17+.
# The realm is copied into data/import so `--import-realm` picks it up.
#
#   powershell -ExecutionPolicy Bypass -File scripts\start_keycloak_native.ps1
#   powershell ... -KeycloakHome D:\tools\keycloak-26.7.4 -JavaHome D:\tools\jre21
#
param(
    [string]$KeycloakHome = "E:\GitHub\.tools\keycloak-26.7.4",
    [string]$JavaHome = "E:\GitHub\.tools\jdk-21.0.13+9-jre",
    [int]$Port = 8080
)

$ErrorActionPreference = "Stop"

$kcBat = Join-Path $KeycloakHome "bin\kc.bat"
$javaExe = Join-Path $JavaHome "bin\java.exe"
if (-not (Test-Path $kcBat)) { throw "kc.bat not found under $KeycloakHome" }
if (-not (Test-Path $javaExe)) { throw "java.exe not found under $JavaHome" }

$env:JAVA_HOME = $JavaHome
# Keycloak 26 no longer ships a default admin; create the dev console account.
$env:KC_BOOTSTRAP_ADMIN_USERNAME = "admin"
$env:KC_BOOTSTRAP_ADMIN_PASSWORD = "admin"
$realm = Join-Path $PSScriptRoot "..\infra\keycloak\realm-kb.json"
$importDir = Join-Path $KeycloakHome "data\import"
New-Item -ItemType Directory -Force -Path $importDir | Out-Null
Copy-Item $realm (Join-Path $importDir "realm-kb.json") -Force

Write-Host "Starting Keycloak $KeycloakHome on http://localhost:$Port (realm kb)"
& $kcBat start-dev --import-realm --http-port $Port
