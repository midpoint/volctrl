@echo off
chcp 65001 >nul
title VolCtrl 打包工具

echo ========================================
echo    VolCtrl - 打包成 EXE
echo ========================================
echo.

echo [1/2] 安装 PyInstaller...
pip install pyinstaller -q

echo [2/2] 开始打包...
echo.

pyinstaller ^
    --onefile ^
    --noconsole ^
    --name VolCtrl ^
    --icon=NONE ^
    --add-binary "python.exe;." ^
    --hidden-import=comtypes ^
    --hidden-import=pycaw ^
    --hidden-import=pystray ^
    --hidden-import=PIL ^
    --hidden-import=keyboard ^
    --hidden-import=winreg ^
    --collect-all=pycaw ^
    --collect-all=pystray ^
    VolCtrl.py

echo.
echo ========================================
echo ✅ 打包完成!
echo ========================================
echo.
echo 输出目录: dist\VolCtrl.exe
echo 按任意键打开输出目录...
pause >nul
explorer dist
