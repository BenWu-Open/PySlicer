# -*- coding: utf-8 -*-
import os
import sys
import pickle
import colorsys
import nibabel as nib
import numpy as np

import vispy
vispy.use('pyqt6')
from vispy import scene
from vispy.color import Colormap

from PyQt6 import QtWidgets, QtGui, QtCore
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout, 
                             QVBoxLayout, QListWidget, QListWidgetItem, QLabel, 
                             QSplitter, QPushButton, QSlider, QCheckBox, QComboBox)
from PyQt6.QtGui import QPixmap
from PyQt6.QtCore import Qt, QThread, pyqtSignal

# =========================================================
# Background Worker to run TotalSegmentator
# =========================================================
class SegmentatorWorker(QThread):
    finished_signal = pyqtSignal(str)
    error_signal = pyqtSignal(str)

    def __init__(self, nifti_path, output_dir):
        super().__init__()
        self.nifti_path = nifti_path
        self.seg_path = os.path.join(output_dir, "organ_segmentation.nii.gz")

    def run(self):
        # If it already exists, skip running it again to save time!
        if os.path.exists(self.seg_path):
            self.finished_signal.emit(self.seg_path)
            return

        try:
            # Import here to avoid slowing down the initial UI load
            from totalsegmentator.python_api import totalsegmentator
            
            # ml=True outputs a single file with all labels instead of 117 separate files
            # fast=True uses a lower-res model for speed (perfect for UI visualization)
            totalsegmentator(self.nifti_path, self.seg_path, fast=True, ml=True)
            
            self.finished_signal.emit(self.seg_path)
        except Exception as e:
            self.error_signal.emit(str(e))

