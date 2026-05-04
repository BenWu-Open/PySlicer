import os
import sys
import numpy as np
import pydicom
import multiprocessing
import logging
from multiprocessing import shared_memory

import vispy
# CRITICAL: This must be called BEFORE scene.SceneCanvas is used
vispy.use('pyqt6') 
from PyQt6 import QtGui, QtWidgets
from PyQt6.QtWidgets import (QApplication, QMainWindow, QGridLayout, QWidget, 
                             QLabel, QFileDialog, QVBoxLayout, QSlider, 
                             QDockWidget, QComboBox, QGroupBox, QPushButton, 
                             QMessageBox, QLineEdit, QHBoxLayout)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction
from vispy import scene

parentdir = (os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(1, parentdir)

import UI.MainUI as Main_UI
from pluginManager import pluginManager
from pluginManager import execute_script

from utils.SlicerLog import SlicerLog
formatter="%(asctime)s [%(levelname)s]\t [%(process)x:%(thread)x][%(funcName)s]   - %(message)s - [%(filename)s(%(lineno)d)]"
SlicerLog.setup_logger('Slicer_Log', "Slicer_Log.log", logging.INFO, formatter = formatter)
logger = SlicerLog.getLogger("Slicer_Log")

# =============================================================================
# MAIN WINDOW (DISPLAY SERVER)
# =============================================================================
class MainWindow(Main_UI.Ui_MainWindow, QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        super().setupUi(self)

        self.pluginManager = pluginManager()
        self.pluginManager.getPlugins()

        # REMOVED self.volume completely. The Main Process acts only as a display.
        self.raw_volume = None 
        self.ds0 = None
        self.cursor = [0, 0, 0] # Z, Y, X
        
        # Inter-Process Communication & Synchronization setup
        self.shm_lock = multiprocessing.Lock()
        self.signal_queue = multiprocessing.Queue()
        self.shm_raw = None
        self.shm_display = None
        self.current_display_shape = None
        
        self.setupPerimeter()
        self.update_Tool_menu()

        # Every 16 milliseconds Heartbeat to check for plugin updates (~60 FPS)
        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self.check_plugin_message)
        self.refresh_timer.start(16)

    def center(self):
        qr = self.frameGeometry()
        cp = QApplication.primaryScreen().availableGeometry().center()
        qr.moveCenter(cp)
        self.move(qr.topLeft())
    
    def setupPerimeter(self):
        self.setObjectName("MainWindow")
        
        icon_full_path = os.path.join(parentdir, 'cgu.ico')
        if os.path.exists(icon_full_path):
            self.app_icon = QtGui.QIcon()
            self.app_icon.addPixmap(QtGui.QPixmap(icon_full_path), QtGui.QIcon.Mode.Normal, QtGui.QIcon.State.Off)
            self.setWindowIcon(self.app_icon)

        self.resize(1200, 700)
        self.center()

        #Setup the dock menus on the left side
        self.MainTool.hide()
        self.MainBar.show()

        # -------------------------------------------------------------
        # Setup the 4 medical views
        # -------------------------------------------------------------
        self.canvases = {}
        self.views = {}
        self.images = {}
        self.lines = {}
        
        self.view_configs = [
            {"name": "Axial", "widget": self.axial_widget},
            {"name": "Sagittal", "widget": self.sagittal_widget},
            {"name": "Coronal", "widget": self.coronal_widget},
            {"name": "3D", "widget": self.d3_widget}
        ]
        
        for config in self.view_configs:
            name = config["name"]
            target_widget = config["widget"]
            
            # 1. Setup Layout on the Designer placeholder
            layout = QVBoxLayout(target_widget)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)
        
            # 2. Setup Title Bar with Maximize Button
            title_bar = QWidget()
            title_bar.setStyleSheet("background-color: #222;")
            title_layout = QHBoxLayout(title_bar)
            title_layout.setContentsMargins(5, 0, 5, 0)

            label = QLabel(f"<b>{name} View</b>")
            label.setStyleSheet("color: white; border: none;")
            
            max_btn = QPushButton("🗖")
            max_btn.setFixedSize(25, 20)
            max_btn.setStyleSheet("background-color: #444; color: white; font-weight: bold;")
            # Connect the button to the toggle method
            max_btn.clicked.connect(lambda checked, n=name: self.toggle_maximize(n))

            title_layout.addWidget(label)
            title_layout.addStretch()
            title_layout.addWidget(max_btn)
            
            layout.addWidget(title_bar)
        
            # 3. Initialize Vispy Canvas
            canvas = scene.SceneCanvas(keys='interactive', show=False) 
            view = canvas.central_widget.add_view()
        
            if name == "3D":
                view.camera = scene.ArcballCamera(fov=45)
            else:
                view.camera = scene.PanZoomCamera(aspect=1)

                # --- THIS LINE TO DISABLE WHEEL ZOOM, Mouse wheel are for ZYX movement not for zoom,
                # zoom is right click and mouse drag
                view.camera._viewbox.events.mouse_wheel.disconnect(view.camera.viewbox_mouse_event)

                canvas.events.mouse_press.connect(self.on_mouse_click)
                canvas.events.mouse_wheel.connect(self.on_mouse_wheel)
        
            # 4. FIX: Safely Bridge Vispy to PyQt6
            native_backend = canvas.native
            
            # Dig past the Vispy wrapper to find the actual Qt object
            if hasattr(native_backend, '_vispy_canvas_'):
                actual_qt_widget = native_backend
            else:
                actual_qt_widget = native_backend
            
            # Extract the actual QWindow handle
            window_handle = getattr(actual_qt_widget, 'windowHandle', lambda: None)()
            if window_handle is None:
                window_handle = actual_qt_widget
            
            try:
                # Add to layout properly bounded
                canvas_container = QWidget.createWindowContainer(window_handle, target_widget)
                canvas_container.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                layout.addWidget(canvas_container)
            except TypeError:
                # Fallback directly to the Qt widget if container rejects it
                layout.addWidget(actual_qt_widget)
        
            # 5. Store references
            self.canvases[name] = canvas
            self.views[name] = view
            self.images[name] = None
        
            # 6. Setup Crosshairs
            if name != "3D":
                # Setup Crosshair lines for 2D views
                h_line = scene.visuals.Line(color='red', width=1, parent=view.scene)
                v_line = scene.visuals.Line(color='red', width=1, parent=view.scene)
                self.lines[name] = [h_line, v_line]

        # -------------------------------------------------------------
        # Windowing setup
        # -------------------------------------------------------------
        control_layout = QVBoxLayout(self.dockWidgetContents)
        
        # COORDINATE DISPLAY
        self.lbl_coords = QLabel("X: 0, Y: 0, Z: 0")
        self.lbl_coords.setStyleSheet("font-weight: bold; color: #d32f2f; margin-bottom: 5px;")
        control_layout.addWidget(self.lbl_coords)

        # MANUAL INPUT
        group_input = QGroupBox("Jump to Coordinates (X, Y, Z)")
        input_layout = QHBoxLayout()
        self.edit_x = QLineEdit(); self.edit_x.setPlaceholderText("X")
        self.edit_y = QLineEdit(); self.edit_y.setPlaceholderText("Y")
        self.edit_z = QLineEdit(); self.edit_z.setPlaceholderText("Z")
        btn_jump = QPushButton("Go")
        btn_jump.clicked.connect(self.jump_to_coordinates)
        input_layout.addWidget(self.edit_x); input_layout.addWidget(self.edit_y); input_layout.addWidget(self.edit_z); input_layout.addWidget(btn_jump)
        group_input.setLayout(input_layout)
        control_layout.addWidget(group_input)

        group_contrast = QGroupBox("Contrast / Brightness")
        c_layout = QVBoxLayout()
        c_layout.addWidget(QLabel("Window Width"))
        self.slider_width = QSlider(Qt.Orientation.Horizontal)
        self.slider_width.setRange(1, 4000); self.slider_width.setValue(1500)
        self.slider_width.valueChanged.connect(self.apply_filter_and_update)
        c_layout.addWidget(self.slider_width)
        c_layout.addWidget(QLabel("Window Level"))
        self.slider_level = QSlider(Qt.Orientation.Horizontal)
        self.slider_level.setRange(-1000, 2000); self.slider_level.setValue(400)
        self.slider_level.valueChanged.connect(self.apply_filter_and_update)
        c_layout.addWidget(self.slider_level)
        group_contrast.setLayout(c_layout)
        control_layout.addWidget(group_contrast)
        
        # =========================================================================
        # Main Functions
        # =========================================================================
        self.actionOpen_Image.triggered.connect(self.load_dicom_series)
        self.actionExit.triggered.connect(QtWidgets.QApplication.quit)

    # =========================================================================
    # populate the plugin menu
    # =========================================================================
    def update_Tool_menu(self):
        """Cleans up and repopulates the dynamic portion of the menu."""
        
        # 1. Clear the old list in memory and physically rescan the folder
        self.pluginManager.plugins.clear()
        self.pluginManager.getPlugins()
        
        # 2. Clear the UI menu
        self.menuTools.clear()
        
        # 3. Add Dynamic Keywords
        for name in self.pluginManager.plugins:
            action = QAction(name, self)
            action.setData(name) # Store the keyword
            action.triggered.connect(self.handle_plugin_click)
            self.menuTools.addAction(action)
        
        # 4. Add the Rescan button at the bottom
        self.menuTools.addSeparator()
        action = QAction("Rescan Plugins", self)
        action.setData("Rescan Plugins") # Store the keyword
        action.triggered.connect(self.handle_plugin_click)
        self.menuTools.addAction(action)

    # =========================================================================
    # Handle the click on Tools for a plugin launch
    # =========================================================================
    def handle_plugin_click(self):
        action = self.sender()
        if action:
            keyword = action.data()
            print(f"Opening: {keyword}")

            plugin_to_run = keyword
            if plugin_to_run == "Rescan Plugins":
                self.update_Tool_menu()
                return

            # Safety check: Prevent image-dependent plugins from running without data
            #if self.raw_volume is None or self.shm_raw is None:
            #    QMessageBox.warning(self, "Warning", "Please load DICOM images first.")
            #    return

            # --- NEW: Build the Context Dictionary for Plugins ---
            plugin_context = {
                "shm_raw_name": self.shm_raw.name,
                "shm_display_name": self.shm_display.name,
                "shape": self.raw_volume.shape,
                "dtype": np.float32,
                "lock": self.shm_lock,
                "signal_queue": self.signal_queue,
                "initial_width": self.slider_width.value(),
                "initial_level": self.slider_level.value()
            }

            process = multiprocessing.Process(
                target=execute_script,
                args=(
                    self.pluginManager.logger_queue,
                    self.pluginManager.result_queue,
                    self.pluginManager.queue_to_child,
                    self.pluginManager.queue_from_child,
                    self.pluginManager.event_parent_sent,
                    self.pluginManager.event_child_sent,
                    self.pluginManager.event_child_sent_finish,
                    plugin_to_run,
                    self.pluginManager.plugins,
                    plugin_context  # <-- Pass context here
                )
            )
            process.start()

    def check_plugin_message(self):
        # 1. Drain the queue
        needs_update = False
        
        while not self.signal_queue.empty():
            try:
                # get_nowait() prevents the program from blocking/freezing
                msg = self.signal_queue.get_nowait()
                
                if msg.get("action") == "REFRESH":
                    # Update shape/state to the latest version
                    self.current_display_shape = tuple(msg.get("shape"))
                    
                    # SYNC Master UI sliders with the plugin!
                    if "width" in msg: 
                        self.slider_width.blockSignals(True)
                        self.slider_width.setValue(msg["width"])
                        self.slider_width.blockSignals(False)
                    if "level" in msg: 
                        self.slider_level.blockSignals(True)
                        self.slider_level.setValue(msg["level"])
                        self.slider_level.blockSignals(False)
                        
                    needs_update = True
                    
            except Exception:
                # Catch empty/full errors if they occur during the loop
                break
        
        # 2. Act only once, using the latest data
        if needs_update:
            self.update_all_views()
            needs_update = False

    # =========================================================================
    # VIEW TOGGLE LOGIC
    # =========================================================================
    def toggle_maximize(self, target_name):
        """Toggles between 4-pane grid view and 1-pane maximized view."""
        target_widget = next(cfg["widget"] for cfg in self.view_configs if cfg["name"] == target_name)
        
        # Check if we are currently maximized by looking for hidden widgets
        is_already_maximized = any(cfg["widget"].isHidden() for cfg in self.view_configs)

        if not is_already_maximized:
            # Hide all other widgets
            for cfg in self.view_configs:
                if cfg["widget"] != target_widget:
                    cfg["widget"].hide()
        else:
            # Show all widgets to restore grid
            for cfg in self.view_configs:
                cfg["widget"].show()
            
        # Force a canvas update to prevent rendering artifacts during resize
        for canvas in self.canvases.values():
            canvas.update()

    # =========================================================================
    # CORE LOGIC
    # =========================================================================
    def jump_to_coordinates(self):
        if self.current_display_shape is None: return
        try:
            z, y, x = int(self.edit_z.text()), int(self.edit_y.text()), int(self.edit_x.text())
            z_m, y_m, x_m = self.current_display_shape
            if 0 <= z < z_m and 0 <= y < y_m and 0 <= x < x_m:
                self.cursor = [z, y, x]
                self.update_cursor_label()
                self.update_all_views()
            else:
                QMessageBox.warning(self, "Out of bounds", f"Max range is Z:{z_m-1}, Y:{y_m-1}, X:{x_m-1}")
        except ValueError:
            QMessageBox.warning(self, "Invalid Input", "Please enter integer numbers for Z, Y, and X.")

    def update_cursor_label(self):
        z, y, x = self.cursor
        self.lbl_coords.setText(f"X: {x}, Y: {y}, Z: {z}")

    def load_dicom_series(self):
        path = QFileDialog.getExistingDirectory(self, "Select DICOM Folder")
        if not path: return
        files = [os.path.join(path, f) for f in os.listdir(path) if f.lower().endswith(".dcm")]
        datasets = [pydicom.dcmread(f) for f in files]
        datasets.sort(key=lambda x: int(getattr(x, "InstanceNumber", 0)))
        if not datasets: return
        
        self.ds0 = datasets[0] 
        self.raw_volume = np.stack([ds.pixel_array for ds in datasets]).astype(np.float32)
        self.current_display_shape = self.raw_volume.shape
        z, y, x = self.current_display_shape
        self.cursor = [z // 2, y // 2, x // 2]
        
        # 1. Clear old SHM if exists
        self.cleanup_shm()

        # 2. Create Shared Memory blocks
        self.shm_raw = shared_memory.SharedMemory(create=True, size=self.raw_volume.nbytes)
        raw_array = np.ndarray(self.raw_volume.shape, dtype=np.float32, buffer=self.shm_raw.buf)
        raw_array[:] = self.raw_volume[:]

        self.shm_display = shared_memory.SharedMemory(create=True, size=self.raw_volume.nbytes)
        
        self.update_cursor_label()
        self.apply_filter_and_update() # Performs initial local windowing 
        self.MainTool.show()

    def apply_filter_and_update(self):
        # Master fallback for sliders if no plugin is active.
        if self.shm_raw is None or self.shm_display is None: return
        
        w, l = self.slider_width.value(), self.slider_level.value()
        low, high = l - (w / 2), l + (w / 2)
        
        with self.shm_lock:
            raw_array = np.ndarray(self.raw_volume.shape, dtype=np.float32, buffer=self.shm_raw.buf)
            disp_array = np.ndarray(self.current_display_shape, dtype=np.float32, buffer=self.shm_display.buf)
            
            # Only do local math if shape hasn't been rotated by a plugin
            if raw_array.shape == disp_array.shape:
                temp = np.clip(raw_array, low, high)
                temp = (temp - low) / (high - low + 1e-5)
                disp_array[:] = temp[:]
        
        self.update_all_views()

    def on_mouse_wheel(self, event):
        if self.current_display_shape is None: return
        name = next(k for k, v in self.canvases.items() if v == event.source)
        direction = int(np.sign(event.delta[1])) 
        z_max, y_max, x_max = self.current_display_shape # NOW USES SHM SHAPE
        
        if name == "Axial": self.cursor[0] = np.clip(self.cursor[0] + direction, 0, z_max - 1)
        elif name == "Sagittal": self.cursor[2] = np.clip(self.cursor[2] + direction, 0, x_max - 1)
        elif name == "Coronal": self.cursor[1] = np.clip(self.cursor[1] + direction, 0, y_max - 1)
        
        self.update_cursor_label()
        self.update_all_views()
        
        # ---------------------------------------------------------
        # CRITICAL FIX: Stop the camera from zooming
        # ---------------------------------------------------------
        event.handled = True

    def on_mouse_click(self, event):
        if self.current_display_shape is None: return

        # 1. Check which button was pressed
        if event.button == 1:
            pass # Left click logic
        elif event.button == 2:
            return # Exit early so right-click doesn't move the crosshairs

        name = next(k for k, v in self.canvases.items() if v == event.source)
        tr = self.views[name].scene.node_transform(self.images[name])
        pos = tr.map(event.pos)[:2]
        z_max, y_max, x_max = self.current_display_shape # NOW USES SHM SHAPE
        
        if name == "Axial": self.cursor[2], self.cursor[1] = np.clip(int(pos[0]), 0, x_max-1), np.clip(int(y_max-pos[1]), 0, y_max-1)
        elif name == "Sagittal": self.cursor[1], self.cursor[0] = np.clip(int(y_max-pos[0]), 0, y_max-1), np.clip(int(z_max-pos[1]), 0, z_max-1)
        elif name == "Coronal": self.cursor[2], self.cursor[0] = np.clip(int(pos[0]), 0, x_max-1), np.clip(int(z_max-pos[1]), 0, z_max-1)
        
        self.update_cursor_label()
        self.update_all_views()

    def update_all_views(self):
        if self.shm_display is None: return
        z, y, x = self.cursor
        
        with self.shm_lock:
            # READ DIRECTLY FROM SHARED MEMORY
            display_volume = np.ndarray(self.current_display_shape, dtype=np.float32, buffer=self.shm_display.buf)
            
            # Check for empty data: If sum is 0, the buffer is likely uninitialized
            if np.sum(display_volume) == 0: return

            # 1. Axial: Already (Y, X). Standard view.
            axial = np.flipud(display_volume[z, :, :])
            
            # 2. Sagittal: Slice is (Z, Y). 
            sagittal = np.fliplr(display_volume[:, :, x])
            
            # 3. Coronal: Slice is (Z, X).
            coronal = (display_volume[:, y, :])

        # Update the panes
        self.draw_pane("Axial", axial, x, y)
        self.draw_pane("Sagittal", sagittal, y, z)
        self.draw_pane("Coronal", coronal, x, z)

        # --- RESTORE THIS CALL ---
        #self.draw_3d_pane()

    def draw_pane(self, name, img_data, cx, cy):
        h, w = img_data.shape
        if self.images[name] is None: 
            self.images[name] = scene.visuals.Image(img_data, parent=self.views[name].scene, cmap="grays")
        else: 
            self.images[name].set_data(img_data)
            
        # --- CRITICAL FIX: VISPY COLOR LIMITS ---
        # If data is normalized (max value is ~1.0), force Vispy to use 0-1 bounds.
        # Otherwise, use the raw data's natural min/max.
        if img_data.max() <= 1.01:
            self.images[name].clim = (0.0, 1.0)
        else:
            self.images[name].clim = (float(img_data.min()), float(img_data.max()))

        self.views[name].camera.rect = (0, 0, w, h)
        self.lines[name][0].set_data(np.array([[0, cy], [w, cy]]))
        self.lines[name][1].set_data(np.array([[cx, 0], [cx, h]]))
        self.canvases[name].update()

    def draw_3d_pane(self):
        if self.shm_display is None: return
        
        with self.shm_lock:
            # 1. Safely read from shared memory
            display_volume = np.ndarray(self.current_display_shape, dtype=np.float32, buffer=self.shm_display.buf)
            
            # 2. Normalize volume to 0.0 - 1.0 for the 3D Volume Visual
            vol_min, vol_max = display_volume.min(), display_volume.max()
            vol_norm = (display_volume - vol_min) / (vol_max - vol_min + 1e-5)
        
        # Calculate dynamic scaling based on current shape
        s = np.array(self.current_display_shape)
        max_dim = s.max()
        scale_tuple = (1.0/max_dim, 1.0/max_dim, 1.0/max_dim)
        
        if self.images["3D"] is None:
            # Initialize Volume
            self.images["3D"] = scene.visuals.Volume(
                vol_norm, 
                parent=self.views["3D"].scene, 
                method='iso', 
                threshold=0.1, # Adjust this slider-like value to see organs
                cmap='viridis',
                interpolation='linear'
            )
            
            # Scale makes it fit in the window; Translate centers it at (0,0,0)
            self.images["3D"].transform = scene.transforms.STTransform(
                scale=scale_tuple,
                translate=(-0.5, -0.5, -0.5)
            )
        else:
            # Update existing volume
            self.images["3D"].set_data(vol_norm)
            
            # Re-apply transform in case a plugin rotated the shape and altered dimensions
            self.images["3D"].transform = scene.transforms.STTransform(
                scale=scale_tuple,
                translate=(-0.5, -0.5, -0.5)
            )
        
        # Set camera distance to perfectly frame the 1.0x1.0x1.0 scaled volume
        self.views["3D"].camera.distance = 2.0
            
        self.canvases["3D"].update()

    def cleanup_shm(self):
        if self.shm_raw:
            self.shm_raw.close()
            self.shm_raw.unlink()
            self.shm_raw = None
        if self.shm_display:
            self.shm_display.close()
            self.shm_display.unlink()
            self.shm_display = None

    def closeEvent(self, event):
        self.cleanup_shm()
        super().closeEvent(event)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())