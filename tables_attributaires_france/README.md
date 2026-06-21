# Tables Attributaires France

Plugin QGIS experimental pour ajouter rapidement des champs attributaires preconfigures a une couche existante.

## Premiere table disponible

La version initiale complete une couche vectorielle du projet avec les champs `Adresses France` suivants :

- `id`
- `numero`
- `voie`
- `adresse`
- `complement`
- `code_postal`
- `ville`
- `commune`
- `code_insee`
- `departement`
- `region`
- `pays`
- `latitude`
- `longitude`
- `source`
- `commentaire`
- `date_creation`

Sur les couches de points, le plugin peut aussi remplir automatiquement `pays`, `date_creation`, `latitude` et `longitude` pour les entites deja presentes.

Si l'option de recherche d'adresse est activee, il interroge le service de geocodage inverse de la Geoplateforme pour completer `adresse`, `code_postal`, `ville`, `commune`, `code_insee`, `departement` et `region` a partir des coordonnees des points.

## Options de traitement

La fenetre du plugin est organisee en trois onglets : `Traitement`, `Champs` et `Export`.

Elle permet de :

- afficher une simulation avant lancement avec le nombre d'entites, les appels adresse estimes, le temps estime et les champs a ajouter ;
- traiter uniquement les entites selectionnees ;
- choisir les champs a creer et remplir ;
- ne remplir que les cellules vides, ou ecraser les valeurs existantes ;
- organiser le formulaire de saisie QGIS en groupes `Adresse`, `Localisation` et `Infos internes` ;
- exporter la couche enrichie en GeoPackage, Shapefile, GeoJSON ou CSV.

Pour les points sans adresse precise, le plugin ajoute le champ `statut_adresse` :

- `adresse_trouvee` : une adresse a ete retrouvee ;
- `commune_seule` : aucune adresse precise, mais une commune a ete retrouvee ;
- `adresse_non_trouvee` : aucun resultat exploitable.

## Installation locale

Copier ce dossier dans le repertoire des plugins QGIS, puis activer le plugin dans le gestionnaire d'extensions.

Chemin Windows typique :

```text
%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\tables_attributaires_france
```

## Prochaines evolutions possibles

- Ajouter d'autres modeles de tables.
- Ajouter des listes de valeurs pour les regions et departements.
- Generer automatiquement des formulaires de saisie QGIS.
- Ajouter l'import/export de modeles de champs.
