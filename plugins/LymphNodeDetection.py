# -*- coding: utf-8 -*-

import os
import sys
import shutil
import zipfile
import webbrowser
import requests
import numpy as np
import pydicom
import cv2

from PyQt6 import QtWidgets
from PyQt6.QtWidgets import QApplication, QWidget, QFileDialog, QMessageBox
from PyQt6.QtGui import QTextCursor
from PyQt6.QtCore import QThread, pyqtSignal

from utils.SlicerLog import SlicerLog
logger = SlicerLog.getLogger("Slicer_Log")

from plugins.UI.lymphnode import Ui_Form
from basePlugin import basePlugin

# =========================================================
# Worker Thread
# =========================================================
class Worker(QThread):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()

    def __init__(self, source, dest, url):
        super().__init__()
        self.source = source
        self.dest = dest
        self.url = url

    def log(self, msg):
        self.log_signal.emit(msg)

    # ---------------------------
    # Normalize
    # ---------------------------
    def normalize(self, img):
        img = img.astype(np.float32)
        img -= np.min(img)
        if np.max(img) != 0:
            img /= np.max(img)
        return (img * 255).astype(np.uint8)

    # ---------------------------
    # Build volume
    # ---------------------------
    def build_volume(self, slices):
        volume = np.stack([s.pixel_array for s in slices], axis=0).astype(np.float32)

        try:
            dz = abs(slices[1].ImagePositionPatient[2] - slices[0].ImagePositionPatient[2])
            dy, dx = map(float, slices[0].PixelSpacing)
        except:
            dz, dy, dx = 1.0, 1.0, 1.0

        spacing = (dz, dy, dx)
        return volume, spacing

    # ---------------------------
    # Simple resample
    # ---------------------------
    def resample_volume(self, volume, spacing, new_spacing=(1,1,1)):
        z, h, w = volume.shape
        dz, dy, dx = spacing

        scale_z = dz / new_spacing[0]
        scale_y = dy / new_spacing[1]
        scale_x = dx / new_spacing[2]

        new_z = int(z * scale_z)
        new_h = int(h * scale_y)
        new_w = int(w * scale_x)

        resized = np.zeros((new_z, new_h, new_w), dtype=np.float32)

        for i in range(z):
            slice_resized = cv2.resize(volume[i], (new_w, new_h))
            z_idx = int(i * scale_z)
            if z_idx < new_z:
                resized[z_idx] = slice_resized

        return resized

    # ---------------------------
    # 2.5D Generators
    # ---------------------------
    def axial(self, volume, out):
        for z in range(1, volume.shape[0]-1):
            img = np.stack([volume[z-1], volume[z], volume[z+1]], axis=-1)
            cv2.imwrite(os.path.join(out, f"axial_{z:04d}.png"), self.normalize(img))
            if z % 30 == 0:
                self.log(f"Axial {z}")

    def coronal(self, volume, out):
        for y in range(1, volume.shape[1]-1):
            img = np.stack([
                volume[:, y-1, :],
                volume[:, y, :],
                volume[:, y+1, :]
            ], axis=-1)
            cv2.imwrite(os.path.join(out, f"coronal_{y:04d}.png"), self.normalize(img))
            if y % 50 == 0:
                self.log(f"Coronal {y}")

    def sagittal(self, volume, out):
        for x in range(1, volume.shape[2]-1):
            img = np.stack([
                volume[:, :, x-1],
                volume[:, :, x],
                volume[:, :, x+1]
            ], axis=-1)
            cv2.imwrite(os.path.join(out, f"sagittal_{x:04d}.png"), self.normalize(img))
            if x % 50 == 0:
                self.log(f"Sagittal {x}")

    # ---------------------------
    # MAIN RUN
    # ---------------------------
    def run(self):
        try:
            self.log("<b>=== Start ===</b>")

            # Load DICOM
            slices = []
            for f in os.listdir(self.source):
                try:
                    dcm = pydicom.dcmread(os.path.join(self.source, f))
                    if hasattr(dcm, "InstanceNumber"):
                        slices.append((int(dcm.InstanceNumber), dcm))
                except:
                    continue

            slices.sort(key=lambda x: x[0])
            slices = [s[1] for s in slices]

            self.log(f"Loaded {len(slices)} slices")

            if len(slices) < 3:
                self.log("Not enough slices")
                return

            # Build + resample
            volume, spacing = self.build_volume(slices)
            self.log(f"Spacing: {spacing}")

            volume = self.resample_volume(volume, spacing)
            self.log(f"Resampled shape: {volume.shape}")

            # Output
            out = os.path.join(self.dest, "2p5D")
            if os.path.exists(out):
                shutil.rmtree(out)
            os.makedirs(out)

            # Generate views
            self.axial(volume, out)
            self.coronal(volume, out)
            self.sagittal(volume, out)

            self.log("2.5D (3 views) done")

            # ZIP
            zip_path = os.path.join(self.dest, "data.zip")
            with zipfile.ZipFile(zip_path, 'w') as z:
                for root, _, files in os.walk(out):
                    for f in files:
                        full = os.path.join(root, f)
                        z.write(full, os.path.relpath(full, out))

            self.log("Zipped")

            # Send
            if self.url:
                self.log("Sending to server...")
                with open(zip_path, 'rb') as f:
                    res = requests.post(self.url, files={'file': f})

                data = res.json()
                tid = data.get("tracking_id", "N/A")
                self.log(f"<b>Tracking ID:</b> {tid}")

                webbrowser.open("http://localhost:8080/results")
            else:
                self.log("No server URL")

        except Exception as e:
            self.log(f"<span style='color:red'>{e}</span>")

        self.finished_signal.emit()


