import json
import os
import time
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from qgis.PyQt.QtCore import QCoreApplication, Qt
from qgis.PyQt.QtWidgets import QAction, QApplication, QMessageBox, QProgressDialog
from qgis.core import (
    QgsAttributeEditorContainer,
    QgsAttributeEditorField,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsEditFormConfig,
    QgsField,
    QgsGeometry,
    QgsProject,
    QgsVectorDataProvider,
    QgsVectorFileWriter,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QVariant

from .table_dialog import TableAddressDialog


class TablesAttributairesFrancePlugin:
    FIELD_ALIASES = {
        "id": ("id",),
        "numero": ("numero",),
        "voie": ("voie",),
        "adresse": ("adresse",),
        "statut_adresse": ("statut_adresse", "statut_adr"),
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

    def tr(self, message):
        return QCoreApplication.translate("TablesAttributairesFrance", message)

    def initGui(self):
        self.action = QAction(self.tr("Ajouter les champs d'adresses France"), self.iface.mainWindow())
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

        simulation = self._simulation_info(
            layer,
            selected_fields,
            selected_only=dialog.selected_only(),
            reverse_geocode=dialog.reverse_geocode(),
            should_export=dialog.should_export(),
            export_path=dialog.export_path(),
        )
        if not self._confirm_simulation(simulation):
            return

        added_fields = self._add_missing_address_fields(layer, selected_fields)
        stats = self._fill_existing_values(
            layer,
            selected_fields=selected_fields,
            selected_only=dialog.selected_only(),
            empty_only=dialog.empty_only(),
            reverse_geocode=dialog.reverse_geocode(),
        )

        if dialog.configure_form():
            self._configure_address_form(layer)

        export_message = ""
        if dialog.should_export():
            export_message = self._export_layer(layer, dialog.export_path(), dialog.export_driver())

        self.iface.setActiveLayer(layer)

        message = self._summary_message(added_fields, stats, dialog.reverse_geocode())
        if added_fields == 0:
            message = self._summary_message(added_fields, stats, dialog.reverse_geocode(), fields_already_present=True)
        if export_message:
            message = f"{message}\n\n{export_message}"

        QMessageBox.information(
            self.iface.mainWindow(),
            self.tr("Mise a jour terminee"),
            message,
        )

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

    def _fields_to_add(self, layer, selected_fields):
        existing_names = {field.name().lower() for field in layer.fields()}
        return [
            field.name()
            for field in self._address_fields()
            if field.name() in selected_fields and not self._has_field(existing_names, field.name())
        ]

    def _simulation_info(self, layer, selected_fields, selected_only=False, reverse_geocode=False, should_export=False, export_path=""):
        feature_count = layer.selectedFeatureCount() if selected_only else layer.featureCount()
        fields_to_add = self._fields_to_add(layer, selected_fields)
        geocode_calls = feature_count if reverse_geocode else 0
        estimated_seconds = self._estimated_processing_seconds(feature_count, geocode_calls)

        return {
            "layer_name": layer.name(),
            "feature_count": feature_count,
            "selected_only": selected_only,
            "fields_to_add": fields_to_add,
            "geocode_calls": geocode_calls,
            "estimated_seconds": estimated_seconds,
            "should_export": should_export,
            "export_path": export_path,
        }

    def _confirm_simulation(self, simulation):
        fields = simulation["fields_to_add"]
        if fields:
            field_lines = "\n".join(f"  - {field_name}" for field_name in fields)
        else:
            field_lines = "  Aucun champ a ajouter"

        scope = "entites selectionnees" if simulation["selected_only"] else "toute la couche"
        export_line = "Non"
        if simulation["should_export"]:
            export_line = simulation["export_path"] or "Oui, mais aucun fichier choisi"

        message = self.tr(
            "Simulation avant lancement\n\n"
            f"Couche : {simulation['layer_name']}\n"
            f"Portee : {scope}\n"
            f"Entites a traiter : {simulation['feature_count']}\n"
            f"Appels adresse estimes : {simulation['geocode_calls']}\n"
            f"Temps estime : {self._format_duration(simulation['estimated_seconds'])}\n"
            f"Export : {export_line}\n\n"
            "Champs qui seront ajoutes :\n"
            f"{field_lines}\n\n"
            "Lancer le traitement ?"
        )

        answer = QMessageBox.question(
            self.iface.mainWindow(),
            self.tr("Simulation du traitement"),
            message,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        return answer == QMessageBox.Yes

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

    def _fill_existing_values(self, layer, selected_fields, selected_only=False, empty_only=True, reverse_geocode=False):
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

        for current_index, feature in enumerate(features, start=1):
            if progress:
                progress.setValue(current_index - 1)
                QApplication.processEvents()
                if progress.wasCanceled():
                    canceled = True
                    break

            attrs = {}
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
                    address = self._reverse_geocode_point(point.x(), point.y())
                    if address:
                        self._apply_address(feature, attrs, field_indexes, address, selected_fields, empty_only)
                        self._count_address_status(stats, address)
                    else:
                        address = self._not_found_address()
                        self._apply_address(feature, attrs, field_indexes, address, selected_fields, empty_only)
                        self._count_address_status(stats, address)
                    time.sleep(0.05)
            elif reverse_geocode:
                stats["without_geometry"] += 1

            if attrs:
                updates[feature.id()] = attrs

        if updates:
            layer.dataProvider().changeAttributeValues(updates)
            layer.updateFields()
            layer.triggerRepaint()
        if progress:
            progress.setValue(len(features))
        if canceled:
            stats["canceled"] = True
            self.iface.messageBar().pushWarning(
                self.tr("Traitement annule"),
                self.tr(f"{len(updates)} entite(s) ont ete preparee(s) avant l'annulation."),
            )
        stats["updated"] = len(updates)
        return stats

    def _apply_address(self, feature, attrs, field_indexes, address, selected_fields, empty_only):
        for field_name, value in address.items():
            self._set_attr(
                feature,
                attrs,
                field_indexes.get(field_name, -1),
                value,
                empty_only,
                field_name in selected_fields,
            )

    def _set_attr(self, feature, attrs, field_index, value, empty_only=True, enabled=True):
        if not enabled or field_index < 0 or value in (None, ""):
            return
        current_value = feature.attribute(field_index)
        if not empty_only or current_value in (None, ""):
            attrs[field_index] = value

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

    def _reverse_geocode_point(self, longitude, latitude):
        return (
            self._reverse_geocode_geoplateforme(longitude, latitude)
            or self._reverse_geocode_geoplateforme(longitude, latitude, "municipality")
            or self._reverse_geocode_legacy_ban(longitude, latitude)
        )

    def _reverse_geocode_geoplateforme(self, longitude, latitude, index="address"):
        params = urlencode(
            {
                "lon": f"{longitude:.8f}",
                "lat": f"{latitude:.8f}",
                "index": index,
                "limit": 1,
            }
        )
        return self._fetch_reverse_geocode(f"https://data.geopf.fr/geocodage/reverse?{params}", index)

    def _reverse_geocode_legacy_ban(self, longitude, latitude):
        params = urlencode(
            {
                "lon": f"{longitude:.8f}",
                "lat": f"{latitude:.8f}",
                "limit": 1,
            }
        )
        return self._fetch_reverse_geocode(f"https://api-adresse.data.gouv.fr/reverse/?{params}", "address")

    def _fetch_reverse_geocode(self, url, index="address"):
        try:
            request = Request(url, headers={"User-Agent": "QGIS Tables Attributaires France/0.1.0"})
            with urlopen(request, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, URLError, ValueError):
            return None

        features = payload.get("features") or []
        if not features:
            return None
        properties = features[0].get("properties") or {}
        return self._address_from_properties(properties, index)

    def _address_from_properties(self, properties, index="address"):
        label = self._first_value(properties, "label", "fulltext", "full_text", "name")
        street = self._first_value(properties, "street", "street_name", "name")
        house_number = self._first_value(properties, "housenumber", "house_number")
        postcode = self._first_value(properties, "postcode", "postalcode", "postal_code")
        city = self._first_value(properties, "city", "municipality", "commune")
        citycode = self._first_value(properties, "citycode", "city_code", "insee", "code_insee")
        context = self._first_value(properties, "context")
        department, region = self._department_region_from_context(context)
        department = self._first_value(properties, "department", "departement") or department
        region = self._first_value(properties, "region") or region

        return {
            "numero": house_number,
            "voie": street,
            "adresse": label,
            "code_postal": postcode,
            "ville": city,
            "commune": city,
            "code_insee": citycode,
            "departement": department,
            "region": region,
            "source": "Geoplateforme/BAN",
            "statut_adresse": "commune_seule" if index == "municipality" else "adresse_trouvee",
        }

    def _not_found_address(self):
        return {
            "source": "Aucune adresse trouvee",
            "statut_adresse": "adresse_non_trouvee",
            "commentaire": "Aucune adresse trouvee pour ce point",
        }

    def _empty_stats(self):
        return {
            "processed": 0,
            "updated": 0,
            "address_found": 0,
            "municipality_only": 0,
            "not_found": 0,
            "without_geometry": 0,
            "canceled": False,
        }

    def _count_address_status(self, stats, address):
        status = address.get("statut_adresse")
        if status == "adresse_trouvee":
            stats["address_found"] += 1
        elif status == "commune_seule":
            stats["municipality_only"] += 1
        elif status == "adresse_non_trouvee":
            stats["not_found"] += 1

    def _first_value(self, properties, *names):
        for name in names:
            value = properties.get(name)
            if value not in (None, ""):
                return value
        return None

    def _department_region_from_context(self, context):
        if not context:
            return None, None
        parts = [part.strip() for part in str(context).split(",") if part.strip()]
        department = parts[1] if len(parts) >= 2 and parts[0].isdigit() else None
        region = parts[-1] if len(parts) >= 2 else None
        return department, region

    def _address_fields(self):
        return [
            QgsField("id", QVariant.Int),
            QgsField("numero", QVariant.String, len=20),
            QgsField("voie", QVariant.String, len=120),
            QgsField("adresse", QVariant.String, len=180),
            QgsField("statut_adresse", QVariant.String, len=40),
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
        field_prefix = field_name[:10].lower()
        for index, field in enumerate(fields):
            if field.name().lower().startswith(field_prefix):
                return index
        return -1

    def _progress_dialog(self, feature_count, reverse_geocode):
        if feature_count < 50 and not reverse_geocode:
            return None
        progress = QProgressDialog(
            self.tr("Mise a jour des adresses..."),
            self.tr("Annuler"),
            0,
            feature_count,
            self.iface.mainWindow(),
        )
        progress.setWindowTitle(self.tr("Traitement en cours"))
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        return progress

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
                    f"Adresses non trouvees : {stats['not_found']}",
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
                    "statut_adresse",
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
                "Infos internes",
                ("id", "source", "commentaire", "date_creation"),
            )
            layer.setEditFormConfig(config)
        except Exception:
            return

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
