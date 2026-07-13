Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$img = [System.Windows.Forms.Clipboard]::GetImage()
if ($img) {
  $out = 'E:\Project\codex-usage-hud\clipboard_last.png'
  $img.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
  Write-Output $out
  Write-Output ('size=' + $img.Width + 'x' + $img.Height)
} else {
  Write-Output 'no-image'
}
