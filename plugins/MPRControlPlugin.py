# -*- coding: utf-8 -*-
import sys
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSpinBox, QPushButton, QGroupBox
from basePlugin import basePlugin


class MPRControlPlugin(basePlugin):
    def __init__(self, context=None):
        super().__init__(context)
        self.name = "Multi-Planar Reconstruction Control"
        self.detail = "Controls axial/coronal/sagittal cursor position in the main MPR viewer."
        context = context or {}
        self.shape = context.get("shape", (1, 1, 1))
        self.signal_queue = context.get("signal_queue")

    def run(self):
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.window = QWidget()
        self.window.setWindowTitle("MPR Control")
        self.window.resize(320, 220)

        zmax, ymax, xmax = self.shape
        layout = QVBoxLayout(self.window)
        layout.addWidget(QLabel(f"Volume shape: Z={zmax}, Y={ymax}, X={xmax}"))

        group = QGroupBox("Jump to voxel coordinate")
        row = QHBoxLayout(group)
        self.z = QSpinBox(); self.z.setRange(0, max(0, zmax - 1)); self.z.setValue(zmax // 2)
        self.y = QSpinBox(); self.y.setRange(0, max(0, ymax - 1)); self.y.setValue(ymax // 2)
        self.x = QSpinBox(); self.x.setRange(0, max(0, xmax - 1)); self.x.setValue(xmax // 2)
        row.addWidget(QLabel("Z")); row.addWidget(self.z)
        row.addWidget(QLabel("Y")); row.addWidget(self.y)
        row.addWidget(QLabel("X")); row.addWidget(self.x)
        layout.addWidget(group)

        self.btn_jump = QPushButton("Update MPR Crosshair")
        self.btn_center = QPushButton("Center")
        layout.addWidget(self.btn_jump)
        layout.addWidget(self.btn_center)

        self.btn_jump.clicked.connect(self.send_cursor)
        self.btn_center.clicked.connect(self.center)
        self.window.show()
        self.app.exec()

    def center(self):
        zmax, ymax, xmax = self.shape
        self.z.setValue(zmax // 2); self.y.setValue(ymax // 2); self.x.setValue(xmax // 2)
        self.send_cursor()

    def send_cursor(self):
        if self.signal_queue:
            self.signal_queue.put({"action": "SET_CURSOR", "cursor": [self.z.value(), self.y.value(), self.x.value()]})
