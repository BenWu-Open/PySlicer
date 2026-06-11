# -*- coding: utf-8 -*-
import os
import sys
import shutil
import zipfile
import json
import requests
import dicom2nifti
import uuid

from PyQt6 import QtWidgets
from PyQt6.QtWidgets import QApplication, QWidget, QFileDialog, QMessageBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout
from PyQt6.QtGui import QTextCursor
from PyQt6.QtCore import QThread, pyqtSignal, Qt

from utils.SlicerLog import SlicerLog
logger = SlicerLog.getLogger("Slicer_Log")

from plugins.UI.lymphnode import Ui_Form
from basePlugin import basePlugin

# =========================================================
# Path Configurations & Module Injection
# =========================================================
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
SUPPORT_DIR = os.path.join(PLUGIN_DIR, "supportfiles")
os.makedirs(SUPPORT_DIR, exist_ok=True)

# Define the local database tracking file inside supportfiles
DB_FILE = os.path.join(SUPPORT_DIR, "lymphnode_tasks.json")

# Inject supportfiles folder into sys.path so we can import ResultViewer
sys.path.insert(0, SUPPORT_DIR)

try:
    from ResultViewer import ResultViewerWindow
except ImportError:
    logger.error(f"ResultViewer.py not found in {SUPPORT_DIR}. Viewing results will be disabled.")

# =========================================================
# Local JSON Database Helpers
# =========================================================
def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_db(data):
    with open(DB_FILE, 'w') as f:
        json.dump(data, f, indent=4)

# =========================================================
# Worker 1: Convert & Submit to Server
# =========================================================
class SubmitWorker(QThread):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(str) # Emits the generated Tracking ID

    def __init__(self, source, dest, url):
        super().__init__()
        self.source = source
        self.dest = dest
        self.url = url

    def log(self, msg):
        self.log_signal.emit(msg)

    def run(self):
        tracking_id = None
        try:
            self.log("<b>=== Starting Pipeline ===</b>")
            
            # Require AI Server URL before doing any heavy processing
            if not self.url:
                raise Exception("No AI Server URL provided. Submission aborted.")
                
            # 1. Convert DICOM to NIfTI
            nifti_dir = os.path.join(self.dest, "NIfTI_Output")
            os.makedirs(nifti_dir, exist_ok=True)
            
            # --- NEW CODE: Bypass Strict DICOM Validation ---
            import dicom2nifti.settings as settings
            settings.disable_validate_slice_increment() # Ignores uneven slice gaps
            settings.disable_validate_orthogonal()      # Ignores slight gantry tilts
            # ------------------------------------------------

            self.log("Converting DICOM folder to NIfTI...")
            dicom2nifti.convert_directory(self.source, nifti_dir, compression=True, reorient=True)
            
            generated_files = [f for f in os.listdir(nifti_dir) if f.endswith('.nii.gz')]
            if not generated_files:
                raise Exception("NIfTI conversion failed. No .nii.gz file found.")
            
            original_nifti = os.path.join(nifti_dir, generated_files[0])
            
            # Extract the relative folder name of the DICOM directory
            folder_name = os.path.basename(os.path.normpath(self.source))
            if not folder_name:
                folder_name = "output"
                
            renamed_nifti = os.path.join(nifti_dir, f"{folder_name}.nii.gz")
            
            # Rename the file to match the relative path
            if original_nifti != renamed_nifti:
                if os.path.exists(renamed_nifti):
                    os.remove(renamed_nifti)
                os.rename(original_nifti, renamed_nifti)
                
            nifti_path = renamed_nifti
            self.log(f"NIfTI created successfully as: {os.path.basename(nifti_path)}")

            # 2. ZIP the NIfTI file for the payload
            zip_path = os.path.join(self.dest, "submission.zip")
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
                z.write(nifti_path, os.path.basename(nifti_path))
            self.log("Payload zipped into submission.zip.")

            # 3. Send to Server (Strict Validation)
            self.log(f"Sending to AI server: {self.url}...")
            with open(zip_path, 'rb') as f:
                res = requests.post(self.url, files={'file': f})
            
            if res.status_code == 200:
                data = res.json()
                tracking_id = data.get("tracking_id")
                
                # Check if server responded OK but forgot to give a tracking ID
                if not tracking_id:
                    raise Exception("Server connected successfully, but no tracker ID was received. Submission failed.")
                    
                self.log(f"<b>Submission Success! Server ID:</b> {tracking_id}")
            else:
                raise Exception(f"Server returned Error {res.status_code}. Submission failed.")

            # 4. Save to Database (ONLY if tracking_id successfully acquired)
            db = load_db()
            db[tracking_id] = {
                "dicom_path": self.source,
                "dest_path": self.dest,
                "nifti_path": nifti_path,
                "status": "Queue"
            }
            save_db(db)

        except Exception as e:
            self.log(f"<span style='color:red'>Error: {str(e)}</span>")
            tracking_id = None # Ensure it is nullified on error

        # Emit the tracking ID if successful, or an empty string if it failed
        self.finished_signal.emit(tracking_id if tracking_id else "")

