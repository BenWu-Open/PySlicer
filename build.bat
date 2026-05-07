cd C:\projects\PySlicer
CALL C:\virtualpython\PYTHON3.12.10\Scripts\activate.bat

pyinstaller PySlicer-OneFolder.spec

echo Done compiling to PySlicer folder package
echo Press Ctrl+C to stop creating 1 file PySlicer package
pause

pyinstaller PySlicer-OneFile.spec
pause