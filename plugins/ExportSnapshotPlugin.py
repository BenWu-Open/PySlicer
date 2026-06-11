# -*- coding: utf-8 -*-
import os
import sys
import cv2
import numpy as np
from multiprocessing import shared_memory
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSpinBox,
    QPushButton, QFileDialog, QMessageBox
)
from basePlugin import basePlugin

class ExportSnapshotPlugin(basePlugin):
    def __init__(self, context=None):
        super().__init__(context)
        self.name = "Export Snapshot"
        self.detail = "Export axial, sagittal, and coronal snapshots to PNG matching UI orientation."
        context = context or {}
        self.shm_display_name = context.get("shm_display_name")
        self.shape = context.get("shape", (1, 1, 1))
        self.dtype = context.get("dtype", np.float32)
        self.lock = context.get("lock")
        self.cursor = context.get("cursor", [0, 0, 0])
        
        # New: Import orientation flags to match UI rendering
        self.flips = {
            "ax_ud": context.get("flip_axial_ud", False),
            "ax_lr": context.get("flip_axial_lr", False),
            "sag_ud": context.get("flip_sagittal_ud", False),
            "sag_lr": context.get("flip_sagittal_lr", False),
            "cor_ud": context.get("flip_coronal_ud", False),
            "cor_lr": context.get("flip_coronal_lr", False),
        }

    def run(self):
        if not self.shm_display_name: return
        self.shm_display = shared_memory.SharedMemory(name=self.shm_display_name)
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.window = QWidget()
        self.window.setWindowTitle("Export Snapshot")
        self.window.resize(360, 180)

        layout = QVBoxLayout(self.window)
        layout.addWidget(QLabel("Choose the voxel coordinate to export as PNG slices."))

        zmax, ymax, xmax = self.shape
        row = QHBoxLayout()
        self.z = QSpinBox(); self.z.setRange(0, max(0, zmax - 1)); self.z.setValue(min(self.cursor[0], max(0, zmax - 1)))
        self.y = QSpinBox(); self.y.setRange(0, max(0, ymax - 1)); self.y.setValue(min(self.cursor[1], max(0, ymax - 1)))
        self.x = QSpinBox(); self.x.setRange(0, max(0, xmax - 1)); self.x.setValue(min(self.cursor[2], max(0, xmax - 1)))
        row.addWidget(QLabel("Z")); row.addWidget(self.z)
        row.addWidget(QLabel("Y")); row.addWidget(self.y)
        row.addWidget(QLabel("X")); row.addWidget(self.x)
        layout.addLayout(row)

        btn_export = QPushButton("Export 3 Views")
        btn_export.clicked.connect(self.export_views)
        layout.addWidget(btn_export)

        self.window.show()
        self.app.exec()
        self.shm_display.close()

    def to_uint8(self, img):
        img = np.asarray(img, dtype=np.float32)
        if img.max() <= 1.01 and img.min() >= 0.0:
            return np.clip(img * 255.0, 0, 255).astype(np.uint8)
        img = img - img.min()
        denom = img.max() + 1e-5
        return np.clip((img / denom) * 255.0, 0, 255).astype(np.uint8)

    def export_views(self):
        target_dir = QFileDialog.getExistingDirectory(self.window, "Choose Output Folder")
        if not target_dir: return

        with self.lock:
            volume = np.ndarray(self.shape, dtype=self.dtype, buffer=self.shm_display.buf).copy()

        z = self.z.value()
        y = self.y.value()
        x = self.x.value()

        # Extract base views
        axial = volume[z, :, :]
        sagittal = volume[:, :, x]
        coronal = volume[:, y, :]

        # Apply the exact UI flips so the snapshots match the screen
        if self.flips["ax_ud"]: axial = np.flipud(axial)
        if self.flips["ax_lr"]: axial = np.fliplr(axial)
        
        if self.flips["sag_ud"]: sagittal = np.flipud(sagittal)
        if self.flips["sag_lr"]: sagittal = np.fliplr(sagittal)
        
        if self.flips["cor_ud"]: coronal = np.flipud(coronal)
        if self.flips["cor_lr"]: coronal = np.fliplr(coronal)

        outputs = {
            f"axial_z{z}.png": axial,
            f"sagittal_x{x}.png": sagittal,
            f"coronal_y{y}.png": coronal,
        }

        for name, img in outputs.items():
            cv2.imwrite(os.path.join(target_dir, name), self.to_uint8(img))

        QMessageBox.information(self.window, "Export Complete", f"Saved 3 PNG files to:\n{target_dir}")