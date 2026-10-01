# 打包分发.ps1 —— 生成一份"干净、可直接发给朋友"的副本
#
# 用法（在程序目录里）：
#   powershell -ExecutionPolicy Bypass -File .\打包分发.ps1
#   powershell -ExecutionPolicy Bypass -File .\打包分发.ps1 -Zip
#
# 它会复制出 "抖音续火花_分发版" 目录，并剔除与本机/本人账号绑定的内容：
#   - browser_data/   你的登录态（含账号凭证）
#   - stickers.json / stickers/   你账号收藏的表情（对方账号里没有）
#   - config.json 里的好友列表     换成默认占位配置
#   - logs/ build/ __pycache__    运行产物与构建脚本
param(
    [switch]$Zip
)

$ErrorActionPreference = "Stop"

$src = Split-Path -Parent $MyInvocation.MyCommand.Path
$dst = Join-Path (Split-Path -Parent $src) "抖音续火花_分发版"

Write-Host "源目录  : $src"
Write-Host "输出目录: $dst"
Write-Host ""

if (Test-Path $dst) {
    Write-Host "清理旧的输出目录…"
    Remove-Item $dst -Recurse -Force
}

Write-Host "复制程序文件（跳过隐私/临时内容）…"
robocopy $src $dst /E /XD browser_data logs build __pycache__ avatars /NFL /NDL /NJH /NJS /NP | Out-Null

# 再清一遍可能被 robocopy 带过来的、与本人账号相关的内容
foreach ($item in @("config.json", "stickers.json", "stickers", "avatars", "conversations.json", "打包分发.ps1")) {
    $full = Join-Path $dst $item
    if (Test-Path $full) {
        Remove-Item $full -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "  已剔除: $item"
    }
}

# 生成一份干净的默认配置（4 个好友 + 1 个群聊占位）
Write-Host "生成默认 config.json…"
$py = Join-Path $dst "runtime\python.exe"
& $py -c "import sys; sys.path.insert(0, r'$dst\app'); import config; config.save({}); print('  默认配置已生成')"

Write-Host ""
Write-Host "完成：$dst"

if ($Zip) {
    $zip = "$dst.zip"
    if (Test-Path $zip) { Remove-Item $zip -Force }
    Write-Host "正在压缩（约 150MB，需要一会儿）…"
    Compress-Archive -Path $dst -DestinationPath $zip -CompressionLevel Optimal
    Write-Host "压缩包：$zip"
}

Write-Host ""
Write-Host "接下来：把这个文件夹（或压缩包）发给朋友。"
Write-Host "朋友第一次使用：双击 exe -> 扫码登录 -> 点「同步账户表情」-> 填自己的好友。"
