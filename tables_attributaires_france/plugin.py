import csv
import json
import os
import time

from qgis.PyQt.QtCore import QCoreApplication, Qt
from qgis.PyQt.QtGui import QIcon, QPixmap
from qgis.PyQt.QtWidgets import (
    QAction,
    QApplication,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)
from qgis.core import (
    QgsAttributeEditorContainer,
    QgsAttributeEditorField,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsEditFormConfig,
    QgsField,
    QgsGeometry,
    QgsApplication,
    QgsProject,
    QgsVectorDataProvider,
    QgsVectorFileWriter,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QVariant

from .geocoding import (
    AddressGeocoder,
    api_error_address,
    cache_key,
    count_address_status,
    department_region_from_context,
    empty_stats,
    first_value,
    is_api_error_result,
    is_cacheable_result,
    normalize_text,
    not_found_address,
    result_text,
)
from .geocoding_task import AddressGeocodingTask
from .table_dialog import TableAddressDialog


class TablesAttributairesFrancePlugin:
    CACHE_FILENAME = "geocoding_cache.json"

    FIELD_ALIASES = {
        "id": ("id",),
        "numero": ("numero",),
        "voie": ("voie",),
        "adresse": ("adresse",),
        "resultat_adresse": ("resultat_adresse", "resultat_a"),
        "statut_adresse": ("statut_adresse", "statut_adr"),
        "type_lieu": ("type_lieu",),
        "complement": ("complement",),
        "code_postal": ("code_postal", "code_posta", "cp"),
        "ville": ("ville",),
        "commune": ("commune",),
        "code_insee": ("code_insee",),
        "departement": ("departement", "departemen"),
        "region": ("region",),
        "pays": ("pays",),
        "latitude": ("latitude", "lat"),
        "longitude": ("longitude", "lon", "long"),
        "source": ("source",),
        "commentaire": ("commentaire", "commentair"),
        "date_creation": ("date_creation", "date_creat"),
    }

    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.menu_name = self.tr("Tables attributaires France")
        self.geocoder = AddressGeocoder()
        self.current_task = None

    def tr(self, message):
        return QCoreApplication.translate("TablesAttributairesFrance", message)

    def initGui(self):
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))
        self.action = QAction(icon, self.tr("Ajouter les champs d'adresses France"), self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToVectorMenu(self.menu_name, self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.action is not None:
            self.iface.removePluginVectorMenu(self.menu_name, self.action)
            self.iface.removeToolBarIcon(self.action)

    def run(self):
        dialog = TableAddressDialog(self.iface.mainWindow())
        if not dialog.exec_():
            return

        layer = dialog.selected_layer()
        if layer is None:
            QMessageBox.warning(
                self.iface.mainWindow(),
                self.tr("Aucune couche"),
                self.tr("Ajoute ou selectionne d'abord une couche vectorielle dans le projet."),
            )
            return

        selected_fields = dialog.selected_fields()
        if not selected_fields:
            QMessageBox.warning(
                self.iface.mainWindow(),
                self.tr("Aucun champ"),
                self.tr("Selectionne au moins un champ a creer ou remplir."),
            )
            return
        if dialog.selected_only() and layer.selectedFeatureCount() == 0:
            QMessageBox.warning(
                self.iface.mainWindow(),
                self.tr("Aucune selection"),
                self.tr(
                    "L'option 'entites selectionnees seulement' est cochee, "
                    "mais aucun point n'est selectionne dans la couche."
                ),
            )
            return
        if dialog.reverse_geocode() and layer.geometryType() != QgsWkbTypes.PointGeometry:
            answer = QMessageBox.question(
                self.iface.mainWindow(),
                self.tr("Couche non ponctuelle"),
                self.tr(
                    "La recherche depuis les coordonnees fonctionne surtout avec des couches de points. "
                    "Continuer quand meme ?"
                ),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        validation_error = self._validate_output_paths(
            should_export=dialog.should_export(),
            export_path=dialog.export_path(),
            should_export_report=dialog.should_export_report(),
            report_path=dialog.report_path(),
        )
        if validation_error:
            QMessageBox.warning(
                self.iface.mainWindow(),
                self.tr("Sortie invalide"),
                validation_error,
            )
            return

        simulation = self._simulation_info(
            layer,
            selected_fields,
            selected_only=dialog.selected_only(),
            reverse_geocode=dialog.reverse_geocode(),
            only_missing_results=dialog.only_missing_results(),
            should_export=dialog.should_export(),
            export_path=dialog.export_path(),
            should_export_report=dialog.should_export_report(),
            report_path=dialog.report_path(),
        )
        if not self._confirm_simulation(simulation):
            return

        added_fields = self._add_missing_address_fields(layer, selected_fields)
        cache = self._load_cache()
        if dialog.reverse_geocode():
            self._start_reverse_geocoding_task(
                layer,
                selected_fields=selected_fields,
                selected_only=dialog.selected_only(),
                empty_only=dialog.empty_only(),
                only_missing_results=dialog.only_missing_results(),
                cache=cache,
                added_fields=added_fields,
                configure_form=dialog.configure_form(),
                should_export=dialog.should_export(),
                export_path=dialog.export_path(),
                export_driver=dialog.export_driver(),
                should_export_report=dialog.should_export_report(),
                report_path=dialog.report_path(),
            )
            return

        stats = self._fill_existing_values(
            layer,
            selected_fields=selected_fields,
            selected_only=dialog.selected_only(),
            empty_only=dialog.empty_only(),
            reverse_geocode=False,
            cache=cache,
        )
        self._save_cache(cache)

        if dialog.configure_form():
            self._configure_address_form(layer)

        export_message = ""
        if dialog.should_export():
            export_message = self._export_layer(layer, dialog.export_path(), dialog.export_driver())
        if dialog.should_export_report():
            report_message = self._export_report(stats, dialog.report_path())
            export_message = f"{export_message}\n{report_message}".strip()

        self.iface.setActiveLayer(layer)

        message = self._summary_message(added_fields, stats, dialog.reverse_geocode())
        if added_fields == 0:
            message = self._summary_message(added_fields, stats, dialog.reverse_geocode(), fields_already_present=True)
        if export_message:
            message = f"{message}\n\n{export_message}"

        self._show_summary_dialog(message, stats, dialog.reverse_geocode())

    def _add_missing_address_fields(self, layer, selected_fields):
        provider = layer.dataProvider()
        if not provider.capabilities() & QgsVectorDataProvider.AddAttributes:
            QMessageBox.warning(
                self.iface.mainWindow(),
                self.tr("Couche non modifiable"),
                self.tr("Cette couche ne permet pas d'ajouter des champs."),
            )
            return 0

        existing_names = {field.name().lower() for field in layer.fields()}
        fields_to_add = [
            field
            for field in self._address_fields()
            if field.name() in selected_fields and not self._has_field(existing_names, field.name())
        ]
        if not fields_to_add:
            return 0

        provider.addAttributes(fields_to_add)
        layer.updateFields()

        self._set_default_value(layer, "pays", "'France'")
        self._set_default_value(layer, "date_creation", "current_date()")

        return len(fields_to_add)

    def _validate_output_paths(self, should_export=False, export_path="", should_export_report=False, report_path=""):
        checks = []
        if should_export:
            checks.append(("export de couche", export_path))
        if should_export_report:
            checks.append(("rapport CSV", report_path))

        for label, path in checks:
            if not path:
                return self.tr(f"Choisis un fichier pour l'{label} avant de lancer le traitement.")
            directory = os.path.dirname(os.path.abspath(path))
            if not os.path.isdir(directory):
                return self.tr(f"Le dossier de sortie n'existe pas pour l'{label} : {directory}")
            if not os.access(directory, os.W_OK):
                return self.tr(f"Le dossier de sortie n'est pas accessible en ecriture : {directory}")
        return ""

    def _fields_to_add(self, layer, selected_fields):
        existing_names = {field.name().lower() for field in layer.fields()}
        return [
            field.name()
            for field in self._address_fields()
            if field.name() in selected_fields and not self._has_field(existing_names, field.name())
        ]

    def _feature_has_result(self, feature, field_indexes):
        for field_name in ("resultat_adresse", "adresse", "commune", "ville", "code_postal"):
            field_index = field_indexes.get(field_name, -1)
            if field_index >= 0 and not self._is_replaceable_value(feature.attribute(field_index)):
                return True
        return False

    def _simulation_info(
        self,
        layer,
        selected_fields,
        selected_only=False,
        reverse_geocode=False,
        only_missing_results=False,
        should_export=False,
        export_path="",
        should_export_report=False,
        report_path="",
    ):
        features = layer.selectedFeatures() if selected_only else list(layer.getFeatures())
        feature_count = len(features)
        fields_to_add = self._fields_to_add(layer, selected_fields)
        geocode_calls = feature_count if reverse_geocode else 0
        if reverse_geocode and only_missing_results:
            field_indexes = {name: self._field_index(layer.fields(), name) for name in self.FIELD_ALIASES}
            geocode_calls = sum(
                1
                for feature in features
                if not self._feature_has_result(feature, field_indexes)
            )
        estimated_seconds = self._estimated_processing_seconds(feature_count, geocode_calls)

        return {
            "layer_name": layer.name(),
            "feature_count": feature_count,
            "selected_only": selected_only,
            "fields_to_add": fields_to_add,
            "geocode_calls": geocode_calls,
            "only_missing_results": only_missing_results,
            "estimated_seconds": estimated_seconds,
            "should_export": should_export,
            "export_path": export_path,
            "should_export_report": should_export_report,
            "report_path": report_path,
        }

    def _confirm_simulation(self, simulation):
        fields = simulation["fields_to_add"]
        scope = "entites selectionnees" if simulation["selected_only"] else "toute la couche"
        export_line = "Non"
        if simulation["should_export"]:
            export_line = simulation["export_path"] or "Oui, mais aucun fichier choisi"
        report_line = "Non"
        if simulation["should_export_report"]:
            report_line = simulation["report_path"] or "Oui, mais aucun fichier choisi"

        dialog = QDialog(self.iface.mainWindow())
        dialog.setWindowTitle(self.tr("Simulation du traitement"))
        dialog.setMinimumWidth(520)
        dialog.setStyleSheet(
            """
            QDialog {
                background: #f7f8fb;
            }
            QFrame#Header, QFrame#Card, QFrame#Fields {
                background: #ffffff;
                border: 1px solid #d8dee9;
                border-radius: 8px;
            }
            QLabel#Title {
                color: #16233a;
                font-size: 18px;
                font-weight: 700;
            }
            QLabel#Subtitle, QLabel#Small {
                color: #5d6878;
            }
            QLabel#Metric {
                color: #16233a;
                font-size: 20px;
                font-weight: 700;
            }
            QLabel#MetricLabel {
                color: #5d6878;
                font-size: 11px;
            }
            QPushButton {
                min-width: 110px;
                padding: 7px 12px;
            }
            """
        )

        main_layout = QVBoxLayout()

        header = QFrame()
        header.setObjectName("Header")
        header_layout = QHBoxLayout()
        logo = QLabel()
        pixmap = QPixmap(os.path.join(os.path.dirname(__file__), "icon.png"))
        if not pixmap.isNull():
            logo.setPixmap(pixmap.scaled(54, 54, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        title_layout = QVBoxLayout()
        title = QLabel(self.tr("Simulation avant lancement"))
        title.setObjectName("Title")
        subtitle = QLabel(self.tr("Verifie le volume, les appels API et les sorties avant de lancer."))
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)
        title_layout.addWidget(title)
        title_layout.addWidget(subtitle)
        header_layout.addWidget(logo)
        header_layout.addLayout(title_layout)
        header.setLayout(header_layout)

        metric_grid = QGridLayout()
        metric_grid.addWidget(self._metric_card("Entites", simulation["feature_count"]), 0, 0)
        metric_grid.addWidget(self._metric_card("Appels API", simulation["geocode_calls"]), 0, 1)
        metric_grid.addWidget(self._metric_card("Temps estime", self._format_duration(simulation["estimated_seconds"])), 0, 2)

        details = QFrame()
        details.setObjectName("Card")
        details_layout = QVBoxLayout()
        for label, value in (
            ("Couche", simulation["layer_name"]),
            ("Portee", scope),
            ("Export couche", export_line),
            ("Rapport CSV", report_line),
        ):
            row = QLabel(f"<b>{label}</b> : {value}")
            row.setWordWrap(True)
            details_layout.addWidget(row)
        details.setLayout(details_layout)

        fields_frame = QFrame()
        fields_frame.setObjectName("Fields")
        fields_layout = QVBoxLayout()
        fields_title = QLabel(self.tr("Champs qui seront ajoutes"))
        fields_title.setObjectName("Title")
        fields_title.setStyleSheet("font-size: 13px;")
        fields_layout.addWidget(fields_title)
        if fields:
            fields_label = QLabel(", ".join(fields))
        else:
            fields_label = QLabel(self.tr("Aucun champ a ajouter"))
        fields_label.setObjectName("Small")
        fields_label.setWordWrap(True)
        fields_layout.addWidget(fields_label)
        fields_frame.setLayout(fields_layout)

        buttons = QHBoxLayout()
        cancel_button = QPushButton(self.tr("Annuler"))
        start_button = QPushButton(self.tr("Lancer"))
        start_button.setDefault(True)
        cancel_button.clicked.connect(dialog.reject)
        start_button.clicked.connect(dialog.accept)
        buttons.addStretch(1)
        buttons.addWidget(cancel_button)
        buttons.addWidget(start_button)

        main_layout.addWidget(header)
        main_layout.addLayout(metric_grid)
        main_layout.addWidget(details)
        main_layout.addWidget(fields_frame)
        main_layout.addLayout(buttons)
        dialog.setLayout(main_layout)
        return dialog.exec_() == QDialog.Accepted

    def _metric_card(self, label, value):
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout()
        metric = QLabel(str(value))
        metric.setObjectName("Metric")
        metric_label = QLabel(self.tr(label))
        metric_label.setObjectName("MetricLabel")
        layout.addWidget(metric)
        layout.addWidget(metric_label)
        card.setLayout(layout)
        return card

    def _start_reverse_geocoding_task(
        self,
        layer,
        selected_fields,
        selected_only=False,
        empty_only=True,
        only_missing_results=False,
        cache=None,
        added_fields=0,
        configure_form=True,
        should_export=False,
        export_path="",
        export_driver="GPKG",
        should_export_report=False,
        report_path="",
    ):
        if not layer.dataProvider().capabilities() & QgsVectorDataProvider.ChangeAttributeValues:
            QMessageBox.warning(
                self.iface.mainWindow(),
                self.tr("Couche non modifiable"),
                self.tr("Cette couche ne permet pas de mettre a jour les valeurs attributaires."),
            )
            return

        jobs, base_updates, base_stats = self._prepare_reverse_geocoding_jobs(
            layer,
            selected_fields,
            selected_only=selected_only,
            empty_only=empty_only,
            only_missing_results=only_missing_results,
        )
        if not jobs and not base_updates:
            self._show_summary_dialog(
                self.tr("Aucun point a traiter avec les options choisies."),
                base_stats,
                True,
            )
            return

        progress = self._progress_dialog(max(len(jobs), 1), True)
        if progress:
            progress["bar"].setRange(0, 100)
            progress["label"].setText(self.tr("Traitement en arriere-plan..."))
            progress["counters"].setText(
                self.tr(f"Points a geocoder : {len(jobs)}\nMise a jour appliquee a la fin du traitement.")
            )

        def finish_task(result, address_updates, address_stats, updated_cache, error_message):
            self.current_task = None
            if progress:
                progress["dialog"].close()
            self._finish_reverse_geocoding_task(
                result,
                error_message,
                layer,
                base_updates,
                base_stats,
                address_updates,
                address_stats,
                updated_cache,
                added_fields,
                configure_form,
                should_export,
                export_path,
                export_driver,
                should_export_report,
                report_path,
            )

        task = AddressGeocodingTask(
            self.tr("Geocodage des adresses France"),
            self.geocoder,
            jobs,
            selected_fields,
            empty_only,
            cache if cache is not None else {},
            self._current_date().toString("yyyy-MM-dd"),
            finish_task,
        )
        if progress:
            task.progressChanged.connect(lambda value: self._update_task_progress(progress, value))
            progress["cancel_button"].clicked.connect(task.cancel)
        self.current_task = task
        QgsApplication.taskManager().addTask(task)

    def _prepare_reverse_geocoding_jobs(
        self,
        layer,
        selected_fields,
        selected_only=False,
        empty_only=True,
        only_missing_results=False,
    ):
        fields = layer.fields()
        field_indexes = {name: self._field_index(fields, name) for name in self.FIELD_ALIASES}
        features = layer.selectedFeatures() if selected_only else list(layer.getFeatures())
        base_updates = {}
        jobs = []
        stats = self._empty_stats()
        stats["processed"] = len(features)
        stats["search_mode"] = "address"
        stats["date"] = self._current_date().toString("yyyy-MM-dd")
        seen_points = set()

        for feature in features:
            attrs = {}
            self._count_manual_result(stats, feature, field_indexes)
            self._set_attr(feature, attrs, field_indexes["id"], feature.id(), empty_only, "id" in selected_fields)
            self._set_attr(feature, attrs, field_indexes["pays"], "France", empty_only, "pays" in selected_fields)
            self._set_attr(
                feature,
                attrs,
                field_indexes["date_creation"],
                self._current_date(),
                empty_only,
                "date_creation" in selected_fields,
            )

            point = self._feature_point_wgs84(layer, feature)
            if point is None:
                stats["without_geometry"] += 1
                if attrs:
                    base_updates[feature.id()] = attrs
                continue

            point_key = (round(point.x(), 6), round(point.y(), 6))
            if point_key in seen_points:
                stats["duplicates"] += 1
            else:
                seen_points.add(point_key)
            self._set_attr(feature, attrs, field_indexes["latitude"], point.y(), empty_only, "latitude" in selected_fields)
            self._set_attr(feature, attrs, field_indexes["longitude"], point.x(), empty_only, "longitude" in selected_fields)

            if only_missing_results and self._feature_has_result(feature, field_indexes):
                if attrs:
                    base_updates[feature.id()] = attrs
                continue

            jobs.append(
                {
                    "fid": feature.id(),
                    "longitude": point.x(),
                    "latitude": point.y(),
                    "attrs": attrs,
                    "values": {
                        index: feature.attribute(index)
                        for index in field_indexes.values()
                        if index >= 0
                    },
                    "field_indexes": field_indexes,
                }
            )

        stats["updated"] = len(base_updates)
        return jobs, base_updates, stats

    def _finish_reverse_geocoding_task(
        self,
        result,
        error_message,
        layer,
        base_updates,
        base_stats,
        address_updates,
        address_stats,
        cache,
        added_fields,
        configure_form,
        should_export,
        export_path,
        export_driver,
        should_export_report,
        report_path,
    ):
        if not result and error_message:
            QMessageBox.warning(
                self.iface.mainWindow(),
                self.tr("Traitement interrompu"),
                self.tr(f"Le traitement a echoue : {error_message}"),
            )

        updates = dict(base_updates)
        for fid, attrs in address_updates.items():
            updates.setdefault(fid, {}).update(attrs)

        if updates:
            layer.dataProvider().changeAttributeValues(updates)
            layer.updateFields()
            layer.triggerRepaint()
        self._save_cache(cache)

        stats = self._merge_stats(base_stats, address_stats)
        stats["updated"] = len(updates)
        stats["canceled"] = stats["canceled"] or address_stats.get("canceled", False)

        if configure_form:
            self._configure_address_form(layer)

        export_message = ""
        if should_export:
            export_message = self._export_layer(layer, export_path, export_driver)
        if should_export_report:
            report_message = self._export_report(stats, report_path)
            export_message = f"{export_message}\n{report_message}".strip()

        self.iface.setActiveLayer(layer)
        message = self._summary_message(added_fields, stats, True, fields_already_present=added_fields == 0)
        if export_message:
            message = f"{message}\n\n{export_message}"
        self._show_summary_dialog(message, stats, True)

    def _merge_stats(self, base_stats, address_stats):
        stats = dict(base_stats)
        for key in (
            "address_found",
            "municipality_only",
            "not_found",
            "api_errors",
            "rural_places",
        ):
            stats[key] = address_stats.get(key, stats.get(key, 0))
        return stats

    def _update_task_progress(self, progress, value):
        percent = int(value)
        progress["bar"].setValue(percent)
        progress["label"].setText(self.tr(f"Traitement en arriere-plan - {percent}%"))

    def _show_summary_dialog(self, message, stats, reverse_geocode):
        dialog = QDialog(self.iface.mainWindow())
        dialog.setWindowTitle(self.tr("Mise a jour terminee"))
        dialog.setMinimumWidth(540)
        dialog.setStyleSheet(
            """
            QDialog { background: #f7f8fb; }
            QFrame#Header, QFrame#Card, QFrame#Summary {
                background: #ffffff;
                border: 1px solid #d8dee9;
                border-radius: 8px;
            }
            QLabel#Title {
                color: #16233a;
                font-size: 18px;
                font-weight: 700;
            }
            QLabel#Subtitle, QLabel#Small {
                color: #5d6878;
            }
            QLabel#Metric {
                color: #16233a;
                font-size: 20px;
                font-weight: 700;
            }
            QLabel#MetricLabel {
                color: #5d6878;
                font-size: 11px;
            }
            QPushButton {
                min-width: 110px;
                padding: 7px 12px;
            }
            """
        )

        layout = QVBoxLayout()
        header = QFrame()
        header.setObjectName("Header")
        header_layout = QHBoxLayout()
        logo = QLabel()
        pixmap = QPixmap(os.path.join(os.path.dirname(__file__), "icon.png"))
        if not pixmap.isNull():
            logo.setPixmap(pixmap.scaled(50, 50, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        title_layout = QVBoxLayout()
        title = QLabel(self.tr("Mise a jour terminee"))
        title.setObjectName("Title")
        subtitle = QLabel(self.tr("Le traitement est termine. Voici le bilan principal."))
        subtitle.setObjectName("Subtitle")
        title_layout.addWidget(title)
        title_layout.addWidget(subtitle)
        header_layout.addWidget(logo)
        header_layout.addLayout(title_layout)
        header.setLayout(header_layout)

        metrics = QGridLayout()
        metrics.addWidget(self._metric_card("Traitees", stats["processed"]), 0, 0)
        metrics.addWidget(self._metric_card("Mises a jour", stats["updated"]), 0, 1)
        if reverse_geocode:
            metrics.addWidget(self._metric_card("Trouvees", stats["address_found"]), 1, 0)
            metrics.addWidget(self._metric_card("Non trouvees", stats["not_found"]), 1, 1)
            metrics.addWidget(self._metric_card("Erreurs API", stats["api_errors"]), 1, 2)

        summary = QFrame()
        summary.setObjectName("Summary")
        summary_layout = QVBoxLayout()
        summary_text = QLabel(message)
        summary_text.setObjectName("Small")
        summary_text.setWordWrap(True)
        summary_layout.addWidget(summary_text)
        summary.setLayout(summary_layout)

        close_button = QPushButton(self.tr("OK"))
        close_button.clicked.connect(dialog.accept)
        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        button_layout.addWidget(close_button)

        layout.addWidget(header)
        layout.addLayout(metrics)
        layout.addWidget(summary)
        layout.addLayout(button_layout)
        dialog.setLayout(layout)
        dialog.exec_()

    def _estimated_processing_seconds(self, feature_count, geocode_calls):
        if geocode_calls:
            return max(1, int(geocode_calls * 0.35))
        return max(1, int(feature_count * 0.01))

    def _format_duration(self, seconds):
        if seconds < 60:
            return f"{seconds} s"
        minutes = seconds // 60
        remaining_seconds = seconds % 60
        if remaining_seconds:
            return f"{minutes} min {remaining_seconds} s"
        return f"{minutes} min"

    def _fill_existing_values(
        self,
        layer,
        selected_fields,
        selected_only=False,
        empty_only=True,
        reverse_geocode=False,
        cache=None,
    ):
        if not layer.dataProvider().capabilities() & QgsVectorDataProvider.ChangeAttributeValues:
            return self._empty_stats()

        fields = layer.fields()
        field_indexes = {name: self._field_index(fields, name) for name in self.FIELD_ALIASES}

        updates = {}
        features = layer.selectedFeatures() if selected_only else list(layer.getFeatures())
        progress = self._progress_dialog(len(features), reverse_geocode)
        canceled = False
        stats = self._empty_stats()
        stats["processed"] = len(features)
        stats["search_mode"] = "address"
        stats["date"] = self._current_date().toString("yyyy-MM-dd")
        seen_points = set()
        cache = cache if cache is not None else {}

        for current_index, feature in enumerate(features, start=1):
            if progress:
                self._update_progress_label(progress, current_index, len(features), stats)
                QApplication.processEvents()
                if progress["canceled"]:
                    canceled = True
                    break

            attrs = {}
            self._count_manual_result(stats, feature, field_indexes)
            self._set_attr(feature, attrs, field_indexes["id"], feature.id(), empty_only, "id" in selected_fields)
            self._set_attr(feature, attrs, field_indexes["pays"], "France", empty_only, "pays" in selected_fields)
            self._set_attr(
                feature,
                attrs,
                field_indexes["date_creation"],
                self._current_date(),
                empty_only,
                "date_creation" in selected_fields,
            )

            point = self._feature_point_wgs84(layer, feature)
            if point is not None:
                point_key = (round(point.x(), 6), round(point.y(), 6))
                if point_key in seen_points:
                    stats["duplicates"] += 1
                else:
                    seen_points.add(point_key)
                self._set_attr(
                    feature,
                    attrs,
                    field_indexes["latitude"],
                    point.y(),
                    empty_only,
                    "latitude" in selected_fields,
                )
                self._set_attr(
                    feature,
                    attrs,
                    field_indexes["longitude"],
                    point.x(),
                    empty_only,
                    "longitude" in selected_fields,
                )
                if reverse_geocode:
                    address = self._reverse_geocode_point(point.x(), point.y(), cache)
                    if address:
                        self._apply_address(feature, attrs, field_indexes, address, selected_fields, empty_only)
                        self._count_address_status(stats, address)
                    else:
                        address = self._not_found_address()
                        self._apply_address(feature, attrs, field_indexes, address, selected_fields, empty_only)
                        self._count_address_status(stats, address)
                    time.sleep(0.02)
            elif reverse_geocode:
                stats["without_geometry"] += 1

            if attrs:
                updates[feature.id()] = attrs

        if updates:
            layer.dataProvider().changeAttributeValues(updates)
            layer.updateFields()
            layer.triggerRepaint()
        if progress:
            self._update_progress_label(progress, len(features), len(features), stats)
            progress["dialog"].close()
        if canceled:
            stats["canceled"] = True
            self.iface.messageBar().pushWarning(
                self.tr("Traitement annule"),
                self.tr(f"{len(updates)} entite(s) ont ete preparee(s) avant l'annulation."),
            )
        stats["updated"] = len(updates)
        return stats

    def _update_progress_label(self, progress, current_index, total, stats):
        progress["bar"].setValue(current_index)
        percent = int((current_index / total) * 100) if total else 100
        progress["label"].setText(self.tr(f"Traitement {current_index}/{total} - {percent}%"))
        progress["counters"].setText(
            self.tr(
                f"Adresses trouvees : {stats['address_found']}    "
                f"Non trouvees : {stats['not_found']}    "
                f"Erreurs API : {stats['api_errors']}\n"
                f"Zones / lieux detectes : {stats['rural_places']}    "
                f"Doublons : {stats['duplicates']}"
            )
        )

    def _apply_address(self, feature, attrs, field_indexes, address, selected_fields, empty_only):
        self._set_attr(
            feature,
            attrs,
            field_indexes.get("resultat_adresse", -1),
            self._result_text(address),
            empty_only,
            "resultat_adresse" in selected_fields,
        )
        for field_name, value in address.items():
            if field_name == "type_lieu" and value == "":
                self._clear_attr_if_replaceable(
                    feature,
                    attrs,
                    field_indexes.get(field_name, -1),
                    field_name in selected_fields,
                )
                continue
            self._set_attr(
                feature,
                attrs,
                field_indexes.get(field_name, -1),
                value,
                empty_only,
                field_name in selected_fields,
            )
        if address.get("statut_adresse") in ("adresse_trouvee", "commune_seule", "zone_rurale"):
            self._clear_attr_if_replaceable(
                feature,
                attrs,
                field_indexes.get("commentaire", -1),
                "commentaire" in selected_fields,
            )

    def _result_text(self, address):
        return result_text(address)

    def _set_attr(self, feature, attrs, field_index, value, empty_only=True, enabled=True):
        if not enabled or field_index < 0 or value in (None, ""):
            return
        current_value = feature.attribute(field_index)
        if not empty_only or self._is_replaceable_value(current_value):
            attrs[field_index] = value

    def _is_replaceable_value(self, value):
        if value in (None, ""):
            return True
        normalized = str(value).strip().lower()
        return (
            normalized.startswith(("erreur_api", "timeout_api", "reponse_api"))
            or normalized in ("erreur api", "aucune adresse trouvee", "inconnu")
            or normalized.startswith("erreur pendant")
        )

    def _clear_attr_if_replaceable(self, feature, attrs, field_index, enabled=True):
        if not enabled or field_index < 0:
            return
        if self._is_replaceable_value(feature.attribute(field_index)):
            attrs[field_index] = ""

    def _feature_point_wgs84(self, layer, feature):
        if layer.geometryType() != QgsWkbTypes.PointGeometry or not feature.hasGeometry():
            return None

        geometry = QgsGeometry(feature.geometry())
        if geometry.isEmpty():
            return None

        transform = QgsCoordinateTransform(
            layer.crs() if layer.crs().isValid() else QgsCoordinateReferenceSystem("EPSG:4326"),
            QgsCoordinateReferenceSystem("EPSG:4326"),
            QgsProject.instance(),
        )
        geometry.transform(transform)

        if geometry.isMultipart():
            points = geometry.asMultiPoint()
            return points[0] if points else None
        return geometry.asPoint()

    def _reverse_geocode_point(self, longitude, latitude, cache=None):
        return self.geocoder.reverse_geocode_point(longitude, latitude, cache)

    def _reverse_geocode_geoplateforme(self, longitude, latitude, index="address"):
        return self.geocoder._reverse_geocode_geoplateforme(longitude, latitude, index)

    def _reverse_geocode_legacy_ban(self, longitude, latitude):
        return self.geocoder._reverse_geocode_legacy_ban(longitude, latitude)

    def _fetch_reverse_geocode(self, url, index="address"):
        return self.geocoder._fetch_reverse_geocode(url, index)

    def _first_successful_geocode(self, *searches):
        return self.geocoder._first_successful_geocode(*searches)

    def _address_from_properties(self, properties, index="address"):
        return self.geocoder._address_from_properties(properties, index)

    def _not_found_address(self):
        return not_found_address()

    def _api_error_address(self, status):
        return api_error_address(status)

    def _empty_stats(self):
        return empty_stats()

    def _count_address_status(self, stats, address):
        count_address_status(stats, address)

    def _count_manual_result(self, stats, feature, field_indexes):
        for field_name in ("adresse", "commune", "ville", "code_postal"):
            field_index = field_indexes.get(field_name, -1)
            if field_index >= 0 and feature.attribute(field_index) not in (None, ""):
                stats["manual_results"] += 1
                return

    def _normalize_text(self, value):
        return normalize_text(value)

    def _cache_key(self, longitude, latitude):
        return cache_key(longitude, latitude)

    def _is_cacheable_result(self, result):
        return is_cacheable_result(result)

    def _is_api_error_result(self, result):
        return is_api_error_result(result)

    def _cache_path(self):
        return os.path.join(os.path.dirname(__file__), self.CACHE_FILENAME)

    def _load_cache(self):
        path = self._cache_path()
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as cache_file:
                cache = json.load(cache_file)
        except (OSError, ValueError):
            return {}
        if isinstance(cache, dict):
            return {
                key: value
                for key, value in cache.items()
                if isinstance(value, dict) and self._is_cacheable_result(value)
            }
        return {}

    def _save_cache(self, cache):
        try:
            with open(self._cache_path(), "w", encoding="utf-8") as cache_file:
                json.dump(cache, cache_file, ensure_ascii=False, indent=2, sort_keys=True)
        except OSError:
            self.iface.messageBar().pushWarning(
                self.tr("Cache"),
                self.tr("Le cache local n'a pas pu etre enregistre."),
            )

    def _detect_place_type(self, properties):
        from .geocoding import detect_place_type

        return detect_place_type(properties)

    def _first_value(self, properties, *names):
        return first_value(properties, *names)

    def _department_region_from_context(self, context):
        return department_region_from_context(context)

    def _address_fields(self):
        return [
            QgsField("id", QVariant.Int),
            QgsField("numero", QVariant.String, len=20),
            QgsField("voie", QVariant.String, len=120),
            QgsField("adresse", QVariant.String, len=180),
            QgsField("resultat_adresse", QVariant.String, len=120),
            QgsField("complement", QVariant.String, len=120),
            QgsField("code_postal", QVariant.String, len=5),
            QgsField("ville", QVariant.String, len=100),
            QgsField("commune", QVariant.String, len=100),
            QgsField("code_insee", QVariant.String, len=5),
            QgsField("departement", QVariant.String, len=80),
            QgsField("region", QVariant.String, len=100),
            QgsField("pays", QVariant.String, len=50),
            QgsField("latitude", QVariant.Double),
            QgsField("longitude", QVariant.Double),
            QgsField("source", QVariant.String, len=80),
            QgsField("commentaire", QVariant.String, len=255),
            QgsField("date_creation", QVariant.Date),
        ]

    def _default_value(self, expression):
        from qgis.core import QgsDefaultValue

        return QgsDefaultValue(expression)

    def _set_default_value(self, layer, field_name, expression):
        field_index = self._field_index(layer.fields(), field_name)
        if field_index >= 0:
            layer.setDefaultValueDefinition(field_index, self._default_value(expression))

    def _current_date(self):
        from qgis.PyQt.QtCore import QDate

        return QDate.currentDate()

    def _has_field(self, existing_names, field_name):
        return any(alias.lower() in existing_names for alias in self.FIELD_ALIASES[field_name])

    def _field_index(self, fields, field_name):
        for alias in self.FIELD_ALIASES[field_name]:
            index = fields.indexOf(alias)
            if index >= 0:
                return index
        return -1

    def _progress_dialog(self, feature_count, reverse_geocode):
        if feature_count < 50 and not reverse_geocode:
            return None
        dialog = QDialog(self.iface.mainWindow())
        dialog.setWindowTitle(self.tr("Traitement en cours"))
        dialog.setMinimumWidth(500)
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setStyleSheet(
            """
            QDialog { background: #f7f8fb; }
            QFrame#Header, QFrame#Card {
                background: #ffffff;
                border: 1px solid #d8dee9;
                border-radius: 8px;
            }
            QLabel#Title {
                color: #16233a;
                font-size: 17px;
                font-weight: 700;
            }
            QLabel#Subtitle, QLabel#Small {
                color: #5d6878;
            }
            QProgressBar {
                min-height: 18px;
                border: 1px solid #c8d0dc;
                border-radius: 9px;
                background: #eef2f7;
                text-align: center;
            }
            QProgressBar::chunk {
                border-radius: 9px;
                background: #2f6f5e;
            }
            QPushButton {
                min-width: 110px;
                padding: 7px 12px;
            }
            """
        )

        state = {"canceled": False}
        layout = QVBoxLayout()
        header = QFrame()
        header.setObjectName("Header")
        header_layout = QHBoxLayout()
        logo = QLabel()
        pixmap = QPixmap(os.path.join(os.path.dirname(__file__), "icon.png"))
        if not pixmap.isNull():
            logo.setPixmap(pixmap.scaled(46, 46, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        title_layout = QVBoxLayout()
        title = QLabel(self.tr("Recherche des adresses"))
        title.setObjectName("Title")
        subtitle = QLabel(self.tr("Le plugin interroge les services d'adresses et mettra la couche a jour a la fin."))
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)
        title_layout.addWidget(title)
        title_layout.addWidget(subtitle)
        header_layout.addWidget(logo)
        header_layout.addLayout(title_layout)
        header.setLayout(header_layout)

        card = QFrame()
        card.setObjectName("Card")
        card_layout = QVBoxLayout()
        label = QLabel(self.tr("Preparation..."))
        label.setObjectName("Small")
        label.setWordWrap(True)
        bar = QProgressBar()
        bar.setRange(0, feature_count)
        counters = QLabel("")
        counters.setObjectName("Small")
        counters.setWordWrap(True)
        card_layout.addWidget(label)
        card_layout.addWidget(bar)
        card_layout.addWidget(counters)
        card.setLayout(card_layout)

        cancel_button = QPushButton(self.tr("Annuler"))
        cancel_button.clicked.connect(lambda: state.update({"canceled": True}))
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(cancel_button)

        layout.addWidget(header)
        layout.addWidget(card)
        layout.addLayout(buttons)
        dialog.setLayout(layout)
        dialog.show()
        QApplication.processEvents()
        state.update({"dialog": dialog, "bar": bar, "label": label, "counters": counters, "cancel_button": cancel_button})
        return state

    def _summary_message(self, added_fields, stats, reverse_geocode, fields_already_present=False):
        intro = "La couche a ete completee avec les champs d'adresses France."
        if fields_already_present:
            intro = "La couche contenait deja les champs demandes."

        lines = [
            intro,
            "",
            f"Entites traitees : {stats['processed']}",
            f"Entites mises a jour : {stats['updated']}",
        ]

        if reverse_geocode:
            lines.extend(
                [
                    "",
                    f"Adresses trouvees : {stats['address_found']}",
                    f"Communes seules : {stats['municipality_only']}",
                    f"Zones rurales / lieux detectes : {stats['rural_places']}",
                    f"Adresses non trouvees : {stats['not_found']}",
                    f"Erreurs API : {stats['api_errors']}",
                    f"Doublons detectes : {stats['duplicates']}",
                    f"Resultats deja presents : {stats['manual_results']}",
                ]
            )
            if stats["without_geometry"]:
                lines.append(f"Sans geometrie exploitable : {stats['without_geometry']}")

        if added_fields:
            lines.append("")
            lines.append(f"Champs ajoutes : {added_fields}")

        if stats["canceled"]:
            lines.append("")
            lines.append("Traitement annule avant la fin.")

        return self.tr("\n".join(lines))

    def _configure_address_form(self, layer):
        try:
            config = layer.editFormConfig()
            config.setLayout(QgsEditFormConfig.TabLayout)
            root = config.invisibleRootContainer()
            root.clear()

            self._add_form_group(
                root,
                layer,
                "Adresse",
                (
                    "numero",
                    "voie",
                    "adresse",
                    "resultat_adresse",
                    "complement",
                    "code_postal",
                    "ville",
                    "commune",
                    "code_insee",
                ),
            )
            self._add_form_group(
                root,
                layer,
                "Localisation",
                ("departement", "region", "pays", "latitude", "longitude"),
            )
            self._add_form_group(
                root,
                layer,
                "Suivi",
                ("source", "commentaire", "date_creation"),
            )
            layer.setEditFormConfig(config)
            self._hide_internal_fields(layer, ("id", "statut_adresse", "type_lieu"))
        except Exception:
            return

    def _hide_internal_fields(self, layer, field_names):
        config = layer.editFormConfig()
        for field_name in field_names:
            field_index = self._field_index(layer.fields(), field_name)
            if field_index < 0:
                continue
            try:
                config.setWidgetType(field_index, "Hidden")
            except Exception:
                pass
        layer.setEditFormConfig(config)

    def _add_form_group(self, root, layer, title, field_names):
        group = QgsAttributeEditorContainer(title, root)
        for field_name in field_names:
            field_index = self._field_index(layer.fields(), field_name)
            if field_index >= 0:
                group.addChildElement(QgsAttributeEditorField(field_name, field_index, group))
        root.addChildElement(group)

    def _export_layer(self, layer, path, driver_name):
        if not path:
            return self.tr("Export non realise : aucun fichier de sortie choisi.")

        path = self._path_with_driver_extension(path, driver_name)
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = driver_name
        options.fileEncoding = "UTF-8"
        options.layerName = layer.name()

        if hasattr(options, "onlySelectedFeatures"):
            options.onlySelectedFeatures = False

        result = QgsVectorFileWriter.writeAsVectorFormatV3(
            layer,
            path,
            QgsProject.instance().transformContext(),
            options,
        )

        error_code = result[0] if isinstance(result, tuple) else result
        if error_code == QgsVectorFileWriter.NoError:
            return self.tr(f"Export realise : {path}")
        return self.tr(f"Export echoue : {path}")

    def _path_with_driver_extension(self, path, driver_name):
        extensions = {
            "GPKG": ".gpkg",
            "ESRI Shapefile": ".shp",
            "GeoJSON": ".geojson",
            "CSV": ".csv",
        }
        _, extension = os.path.splitext(path)
        if extension:
            return path
        return f"{path}{extensions.get(driver_name, '')}"

    def _export_report(self, stats, path):
        if not path:
            return self.tr("Rapport non realise : aucun fichier CSV choisi.")

        path = self._path_with_report_extension(path)
        rows = [
            ("date_traitement", stats["date"]),
            ("mode_recherche", stats["search_mode"]),
            ("total_recherche", stats["processed"]),
            ("total_mis_a_jour", stats["updated"]),
            ("total_trouve", stats["address_found"] + stats["municipality_only"]),
            ("adresses_trouvees", stats["address_found"]),
            ("communes_seules", stats["municipality_only"]),
            ("zones_rurales_lieux", stats["rural_places"]),
            ("total_non_trouve", stats["not_found"]),
            ("erreurs_api", stats["api_errors"]),
            ("doublons", stats["duplicates"]),
            ("resultats_manuels", stats["manual_results"]),
            ("sans_geometrie", stats["without_geometry"]),
            ("annule", "oui" if stats["canceled"] else "non"),
        ]

        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as report_file:
                writer = csv.writer(report_file, delimiter=";")
                writer.writerow(("indicateur", "valeur"))
                writer.writerows(rows)
        except OSError:
            return self.tr(f"Rapport echoue : {path}")
        return self.tr(f"Rapport CSV realise : {path}")

    def _path_with_report_extension(self, path):
        _, extension = os.path.splitext(path)
        if extension:
            return path
        return f"{path}.csv"
