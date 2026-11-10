param([string]$In,[string]$Out)
$ErrorActionPreference='Stop'
$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0
try {
  $doc = $word.Documents.Open($In, $false, $true)
  $pages = $doc.ComputeStatistics(2)
  $doc.ExportAsFixedFormat($Out, 17)
  $doc.Close($false)
  Write-Output "pages=$pages"
} finally { $word.Quit() }
