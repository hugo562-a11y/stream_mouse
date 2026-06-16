@echo off
echo Building StreamMouse.exe ...
pyinstaller stream_mouse.spec --clean
echo.
if exist "dist\StreamMouse.exe" (
    echo Done!  dist\StreamMouse.exe
) else (
    echo Build failed.
)
pause
