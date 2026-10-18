param([string]$docx, [string]$pdf)
# 用 Word 更新目录与页码、保存（不写入本机 Office 账户名等个人信息），可选导出 PDF。
# 用法：powershell -ExecutionPolicy Bypass -File finalize.ps1 -docx <docx 绝对路径> [-pdf <pdf 绝对路径>]
$ErrorActionPreference = "Stop"
$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0
try {
  $doc = $word.Documents.Open($docx, $false, $false)
  foreach ($toc in $doc.TablesOfContents) { $toc.Update() | Out-Null }
  $doc.Fields.Update() | Out-Null
  foreach ($toc in $doc.TablesOfContents) { $toc.UpdatePageNumbers() | Out-Null }
  $doc.RemovePersonalInformation = $true   # 保存时清除作者、最后修改人等个人信息
  $doc.Save()
  if ($pdf) { $doc.ExportAsFixedFormat($pdf, 17) }
  Write-Output ("pages=" + $doc.ComputeStatistics(2))
  $doc.Close($false)
} finally {
  $word.Quit()
  [System.Runtime.Interopservices.Marshal]::ReleaseComObject($word) | Out-Null
}
# 保险：再把文档属性里的作者 / 最后修改人统一成程序名，去掉公司、经理等字段
$scrub = Join-Path $PSScriptRoot "scrub_docx.py"
$python = Join-Path $PSScriptRoot "..\..\_deps\venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
& $python $scrub $docx
