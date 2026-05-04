import sys
import os
from PyQt6.QtWidgets import (QApplication)
import multiprocessing

parentdir = (os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(1, parentdir)

import MainWindow as MainWindow

if __name__ == "__main__":
    app = QApplication(sys.argv)
    multiprocessing.freeze_support()    # to support multi-thread with pyinstaller
    window = MainWindow.MainWindow()
    window.show()
    sys.exit(app.exec())

