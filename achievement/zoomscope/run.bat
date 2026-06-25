@echo off
chcp 65001 >nul
title ZoomScope 放大镜
echo.
echo   ╔══════════════════════════════╗
echo   ║   ZoomScope v1.0           ║
echo   ║   游戏准星区域放大镜       ║
echo   ╚══════════════════════════════╝
echo.
echo   启动后把窗口拖到副屏上即可
echo.
D:\Miniconda3\python.exe D:\projects\zoomscope\zoomscope.py %*
pause
