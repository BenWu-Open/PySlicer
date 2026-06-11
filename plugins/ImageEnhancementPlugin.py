# -*- coding: utf-8 -*-
import sys
import numpy as np
import cv2
from multiprocessing import shared_memory
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel, QSlider, QComboBox, QPushButton, QGroupBox
from PyQt6.QtCore import Qt
from basePlugin import basePlugin


class ImageEnhancementPlugin(basePlugin):
    def __init__(self, context=None):
        super().__init__(context)
        self.name = "Image Filtering / Enhancement"
        self.detail = "Apply windowing, smoothing, sharpening, histogram equalization, and edge enhancement."
        context = context or {}
        self.shm_raw_name = context.get("shm_raw_name")
        self.shm_display_name = context.get("shm_display_name")
        self.shape = context.get("shape")
        self.dtype = context.get("dtype", np.float32)
        self.lock = context.get("lock")
        self.signal_queue = context.get("signal_queue")
        self.init_w = context.get("initial_width", 1500)
        self.init_l = context.get("initial_level", 400)

    def run(self):
        self.shm_raw = shared_memory.SharedMemory(name=self.shm_raw_name)
        self.shm_display = shared_memory.SharedMemory(name=self.shm_display_name)
        self.raw_ref = np.ndarray(self.shape, dtype=self.dtype, buffer=self.shm_raw.buf)

        self.app = QApplication.instance() or QApplication(sys.argv)
        self.window = QWidget()
        self.window.setWindowTitle("Image Filtering / Enhancement")
        self.window.resize(360, 380)
        layout = QVBoxLayout(self.window)

        contrast = QGroupBox("Windowing")
        c = QVBoxLayout(contrast)
        c.addWidget(QLabel("Window Width"))
        self.width = QSlider(Qt.Orientation.Horizontal); self.width.setRange(1, 4000); self.width.setValue(self.init_w)
        c.addWidget(self.width)
        c.addWidget(QLabel("Window Level"))
        self.level = QSlider(Qt.Orientation.Horizontal); self.level.setRange(-1000, 2000); self.level.setValue(self.init_l)
        c.addWidget(self.level)
        layout.addWidget(contrast)

        filt = QGroupBox("Filter")
        f = QVBoxLayout(filt)
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["None", "Gaussian Blur", "Median Blur", "Sharpen", "Histogram Equalization", "CLAHE", "Edge Detection"])
        f.addWidget(self.filter_combo)
        f.addWidget(QLabel("Strength / Kernel"))
        self.strength = QSlider(Qt.Orientation.Horizontal); self.strength.setRange(1, 9); self.strength.setValue(3)
        f.addWidget(self.strength)
        layout.addWidget(filt)

        self.btn_apply = QPushButton("Apply Enhancement")
        layout.addWidget(self.btn_apply)
        self.btn_apply.clicked.connect(self.process)
        self.process()
        self.window.show()
        self.app.exec()
        self.shm_raw.close(); self.shm_display.close()

    def window_to_uint8(self, vol):
        w = self.width.value(); l = self.level.value()
        low, high = l - w/2, l + w/2
        vol = np.clip(vol, low, high)
        vol = (vol - low) / (high - low + 1e-5)
        return (vol * 255).astype(np.uint8)

    def process_slice_filter(self, img, mode, k):
        if k % 2 == 0:
            k += 1
        if mode == "Gaussian Blur":
            return cv2.GaussianBlur(img, (k, k), 0)
        if mode == "Median Blur":
            return cv2.medianBlur(img, k)
        if mode == "Sharpen":
            kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32)
            return cv2.filter2D(img, -1, kernel)
        if mode == "Histogram Equalization":
            return cv2.equalizeHist(img)
        if mode == "CLAHE":
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            return clahe.apply(img)
        if mode == "Edge Detection":
            return cv2.Canny(img, 50, 150)
        return img

    def process(self):
        mode = self.filter_combo.currentText()
        k = self.strength.value()
        work = self.window_to_uint8(np.copy(self.raw_ref))
        if mode != "None":
            out = np.empty_like(work)
            for z in range(work.shape[0]):
                out[z] = self.process_slice_filter(work[z], mode, k)
            work = out
        work = work.astype(np.float32) / 255.0
        work = np.ascontiguousarray(work)
        with self.lock:
            display = np.ndarray(work.shape, dtype=self.dtype, buffer=self.shm_display.buf)
            display[:] = work[:]
        if self.signal_queue:
            self.signal_queue.put({"action": "REFRESH", "shape": work.shape, "width": self.width.value(), "level": self.level.value()})
