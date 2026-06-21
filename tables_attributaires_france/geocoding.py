import json
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def normalize_text(value):
    normalized = unicodedata.normalize("NFKD", str(value))
    return "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    ).lower()


def first_value(properties, *names):
    for name in names:
        value = properties.get(name)
        if value not in (None, ""):
            return value
    return None


def department_region_from_context(context):
    if not context:
        return None, None
    parts = [part.strip() for part in str(context).split(",") if part.strip()]
    department = parts[1] if len(parts) >= 2 and parts[0].isdigit() else None
    region = parts[-1] if len(parts) >= 2 else None
    return department, region


def detect_place_type(properties):
    text = normalize_text(
        " ".join(
            str(value)
            for value in properties.values()
            if value not in (None, "")
        )
    )
    patterns = (
        ("lieu-dit", ("lieu-dit", "lieudit", "lieu dit")),
        ("ferme", ("ferme",)),
        ("zone_artisanale", ("zone artisanale", "za ", "z.a.")),
        ("chemin_communal", ("chemin communal",)),
        ("foret", ("foret", "bois")),
        ("parcelle", ("parcelle",)),
        ("station", ("station",)),
        ("chateau_eau", ("chateau d'eau", "chateau eau")),
    )
    for place_type, needles in patterns:
        if any(needle in text for needle in needles):
            return place_type
    return ""


def not_found_address():
    return {
        "source": "Aucune adresse trouvee",
        "statut_adresse": "adresse_non_trouvee",
        "type_lieu": "",
        "commentaire": "Aucune adresse trouvee pour ce point",
    }


def api_error_address(status):
    return {
        "source": "Erreur API",
        "statut_adresse": status,
        "type_lieu": "",
        "commentaire": "Erreur pendant la recherche d'adresse",
    }


def empty_stats():
    return {
        "processed": 0,
        "updated": 0,
        "address_found": 0,
        "municipality_only": 0,
        "not_found": 0,
        "api_errors": 0,
        "rural_places": 0,
        "duplicates": 0,
        "manual_results": 0,
        "without_geometry": 0,
        "canceled": False,
        "search_mode": "",
        "date": "",
    }


def count_address_status(stats, address):
    if address.get("type_lieu") and address.get("type_lieu") != "inconnu":
        stats["rural_places"] += 1
    status = address.get("statut_adresse")
    if status == "adresse_trouvee":
        stats["address_found"] += 1
    elif status == "commune_seule":
        stats["municipality_only"] += 1
    elif status == "adresse_non_trouvee":
        stats["not_found"] += 1
    elif status and status.startswith(("erreur_api", "timeout_api", "reponse_api")):
        stats["api_errors"] += 1


def result_text(address):
    status_labels = {
        "adresse_trouvee": "Adresse trouvee",
        "commune_seule": "Commune seule",
        "zone_rurale": "Zone rurale",
        "adresse_non_trouvee": "Adresse non trouvee",
        "timeout_api": "Erreur API : timeout",
        "reponse_api_invalide": "Erreur API : reponse invalide",
        "erreur_api": "Erreur API",
    }
    status = address.get("statut_adresse", "")
    if status.startswith("erreur_api_"):
        label = f"Erreur API : {status.replace('erreur_api_', '')}"
    else:
        label = status_labels.get(status, status)

    place_type = address.get("type_lieu")
    if place_type:
        return f"{label} - {place_type}"
    return label


def cache_key(longitude, latitude):
    return f"address:{round(longitude, 6)}:{round(latitude, 6)}"


def is_api_error_result(result):
    status = result.get("statut_adresse", "") if result else ""
    return status.startswith(("erreur_api", "timeout_api", "reponse_api"))


def is_cacheable_result(result):
    if not result:
        return False
    return not is_api_error_result(result)


class AddressGeocoder:
    def reverse_geocode_point(self, longitude, latitude, cache=None):
        cache = cache if cache is not None else {}
        key = cache_key(longitude, latitude)
        cached_result = cache.get(key)
        if cached_result:
            return cached_result

        result = self._first_successful_geocode(
            lambda: self._reverse_geocode_geoplateforme(longitude, latitude),
            lambda: self._reverse_geocode_legacy_ban(longitude, latitude),
        )
        if is_cacheable_result(result):
            cache[key] = result
        return result

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
        except TimeoutError:
            return api_error_address("timeout_api")
        except HTTPError as error:
            return api_error_address(f"erreur_api_{error.code}")
        except (OSError, URLError):
            return api_error_address("erreur_api")
        except ValueError:
            return api_error_address("reponse_api_invalide")

        features = payload.get("features") or []
        if not features:
            return None
        properties = features[0].get("properties") or {}
        return self._address_from_properties(properties, index)

    def _first_successful_geocode(self, *searches):
        last_error = None
        had_no_result = False
        for search in searches:
            result = search()
            if result is None:
                had_no_result = True
                continue
            if is_api_error_result(result):
                last_error = result
                continue
            return result
        if had_no_result:
            return None
        return last_error

    def _address_from_properties(self, properties, index="address"):
        label = first_value(properties, "label", "fulltext", "full_text", "name")
        street = first_value(properties, "street", "street_name", "name")
        house_number = first_value(properties, "housenumber", "house_number")
        postcode = first_value(properties, "postcode", "postalcode", "postal_code")
        city = first_value(properties, "city", "municipality", "commune")
        citycode = first_value(properties, "citycode", "city_code", "insee", "code_insee")
        context = first_value(properties, "context")
        department, region = department_region_from_context(context)
        department = first_value(properties, "department", "departement") or department
        region = first_value(properties, "region") or region
        place_type = detect_place_type(properties)
        status = "commune_seule" if index == "municipality" else "adresse_trouvee"
        if place_type and index != "address":
            status = "zone_rurale"

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
            "statut_adresse": status,
            "type_lieu": place_type,
        }