# =========================================================
# Worker 2: Poll Status & Download Results
# =========================================================
class StatusWorker(QThread):
    log_signal = pyqtSignal(str)
    status_updated_signal = pyqtSignal(str, str) # tracking_id, new_status

    def __init__(self, tracking_id, dest_path, url):
        super().__init__()
        self.tracking_id = tracking_id
        self.dest_path = dest_path
        self.url = url

    def log(self, msg):
        self.log_signal.emit(msg)

    def run(self):
        self.log(f"Checking status for <b>{self.tracking_id}</b>...")
        
        try:
            # 1. Ask server for status
            status_url = f"{self.url}/{self.tracking_id}"
            res = requests.get(status_url)
            
            if res.status_code == 200:
                data = res.json()
                new_status = data.get("status", "Queue")
                
                self.log(f"Server replied: Status is {new_status}")
                
                # 2. If finished, download and extract results
                if new_status == "Finished":
                    self.log("Downloading AI results zip...")
                    download_url = data.get("download_url", f"{status_url}/download")
                    zip_res = requests.get(download_url, stream=True)
                    
                    if zip_res.status_code == 200:
                        zip_save_path = os.path.join(self.dest_path, f"{self.tracking_id}_results.zip")
                        with open(zip_save_path, 'wb') as f:
                            shutil.copyfileobj(zip_res.raw, f)
                            
                        self.log("Extracting results...")
                        with zipfile.ZipFile(zip_save_path, 'r') as z:
                            z.extractall(self.dest_path)
                        self.log("<span style='color:green'>Results ready for viewing!</span>")
                    else:
                        raise Exception("Failed to download results zip.")
                        
                # 3. Update Database only upon successful server response
                db = load_db()
                if self.tracking_id in db:
                    db[self.tracking_id]["status"] = new_status
                    save_db(db)
                    
                self.status_updated_signal.emit(self.tracking_id, new_status)
            else:
                self.log(f"<span style='color:red'>Server Error: {res.status_code}</span>")

        except Exception as e:
            self.log(f"<span style='color:red'>Check Error: {str(e)}</span>")


