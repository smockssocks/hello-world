@echo off
rem Microsoft Store / Game Pass versions of Forza can't send telemetry to 127.0.0.1 by default.
rem This adds a loopback exemption for every installed Forza package. Run as administrator.
net session >nul 2>&1 || (echo Please right-click this file and choose "Run as administrator". & pause & exit /b 1)
powershell -NoProfile -Command "Get-AppxPackage | Where-Object { $_.Name -match 'Forza|SunriseBaseGame|624F8B84B80' } | ForEach-Object { Write-Host ('Allowing ' + $_.Name); CheckNetIsolation LoopbackExempt -a -n=\"$($_.PackageFamilyName)\" }"
echo Done.
pause
