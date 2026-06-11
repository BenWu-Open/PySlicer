import os
import sys
import json
import uuid
import numpy as np
import pydicom
import multiprocessing
import logging
import math
from multiprocessing import shared_memory

import vispy
# CRITICAL: This must be called BEFORE scene.SceneCanvas is used
vispy.use('pyqt6') 
from PyQt6 import QtGui, QtWidgets
from PyQt6.QtWidgets import (QApplication, QMainWindow, QGridLayout, QWidget, 
                             QLabel, QFileDialog, QVBoxLayout, QSlider, 
                             QDockWidget, QComboBox, QGroupBox, QPushButton, 
                             QMessageBox, QLineEdit, QHBoxLayout, QScrollBar,
                             QCheckBox, QInputDialog)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction
from vispy import scene

import shutil
import zipfile
import requests
import dicom2nifti
import uuid
from totalsegmentator.python_api import totalsegmentator
import pickle
import colorsys
import nibabel as nib





'''
from PyQt5.QtGui import QMovie
self.label_loading         = self.tab_DSA_TopNList.findChild(QtWidgets.QLabel,       'label_loading')
            
self.movie = QMovie(os.path.join(parentdir, "Tools\\image\\loading-small.gif"))
self.label_loading.setMovie(self.movie)
self.label_loading.setHidden(True)
'''

# Ensure the working directory is correct when running as a compiled executable
if getattr(sys, 'frozen', False):
    GLOBAL_CWD = os.path.dirname(sys.executable)
    os.chdir(GLOBAL_CWD)
    sys.path.insert(1, GLOBAL_CWD)

    if (os.path.exists(os.getcwd() + r'/plugins')) == False:
        os.makedirs('plugins')

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
# CUSTOM JSON ENCODER FOR NUMPY TYPES
# =============================================================================
class NumpyEncoder(json.JSONEncoder):
    """ Special json encoder for numpy types to prevent truncation errors when saving """
    def default(self, obj):
        if isinstance(obj, np.integer): return int(obj)
        if isinstance(obj, np.floating): return float(obj)
        if isinstance(obj, np.ndarray): return obj.tolist()
        return super(NumpyEncoder, self).default(obj)

