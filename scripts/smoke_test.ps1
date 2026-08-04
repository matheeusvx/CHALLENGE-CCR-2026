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
    [int]$MaxScenes = 12,
    [ValidateSet("newest", "oldest")]
    [string]$SceneOrder = "newest",
    [double]$MinValidPixelPercentage = 70,
    [int]$MinObservations = 4,
    [switch]$IncludeLowQualityScenes,
    [string]$PythonExe = ".venv\Scripts\python.exe"
)

$invariantCulture = [System.Globalization.CultureInfo]::InvariantCulture
$cliArguments = @(
    "-m", "src.satellite_monitoring.cli",
    "--latitude", $Latitude.ToString($invariantCulture),
    "--longitude", $Longitude.ToString($invariantCulture),
    "--radius-meters", $RadiusMeters.ToString($invariantCulture),
    "--start-date", $StartDate,
    "--end-date", $EndDate,
    "--max-cloud-cover", $MaxCloudCover.ToString($invariantCulture),
    "--max-scenes", $MaxScenes.ToString($invariantCulture),
    "--scene-order", $SceneOrder,
    "--min-valid-pixel-percentage", $MinValidPixelPercentage.ToString($invariantCulture),
    "--min-observations", $MinObservations.ToString($invariantCulture)
)

if ($IncludeLowQualityScenes) {
    $cliArguments += "--include-low-quality-scenes"
}

& $PythonExe @cliArguments
exit $LASTEXITCODE
