import time

from qgis.core import QgsTask

from .geocoding import count_address_status, empty_stats, not_found_address, result_text


class AddressGeocodingTask(QgsTask):
    def __init__(self, description, geocoder, jobs, selected_fields, empty_only, cache, date_text, on_finished):
        super().__init__(description, QgsTask.CanCancel)
        self.geocoder = geocoder
        self.jobs = jobs
        self.selected_fields = selected_fields
        self.empty_only = empty_only
        self.cache = cache
        self.date_text = date_text
        self.on_finished = on_finished
        self.updates = {}
        self.stats = empty_stats()
        self.error_message = ""

    def run(self):
        try:
            self.stats["processed"] = len(self.jobs)
            self.stats["search_mode"] = "address"
            self.stats["date"] = self.date_text
            total = len(self.jobs)

            for index, job in enumerate(self.jobs, start=1):
                if self.isCanceled():
                    self.stats["canceled"] = True
                    break

                attrs = dict(job.get("attrs") or {})
                address = self.geocoder.reverse_geocode_point(job["longitude"], job["latitude"], self.cache)
                if not address:
                    address = not_found_address()

                self._apply_address(job, attrs, address)
                count_address_status(self.stats, address)

                if attrs:
                    self.updates[job["fid"]] = attrs

                self.setProgress((index / total) * 100 if total else 100)
                time.sleep(0.02)
            self.stats["updated"] = len(self.updates)
            return True
        except Exception as error:
            self.error_message = str(error)
            return False

    def finished(self, result):
        self.on_finished(result, self.updates, self.stats, self.cache, self.error_message)

    def _apply_address(self, job, attrs, address):
        field_indexes = job["field_indexes"]
        values = job["values"]
        self._set_attr(
            values,
            attrs,
            field_indexes.get("resultat_adresse", -1),
            result_text(address),
            "resultat_adresse" in self.selected_fields,
        )
        for field_name, value in address.items():
            if field_name == "type_lieu" and value == "":
                self._clear_attr_if_replaceable(
                    values,
                    attrs,
                    field_indexes.get(field_name, -1),
                    field_name in self.selected_fields,
                )
                continue
            self._set_attr(
                values,
                attrs,
                field_indexes.get(field_name, -1),
                value,
                field_name in self.selected_fields,
            )
        if address.get("statut_adresse") in ("adresse_trouvee", "commune_seule", "zone_rurale"):
            self._clear_attr_if_replaceable(
                values,
                attrs,
                field_indexes.get("commentaire", -1),
                "commentaire" in self.selected_fields,
            )

    def _set_attr(self, values, attrs, field_index, value, enabled=True):
        if not enabled or field_index < 0 or value in (None, ""):
            return
        current_value = values.get(field_index)
        if not self.empty_only or self._is_replaceable_value(current_value):
            attrs[field_index] = value

    def _clear_attr_if_replaceable(self, values, attrs, field_index, enabled=True):
        if not enabled or field_index < 0:
            return
        if self._is_replaceable_value(values.get(field_index)):
            attrs[field_index] = ""

    def _is_replaceable_value(self, value):
        if value in (None, ""):
            return True
        normalized = str(value).strip().lower()
        return (
            normalized.startswith(("erreur_api", "timeout_api", "reponse_api"))
            or normalized in ("erreur api", "aucune adresse trouvee", "inconnu")
            or normalized.startswith("erreur pendant")
        )
