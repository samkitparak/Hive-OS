# Removes HIVE OS startup tasks. Data folders are deliberately preserved.

$ErrorActionPreference = "Stop"
schtasks /Delete /TN "HIVE OS Central" /F 2>$null
Get-ScheduledTask -TaskName "HIVE Agent -*" -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
Get-NetFirewallRule -DisplayName "HIVE OS Dashboard" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Get-NetFirewallRule -DisplayName "HIVE OS API" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Get-NetFirewallRule -DisplayName "HIVE OS MQTT" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Write-Host "HIVE startup tasks and firewall rules removed. C:\HIVE-OS and C:\HIVE-Agent were preserved."