# =========================================================
# UI Controller
# =========================================================
class LymphNode_Detection(basePlugin):
    def __init__(self, context=None):
        super().__init__(context)
        self.name = "LymphNode Detection"
        self.plugin_context = context

    def run(self):
        logger.info(f"Running LymphNode Detection")
        
        self.app = QApplication.instance()
        if self.app is None:
            self.app = QApplication(sys.argv)

        self.window = QWidget()
        self.ui = Ui_Form()
        self.ui.setupUi(self.window)

        self.SourcePath = None
        self.DestinationPath = None
        
        # Setup Text Cursors for dual logs
        self.cursorSubmit = QTextCursor(self.ui.Status_textBrowser.document())
        self.cursorStatus = QTextCursor(self.ui.Status_textBrowser_Status.document())

        # Setup layout inside the specific UI scroll area you provided
        self.task_layout = QVBoxLayout(self.ui.scrollAreaWidgetContents)
        self.task_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Connect Submit Panel Buttons
        self.ui.SourcePath_Button.clicked.connect(self.get_source)
        self.ui.DestinationPath_Button.clicked.connect(self.get_dest)
        self.ui.Start_pushButton.clicked.connect(self.start_submission)
        self.ui.Cancel_pushButton.clicked.connect(self.handle_close)
        
        # Connect Status Panel Buttons
        self.ui.Retrieve_pushButton_Status.clicked.connect(self.check_all_pending_tasks)
        self.ui.Cancel_pushButton_Status.clicked.connect(self.handle_close)

        # Build initial UI list from JSON
        self.refresh_task_list()

        self.window.show()
        self.app.exec()

    def handle_close(self):
        self.window.close()
        self.app.quit()

    # --- Logging Helpers ---
    def log_submit(self, text):
        self.cursorSubmit.insertHtml(text + "<br>")
        self.ui.Status_textBrowser.moveCursor(QTextCursor.MoveOperation.End)

    def log_status(self, text):
        self.cursorStatus.insertHtml(text + "<br>")
        self.ui.Status_textBrowser_Status.moveCursor(QTextCursor.MoveOperation.End)

    # --- Folder Selection ---
    def get_source(self):
        self.SourcePath = QFileDialog.getExistingDirectory(self.window, "Select DICOM Source Folder")
        if self.SourcePath:
            self.ui.SourcePath_lineEdit.setText(self.SourcePath)

    def get_dest(self):
        self.DestinationPath = QFileDialog.getExistingDirectory(self.window, "Select Output Destination")
        if self.DestinationPath:
            self.ui.DestinationPath_lineEdit.setText(self.DestinationPath)

    # =========================================================
    # Submit Pipeline
    # =========================================================
    def start_submission(self):
        if not self.SourcePath or not self.DestinationPath:
            QMessageBox.warning(self.window, "Error", "Please select both Source and Destination folders.")
            return

        url = self.ui.AIServer_lineEdit.text().strip()

        self.submit_worker = SubmitWorker(self.SourcePath, self.DestinationPath, url)
        self.submit_worker.log_signal.connect(self.log_submit)
        self.submit_worker.finished_signal.connect(self.on_submit_done)

        self.ui.Start_pushButton.setEnabled(False)
        self.submit_worker.start()

    def on_submit_done(self, tracking_id):
        if tracking_id:
            self.log_submit("<b>=== Task Submitted Successfully ===</b>")
            self.refresh_task_list()
        else:
            self.log_submit("<b>=== Task Submission Failed ===</b>")
            
        self.ui.Start_pushButton.setEnabled(True)

    # =========================================================
    # Status Pipeline & UI List Generation
    # =========================================================
    def refresh_task_list(self):
        """Clears the scroll area and rebuilds it dynamically from the JSON database"""
        while self.task_layout.count():
            item = self.task_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()

        db = load_db()
        for tid, info in reversed(list(db.items())): # Show newest at top
            self.add_task_ui_row(tid, info)

    def add_task_ui_row(self, tracking_id, info):
        """Creates a single row in the scroll area for a task"""
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(5, 5, 5, 5)
        
        # Format a clean UI string
        folder_name = os.path.basename(os.path.normpath(info.get('dicom_path', 'Unknown')))
        lbl_info = QLabel(f"<b>{folder_name}</b><br><span style='font-size:10px; color:gray;'>ID: {tracking_id}</span>")
        
        status = info.get('status', 'Unknown')
        lbl_status = QLabel(f"[{status}]")
        
        btn_action = QPushButton()
        
        if status == "Finished":
            lbl_status.setStyleSheet("color: green; font-weight: bold;")
            btn_action.setText("View Results")
            btn_action.setStyleSheet("background-color: #2e7d32; color: white;")
        else:
            lbl_status.setStyleSheet("color: orange; font-weight: bold;")
            btn_action.setText("Check Status")
            
        # Lambda closure to pass the specific ID to the click event
        btn_action.clicked.connect(lambda checked, t=tracking_id, s=status: self.handle_task_action(t, s))

        row_layout.addWidget(lbl_info)
        row_layout.addStretch()
        row_layout.addWidget(lbl_status)
        row_layout.addWidget(btn_action)
        
        self.task_layout.addWidget(row_widget)

    def handle_task_action(self, tracking_id, current_status):
        """Triggered when the specific button on a task row is clicked"""
        if current_status == "Finished":
            self.open_result_viewer(tracking_id)
        else:
            self.check_single_status(tracking_id)

    def check_all_pending_tasks(self):
        """Triggered by the main 'Retrieve' button to ping all non-finished tasks"""
        url = self.ui.AIServer_lineEdit_Status.text().strip()
        if not url:
            QMessageBox.warning(self.window, "Error", "AI Server Status URL cannot be empty!")
            return

        db = load_db()
        pending = [tid for tid, info in db.items() if info.get("status") != "Finished"]
        
        if not pending:
            self.log_status("All tasks are already finished.")
            return
            
        for tid in pending:
            self.check_single_status(tid)

    def check_single_status(self, tracking_id):
        """Spawns a worker to ping the server for a specific task"""
        url = self.ui.AIServer_lineEdit_Status.text().strip()
        if not url:
            QMessageBox.warning(self.window, "Error", "AI Server Status URL cannot be empty!")
            return

        db = load_db()
        task_info = db.get(tracking_id, {})
        dest_path = task_info.get("dest_path", self.DestinationPath)

        # We don't store workers in self.worker to avoid overriding if multiple are clicked
        worker = StatusWorker(tracking_id, dest_path, url)
        worker.log_signal.connect(self.log_status)
        worker.status_updated_signal.connect(self.on_status_updated)
        
        # Keep reference to prevent garbage collection during run
        if hasattr(self, 'active_status_workers'):
            self.active_status_workers.append(worker)
        else:
            self.active_status_workers = [worker]
            
        worker.finished.connect(lambda w=worker: self.active_status_workers.remove(w) if w in self.active_status_workers else None)
        worker.start()

    def on_status_updated(self, tracking_id, new_status):
        self.log_status(f"<b>Task {tracking_id} updated to {new_status}.</b>")
        self.refresh_task_list()

    # =========================================================
    # Phase 3: Launch 3D Viewer
    # =========================================================
    def open_result_viewer(self, tracking_id):
        db = load_db()
        task_info = db.get(tracking_id, {})
        
        nifti_path = task_info.get("nifti_path", "")
        dest_path = task_info.get("dest_path", "")
        
        # Determine the extracted paths based on the tracking ID
        pkl_path = os.path.join(dest_path, f"{tracking_id}_boxes.pkl")
        
        if not os.path.exists(pkl_path):
            found_pkls = [f for f in os.listdir(dest_path) if f.endswith('.pkl')]
            if found_pkls: pkl_path = os.path.join(dest_path, found_pkls[0])

        # The viewer expects the folder containing the PNGs, which is the destination folder itself
        png_folder = dest_path

        if 'ResultViewerWindow' in globals():
            self.viewer = ResultViewerWindow(nifti_path, pkl_path, png_folder)
            self.viewer.show()
            self.log_status(f"Launched 3D Results Viewer for {tracking_id}")
        else:
            QMessageBox.critical(self.window, "Error", "ResultViewer.py module is missing in supportfiles directory. Cannot open viewer.")

# =========================================================
# RUN
# =========================================================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = LymphNode_Detection()
    w.show()
    sys.exit(app.exec())