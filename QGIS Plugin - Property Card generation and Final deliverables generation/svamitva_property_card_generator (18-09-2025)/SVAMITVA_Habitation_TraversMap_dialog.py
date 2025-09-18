# -*- coding: utf-8 -*-
from qgis.PyQt.QtWidgets import QDialog
import os
import shutil
from qgis.PyQt import uic
from qgis.PyQt.QtGui import QColor, QFont, QIcon, QPixmap
from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox
from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsWkbTypes,
    QgsFeature,
    QgsField,
    QgsVectorDataProvider,
    QgsExpression,
    QgsSymbol,
    QgsFillSymbol,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsRuleBasedRenderer,
    QgsSingleSymbolRenderer,
    QgsPalLayerSettings,
    QgsTextFormat,
    QgsTextBufferSettings,
    QgsVectorLayerSimpleLabeling,
    QgsTextBufferSettings,
    QgsProperty,
    QgsFeatureRequest,
    QgsGeometry,
    QgsVectorFileWriter,
    QgsRuleBasedLabeling,
    QgsTextBackgroundSettings,
    QgsPrintLayout,
    QgsLayoutAtlas,
    QgsLayoutItemLabel,
    QgsReadWriteContext,
    QgsPropertyCollection,
    QgsUnitTypes,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform
)
import processing

# Load the UI
FORM_CLASS, _ = uic.loadUiType(
    os.path.join(os.path.dirname(__file__), 'ui_habitation_traverse_map.ui')
)