# =========================================================
# UI
# =========================================================
class LymphNode_Detection(basePlugin):
    # UPDATE: Accept context=None to match the updated pluginManager signature
    def __init__(self, context=None):
        super().__init__(context)
        self.name = "LymphNode Detection"
        self.plugin_context = context

    def run(self):
        logger.info(f"Running LymphNode Detection")
        
        # Safely reuse QApplication if it already exists
        self.app = QApplication.instance()
        if self.app is None:
            self.app = QApplication(sys.argv)

        self.window = QWidget()
        self.ui = Ui_Form()
        self.ui.setupUi(self.window)

        self.SourcePath = None
        self.DestinationPath = None

        self.cursorStatus = QTextCursor(self.ui.Status_textBrowser.document())

        self.ui.SourcePath_Button.clicked.connect(self.get_source)
        self.ui.DestinationPath_Button.clicked.connect(self.get_dest)
        self.ui.Start_pushButton.clicked.connect(self.start)
        self.ui.Cancel_pushButton.clicked.connect(self.handle_close)

        self.window.show()
        self.app.exec()

    def handle_close(self):
        # Stop worker if running
        if hasattr(self, "worker") and self.worker.isRunning():
            self.worker.quit()
            self.worker.wait()
        
        self.window.close()
        self.app.quit()


    def insertText(self, text):
        self.cursorStatus.insertHtml(text + "<br>")
        self.ui.Status_textBrowser.moveCursor(QTextCursor.MoveOperation.End)

    def get_source(self):
        self.SourcePath = QFileDialog.getExistingDirectory(self.window, "DICOM Folder")
        if self.SourcePath:
            self.ui.SourcePath_lineEdit.setText(self.SourcePath)

    def get_dest(self):
        self.DestinationPath = QFileDialog.getExistingDirectory(self.window, "Output Folder")
        if self.DestinationPath:
            self.ui.DestinationPath_lineEdit.setText(self.DestinationPath)

    def start(self):
        if not self.SourcePath or not self.DestinationPath:
            QMessageBox.warning(self.window, "Error", "Select folders")
            return

        url =self.ui.AIServer_lineEdit.text().strip()

        self.worker = Worker(self.SourcePath, self.DestinationPath, url)
        self.worker.log_signal.connect(self.insertText)
        self.worker.finished_signal.connect(self.done)

        self.ui.Start_pushButton.setEnabled(False)
        self.worker.start()

    def done(self):
        self.insertText("<b>=== Finished ===</b>")
        self.ui.Start_pushButton.setEnabled(True)


# =========================================================
# RUN
# =========================================================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = LymphNode_Detection()
    w.show()
    sys.exit(app.exec())