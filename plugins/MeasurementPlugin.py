# -*- coding: utf-8 -*-
import sys
import math
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSpinBox, QPushButton, QTextEdit, QGroupBox
from basePlugin import basePlugin


class MeasurementPlugin(basePlugin):
    def __init__(self, context=None):
        super().__init__(context)
        self.name = "Measurement Tool"
        self.detail = "Distance and HU/intensity measurement using voxel coordinates."
        context = context or {}
        self.shape = context.get("shape", (1, 1, 1))
        self.spacing = context.get("spacing", (1.0, 1.0, 1.0))  # dz, dy, dx in mm
        self.signal_queue = context.get("signal_queue")
        self.current_cursor = context.get("cursor", [0, 0, 0])

    def run(self):
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.window = QWidget()
        self.window.setWindowTitle("Measurement Tool")
        self.window.resize(460, 430)
        layout = QVBoxLayout(self.window)
        layout.addWidget(QLabel(f"Spacing: dz={self.spacing[0]:.4g} mm, dy={self.spacing[1]:.4g} mm, dx={self.spacing[2]:.4g} mm"))

        self.p1 = self.make_point_group("Point 1")
        self.p2 = self.make_point_group("Point 2")
        layout.addWidget(self.p1["group"])
        layout.addWidget(self.p2["group"])

        btns = QHBoxLayout()
        self.btn_calc = QPushButton("Calculate Distance")
        self.btn_send_p1 = QPushButton("Show Point 1")
        self.btn_send_p2 = QPushButton("Show Point 2")
        btns.addWidget(self.btn_calc); btns.addWidget(self.btn_send_p1); btns.addWidget(self.btn_send_p2)
        layout.addLayout(btns)

        self.out = QTextEdit(); self.out.setReadOnly(True)
        layout.addWidget(self.out)

        self.btn_calc.clicked.connect(self.calculate)
        self.btn_send_p1.clicked.connect(lambda: self.show_point(self.p1))
        self.btn_send_p2.clicked.connect(lambda: self.show_point(self.p2))

        self.window.show()
        self.app.exec()

    def make_point_group(self, title):
        zmax, ymax, xmax = self.shape
        group = QGroupBox(title)
        row = QHBoxLayout(group)
        z = QSpinBox(); z.setRange(0, max(0, zmax - 1))
        y = QSpinBox(); y.setRange(0, max(0, ymax - 1))
        x = QSpinBox(); x.setRange(0, max(0, xmax - 1))
        row.addWidget(QLabel("Z")); row.addWidget(z)
        row.addWidget(QLabel("Y")); row.addWidget(y)
        row.addWidget(QLabel("X")); row.addWidget(x)
        return {"group": group, "z": z, "y": y, "x": x}

    def coords(self, p):
        return p["z"].value(), p["y"].value(), p["x"].value()

    def show_point(self, p):
        if self.signal_queue:
            self.signal_queue.put({"action": "SET_CURSOR", "cursor": list(self.coords(p))})

    def calculate(self):
        z1, y1, x1 = self.coords(self.p1)
        z2, y2, x2 = self.coords(self.p2)
        dz, dy, dx = self.spacing
        physical = ((z2-z1)*dz, (y2-y1)*dy, (x2-x1)*dx)
        dist = math.sqrt(sum(v*v for v in physical))
        voxel_dist = math.sqrt((z2-z1)**2 + (y2-y1)**2 + (x2-x1)**2)
        self.out.setPlainText(
            f"Point 1: (Z={z1}, Y={y1}, X={x1})\n"
            f"Point 2: (Z={z2}, Y={y2}, X={x2})\n\n"
            f"Voxel distance: {voxel_dist:.3f} voxels\n"
            f"Physical distance: {dist:.3f} mm\n"
            f"Components: ΔZ={physical[0]:.3f} mm, ΔY={physical[1]:.3f} mm, ΔX={physical[2]:.3f} mm"
        )