# =============================================================================
# MAIN WINDOW (DISPLAY SERVER)
# =============================================================================
class MainWindow(Main_UI.Ui_MainWindow, QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        super().setupUi(self)

        self.pluginManager = pluginManager()
        self.pluginManager.getPlugins()

        self.raw_volume = None 
        self.ds0 = None
        self.spacing = (1.0, 1.0, 1.0)  # dz, dy, dx in mm
        self.cursor = [0, 0, 0] # Internal arrays strictly use 0-based format
        
        # --- Series UID for Safety Guardrails ---
        self.series_uid = None
        
        # ---------------------------------------------------------
        # Markups State (Aligned with 3D Slicer Terminology)
        # ---------------------------------------------------------
        self.markups = {"Fiducial": [], "ClosedCurve": []}
        self.current_curve_pixels = [] # For internal UI rendering
        self.current_curve_world = []  # For standard Slicer exporting
        self.annotation_mode = "Fiducial" 
        
        # ---------------------------------------------------------
        # DICOM Spatial Metadata
        # ---------------------------------------------------------
        self.pixel_spacing = [1.0, 1.0] 
        self.slice_thickness = 1.0      
        self.origin = [0.0, 0.0, 0.0]   
        
        self.x_sign = 1.0
        self.y_sign = 1.0
        self.z_sign = 1.0

        # ---------------------------------------------------------
        # Dynamic Orientation Flags (Guarantees LPS format)
        # ---------------------------------------------------------
        self.flip_axial_ud = False
        self.flip_axial_lr = False
        self.flip_sagittal_ud = False
        self.flip_sagittal_lr = False
        self.flip_coronal_ud = False
        self.flip_coronal_lr = False
        
        # Scrollbar Direction Logic
        self.x_dir_invert = False
        self.y_dir_invert = False
        self.z_dir_invert = False

        # ---------------------------------------------------------
        # Hairline / Crosshair State
        # ---------------------------------------------------------
        self.show_crosshair = False


        # Inter-Process Communication
        self.shm_lock = multiprocessing.Lock()
        self.signal_queue = multiprocessing.Queue()
        self.shm_raw = None
        self.shm_display = None
        self.current_display_shape = None
        
        self.setupPerimeter()
        self.update_Tool_menu()

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

        self.MainTool.hide()
        self.MainBar.show()

        # -------------------------------------------------------------
        # Setup the 4 medical views (Visual Graphics Layer)
        # -------------------------------------------------------------
        self.canvases = {}      
        self.views = {}         
        self.images = {}        
        self.lines = {}         
        self.scrollbars = {}    
        self.visual_fiducials = {} 
        self.visual_curves = {}
        
        self.view_configs = [
            {"name": "Axial", "widget": self.axial_widget},
            {"name": "Sagittal", "widget": self.sagittal_widget},
            {"name": "Coronal", "widget": self.coronal_widget},
            {"name": "3D", "widget": self.d3_widget}
        ]
        
        for config in self.view_configs:
            name = config["name"]
            target_widget = config["widget"]
            
            layout = QVBoxLayout(target_widget)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)
        
            title_bar = QWidget()
            title_bar.setStyleSheet("background-color: #222;")
            title_layout = QHBoxLayout(title_bar)
            title_layout.setContentsMargins(5, 0, 5, 0)

            label = QLabel(f"<b>{name} View</b>")
            label.setStyleSheet("color: white; border: none;")
            
            max_btn = QPushButton("🗖")
            max_btn.setFixedSize(25, 20)
            max_btn.setStyleSheet("background-color: #444; color: white; font-weight: bold;")
            max_btn.clicked.connect(lambda checked, n=name: self.toggle_maximize(n))

            title_layout.addWidget(label)
            title_layout.addStretch()
            title_layout.addWidget(max_btn)
            
            layout.addWidget(title_bar)
        
            canvas_scroll_layout = QHBoxLayout()
            canvas = scene.SceneCanvas(keys='interactive', show=False) 
            view = canvas.central_widget.add_view()
        
            if name == "3D":
                view.camera = scene.ArcballCamera(fov=45)
            else:
                view.camera = scene.PanZoomCamera(aspect=1)
                view.camera._viewbox.events.mouse_wheel.disconnect(view.camera.viewbox_mouse_event)
                canvas.events.mouse_press.connect(self.on_mouse_click)
                canvas.events.mouse_wheel.connect(self.on_mouse_wheel)

                scrollbar = QScrollBar(Qt.Orientation.Vertical)
                scrollbar.valueChanged.connect(lambda value, n=name: self.on_scrollbar_scroll(n, value))
                self.scrollbars[name] = scrollbar
        
            native_backend = canvas.native
            if hasattr(native_backend, '_vispy_canvas_'): actual_qt_widget = native_backend
            else: actual_qt_widget = native_backend
            
            window_handle = getattr(actual_qt_widget, 'windowHandle', lambda: None)()
            if window_handle is None: window_handle = actual_qt_widget
            
            try:
                canvas_container = QWidget.createWindowContainer(window_handle, target_widget)
                canvas_container.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                canvas_scroll_layout.addWidget(canvas_container, stretch=1)
            except TypeError:
                canvas_scroll_layout.addWidget(actual_qt_widget, stretch=1)
                
            if name != "3D":
                canvas_scroll_layout.addWidget(self.scrollbars[name])
                
            layout.addLayout(canvas_scroll_layout)
        
            self.canvases[name] = canvas
            self.views[name] = view
            self.images[name] = None
            
            if name != "3D":
                self.visual_fiducials[name] = scene.visuals.Markers(parent=view.scene)
                self.visual_fiducials[name].set_gl_state('translucent', depth_test=False)
                
                self.visual_curves[name] = [] 
                
                h_line = scene.visuals.Line(color='red', width=1, parent=view.scene)
                v_line = scene.visuals.Line(color='red', width=1, parent=view.scene)
                h_line.set_gl_state('translucent', depth_test=False)
                v_line.set_gl_state('translucent', depth_test=False)
                self.lines[name] = [h_line, v_line]

        # -------------------------------------------------------------
        # Connect MainUI Control Actions
        # -------------------------------------------------------------
        self.scroll_layout = QVBoxLayout(self.scrollAreaWidgetContents)
        self.scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        self.btn_jump.clicked.connect(self.jump_to_coordinates)
        
        self.slider_width.setRange(1, 4000)
        self.slider_width.setValue(1500)
        self.slider_level.setRange(-1000, 2000)
        self.slider_level.setValue(400)
        
        self.slider_width.valueChanged.connect(self.apply_filter_and_update)
        self.slider_level.valueChanged.connect(self.apply_filter_and_update)
        
        self.checkBox_crosshair.stateChanged.connect(self.toggle_crosshair_ui)
        self.checkBox_3D.stateChanged.connect(self.toggle_3d_pane)

        self.group_anno.toggled.connect(self.on_anno_group_toggled) 
        self.radioButton_Point.toggled.connect(self.change_anno_mode)
        self.radioButton_Curve.toggled.connect(self.change_anno_mode)
        self.btn_close_curve.clicked.connect(self.finish_curve)
        self.btn_save.clicked.connect(self.save_markups)
        self.btn_load.clicked.connect(self.load_markups)
        
        self.actionOpen_Image.triggered.connect(self.load_dicom_series)
        self.actionClose_Image.triggered.connect(self.close_image)
        self.actionExit.triggered.connect(QtWidgets.QApplication.quit)

        self.change_anno_mode()

    # =========================================================================
    # MARKUP LIST UI & LOGIC
    # =========================================================================
    def on_anno_group_toggled(self, is_checked):
        if not is_checked:
            self.current_curve_pixels = []
            self.current_curve_world = []
            self.update_all_views()

    def change_anno_mode(self):
        if self.radioButton_Curve.isChecked():
            self.annotation_mode = "ClosedCurve"
            self.btn_close_curve.show()
        else:
            self.annotation_mode = "Fiducial"
            self.btn_close_curve.hide()
            self.current_curve_pixels = []
            self.current_curve_world = []
            self.update_all_views()

    def finish_curve(self):
        if len(self.current_curve_pixels) > 2:
            curve_name, ok = QInputDialog.getText(self, "Curve Name", "Enter name for this Closed Curve:")
            if ok and curve_name.strip():
                anno_id = str(uuid.uuid4())
                new_curve = {
                    "id": anno_id, 
                    "name": curve_name.strip(), 
                    "visible": True, 
                    "pixels": self.current_curve_pixels,
                    "world_lps": self.current_curve_world
                }
                self.markups["ClosedCurve"].append(new_curve)
                self.add_markup_ui_row("ClosedCurve", new_curve)
                
        self.current_curve_pixels = []
        self.current_curve_world = []
        self.update_all_views()

    def add_markup_ui_row(self, anno_type, anno_data):
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 2, 0, 2)
        
        chk = QCheckBox()
        chk.setChecked(anno_data.get("visible", True))
        chk.toggled.connect(lambda checked, d=anno_data: self.toggle_markup_visibility(d, checked))
        
        # --- NEW: Extract XYZ for display in the UI list ---
        coord_str = ""
        if "pixel_xyz" in anno_data:
            x, y, z = anno_data["pixel_xyz"]
            coord_str = f"  ({x}, {y}, {z})"
        elif "pixels" in anno_data and anno_data["pixels"]:
            x, y, z = anno_data["pixels"][0]
            coord_str = f"  ({x}, {y}, {z}...)"
        elif "coordinate" in anno_data: # Legacy point fallback
            try:
                x, y, z = anno_data["coordinate"]["pixel_xyz"]
                coord_str = f"  ({x}, {y}, {z})"
            except KeyError:
                pass
                
        lbl = QLabel(f"[{anno_type[:3].upper()}] {anno_data['name']}{coord_str}")
        
        btn_del = QPushButton("X")
        btn_del.setFixedSize(20, 20)
        btn_del.setStyleSheet("color: white; background-color: darkred; font-weight: bold; border-radius: 2px;")
        btn_del.clicked.connect(lambda _, rw=row_widget, d=anno_data, at=anno_type: self.delete_markup(rw, d, at))
        
        row_layout.addWidget(chk)
        row_layout.addWidget(lbl)
        row_layout.addStretch()
        row_layout.addWidget(btn_del)
        
        self.scroll_layout.addWidget(row_widget)

    def toggle_markup_visibility(self, anno_data, is_visible):
        anno_data["visible"] = is_visible
        self.update_all_views()
        
    def delete_markup(self, row_widget, anno_data, anno_type):
        if anno_type == "Fiducial" and anno_data in self.markups["Fiducial"]:
            self.markups["Fiducial"].remove(anno_data)
        elif anno_type == "ClosedCurve" and anno_data in self.markups["ClosedCurve"]:
            self.markups["ClosedCurve"].remove(anno_data)
            
        row_widget.setParent(None)
        row_widget.deleteLater()
        self.update_all_views()

    def clear_markup_ui(self):
        while self.scroll_layout.count():
            item = self.scroll_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()

    def rebuild_markup_ui(self):
        self.clear_markup_ui()
        for pt in self.markups["Fiducial"]:
            self.add_markup_ui_row("Fiducial", pt)
        for crv in self.markups["ClosedCurve"]:
            self.add_markup_ui_row("ClosedCurve", crv)

    # =========================================================================
    # 3D SLICER COMPATIBLE JSON EXPORT/IMPORT
    # =========================================================================
    def save_markups(self):
        if not self.markups["Fiducial"] and not self.markups["ClosedCurve"]:
            QMessageBox.information(self, "Info", "No markups to save.")
            return
            
        filters = "3D Slicer Markups (*.mrk.json);;ITK-SNAP Landmarks (*.txt);;Raw JSON (*.json)"
        file_path, selected_filter = QFileDialog.getSaveFileName(self, "Save Markups", "", filters)
        
        if not file_path: return
            
        try:
            if "ITK-SNAP" in selected_filter:
                with open(file_path, 'w') as f:
                    f.write("# ITK-SNAP Landmark File exported from PySlicer\n")
                    f.write("# X Y Z Label\n")
                    
                    for pt in self.markups["Fiducial"]:
                        if pt["visible"]:
                            x, y, z = pt["world_lps"]
                            safe_name = pt["name"].replace(" ", "_")
                            f.write(f"{x:.4f} {y:.4f} {z:.4f} {safe_name}\n")
                            
                    for crv in self.markups["ClosedCurve"]:
                        if crv["visible"]:
                            for idx, pt in enumerate(crv["world_lps"]):
                                x, y, z = pt
                                safe_name = crv["name"].replace(" ", "_")
                                f.write(f"{x:.4f} {y:.4f} {z:.4f} {safe_name}_P{idx}\n")
                                
                QMessageBox.information(self, "Success", "Markups saved as ITK-SNAP Landmarks.")
                
            else:
                slicer_format = {
                    "@schema": "https://raw.githubusercontent.com/slicer/slicer/master/Modules/Loadable/Markups/Resources/Schema/markups-schema-v1.0.0.json#",
                    "pyslicer_metadata": {
                        "series_uid": self.series_uid,
                        "origin": self.origin,
                        "pixel_spacing": self.pixel_spacing,
                        "slice_thickness": self.slice_thickness
                    },
                    "markups": []
                }

                if self.markups["Fiducial"]:
                    fiducial_group = {
                        "type": "Fiducial",
                        "name": "PySlicer_Fiducials",
                        "coordinateSystem": "LPS",
                        "controlPoints": []
                    }
                    for f_obj in self.markups["Fiducial"]:
                        if f_obj["visible"]:
                            fiducial_group["controlPoints"].append({
                                "id": f_obj["id"],
                                "label": f_obj["name"],
                                "position": f_obj["world_lps"]
                            })
                    slicer_format["markups"].append(fiducial_group)

                for c in self.markups["ClosedCurve"]:
                    if c["visible"]:
                        curve_markup = {
                            "type": "ClosedCurve",
                            "name": c["name"],
                            "coordinateSystem": "LPS",
                            "controlPoints": []
                        }
                        for idx, pt in enumerate(c["world_lps"]):
                            curve_markup["controlPoints"].append({
                                "id": f"P{idx}",
                                "label": f"{c['name']}_{idx}",
                                "position": pt
                            })
                        slicer_format["markups"].append(curve_markup)

                with open(file_path, 'w') as f:
                    json.dump(slicer_format, f, indent=4, cls=NumpyEncoder)
                
                if ".mrk.json" in selected_filter:
                    QMessageBox.information(self, "Success", "Markups saved in 3D Slicer format.")
                else:
                    QMessageBox.information(self, "Success", "Markups saved as Raw JSON.")
                    
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not save file: {e}")

    def load_markups(self):
        if self.series_uid is None:
            QMessageBox.warning(self, "Warning", "Please load a DICOM series before loading markups.")
            return

        filters = "All Markups (*.mrk.json *.json *.txt);;Slicer JSON (*.mrk.json *.json);;ITK-SNAP Text (*.txt)"
        file_path, _ = QFileDialog.getOpenFileName(self, "Load Markups", "", filters)
        if not file_path: return
        
        try:
            if file_path.endswith('.txt'):
                self.markups = {"Fiducial": [], "ClosedCurve": []}
                self.current_curve_pixels = []
                self.current_curve_world = []
                
                with open(file_path, 'r') as f:
                    lines = f.readlines()
                    
                points_loaded = 0
                for line in lines:
                    line = line.strip()
                    if not line or line.startswith('#'): continue 
                    
                    parts = line.split()
                    if len(parts) >= 4:
                        try:
                            x, y, z = float(parts[0]), float(parts[1]), float(parts[2])
                            name = " ".join(parts[3:]).replace("_", " ") 
                            
                            pixel_xyz = self.get_pixel_coordinates(x, y, z)
                            
                            self.markups["Fiducial"].append({
                                "id": str(uuid.uuid4()), 
                                "name": name, 
                                "visible": True, 
                                "pixel_xyz": pixel_xyz,
                                "world_lps": [x, y, z]
                            })
                            points_loaded += 1
                        except ValueError:
                            continue 
                            
                self.rebuild_markup_ui()
                self.update_all_views()
                QMessageBox.information(self, "Success", f"Loaded {points_loaded} ITK-SNAP Landmarks successfully.\n\nNote: ITK-SNAP does not support safety guardrails.")
                return

            with open(file_path, 'r') as f:
                data = json.load(f)
            
            meta = data.get("pyslicer_metadata", data.get("metadata", {}))
            json_uid = meta.get("series_uid", "UNKNOWN")
            json_origin = meta.get("origin", [])
            json_spacing = meta.get("pixel_spacing", [])
            json_thick = meta.get("slice_thickness", 0.0)

            if json_uid == "UNKNOWN":
                reply = QMessageBox.warning(self, "Unknown Identity",
                                             "This JSON lacks specific patient identification metadata.\n"
                                             "Are you absolutely sure it belongs to the current patient?",
                                             QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                             QMessageBox.StandardButton.No)
                if reply == QMessageBox.StandardButton.No: return
            elif json_uid != self.series_uid:
                reply = QMessageBox.critical(self, "UID Mismatch",
                                             "DANGER: The loaded markups belong to a DIFFERENT scan/patient!\n\n"
                                             "Do you want to FORCE load them anyway?",
                                             QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                             QMessageBox.StandardButton.No)
                if reply == QMessageBox.StandardButton.No: return

            geometry_warnings = []
            if len(json_origin) == 3 and len(self.origin) == 3:
                if not all(math.isclose(json_origin[i], self.origin[i], abs_tol=0.01) for i in range(3)):
                    geometry_warnings.append("• Patient Origin has shifted.")
            if len(json_spacing) == 2 and len(self.pixel_spacing) == 2:
                if not all(math.isclose(json_spacing[i], self.pixel_spacing[i], abs_tol=0.01) for i in range(2)):
                    geometry_warnings.append("• Pixel Spacing (Resolution) is different.")
            if json_thick > 0 and not math.isclose(json_thick, self.slice_thickness, abs_tol=0.01):
                geometry_warnings.append("• Slice Thickness is different.")

            if geometry_warnings:
                msg = ("WARNING: The spatial geometry of this JSON does not match the loaded DICOM.\n\n" + 
                       "\n".join(geometry_warnings) + "\n\nProceed anyway?")
                reply = QMessageBox.warning(self, "Spatial Mismatch", msg,
                                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                            QMessageBox.StandardButton.No)
                if reply == QMessageBox.StandardButton.No: return

            # Support Legacy Points/Curves from earlier PySlicer versions
            if "annotations" in data and ("points" in data["annotations"] or "labels" in data["annotations"]):
                loaded_points = []
                raw_points = data.get("annotations", {}).get("points", data.get("annotations", {}).get("labels", []))
                for pt in raw_points:
                    if "point" not in pt and "coordinate" not in pt: 
                        loaded_points.append({"id": str(uuid.uuid4()), "name": "Legacy Point", "visible": True, "pixel_xyz": pt})
                    else:
                        coord = pt.get("coordinate", pt.get("point"))
                        loaded_points.append({
                            "id": pt.get("id", str(uuid.uuid4())), 
                            "name": pt.get("name", "Point"), 
                            "visible": pt.get("visible", True), 
                            "pixel_xyz": coord.get("pixel_xyz", []),
                            "world_lps": coord.get("world_lps", [])
                        })
                
                loaded_curves = []
                raw_curves = data.get("annotations", {}).get("curves", data.get("annotations", {}).get("segmentations", []))
                for crv in raw_curves:
                    if isinstance(crv, list): 
                        loaded_curves.append({"id": str(uuid.uuid4()), "name": "Legacy Curve", "visible": True, "pixels": crv})
                    else:
                        vertices = crv.get("vertices", crv.get("points", []))
                        pixels = [v.get("pixel_xyz", []) for v in vertices if "pixel_xyz" in v]
                        world = [v.get("world_lps", []) for v in vertices if "world_lps" in v]
                        loaded_curves.append({
                            "id": crv.get("id", str(uuid.uuid4())), 
                            "name": crv.get("name", "Curve"), 
                            "visible": crv.get("visible", True), 
                            "pixels": pixels,
                            "world_lps": world
                        })
                        
                self.markups = {"Fiducial": loaded_points, "ClosedCurve": loaded_curves}

            else:
                # Load Native Slicer Format
                self.markups = {"Fiducial": [], "ClosedCurve": []}
                
                for item in data.get("markups", []):
                    markup_type = item.get("type", "")
                    
                    if markup_type == "Fiducial":
                        for cp in item.get("controlPoints", []):
                            world_lps = cp.get("position", [])
                            if len(world_lps) == 3:
                                pixel_xyz = self.get_pixel_coordinates(*world_lps)
                                self.markups["Fiducial"].append({
                                    "id": cp.get("id", str(uuid.uuid4())), 
                                    "name": cp.get("label", "Fiducial"), 
                                    "visible": True, 
                                    "pixel_xyz": pixel_xyz,
                                    "world_lps": world_lps
                                })
                                
                    elif markup_type == "ClosedCurve":
                        curve_pixels = []
                        curve_world = []
                        for cp in item.get("controlPoints", []):
                            world_lps = cp.get("position", [])
                            if len(world_lps) == 3:
                                curve_world.append(world_lps)
                                curve_pixels.append(self.get_pixel_coordinates(*world_lps))
                                
                        if curve_pixels:
                            self.markups["ClosedCurve"].append({
                                "id": str(uuid.uuid4()), 
                                "name": item.get("name", "ClosedCurve"), 
                                "visible": True, 
                                "pixels": curve_pixels,
                                "world_lps": curve_world
                            })

            self.current_curve_pixels = []
            self.current_curve_world = []
            
            self.rebuild_markup_ui()
            self.update_all_views()
            QMessageBox.information(self, "Success", "Markups loaded successfully.")
            
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load file:\n{str(e)}")

    def get_world_coordinates(self, pixel_x, pixel_y, pixel_z):
        world_x = self.origin[0] + (pixel_x * self.pixel_spacing[1] * self.x_sign)
        world_y = self.origin[1] + (pixel_y * self.pixel_spacing[0] * self.y_sign)
        world_z = self.origin[2] + (pixel_z * self.slice_thickness * self.z_sign)
        return [world_x, world_y, world_z]
        
    def get_pixel_coordinates(self, world_x, world_y, world_z):
        pixel_x = int(round((world_x - self.origin[0]) / (self.pixel_spacing[1] * self.x_sign)))
        pixel_y = int(round((world_y - self.origin[1]) / (self.pixel_spacing[0] * self.y_sign)))
        pixel_z = int(round((world_z - self.origin[2]) / (self.slice_thickness * self.z_sign)))
        return [pixel_x + 1, pixel_y + 1, pixel_z + 1] 

    def translate_coordinates(self, name, x_val, y_val):
        if self.current_display_shape is None: return x_val, y_val
        z_max, y_max, x_max = self.current_display_shape
        
        if name == "Axial":
            out_x = (x_max - 1) - x_val if self.flip_axial_lr else x_val
            out_y = (y_max - 1) - y_val if self.flip_axial_ud else y_val
            return out_x, out_y
        elif name == "Sagittal":
            out_x = (y_max - 1) - x_val if self.flip_sagittal_lr else x_val
            out_y = (z_max - 1) - y_val if self.flip_sagittal_ud else y_val
            return out_x, out_y
        elif name == "Coronal":
            out_x = (x_max - 1) - x_val if self.flip_coronal_lr else x_val
            out_y = (z_max - 1) - y_val if self.flip_coronal_ud else y_val
            return out_x, out_y

    # =========================================================================
    # SCROLLBAR SYNCHRONIZATION LOGIC
    # =========================================================================
    def on_scrollbar_scroll(self, view_name, value):
        if self.current_display_shape is None: return
        z_max, y_max, x_max = self.current_display_shape
        
        if view_name == "Axial": self.cursor[0] = (z_max - 1) - value if self.z_dir_invert else value
        elif view_name == "Sagittal": self.cursor[2] = (x_max - 1) - value if self.x_dir_invert else value
        elif view_name == "Coronal": self.cursor[1] = (y_max - 1) - value if self.y_dir_invert else value
        
        self.update_cursor_label()
        self.update_all_views()

    def sync_scrollbars_to_cursor(self):
        if not hasattr(self, 'scrollbars') or self.current_display_shape is None: return
        z, y, x = self.cursor
        z_max, y_max, x_max = self.current_display_shape
        
        for name, sb in self.scrollbars.items():
            sb.blockSignals(True)
            if name == "Axial": sb.setValue((z_max - 1) - z if self.z_dir_invert else z)
            elif name == "Sagittal": sb.setValue((x_max - 1) - x if self.x_dir_invert else x)
            elif name == "Coronal": sb.setValue((y_max - 1) - y if self.y_dir_invert else y)
            sb.blockSignals(False)

    # =========================================================================
    # PLUGIN MANAGEMENT
    # =========================================================================
    def update_Tool_menu(self):
        self.pluginManager.plugins.clear()
        self.pluginManager.getPlugins()
        self.menuTools.clear()
        
        for name in self.pluginManager.plugins:
            action = QAction(name, self)
            action.setData(name)
            action.triggered.connect(self.handle_plugin_click)
            self.menuTools.addAction(action)
        
        self.menuTools.addSeparator()
        action = QAction("Rescan Plugins", self)
        action.setData("Rescan Plugins")
        action.triggered.connect(self.handle_plugin_click)
        self.menuTools.addAction(action)

    def handle_plugin_click(self):
        action = self.sender()
        if action:
            keyword = action.data()
            print(f"Opening: {keyword}")

            if keyword == "Rescan Plugins":
                self.update_Tool_menu()
                return

            if self.raw_volume is None or self.shm_raw is None:
                QMessageBox.warning(self, "Warning", "Please load DICOM images first.")
                return
            
            # --- NEW FOR METADATA INSPECTOR: Dynamically get all DICOM Tags ---
            metadata_dict = {}
            if self.ds0 is not None:
                for elem in self.ds0:
                    # Exclude the massive PixelData tag to save memory and avoid serialization errors
                    if elem.keyword != "PixelData": 
                        metadata_dict[elem.keyword] = str(elem.value)

            plugin_context = {
                "signal_queue": self.signal_queue,
                "cursor": list(self.cursor),
                "spacing": getattr(self, "spacing", (1.0, 1.0, 1.0)),
                "initial_width": self.slider_width.value(),
                "initial_level": self.slider_level.value(),
                "metadata": metadata_dict,  # Inject dynamic dictionary
                # --- NEW FOR SNAPSHOT EXPORT: Expose spatial flip flags ---
                "flip_axial_ud": self.flip_axial_ud,
                "flip_axial_lr": self.flip_axial_lr,
                "flip_sagittal_ud": self.flip_sagittal_ud,
                "flip_sagittal_lr": self.flip_sagittal_lr,
                "flip_coronal_ud": self.flip_coronal_ud,
                "flip_coronal_lr": self.flip_coronal_lr,
            }

            if self.raw_volume is not None and self.shm_raw is not None and self.shm_display is not None:
                plugin_context.update({
                    "shm_raw_name": self.shm_raw.name,
                    "shm_display_name": self.shm_display.name,
                    "shape": self.raw_volume.shape,
                    "dtype": np.float32,
                    "lock": self.shm_lock,
                })

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
                    keyword,
                    self.pluginManager.plugins,
                    plugin_context 
                )
            )
            process.start()

    def check_plugin_message(self):
        needs_update = False
        
        while not self.signal_queue.empty():
            try:
                msg = self.signal_queue.get_nowait()

                if msg.get("action") == "SET_CURSOR":
                    cur = msg.get("cursor", self.cursor)
                    if self.current_display_shape is not None:
                        zmax, ymax, xmax = self.current_display_shape
                        self.cursor = [
                            int(np.clip(cur[0], 0, zmax - 1)),
                            int(np.clip(cur[1], 0, ymax - 1)),
                            int(np.clip(cur[2], 0, xmax - 1)),
                        ]
                        self.update_cursor_label()
                        self.sync_scrollbars_to_cursor()
                        needs_update = True
                    continue

                if msg.get("action") == "REFRESH":
                    if msg.get("shape"):
                        self.current_display_shape = tuple(msg.get("shape"))
                    
                    if "width" in msg: 
                        self.slider_width.blockSignals(True)
                        self.slider_width.setValue(msg["width"])
                        self.slider_width.blockSignals(False)
                    if "level" in msg: 
                        self.slider_level.blockSignals(True)
                        self.slider_level.setValue(msg["level"])
                        self.slider_level.blockSignals(False)
                    
                    # --- NEW FOR WINDOW PRESET: Apply math on slider change ---
                    if msg.get("force_apply", False):
                        self.apply_filter_and_update()
                        
                    needs_update = True
            except Exception:
                break
        
        if needs_update:
            self.update_all_views()

    # =========================================================================
    # VIEW TOGGLE LOGIC
    # =========================================================================
    def toggle_maximize(self, target_name):
        target_widget = next(cfg["widget"] for cfg in self.view_configs if cfg["name"] == target_name)
        is_already_maximized = any(cfg["widget"].isHidden() for cfg in self.view_configs)

        if not is_already_maximized:
            for cfg in self.view_configs:
                if cfg["widget"] != target_widget: cfg["widget"].hide()
        else:
            for cfg in self.view_configs: cfg["widget"].show()
            
        for canvas in self.canvases.values(): canvas.update()

    def toggle_crosshair_ui(self, state):
        """Enable/Disable Hairline based on a UI Checkbox."""
        self.show_crosshair = (state == Qt.CheckState.Checked.value)
        self.update_all_views()

    def toggle_3d_pane(self, state):
        is_checked = (state == Qt.CheckState.Checked.value)
        if self.images.get("3D") is not None:
            self.images["3D"].visible = is_checked
            self.canvases["3D"].update()
        if is_checked:
            self.draw_3d_pane()

    # =========================================================================
    # CORE LOGIC & DICOM LOADING/CLOSING
    # =========================================================================
    def close_image(self):
        self.cleanup_shm()
        self.raw_volume = None
        self.ds0 = None
        self.current_display_shape = None
        self.series_uid = None
        
        self.markups = {"Fiducial": [], "ClosedCurve": []}
        self.current_curve_pixels = []
        self.current_curve_world = []
        self.clear_markup_ui()
        
        for name in self.images:
            if self.images[name] is not None:
                self.images[name].parent = None
                self.images[name] = None
                
        for name in self.visual_fiducials:
            if self.visual_fiducials[name]:
                self.visual_fiducials[name].visible = False
                
        for name in self.visual_curves:
            for vis in self.visual_curves[name]:
                vis.parent = None
            self.visual_curves[name] = []
            
        self.lbl_coords.setText("X: 1, Y: 1, Z: 1")
        self.cursor = [0, 0, 0]
        
        for canvas in self.canvases.values():
            canvas.update()
            
        self.MainTool.hide()
        
    def jump_to_coordinates(self):
        if self.current_display_shape is None: return
        try:
            z = int(self.edit_z.text()) - 1
            y = int(self.edit_y.text()) - 1
            x = int(self.edit_x.text()) - 1
            z_m, y_m, x_m = self.current_display_shape
            
            if 0 <= z < z_m and 0 <= y < y_m and 0 <= x < x_m:
                self.cursor = [z, y, x]
                self.update_cursor_label()
                self.sync_scrollbars_to_cursor()
                self.update_all_views()
            else:
                QMessageBox.warning(self, "Out of bounds", f"Max range is X:{x_m}, Y:{y_m}, Z:{z_m}")
        except ValueError:
            QMessageBox.warning(self, "Invalid Input", "Please enter integer numbers for X, Y, and Z.")

    def update_cursor_label(self):
        z, y, x = self.cursor
        self.lbl_coords.setText(f"X: {x + 1}, Y: {y + 1}, Z: {z + 1}")

    def load_dicom_series(self):
        dialog = QFileDialog(self, "Select DICOM Folder")
        dialog.setFileMode(QFileDialog.FileMode.Directory)
        dialog.setOption(QFileDialog.Option.ShowDirsOnly, False)
        
        if dialog.exec():
            path = dialog.selectedFiles()[0]
            self.load_dicom_series_from_folder(path)

    def load_dicom_series_from_folder(self, path, series_uid=None):
        if not path:
            return
        files = []
        for root, _, names in os.walk(path):
            for name in names:
                if name.lower().endswith('.dcm'):
                    files.append(os.path.join(root, name))

        if not files:
            QMessageBox.warning(self, "Error", "No DICOM files found in this folder.")
            return

        datasets = []
        for f in files:
            try:
                ds = pydicom.dcmread(f, force=True)
                if series_uid is None or str(getattr(ds, "SeriesInstanceUID", "")) == str(series_uid):
                    if hasattr(ds, "pixel_array"):
                        datasets.append(ds)
            except Exception:
                pass

        if not datasets:
            QMessageBox.warning(self, "DICOM", "No readable DICOM slices found for the selected series.")
            return

        def sort_key(ds):
            ipp = getattr(ds, "ImagePositionPatient", None)
            if ipp is not None and len(ipp) >= 3:
                return float(ipp[2])
            return int(getattr(ds, "InstanceNumber", 0))

        datasets.sort(key=sort_key)
        self.ds0 = datasets[0]
        self.raw_volume = np.stack([ds.pixel_array for ds in datasets]).astype(np.float32)

        # CT HU conversion if available
        slope = float(getattr(self.ds0, "RescaleSlope", 1.0))
        intercept = float(getattr(self.ds0, "RescaleIntercept", 0.0))
        self.raw_volume = self.raw_volume * slope + intercept

        self.current_display_shape = self.raw_volume.shape
        z, y, x = self.current_display_shape
        self.cursor = [z // 2, y // 2, x // 2]
        
        self.series_uid = str(getattr(self.ds0, "SeriesInstanceUID", "UNKNOWN_SERIES"))
        
        self.pixel_spacing = [float(f) for f in getattr(self.ds0, "PixelSpacing", [1.0, 1.0])] 
        self.slice_thickness = float(getattr(self.ds0, "SliceThickness", 1.0))
        self.origin = [float(f) for f in getattr(self.ds0, "ImagePositionPatient", [0.0, 0.0, 0.0])]
        
        # Calculate precise spacing based on ImagePositionPatient if available (for the new plugins)
        try:
            dy, dx = map(float, self.ds0.PixelSpacing)
        except Exception:
            dy, dx = 1.0, 1.0
        try:
            if len(datasets) > 1:
                dz = abs(float(datasets[1].ImagePositionPatient[2]) - float(datasets[0].ImagePositionPatient[2]))
            else:
                dz = float(getattr(self.ds0, "SliceThickness", 1.0))
        except Exception:
            dz = float(getattr(self.ds0, "SliceThickness", 1.0))
            
        self.spacing = (dz, dy, dx)

        iop = getattr(self.ds0, "ImageOrientationPatient", [1, 0, 0, 0, 1, 0])
        ipp_last = getattr(datasets[-1], "ImagePositionPatient", [0, 0, 1])

        self.x_sign = 1 if iop[0] > 0 else -1  
        self.y_sign = 1 if iop[4] > 0 else -1  
        self.z_sign = 1 if (len(datasets) > 1 and ipp_last[2] > self.origin[2]) else -1 

        self.flip_axial_ud = (self.y_sign > 0)    
        self.flip_axial_lr = (self.x_sign < 0)   
        self.flip_coronal_ud = (self.z_sign < 0) 
        self.flip_coronal_lr = (self.x_sign < 0) 
        self.flip_sagittal_ud = (self.z_sign < 0) 
        self.flip_sagittal_lr = (self.y_sign > 0) 

        self.x_dir_invert = (self.x_sign < 0)
        self.y_dir_invert = (self.y_sign < 0)
        self.z_dir_invert = (self.z_sign > 0)

        if hasattr(self, 'scrollbars'):
            self.scrollbars["Axial"].setRange(0, z - 1)
            self.scrollbars["Sagittal"].setRange(0, x - 1)
            self.scrollbars["Coronal"].setRange(0, y - 1)
            self.sync_scrollbars_to_cursor()

        self.cleanup_shm()

        self.shm_raw = shared_memory.SharedMemory(create=True, size=self.raw_volume.nbytes)
        raw_array = np.ndarray(self.raw_volume.shape, dtype=np.float32, buffer=self.shm_raw.buf)
        raw_array[:] = self.raw_volume[:]

        self.shm_display = shared_memory.SharedMemory(create=True, size=self.raw_volume.nbytes)
        
        self.update_cursor_label()
        
        # --- YOUR ORIGINAL CONTRAST IMPLEMENTATION ---
        self.apply_filter_and_update() 
        
        self.MainBar.hide()
        self.MainTool.show()

    def apply_filter_and_update(self):
        if self.shm_raw is None or self.shm_display is None: return
        
        w, l = self.slider_width.value(), self.slider_level.value()
        low, high = l - (w / 2), l + (w / 2)
        
        with self.shm_lock:
            raw_array = np.ndarray(self.raw_volume.shape, dtype=np.float32, buffer=self.shm_raw.buf)
            disp_array = np.ndarray(self.current_display_shape, dtype=np.float32, buffer=self.shm_display.buf)
            
            if raw_array.shape == disp_array.shape:
                temp = np.clip(raw_array, low, high)
                temp = (temp - low) / (high - low + 1e-5)
                disp_array[:] = temp[:]
        
        self.update_all_views()

    def on_mouse_wheel(self, event):
        if self.current_display_shape is None: return
        view_name = next(k for k, v in self.canvases.items() if v == event.source)
        direction = int(np.sign(event.delta[1])) 
        z_max, y_max, x_max = self.current_display_shape 
        
        if view_name == "Axial": self.cursor[0] = np.clip(self.cursor[0] + direction, 0, z_max - 1)
        elif view_name == "Sagittal": self.cursor[2] = np.clip(self.cursor[2] + direction, 0, x_max - 1)
        elif view_name == "Coronal": self.cursor[1] = np.clip(self.cursor[1] + direction, 0, y_max - 1)
        
        self.update_cursor_label()
        self.sync_scrollbars_to_cursor()
        self.update_all_views()
        event.handled = True

    def on_mouse_click(self, event):
        if self.current_display_shape is None: return

        if event.button == 1: pass 
        elif event.button == 2: return 

        view_name = next(k for k, v in self.canvases.items() if v == event.source)
        
        if self.images[view_name] is not None:
            transform = self.images[view_name].get_transform("canvas", "visual")
            pos = transform.map(event.pos)
            click_col, click_row = int(pos[0]), int(pos[1])
        else:
            return
        
        z_max, y_max, x_max = self.current_display_shape 
        
        array_x, array_y = self.translate_coordinates(view_name, click_col, click_row)

        if view_name == "Axial": 
            self.cursor[2] = np.clip(array_x, 0, x_max - 1)
            self.cursor[1] = np.clip(array_y, 0, y_max - 1)
        elif view_name == "Sagittal": 
            self.cursor[1] = np.clip(array_x, 0, y_max - 1)
            self.cursor[0] = np.clip(array_y, 0, z_max - 1)
        elif view_name == "Coronal": 
            self.cursor[2] = np.clip(array_x, 0, x_max - 1)
            self.cursor[0] = np.clip(array_y, 0, z_max - 1)
            
        self.update_cursor_label()
        self.sync_scrollbars_to_cursor()
        
        if self.group_anno.isChecked() and self.annotation_mode != "None":
            click_x = int(self.cursor[2])
            click_y = int(self.cursor[1])
            click_z = int(self.cursor[0])
            
            raw_world = self.get_world_coordinates(click_x, click_y, click_z)
            world_coords = [float(w) for w in raw_world]
            pixel_xyz_1based = [click_x + 1, click_y + 1, click_z + 1]
            
            if self.annotation_mode == "Fiducial":
                anno_id = str(uuid.uuid4())
                new_fiducial = {
                    "id": anno_id, 
                    "name": "Draft...", 
                    "visible": True, 
                    "pixel_xyz": pixel_xyz_1based,
                    "world_lps": world_coords
                }
                self.markups["Fiducial"].append(new_fiducial)
                
                self.update_all_views()
                QApplication.processEvents() 
                
                pt_name, ok = QInputDialog.getText(self, "Fiducial Name", "Enter name for this Fiducial Point:")
                
                if ok and pt_name.strip():
                    new_fiducial["name"] = pt_name.strip()
                    self.add_markup_ui_row("Fiducial", new_fiducial)
                else:
                    self.markups["Fiducial"].remove(new_fiducial)
                    
            elif self.annotation_mode == "ClosedCurve":
                self.current_curve_pixels.append(pixel_xyz_1based)
                self.current_curve_world.append(world_coords)
        
        self.update_all_views()

    def update_all_views(self):
        if self.shm_display is None: return
        z, y, x = self.cursor
        
        with self.shm_lock:
            display_volume = np.ndarray(self.current_display_shape, dtype=np.float32, buffer=self.shm_display.buf)
            if np.sum(display_volume) == 0: return

            axial = display_volume[z, :, :]
            sagittal = display_volume[:, :, x]
            coronal = display_volume[:, y, :]

            if self.flip_axial_ud: axial = np.flipud(axial)
            if self.flip_axial_lr: axial = np.fliplr(axial)

            if self.flip_sagittal_ud: sagittal = np.flipud(sagittal)
            if self.flip_sagittal_lr: sagittal = np.fliplr(sagittal)

            if self.flip_coronal_ud: coronal = np.flipud(coronal)
            if self.flip_coronal_lr: coronal = np.fliplr(coronal)

        self.draw_pane("Axial", axial, x, y)
        self.draw_pane("Sagittal", sagittal, y, z)
        self.draw_pane("Coronal", coronal, x, z)

        if self.checkBox_3D.isChecked():
            self.draw_3d_pane()

    def draw_pane(self, name, img_data, cx, cy):
        h, w = img_data.shape
        is_first_load = False

        if self.images[name] is None: 
            self.images[name] = scene.visuals.Image(img_data, parent=self.views[name].scene, cmap="grays")
            is_first_load = True
            
            self.lines[name][0].parent = None
            self.lines[name][0].parent = self.views[name].scene
            self.lines[name][1].parent = None
            self.lines[name][1].parent = self.views[name].scene
            
            self.visual_fiducials[name].parent = None
            self.visual_fiducials[name].parent = self.views[name].scene
            
        else: 
            if self.images[name]._data.shape != img_data.shape:
                is_first_load = True
            self.images[name].set_data(img_data)
            
        if img_data.max() <= 1.01: self.images[name].clim = (0.0, 1.0)
        else: self.images[name].clim = (float(img_data.min()), float(img_data.max()))

        ps_y = self.pixel_spacing[0] if self.pixel_spacing[0] > 0 else 1.0
        ps_x = self.pixel_spacing[1] if self.pixel_spacing[1] > 0 else 1.0
        st = self.slice_thickness if self.slice_thickness > 0 else 1.0

        if name == "Axial":      aspect_ratio = ps_x / ps_y
        elif name == "Sagittal": aspect_ratio = ps_y / st 
        elif name == "Coronal":  aspect_ratio = ps_x / st 
        
        self.views[name].camera.aspect = aspect_ratio

        if is_first_load:
            self.views[name].camera.rect = (0, 0, w, h)

        cursor_z, cursor_y, cursor_x = self.cursor

        if name == "Axial": visual_cx, visual_cy = self.translate_coordinates(name, cursor_x, cursor_y)
        elif name == "Sagittal": visual_cx, visual_cy = self.translate_coordinates(name, cursor_y, cursor_z)
        elif name == "Coronal": visual_cx, visual_cy = self.translate_coordinates(name, cursor_x, cursor_z)

        self.lines[name][0].set_data(np.array([[0, visual_cy], [w, visual_cy]]))
        self.lines[name][1].set_data(np.array([[visual_cx, 0], [visual_cx, h]]))
        
        # --- NEW: Apply CrossHair Visibility ---
        self.lines[name][0].visible = self.show_crosshair
        self.lines[name][1].visible = self.show_crosshair
        
        # 1. Draw Fiducials
        draw_pts = []
        for pt_obj in self.markups["Fiducial"]:
            if not pt_obj.get("visible", True): continue 
            
            px = pt_obj["pixel_xyz"][0] - 1
            py = pt_obj["pixel_xyz"][1] - 1
            pz = pt_obj["pixel_xyz"][2] - 1
            
            if (name == "Axial" and pz == cursor_z) or \
               (name == "Sagittal" and px == cursor_x) or \
               (name == "Coronal" and py == cursor_y):
                
                if name == "Axial": vx, vy = self.translate_coordinates(name, px, py)
                elif name == "Sagittal": vx, vy = self.translate_coordinates(name, py, pz)
                elif name == "Coronal": vx, vy = self.translate_coordinates(name, px, pz)
                draw_pts.append([vx, vy])
            
        if draw_pts:
            self.visual_fiducials[name].set_data(np.array(draw_pts), symbol='o', face_color='yellow', size=8)
            self.visual_fiducials[name].visible = True
        else:
            self.visual_fiducials[name].visible = False

        # 2. Draw Closed Curves
        for visual in self.visual_curves[name]:
            visual.parent = None
        self.visual_curves[name] = []

        all_curves = [crv["pixels"] for crv in self.markups["ClosedCurve"] if crv.get("visible", True)]
        if self.current_curve_pixels: 
            all_curves.append(self.current_curve_pixels)
        
        for crv in all_curves:
            crv_pts = []
            for pt in crv:
                px = pt[0] - 1
                py = pt[1] - 1
                pz = pt[2] - 1
                
                if (name == "Axial" and pz == cursor_z) or \
                   (name == "Sagittal" and px == cursor_x) or \
                   (name == "Coronal" and py == cursor_y):
                    
                    if name == "Axial": vx, vy = self.translate_coordinates(name, px, py)
                    elif name == "Sagittal": vx, vy = self.translate_coordinates(name, py, pz)
                    elif name == "Coronal": vx, vy = self.translate_coordinates(name, px, pz)
                    crv_pts.append([vx, vy])

            if len(crv_pts) > 1:
                if crv != self.current_curve_pixels and len(crv_pts) > 2:
                    crv_pts.append(crv_pts[0])
                line = scene.visuals.Line(pos=np.array(crv_pts), color='green', width=4, parent=self.views[name].scene)
                line.set_gl_state('translucent', depth_test=False)
                self.visual_curves[name].append(line)
            elif len(crv_pts) == 1:
                mark = scene.visuals.Markers(pos=np.array(crv_pts), symbol='+', face_color='green', size=7, parent=self.views[name].scene)
                mark.set_gl_state('translucent', depth_test=False) 
                self.visual_curves[name].append(mark)
                
        self.canvases[name].update()

    def draw_3d_pane(self):
        if self.shm_display is None: return
        
        with self.shm_lock:
            display_volume = np.ndarray(self.current_display_shape, dtype=np.float32, buffer=self.shm_display.buf)
            vol_min, vol_max = display_volume.min(), display_volume.max()
            vol_norm = (display_volume - vol_min) / (vol_max - vol_min + 1e-5)
        
        s = np.array(self.current_display_shape)
        max_dim = s.max()
        scale_tuple = (1.0/max_dim, 1.0/max_dim, 1.0/max_dim)
        
        if self.images["3D"] is None:
            self.images["3D"] = scene.visuals.Volume(
                vol_norm, 
                parent=self.views["3D"].scene, 
                method='iso', 
                threshold=0.1, 
                cmap='viridis',
                interpolation='linear'
            )
            
            self.images["3D"].transform = scene.transforms.STTransform(
                scale=scale_tuple,
                translate=(-0.5, -0.5, -0.5)
            )
            self.views["3D"].camera.distance = 2.0
        else:
            self.images["3D"].set_data(vol_norm)
            self.images["3D"].transform = scene.transforms.STTransform(
                scale=scale_tuple,
                translate=(-0.5, -0.5, -0.5)
            )
            
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