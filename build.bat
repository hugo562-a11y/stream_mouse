@echo off
echo Building StreamMouse ...
python -m PyInstaller stream_mouse.spec --clean
echo.
if exist "dist\StreamMouse\StreamMouse.exe" (
    echo Build OK! Zipping...
    powershell -Command "Compress-Archive -Path 'dist\StreamMouse' -DestinationPath 'dist\StreamMouse.zip' -Force"
    echo Done!  dist\StreamMouse.zip
) else (
    echo Build failed.
)
pause
