# -*- coding: utf-8 -*-
import sys
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel, QComboBox, QPushButton
from basePlugin import basePlugin

class WindowPresetPlugin(basePlugin):
    PRESETS = {
        "Soft Tissue": (400, 40),
        "Lung": (1500, -600),
        "Bone": (2000, 300),
        "Brain": (80, 40),
        "Liver": (150, 70),
    }

    def __init__(self, context=None):
        super().__init__(context)
        self.name = "Window Presets"
        self.detail = "Quick CT window/level presets."
        context = context or {}
        self.signal_queue = context.get("signal_queue")
        self.initial_width = context.get("initial_width", 1500)
        self.initial_level = context.get("initial_level", 400)

    def run(self):
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.window = QWidget()
        self.window.setWindowTitle("Window Presets")
        self.window.resize(320, 180)

        layout = QVBoxLayout(self.window)
        layout.addWidget(QLabel(f"Current width/level: {self.initial_width} / {self.initial_level}"))

        self.combo = QComboBox()
        self.combo.addItems(self.PRESETS.keys())
        layout.addWidget(self.combo)

        self.preview = QLabel("")
        layout.addWidget(self.preview)

        btn_apply = QPushButton("Apply Preset")
        btn_apply.clicked.connect(self.apply_preset)
        layout.addWidget(btn_apply)

        self.combo.currentTextChanged.connect(self.update_preview)
        self.update_preview(self.combo.currentText())

        self.window.show()
        self.app.exec()

    def update_preview(self, preset_name):
        width, level = self.PRESETS[preset_name]
        self.preview.setText(f"Width: {width} | Level: {level}")

    def apply_preset(self):
        if not self.signal_queue: return
        preset_name = self.combo.currentText()
        width, level = self.PRESETS[preset_name]
        self.signal_queue.put({
            "action": "REFRESH",
            "width": width,
            "level": level,
            "force_apply": True 
        })