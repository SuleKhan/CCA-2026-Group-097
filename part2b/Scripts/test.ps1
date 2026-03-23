Import-Module BurntToast
New-BurntToastNotification -Text "PARSEC Job Finished", "Logs are ready!"
[console]::beep(1000, 500)