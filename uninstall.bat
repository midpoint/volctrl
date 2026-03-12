@echo off
chcp 65001 >nul
title VolCtrl 卸载

echo 正在移除开机自启动...
python "%~dp0VolCtrl.py" --uninstall

echo.
echo ✅ 已卸载 VolCtrl
echo 如需完全移除，请删除本文件夹
pause
