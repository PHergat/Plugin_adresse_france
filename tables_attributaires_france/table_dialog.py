from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from qgis.core import QgsMapLayerType, QgsProject, QgsWkbTypes


class TableAddressDialog(QDialog):
    FIELD_GROUPS = [
        (
            "Adresse",
            (
                ("numero", "Numero"),
                ("voie", "Voie"),
                ("adresse", "Adresse complete"),
                ("statut_adresse", "Statut adresse"),
                ("complement", "Complement"),
                ("code_postal", "Code postal"),
                ("ville", "Ville"),
                ("commune", "Commune"),
                ("code_insee", "Code INSEE"),
            ),
        ),
        (
            "Localisation",
            (
                ("departement", "Departement"),
                ("region", "Region"),
                ("pays", "Pays"),
                ("latitude", "Latitude"),
                ("longitude", "Longitude"),
            ),
        ),
        (
            "Infos internes",
            (
                ("id", "Identifiant"),
                ("source", "Source"),
                ("commentaire", "Commentaire"),
                ("date_creation", "Date de creation"),
            ),
        ),
    ]
    DEFAULT_FIELDS = {
        "id",
        "numero",
        "voie",
        "adresse",
        "statut_adresse",
        "code_postal",
        "ville",
        "commune",
        "code_insee",
        "departement",
        "region",
        "pays",
        "latitude",
        "longitude",
        "source",
        "commentaire",
        "date_creation",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Assistant adresses France")
        self.resize(760, 720)
        self._apply_style()

        self._field_inputs = {}
        self._layers = self._project_vector_layers()

        tabs = QTabWidget()
        tabs.addTab(self._build_processing_tab(), "Traitement")
        tabs.addTab(self._build_fields_tab(), "Champs")
        tabs.addTab(self._build_export_tab(), "Export")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Mettre a jour")
        buttons.button(QDialogButtonBox.Ok).setEnabled(bool(self._layers))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addWidget(self._build_header())
        layout.addWidget(tabs)
        layout.addWidget(buttons)
        self.setLayout(layout)
        self._set_export_enabled(False)

    def selected_layer(self):
        layer_id = self._layer_input.currentData()
        if not layer_id:
            return None
        return QgsProject.instance().mapLayer(layer_id)

    def selected_only(self):
        return self._selected_only_input.isChecked()

    def empty_only(self):
        return self._empty_only_input.isChecked()

    def reverse_geocode(self):
        return self._reverse_geocode_input.isChecked()

    def configure_form(self):
        return self._configure_form_input.isChecked()

    def selected_fields(self):
        return {
            field_name
            for field_name, checkbox in self._field_inputs.items()
            if checkbox.isChecked()
        }

    def should_export(self):
        return self._export_input.isChecked()

    def export_path(self):
        return self._export_path_input.text().strip()

    def export_driver(self):
        return self._export_format_input.currentData()

    def _build_header(self):
        header = QFrame()
        header.setObjectName("Header")
        title = QLabel("Assistant adresses France")
        title.setObjectName("Title")
        subtitle = QLabel("Complete une couche de points avec les champs adresse, localisation et export.")
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)

        layout = QVBoxLayout()
        layout.addWidget(title)
        layout.addWidget(subtitle)
        header.setLayout(layout)
        return header

    def _build_processing_tab(self):
        tab = QWidget()
        layout = QVBoxLayout()

        source_box = QGroupBox("Couche source")
        source_layout = QFormLayout()
        self._layer_input = QComboBox()
        if self._layers:
            for layer in self._layers:
                geometry_label = QgsWkbTypes.displayString(layer.wkbType()) or "table"
                selected_count = layer.selectedFeatureCount()
                suffix = f" - {selected_count} selection(s)" if selected_count else ""
                self._layer_input.addItem(f"{layer.name()} ({geometry_label}{suffix})", layer.id())
        else:
            self._layer_input.addItem("Aucune couche vectorielle disponible", None)
        source_layout.addRow("Couche", self._layer_input)
        source_box.setLayout(source_layout)

        options_box = QGroupBox("Options")
        options_layout = QVBoxLayout()
        self._selected_only_input = QCheckBox("Traiter seulement les entites selectionnees")
        self._selected_only_input.setChecked(False)
        self._empty_only_input = QCheckBox("Ne remplir que les cellules vides")
        self._empty_only_input.setChecked(True)
        self._reverse_geocode_input = QCheckBox("Rechercher les adresses depuis les coordonnees")
        self._reverse_geocode_input.setChecked(True)
        self._configure_form_input = QCheckBox("Organiser le formulaire de saisie QGIS")
        self._configure_form_input.setChecked(True)

        for checkbox in (
            self._selected_only_input,
            self._empty_only_input,
            self._reverse_geocode_input,
            self._configure_form_input,
        ):
            options_layout.addWidget(checkbox)
        options_box.setLayout(options_layout)

        layout.addWidget(source_box)
        layout.addWidget(options_box)
        layout.addStretch(1)
        tab.setLayout(layout)
        return tab

    def _build_fields_tab(self):
        tab = QWidget()
        layout = QVBoxLayout()

        actions = QHBoxLayout()
        select_recommended = QPushButton("Selection conseillee")
        select_all = QPushButton("Tout cocher")
        select_none = QPushButton("Tout decocher")
        select_recommended.clicked.connect(lambda: self._set_field_selection(self.DEFAULT_FIELDS))
        select_all.clicked.connect(lambda: self._set_field_selection(set(self._field_inputs)))
        select_none.clicked.connect(lambda: self._set_field_selection(set()))
        actions.addWidget(select_recommended)
        actions.addWidget(select_all)
        actions.addWidget(select_none)
        actions.addStretch(1)

        field_container = QWidget()
        field_grid = QGridLayout()
        for column, (group_title, fields) in enumerate(self.FIELD_GROUPS):
            group_box = QGroupBox(group_title)
            group_layout = QVBoxLayout()
            for field_name, label in fields:
                checkbox = QCheckBox(label)
                checkbox.setChecked(field_name in self.DEFAULT_FIELDS)
                self._field_inputs[field_name] = checkbox
                group_layout.addWidget(checkbox)
            group_layout.addStretch(1)
            group_box.setLayout(group_layout)
            field_grid.addWidget(group_box, 0, column)
        field_container.setLayout(field_grid)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(field_container)

        layout.addLayout(actions)
        layout.addWidget(scroll)
        tab.setLayout(layout)
        return tab

    def _build_export_tab(self):
        tab = QWidget()
        layout = QVBoxLayout()

        export_box = QGroupBox("Sortie")
        export_layout = QFormLayout()
        self._export_input = QCheckBox("Exporter la couche apres traitement")
        self._export_input.toggled.connect(self._set_export_enabled)
        self._export_format_input = QComboBox()
        self._export_format_input.addItem("GeoPackage (.gpkg)", "GPKG")
        self._export_format_input.addItem("Shapefile (.shp)", "ESRI Shapefile")
        self._export_format_input.addItem("GeoJSON (.geojson)", "GeoJSON")
        self._export_format_input.addItem("CSV (.csv)", "CSV")

        self._export_path_input = QLineEdit()
        self._export_path_input.setPlaceholderText("Choisir un fichier de sortie")
        self._export_browse_button = QPushButton("Parcourir")
        self._export_browse_button.clicked.connect(self._choose_export_path)

        export_path_layout = QHBoxLayout()
        export_path_layout.addWidget(self._export_path_input)
        export_path_layout.addWidget(self._export_browse_button)

        export_layout.addRow("", self._export_input)
        export_layout.addRow("Format", self._export_format_input)
        export_layout.addRow("Fichier", export_path_layout)
        export_box.setLayout(export_layout)

        format_note = QLabel("GeoPackage est conseille pour conserver les noms de champs complets.")
        format_note.setObjectName("Hint")
        format_note.setWordWrap(True)

        layout.addWidget(export_box)
        layout.addWidget(format_note)
        layout.addStretch(1)
        tab.setLayout(layout)
        return tab

    def _project_vector_layers(self):
        return [
            layer
            for layer in QgsProject.instance().mapLayers().values()
            if layer.type() == QgsMapLayerType.VectorLayer
        ]

    def _set_field_selection(self, selected_fields):
        for field_name, checkbox in self._field_inputs.items():
            checkbox.setChecked(field_name in selected_fields)

    def _set_export_enabled(self, enabled):
        self._export_format_input.setEnabled(enabled)
        self._export_path_input.setEnabled(enabled)
        self._export_browse_button.setEnabled(enabled)

    def _choose_export_path(self):
        filters = {
            "GPKG": "GeoPackage (*.gpkg)",
            "ESRI Shapefile": "Shapefile (*.shp)",
            "GeoJSON": "GeoJSON (*.geojson)",
            "CSV": "CSV (*.csv)",
        }
        driver = self.export_driver()
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Exporter la couche",
            "",
            filters.get(driver, "Tous les fichiers (*.*)"),
        )
        if path:
            self._export_path_input.setText(path)

    def _apply_style(self):
        self.setStyleSheet(
            """
            QDialog {
                background: #f6f7f9;
            }
            QFrame#Header {
                background: #ffffff;
                border: 1px solid #d8dde6;
                border-radius: 8px;
            }
            QLabel#Title {
                color: #1f2937;
                font-size: 20px;
                font-weight: 700;
            }
            QLabel#Subtitle, QLabel#Hint {
                color: #5b6472;
            }
            QTabWidget::pane {
                border: 1px solid #d8dde6;
                background: #ffffff;
                border-radius: 6px;
            }
            QTabBar::tab {
                background: #e9edf3;
                min-width: 130px;
                min-height: 28px;
                padding: 7px 14px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background: #ffffff;
                color: #14532d;
                font-weight: 600;
            }
            QGroupBox {
                background: #ffffff;
                border: 1px solid #d8dde6;
                border-radius: 6px;
                margin-top: 12px;
                padding: 14px 10px 10px 10px;
                font-weight: 600;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
            QPushButton {
                padding: 6px 12px;
            }
            QComboBox, QLineEdit {
                padding: 5px;
            }
            """
        )