class SVAMITVAHabitationTraverseMapDialog(QDialog, FORM_CLASS):
    def __init__(self, iface, parent=None):
        super(SVAMITVAHabitationTraverseMapDialog, self).__init__(parent)
        self.iface = iface
        self.setupUi(self)

        # ✅ Set window icon from Habitation_Traverse_icon.png
        icon_path = os.path.join(os.path.dirname(__file__), "Habitation_Traverse_icon.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
            
        # Connect UI actions
        self.generateTraverseButton.clicked.connect(self.generate_traverse_map)
        self.browseOutputButton.clicked.connect(self.browse_output_folder)

        # Ensure textEdit and progressBar exist (from UI)
        # Use textEdit for logs (UI: self.logTextEdit)
        # Populate polygon layers now and whenever the dialog is shown (to reflect project changes)
        try:
            self.populate_polygon_layers()
        except Exception:
            # populate again on showEvent if something went wrong now
            pass

    def showEvent(self, event):
        """Refresh polygon list when dialog is shown (keeps list up-to-date)."""
        super().showEvent(event)
        try:
            self.populate_polygon_layers()
        except Exception as e:
            # best-effort: log any issue without breaking UI
            try:
                self.logTextEdit.append(f"⚠️ Error refreshing polygon layers on show: {e}")
            except Exception:
                pass

    def populate_polygon_layers(self):
        """Populate polygonLayerComboBox with polygon layers (store layer.id() in itemData)."""
        self.polygonLayerComboBox.clear()
        layers = QgsProject.instance().mapLayers().values()
        count = 0
        for layer in layers:
            if isinstance(layer, QgsVectorLayer) and layer.geometryType() == QgsWkbTypes.PolygonGeometry:
                self.polygonLayerComboBox.addItem(layer.name(), layer.id())
                count += 1
        self.logTextEdit.append(f"Polygon layer list populated ({count} polygon layer(s) found).")

    def browse_output_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select output folder", os.path.expanduser("~"))
        if folder:
            self.outputFolderLineEdit.setText(folder)

    def _safe_write_layer(self, qlayer, out_path):
        """
        Write a QgsVectorLayer to a shapefile path robustly, handling differing QGIS APIs.
        Returns (success: bool, message: str)
        """
        try:
            # try legacy API first
            res = QgsVectorFileWriter.writeAsVectorFormat(qlayer, out_path, "UTF-8", qlayer.crs(), "ESRI Shapefile")
            if isinstance(res, (tuple, list)):
                err = res[0]
                msg = res[1] if len(res) > 1 else ""
            else:
                err = res
                msg = ""
            success = (err == QgsVectorFileWriter.NoError)
            return success, msg or ("NoError" if success else f"Error code {err}")
        except Exception as e:
            # fallback: try writeAsVectorFormatV2 if available (QGIS newer API)
            try:
                # build options only if available; but this is a best-effort fallback
                res2 = QgsVectorFileWriter.writeAsVectorFormatV2(qlayer, out_path, QgsProject.instance().transformContext())
                # If it didn't raise, assume success (no consistent return across versions)
                return True, "Saved via writeAsVectorFormatV2"
            except Exception as e2:
                return False, f"{e} / fallback failed: {e2}"

    def generate_traverse_map(self):
        """
        Full pipeline for Habitation Traverse Map generation described by user.
        """
        # Switch to Log tab immediately (index 1 expected)
        try:
            self.tabWidget.setCurrentIndex(1)
        except Exception:
            pass

        # Clear previous logs & start fresh
        try:
            self.logTextEdit.clear()
        except Exception:
            pass
        try:
            self.progressBar.setValue(0)
        except Exception:
            pass

        self.logTextEdit.append("Starting Habitation traverse map pipeline...")

        # 1) Read input widgets
        district = self.districtComboBox.currentText().strip() if hasattr(self, 'districtComboBox') else ""
        mandal = self.mandalLineEdit.text().strip()
        village = self.villageLineEdit.text().strip()
        lgd_code = self.lgdLineEdit.text().strip()
        base_folder = self.outputFolderLineEdit.text().strip()

        if not (district and mandal and village and lgd_code and base_folder):
            QMessageBox.warning(self, "Missing Input", "⚠️ Please fill all fields and select output folder.")
            self.logTextEdit.append("❌ Missing required inputs.")
            return

        # Create folder structure
        out_dir = os.path.join(base_folder, "Final deliverables", district, mandal, f"{village}_{lgd_code}")
        shapefiles_dir = os.path.join(out_dir, "Shapefiles")
        try:
            os.makedirs(shapefiles_dir, exist_ok=True)
            self.logTextEdit.append(f"📂 Created folders: {shapefiles_dir}")
        except Exception as e:
            self.logTextEdit.append(f"⚠️ Could not create output folders: {e}")
            return
        try:
            self.progressBar.setValue(5)
        except Exception:
            pass

        # 2) Get selected polygon layer (from combo box data which stores layer id)
        layer_id = self.polygonLayerComboBox.currentData()
        orig_layer = QgsProject.instance().mapLayer(layer_id) if layer_id else None
        if orig_layer is None:
            self.logTextEdit.append("❌ No polygon layer selected or layer not found in project.")
            return

        # 3) Save selected polygon layer into shapefiles folder as village_lgdcode.shp
        parcel_out_path = os.path.join(shapefiles_dir, f"{village}_{lgd_code}.shp")
        self.logTextEdit.append("Saving selected polygon layer to shapefiles folder...")
        ok, msg = self._safe_write_layer(orig_layer, parcel_out_path)
        if not ok:
            self.logTextEdit.append(f"❌ Failed to save polygon layer: {msg}")
            return
        self.logTextEdit.append(f"✅ Polygon layer saved: {parcel_out_path}")
        try:
            self.progressBar.setValue(15)
        except Exception:
            pass

        # 4) Reload saved polygon shapefile so rest of pipeline uses saved copy
        parcel_layer = QgsVectorLayer(parcel_out_path, f"{village}_{lgd_code}", "ogr")
        if not parcel_layer.isValid():
            self.logTextEdit.append("❌ Reloaded parcel layer is invalid.")
            return
        QgsProject.instance().addMapLayer(parcel_layer)
        self.logTextEdit.append(f"Added saved parcel layer to project: {parcel_layer.name()}")
        try:
            self.progressBar.setValue(20)
        except Exception:
            pass

        # 5) Dissolve (native:dissolve)
        dissolve_path = os.path.join(shapefiles_dir, f"{village}_{lgd_code}_dissolve.shp")
        try:
            self.logTextEdit.append("Dissolving parcel layer...")
            # processing.run returns dict; use OUTPUT key as we provided path variable
            processing.run("native:dissolve", {'INPUT': parcel_layer, 'OUTPUT': dissolve_path})
            dissolve_layer = QgsVectorLayer(dissolve_path, os.path.basename(dissolve_path), "ogr")
            if not dissolve_layer.isValid():
                raise RuntimeError("Dissolved layer invalid after processing.")

            # ✅ Apply symbology
            symbol = QgsFillSymbol.createSimple({
                'color': '255,255,255,0',    # transparent fill (RGBA with alpha=0)
                'outline_color': '0,0,0,255', # black border
                'outline_width': '1.0',       # stroke width
                'outline_width_unit': 'MM'    # millimeters
            })
            dissolve_layer.setRenderer(QgsSingleSymbolRenderer(symbol))
            dissolve_layer.triggerRepaint()
            
            QgsProject.instance().addMapLayer(dissolve_layer)
            self.logTextEdit.append(f"✅ Dissolve complete: {dissolve_path}")
        except Exception as e:
            self.logTextEdit.append(f"⚠️ Dissolve failed: {e}")
            return
        try:
            self.progressBar.setValue(40)
        except Exception:
            pass

        # 6) Extract vertices (native:extractvertices)
        vertices_path = os.path.join(shapefiles_dir, f"{village}_{lgd_code}_vertices.shp")
        try:
            self.logTextEdit.append("Extracting vertices from dissolved layer...")
            processing.run("native:extractvertices", {'INPUT': dissolve_layer, 'OUTPUT': vertices_path})
            vertices = QgsVectorLayer(vertices_path, os.path.basename(vertices_path), "ogr")
            if not vertices.isValid():
                raise RuntimeError("Vertices layer invalid after processing.")
            QgsProject.instance().addMapLayer(vertices)
            self.logTextEdit.append(f"✅ Vertices extracted: {vertices_path}")
        except Exception as e:
            self.logTextEdit.append(f"⚠️ Extract vertices failed: {e}")
            return
        try:
            self.progressBar.setValue(55)
        except Exception:
            pass

        # 7) Add point_ID, Easting_X, Northing_Y fields and populate values
        try:
            self.logTextEdit.append("Adding fields (point_ID, Easting_X, Northing_Y) and populating...")
            dp = vertices.dataProvider()
            new_fields = []
            if vertices.fields().indexFromName("point_ID") == -1:
                new_fields.append(QgsField("point_ID", QVariant.Int, "", 10))
            if vertices.fields().indexFromName("Easting_X") == -1:
                new_fields.append(QgsField("Easting_X", QVariant.Double, "", 20, 10))
            if vertices.fields().indexFromName("Northing_Y") == -1:
                new_fields.append(QgsField("Northing_Y", QVariant.Double, "", 20, 10))
            if new_fields:
                dp.addAttributes(new_fields)
                vertices.updateFields()

            # populate attributes
            vertices.startEditing()
            idx_pid = vertices.fields().indexFromName("point_ID")
            idx_e = vertices.fields().indexFromName("Easting_X")
            idx_n = vertices.fields().indexFromName("Northing_Y")
            
            has_vertex_ind = vertices.fields().indexFromName("vertex_ind") != -1
            
            for i, feat in enumerate(vertices.getFeatures()):
                geom = feat.geometry()
                pt = None
                try:
                    # most vertices will be a point so asPoint() should succeed
                    pt = geom.asPoint()
                except Exception:
                    try:
                        pt = geom.centroid().asPoint()
                    except Exception:
                        pt = None

                # ✅ Use vertex_ind + 1 if available
                if idx_pid != -1:
                    if has_vertex_ind:
                        try:
                            val = int(feat.attribute("vertex_ind")) + 1
                            vertices.changeAttributeValue(feat.id(), idx_pid, val)
                        except Exception:
                            # fallback: if vertex_ind not valid, use i + 1
                            vertices.changeAttributeValue(feat.id(), idx_pid, i + 1)
                    else:
                        vertices.changeAttributeValue(feat.id(), idx_pid, i + 1)

                if idx_e != -1 and pt:
                    vertices.changeAttributeValue(feat.id(), idx_e, round(float(pt.x()), 10))
                if idx_n != -1 and pt:
                    vertices.changeAttributeValue(feat.id(), idx_n, round(float(pt.y()), 10))
            vertices.commitChanges()
            self.logTextEdit.append("✅ Fields added and populated.")
        except Exception as e:
            self.logTextEdit.append(f"⚠️ Error adding/populating fields: {e}")
            return
        try:
            self.progressBar.setValue(70)
        except Exception:
            pass

        # 8) Delete duplicates by (PPM, Easting_X, Northing_Y) — manual dedupe
        try:
            self.logTextEdit.append("Removing duplicate vertices by (PPM, Easting_X, Northing_Y)...")
            seen = set()
            unique_feats = []
            fields = vertices.fields()
            ppm_idx = fields.indexFromName("PPM") if fields.indexFromName("PPM") != -1 else -1
            ex_idx = fields.indexFromName("Easting_X")
            ny_idx = fields.indexFromName("Northing_Y")

            for feat in vertices.getFeatures():
                ppm_val = feat.attribute(ppm_idx) if ppm_idx != -1 else None
                ex = feat.attribute(ex_idx)
                ny = feat.attribute(ny_idx)
                # guard if ex/ny are None
                ex_val = round(float(ex), 3) if (ex is not None and str(ex).strip() != "") else None
                ny_val = round(float(ny), 3) if (ny is not None and str(ny).strip() != "") else None
                key = (ppm_val, ex_val, ny_val)
                if key in seen:
                    continue
                seen.add(key)
                unique_feats.append(feat)

            # create memory layer with same fields & geometry
            no_dup = QgsVectorLayer("Point?crs=" + vertices.crs().authid(),
                                    f"{village}_{lgd_code}__no_dup_vertices", "memory")
            no_dup_dp = no_dup.dataProvider()
            no_dup_dp.addAttributes(vertices.fields())
            no_dup.updateFields()
            no_dup.startEditing()
            for f in unique_feats:
                newf = QgsFeature(no_dup.fields())
                newf.setGeometry(f.geometry())
                # copy attributes by name
                for idx in range(len(no_dup.fields())):
                    fldname = no_dup.fields()[idx].name()
                    newf.setAttribute(idx, f.attribute(fldname))
                no_dup.addFeature(newf)
            no_dup.commitChanges()

            # --- Update point_ID = vertex_ind + 1 ---
            try:
                # Update point_ID = vertex_ind + 1
                idx_pid = no_dup.fields().indexFromName("point_ID")
                idx_vid = no_dup.fields().indexFromName("vertex_ind")
                if idx_pid != -1 and idx_vid != -1:
                    no_dup.startEditing()
                    for feat in no_dup.getFeatures():
                        vid = feat.attribute(idx_vid)
                        if vid is not None:
                            no_dup.changeAttributeValue(feat.id(), idx_pid, int(vid) + 1)
                    no_dup.commitChanges()
                    self.logTextEdit.append("🔄 Updated point_ID = vertex_ind + 1 for no-duplicate vertices.")
            except Exception as e:
                self.logTextEdit.append(f"⚠️ Error updating point_ID: {e}")
                
            # Save no-duplicate vertices to shapefile
            no_dup_path = os.path.join(shapefiles_dir, f"{village}_{lgd_code}_no_dup_vertices.shp")
            ok, msg = self._safe_write_layer(no_dup, no_dup_path)
            if not ok:
                self.logTextEdit.append(f"⚠️ Error saving no-duplicate vertices: {msg}")
            else:
                no_dup_layer = QgsVectorLayer(no_dup_path, os.path.basename(no_dup_path), "ogr")
                if no_dup_layer.isValid():
                    QgsProject.instance().addMapLayer(no_dup_layer)
                self.logTextEdit.append(f"✅ No-duplicate vertices created and saved: {no_dup_path}")
        except Exception as e:
            self.logTextEdit.append(f"⚠️ Error in duplicate-removal: {e}")
            return
        try:
            self.progressBar.setValue(95)
        except Exception:
            pass

        self.logTextEdit.append("✅ Habitation traverse map pipeline completed.")
        try:
            self.progressBar.setValue(100)
        except Exception:
            pass