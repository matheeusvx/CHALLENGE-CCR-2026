param(
    [Parameter(Mandatory = $true)]
    [double]$Latitude,

    [Parameter(Mandatory = $true)]
    [double]$Longitude,

    [Parameter(Mandatory = $true)]
    [double]$RadiusMeters,

    [Parameter(Mandatory = $true)]
    [string]$StartDate,

    [Parameter(Mandatory = $true)]
    [string]$EndDate,

    [double]$MaxCloudCover = 20,
    [string]$PythonExe = ".venv\Scripts\python.exe"
)

& $PythonExe -m src.satellite_monitoring.cli `
    --latitude $Latitude `
    --longitude $Longitude `
    --radius-meters $RadiusMeters `
    --start-date $StartDate `
    --end-date $EndDate `
    --max-cloud-cover $MaxCloudCover `
    --max-scenes 1

exit $LASTEXITCODE
