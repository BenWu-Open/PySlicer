import sys
import numpy as np
from multiprocessing import shared_memory
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QLabel, 
                             QSlider, QComboBox, QGroupBox, QPushButton)
from PyQt6.QtCore import Qt

from utils.SlicerLog import SlicerLog
logger = SlicerLog.getLogger("Slicer_Log")

from basePlugin import basePlugin

class TransformPlugin(basePlugin):
    def __init__(self, context):
        super().__init__(context)
        self.name = "TransformPlugin"
        
        # Unpack Context with .get() to avoid key errors if context is missing
        self.shm_raw_name = context.get("shm_raw_name")
        self.shm_display_name = context.get("shm_display_name")
        self.shape = context.get("shape")
        self.dtype = context.get("dtype")
        self.lock = context.get("lock")
        self.signal_queue = context.get("signal_queue")
        
        # Inherit values from Master so sliders match current view
        self.init_w = context.get("initial_width", 1500)
        self.init_l = context.get("initial_level", 400)

        # Initialize placeholders
        self.shm_raw = None
        self.shm_display = None

    def run(self):
        if not self.shm_raw_name:
            print("Error: No shared memory context provided.")
            return

        # 1. Attach to shared memory ONCE when the plugin starts
        self.shm_raw = shared_memory.SharedMemory(name=self.shm_raw_name)
        self.shm_display = shared_memory.SharedMemory(name=self.shm_display_name)

        # High-Fidelity Reference: Keep raw data pristine for calculations
        self.raw_ref = np.ndarray(self.shape, dtype=self.dtype, buffer=self.shm_raw.buf)

        # We must create a NEW QApplication because this is running in a child process
        self.app = QApplication.instance()
        if self.app is None: 
            self.app = QApplication(sys.argv)
            
        self.window = QWidget()
        self.window.setWindowTitle("Image Transform Tool")
        self.window.resize(300, 400)
        
        layout = QVBoxLayout(self.window)

        # Windowing UI
        group_contrast = QGroupBox("Contrast / Brightness")
        c_layout = QVBoxLayout()
        c_layout.addWidget(QLabel("Window Width"))
        self.slider_width = QSlider(Qt.Orientation.Horizontal)
        self.slider_width.setRange(1, 4000)
        self.slider_width.setValue(self.init_w) # Set inherited value
        c_layout.addWidget(self.slider_width)
        
        c_layout.addWidget(QLabel("Window Level"))
        self.slider_level = QSlider(Qt.Orientation.Horizontal)
        self.slider_level.setRange(-1000, 2000)
        self.slider_level.setValue(self.init_l) # Set inherited value
        c_layout.addWidget(self.slider_level)
        group_contrast.setLayout(c_layout)
        layout.addWidget(group_contrast)
        
        # Transform UI
        group_transform = QGroupBox("View Transformations")
        t_layout = QVBoxLayout()
        t_layout.addWidget(QLabel("Rotate (Axial Plane)"))
        self.rotation_combo = QComboBox()
        self.rotation_combo.addItems(["0", "90", "180", "270"])
        t_layout.addWidget(self.rotation_combo)
        
        t_layout.addWidget(QLabel("Flip"))
        self.flip_combo = QComboBox()
        self.flip_combo.addItems(["None", "Horizontal", "Vertical", "Both"])
        t_layout.addWidget(self.flip_combo)
        group_transform.setLayout(t_layout)
        layout.addWidget(group_transform)
        
        # Trigger Button (To avoid calculating on every pixel drag, calculate on button press)
        self.btn_apply = QPushButton("Apply Transformations")
        self.btn_apply.clicked.connect(self.process_image_data)
        layout.addWidget(self.btn_apply)

        # Force initial calculation to set the screen state
        self.process_image_data()

        self.window.show()
        self.app.exec()

        # 2. Only close when the app (plugin) is exiting
        self.shm_raw.close()
        self.shm_display.close()

    def process_image_data(self):
        # 1. ALWAYS START FROM RAW DICOM to prevent data loss/compounding errors
        working_array = np.copy(self.raw_ref)
        
        # 3. Apply Windowing
        w = self.slider_width.value()
        l = self.slider_level.value()
        low = l - (w / 2)
        high = l + (w / 2)
        
        working_array = np.clip(working_array, low, high)
        working_array = (working_array - low) / (high - low + 1e-5)
        
        # 4. Apply Transformations
        rot_k = self.rotation_combo.currentIndex()
        if rot_k > 0:
            # Rotate on the YX axes (axes 1 and 2 in Z,Y,X)
            working_array = np.rot90(working_array, k=rot_k, axes=(1, 2)) 
            
        mode = self.flip_combo.currentText()
        if mode == "Horizontal": 
            working_array = np.flip(working_array, axis=2) # Flip X
        elif mode == "Vertical": 
            working_array = np.flip(working_array, axis=1) # Flip Y
        elif mode == "Both": 
            working_array = np.flip(working_array, axis=(1, 2))
            
        # --- CRITICAL FIX: MEMORY CONTIGUITY ---
        # Force NumPy to realign the bytes in RAM so it perfectly matches 
        # the linear structure expected by multiprocessing Shared Memory.
        working_array = np.ascontiguousarray(working_array)
            
        # 5. Safely write to Display Buffer
        with self.lock:
            display_buffer_array = np.ndarray(working_array.shape, dtype=self.dtype, buffer=self.shm_display.buf)
            display_buffer_array[:] = working_array[:]
            
        # 6. Signal the master to redraw and sync current slider values
        self.signal_queue.put({
            "action": "REFRESH",
            "shape": working_array.shape,
            "width": w,
            "level": l
        })