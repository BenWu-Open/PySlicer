# -*- coding: utf-8 -*-
import sys
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem, QLabel
from basePlugin import basePlugin

class MetadataInspectorPlugin(basePlugin):
    def __init__(self, context=None):
        super().__init__(context)
        self.name = "Metadata Inspector"
        self.detail = "Inspect key DICOM metadata and current volume geometry."
        context = context or {}
        self.metadata = context.get("metadata", {})
        self.spacing = context.get("spacing", (1.0, 1.0, 1.0))
        self.cursor = context.get("cursor", [0, 0, 0])
        self.shape = context.get("shape", (0, 0, 0))

    def run(self):
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.window = QWidget()
        self.window.setWindowTitle("Metadata Inspector")
        self.window.resize(640, 420)

        layout = QVBoxLayout(self.window)
        layout.addWidget(QLabel(
            f"Cursor Z/Y/X: {self.cursor[0]} / {self.cursor[1]} / {self.cursor[2]} | "
            f"Spacing dz/dy/dx: {self.spacing[0]:.4g} / {self.spacing[1]:.4g} / {self.spacing[2]:.4g} mm"
        ))

        table = QTableWidget(0, 2)
        table.setHorizontalHeaderLabels(["Field", "Value"])
        table.horizontalHeader().setStretchLastSection(True)

        # Original entries as requested
        rows = [
            ("Volume Shape", str(self.shape)),
            ("Patient Name", self.metadata.get("PatientName", "")),
            ("Patient ID", self.metadata.get("PatientID", "")),
            ("Study Description", self.metadata.get("StudyDescription", "")),
            ("Series Description", self.metadata.get("SeriesDescription", "")),
            ("Modality", self.metadata.get("Modality", "")),
            ("Study Date", self.metadata.get("StudyDate", "")),
            ("Series UID", self.metadata.get("SeriesInstanceUID", "")),
            ("Rows", str(self.metadata.get("Rows", ""))),
            ("Columns", str(self.metadata.get("Columns", ""))),
            ("Slices", str(self.metadata.get("Slices", ""))),
        ]

        table.setRowCount(len(rows))
        for row_idx, (key, value) in enumerate(rows):
            table.setItem(row_idx, 0, QTableWidgetItem(str(key)))
            table.setItem(row_idx, 1, QTableWidgetItem(str(value)))

        layout.addWidget(table)
        self.window.show()
        self.app.exec()