# =========================================================
# Custom Graphics View for 2D Zoom and Pan
# =========================================================
class ZoomableView(QtWidgets.QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene = QtWidgets.QGraphicsScene(self)
        self.setScene(self.scene)
        self.pixmap_item = QtWidgets.QGraphicsPixmapItem()
        self.scene.addItem(self.pixmap_item)
        
        self.setDragMode(QtWidgets.QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QtWidgets.QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setStyleSheet("background-color: #111111; border: 1px solid #444444;")

    def set_image(self, pixmap):
        self.pixmap_item.setPixmap(pixmap)
        self.scene.setSceneRect(self.pixmap_item.boundingRect())
        self.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def wheelEvent(self, event):
        zoom_in_factor = 1.15
        zoom_out_factor = 1 / zoom_in_factor
        zoom_factor = zoom_in_factor if event.angleDelta().y() > 0 else zoom_out_factor
        self.scale(zoom_factor, zoom_factor)

# =========================================================
# Main Result Viewer Window
# =========================================================
class ResultViewerWindow(QMainWindow):
    def __init__(self, nifti_path, pkl_path, png_folder_path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("LymphNode Detection & Whole Body Segmentation")
        self.resize(1280, 800)

        self.nifti_path = nifti_path
        self.pkl_path = pkl_path
        self.png_folder_path = png_folder_path

        self.vol_min = 0
        self.vol_max = 1
        self.max_seg_label = 0

        self.init_ui()
        
        # --- Instant Load vs Background Pipeline ---
        expected_seg_path = os.path.join(self.png_folder_path, "organ_segmentation.nii.gz")
        
        if os.path.exists(expected_seg_path):
            self.lbl_3d_title.setText("<b>Loading 3D Engine from Cache...</b>")
            self.load_and_render_data(expected_seg_path)
        else:
            self.lbl_3d_title.setText("<b>Running TotalSegmentator (AI Organ Mapping)... Please wait, this takes a few minutes...</b>")
            self.seg_worker = SegmentatorWorker(self.nifti_path, self.png_folder_path)
            self.seg_worker.finished_signal.connect(self.on_segmentation_finished)
            self.seg_worker.error_signal.connect(self.on_segmentation_error)
            self.seg_worker.start()

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)

        # --- View Control Toolbar ---
        toolbar_layout = QHBoxLayout()
        btn_split = QPushButton("Split View")
        btn_split.setStyleSheet("background-color: #555555; color: white; font-weight: bold;")
        btn_max_2d = QPushButton("Maximize 2D View")
        btn_max_2d.setStyleSheet("background-color: #555555; color: white; font-weight: bold;")
        btn_max_3d = QPushButton("Maximize 3D View")
        btn_max_3d.setStyleSheet("background-color: #555555; color: white; font-weight: bold;")
        
        btn_reset_3d = QPushButton("Reset 3D Camera")
        btn_reset_3d.setStyleSheet("background-color: #555555; color: white; font-weight: bold;")
        
        btn_split.clicked.connect(lambda: self.set_view_mode("split"))
        btn_max_2d.clicked.connect(lambda: self.set_view_mode("2d"))
        btn_max_3d.clicked.connect(lambda: self.set_view_mode("3d"))
        btn_reset_3d.clicked.connect(self.reset_3d_camera)

        toolbar_layout.addWidget(btn_split)
        toolbar_layout.addWidget(btn_max_2d)
        toolbar_layout.addWidget(btn_max_3d)
        toolbar_layout.addSpacing(20)
        toolbar_layout.addWidget(btn_reset_3d)
        toolbar_layout.addStretch()
        main_layout.addLayout(toolbar_layout)

        # --- Main Splitter ---
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(self.splitter)

        # --- Left Panel: 2D View ---
        self.left_widget = QWidget()
        left_layout = QVBoxLayout(self.left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        
        self.png_list = QListWidget()
        self.png_list.setMaximumHeight(120)
        self.png_list.itemClicked.connect(self.display_selected_png)
        self.image_preview = ZoomableView()

        left_layout.addWidget(QLabel("<b>AI Detected Slices (2D Zoomable View)</b>"))
        left_layout.addWidget(self.png_list)
        left_layout.addWidget(self.image_preview)
        self.splitter.addWidget(self.left_widget)

        # --- Right Panel: 3D View ---
        self.right_widget = QWidget()
        right_layout = QVBoxLayout(self.right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        
        self.lbl_3d_title = QLabel("<b>Anatomical Localization (Loading...)</b>")
        self.lbl_3d_title.setWordWrap(True)
        
        # 3D Contrast & Rendering Controls
        contrast_layout = QHBoxLayout()
        
        # Layer Visibility Toggles
        self.chk_ct = QCheckBox("Show CT Scan")
        self.chk_ct.setChecked(True)
        self.chk_ct.stateChanged.connect(self.toggle_layers)
        
        self.chk_organs = QCheckBox("Show AI Organs")
        self.chk_organs.setChecked(True)
        self.chk_organs.stateChanged.connect(self.toggle_layers)

        self.chk_boxes = QCheckBox("Show Lymph Nodes")
        self.chk_boxes.setChecked(True)
        self.chk_boxes.stateChanged.connect(self.toggle_layers)
        
        # Organ Colormap Toggle
        self.combo_organ_color = QComboBox()
        self.combo_organ_color.addItems(["Multi-Color", "Grayscale"])
        self.combo_organ_color.currentTextChanged.connect(self.update_organ_color)
        
        self.slider_level = QSlider(Qt.Orientation.Horizontal)
        self.slider_level.setRange(-1000, 2000)
        self.slider_level.setValue(200) # Base CT Contrast
        self.slider_level.valueChanged.connect(self.update_3d_contrast)
        
        self.slider_width = QSlider(Qt.Orientation.Horizontal)
        self.slider_width.setRange(10, 3000)
        self.slider_width.setValue(800) 
        self.slider_width.valueChanged.connect(self.update_3d_contrast)

        contrast_layout.addWidget(self.chk_ct)
        contrast_layout.addWidget(self.chk_organs)
        contrast_layout.addWidget(QLabel(" Color:"))
        contrast_layout.addWidget(self.combo_organ_color)
        contrast_layout.addWidget(self.chk_boxes)
        contrast_layout.addWidget(QLabel(" | CT Level:"))
        contrast_layout.addWidget(self.slider_level)
        contrast_layout.addWidget(QLabel(" Width:"))
        contrast_layout.addWidget(self.slider_width)

        # VisPy Canvas
        self.canvas = scene.SceneCanvas(keys='interactive', show=True)
        self.view = self.canvas.central_widget.add_view()
        self.view.camera = 'turntable'
        
        right_layout.addWidget(self.lbl_3d_title)
        right_layout.addLayout(contrast_layout)
        right_layout.addWidget(self.canvas.native)
        self.splitter.addWidget(self.right_widget)

        self.splitter.setSizes([450, 830])

    def set_view_mode(self, mode):
        if mode == "split":
            self.left_widget.show()
            self.right_widget.show()
            self.splitter.setSizes([450, 830])
        elif mode == "2d":
            self.left_widget.show()
            self.right_widget.hide()
        elif mode == "3d":
            self.left_widget.hide()
            self.right_widget.show()

    def reset_3d_camera(self):
        if hasattr(self, 'view'):
            self.view.camera.distance = 2.0
            self.view.camera.center = (0, 0, 0)
            self.view.camera.azimuth = 0     
            self.view.camera.elevation = 0   
            self.canvas.update()

    def toggle_layers(self):
        if hasattr(self, 'ct_visual'):
            self.ct_visual.visible = self.chk_ct.isChecked()
        if hasattr(self, 'organ_visual'):
            self.organ_visual.visible = self.chk_organs.isChecked()
        if hasattr(self, 'box_visual'):
            self.box_visual.visible = self.chk_boxes.isChecked()
            if hasattr(self, 'text_visual'):
                self.text_visual.visible = self.chk_boxes.isChecked()

    def update_3d_contrast(self):
        if hasattr(self, 'ct_visual'):
            lvl = self.slider_level.value()
            wid = self.slider_width.value()
            low_hu = lvl - (wid / 2.0)
            high_hu = lvl + (wid / 2.0)
            clim_low = (low_hu - self.vol_min) / (self.vol_max - self.vol_min + 1e-5)
            clim_high = (high_hu - self.vol_min) / (self.vol_max - self.vol_min + 1e-5)
            self.ct_visual.clim = (float(clim_low), float(clim_high))

    def update_organ_color(self, mode):
        """Switches the TotalSegmentator render layer between Multi-Color and Grayscale"""
        if hasattr(self, 'organ_visual') and self.max_seg_label > 0:
            colors = [(0.0, 0.0, 0.0, 0.0)] # 0 = Background (Transparent)
            
            if mode == "Multi-Color":
                for i in range(1, self.max_seg_label + 1):
                    rgb = colorsys.hsv_to_rgb(i / float(self.max_seg_label), 0.8, 0.9)
                    colors.append((rgb[0], rgb[1], rgb[2], 0.4))
            else:
                for i in range(1, self.max_seg_label + 1):
                    gray_val = (i / float(self.max_seg_label)) * 0.7 + 0.3
                    colors.append((gray_val, gray_val, gray_val, 0.4))
                    
            self.organ_visual.cmap = Colormap(colors)

    def on_segmentation_error(self, err_msg):
        self.lbl_3d_title.setText(f"<b><span style='color:red'>TotalSegmentator Error: {err_msg}</span></b>")

    def on_segmentation_finished(self, seg_path):
        self.lbl_3d_title.setText("<b>Loading 3D Engine...</b>")
        self.load_and_render_data(seg_path)

    def load_and_render_data(self, seg_path=None):
        # 1. Populate 2D PNGs
        if os.path.exists(self.png_folder_path):
            png_files = [f for f in os.listdir(self.png_folder_path) if f.lower().endswith('.png')]
            for f in sorted(png_files):
                item = QListWidgetItem(f)
                item.setData(Qt.ItemDataRole.UserRole, os.path.join(self.png_folder_path, f))
                self.png_list.addItem(item)
            if png_files:
                self.png_list.setCurrentRow(0)
                self.display_selected_png(self.png_list.item(0))

        # 2. Parse Raw CT NIfTI
        if not os.path.exists(self.nifti_path):
            return

        nii = nib.load(self.nifti_path)
        vol_data = nii.get_fdata()
        
        self.vol_min = np.min(vol_data)
        self.vol_max = np.max(vol_data)
        
        if self.vol_max - self.vol_min != 0:
            vol_normalized = (vol_data - self.vol_min) / (self.vol_max - self.vol_min)
        else:
            vol_normalized = np.zeros_like(vol_data)
            
        vol_data_zyx = np.transpose(vol_normalized, (2, 1, 0)).astype(np.float32)
        
        # Calculate Physical Transform
        try: zooms = nii.header.get_zooms()[:3]
        except Exception: zooms = (1.0, 1.0, 1.0)
            
        shape_x, shape_y, shape_z = vol_data.shape
        max_dim = max(shape_x * zooms[0], shape_y * zooms[1], shape_z * zooms[2])
        
        scale_x = zooms[0] / max_dim
        scale_y = zooms[1] / max_dim
        scale_z = zooms[2] / max_dim

        self.shared_transform = scene.transforms.STTransform(
            scale=(scale_x, scale_y, scale_z),
            translate=(-0.5 * (shape_x * scale_x),
                       -0.5 * (shape_y * scale_y),
                       -0.5 * (shape_z * scale_z))
        )
        
        # Create Volume 1: CT Skeleton (MIP)
        self.ct_visual = scene.visuals.Volume(
            vol_data_zyx,
            parent=self.view.scene,
            method='mip',
            cmap='grays',
            interpolation='linear'
        )
        self.ct_visual.transform = self.shared_transform
        self.update_3d_contrast()
        self.view.camera.distance = 2.0

        # 3. Parse and Render AI Organs
        if seg_path and os.path.exists(seg_path):
            seg_nii = nib.load(seg_path)
            seg_data = seg_nii.get_fdata()
            
            self.max_seg_label = int(np.max(seg_data))
            if self.max_seg_label > 0:
                seg_normalized = (seg_data / float(self.max_seg_label))
                seg_data_zyx = np.transpose(seg_normalized, (2, 1, 0)).astype(np.float32)

                self.organ_visual = scene.visuals.Volume(
                    seg_data_zyx,
                    parent=self.view.scene,
                    method='translucent',
                    interpolation='nearest' 
                )
                self.organ_visual.transform = self.shared_transform
                self.update_organ_color(self.combo_organ_color.currentText())

        # 4. Parse AI Lymph Node Pickles
        if os.path.exists(self.pkl_path):
            try:
                with open(self.pkl_path, 'rb') as f:
                    data = pickle.load(f)
                
                pred_boxes = data.get('pred_boxes', [])
                pred_scores = data.get('pred_scores', [])
                total_boxes = len(pred_boxes)
                
                if total_boxes > 0:
                    if len(pred_scores) == len(pred_boxes):
                        sorted_pairs = sorted(zip(pred_scores, pred_boxes), key=lambda x: x[0], reverse=True)
                        top_boxes = [p[1] for p in sorted_pairs[:3]]
                        top_scores = [p[0] for p in sorted_pairs[:3]]
                    else:
                        top_boxes = pred_boxes[:3]
                        top_scores = [None] * len(top_boxes)

                    # --- Build the dynamic text for the 3D Title ---
                    title_html = f"<b>Anatomical Localization (Showing Top {len(top_boxes)} of {total_boxes} Lymph Nodes + AI Organs)</b><br>"
                    
                    # ADDED THE LITERAL STRING LEGEND HERE:
                    #title_html += f"&nbsp;&nbsp;<span style='color:black; font-size:12px; font-weight:normal;'>Legend: [x1, y1, z1, x2, y2, z2]</span><br>"
                    title_html += f"&nbsp;&nbsp;<span style='color:black; font-size:12px; font-weight:normal;'>Legend: [z1, y1, z2, y2, x1, x2]</span><br>"

                    for idx, (b, s) in enumerate(zip(top_boxes, top_scores)):
                        b_ints = [int(x) for x in b[:6]]
                        sc_str = f"{s:.2f}" if s is not None else "N/A"
                        box_str = f"[{b_ints[0]}, {b_ints[1]}, {b_ints[2]}, {b_ints[3]}, {b_ints[4]}, {b_ints[5]}]"
                        
                        title_html += f"&nbsp;&nbsp;<span style='color:#00aaff; font-size:12px; font-weight:bold;'>"
                        title_html += f"Node {idx+1} | Score: {sc_str} | Coordinates: {box_str}</span><br>"

                    self.lbl_3d_title.setText(title_html)
                    self.render_3d_boxes(top_boxes, top_scores)
                else:
                    self.lbl_3d_title.setText("<b>Anatomical Localization (0 Lymph Nodes Detected)</b>")
                    
            except Exception as e:
                print(f"Failed parsing pickle: {e}")

    def display_selected_png(self, item):
        file_path = item.data(Qt.ItemDataRole.UserRole)
        if file_path and os.path.exists(file_path):
            pixmap = QPixmap(file_path)
            self.image_preview.set_image(pixmap)

    def render_3d_boxes(self, boxes, scores):
        line_segments = []
        text_positions = []
        text_strings = []
        
        for i, box in enumerate(boxes):
            #x1, y1, z1, x2, y2, z2 = box[:6]
            z1, y1, z2, y2, x1, x2 = box[:6]

            vertices = np.array([
                [x1, y1, z1], [x2, y1, z1], [x2, y1, z1], [x2, y2, z1],
                [x2, y2, z1], [x1, y2, z1], [x1, y2, z1], [x1, y1, z1],
                
                [x1, y1, z2], [x2, y1, z2], [x2, y1, z2], [x2, y2, z2],
                [x2, y2, z2], [x1, y2, z2], [x1, y2, z2], [x1, y1, z2],
                
                [x1, y1, z1], [x1, y1, z2], [x2, y1, z1], [x2, y1, z2],
                [x2, y2, z1], [x2, y2, z2], [x1, y2, z1], [x1, y2, z2],
            ])
            line_segments.append(vertices)

            if scores[i] is not None:
                text_positions.append([x1, y2, z2])
                text_strings.append(f"{scores[i]:.2f}")

        if line_segments:
            all_segments = np.vstack(line_segments)
            
            self.box_visual = scene.visuals.Line(
                pos=all_segments, color='red', width=3.0, 
                connect='segments', parent=self.view.scene
            )
            self.box_visual.transform = self.shared_transform

            if text_positions:
                self.text_visual = scene.visuals.Text(
                    text=text_strings, pos=text_positions, color='#00aaff',
                    font_size=16, bold=True, parent=self.view.scene
                )
                self.text_visual.transform = self.shared_transform

    def closeEvent(self, event):
        self.canvas.close()
        event.accept()

# ==========================================
# STANDALONE TESTING BLOCK
# ==========================================
if __name__ == "__main__":
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    
    # -------------------------------------------------------------
    # UPDATE THESE PATHS TO YOUR ACTUAL FOLDER LOCATIONS FOR TESTING
    # -------------------------------------------------------------
    test_nifti = r"C:\projects\real_payload\ABD_LYMPH_007_0000.nii.gz"
    test_pkl = r"C:\projects\real_payload\ABD_LYMPH_007_boxes.pkl"
    test_png_folder = r"C:\projects\real_payload"
    
    if not os.path.exists(test_nifti):
        print(f"WARNING: NIfTI file not found at: {test_nifti}")
        
    viewer = ResultViewerWindow(test_nifti, test_pkl, test_png_folder)
    viewer.show()
    
    sys.exit(app.exec())