# Tables Attributaires France

Plugin QGIS pour enrichir une couche de points avec des champs d'adresse France et lancer un geocodage inverse depuis les coordonnees.

## Fonctions principales

- choix de la couche vectorielle a traiter ;
- traitement de toute la couche ou seulement des entites selectionnees ;
- option pour ne traiter que les points sans resultat existant ;
- choix des champs a creer/remplir ;
- simulation avant lancement avec volume, appels API estimes, duree estimee et sorties prevues ;
- geocodage inverse via les services Geoplateforme/BAN ;
- cache local pour eviter de refaire les memes recherches ;
- traitement reseau lance en tache QGIS (`QgsTask`) pour limiter le blocage de l'interface ;
- resume de fin avec adresses trouvees, non trouvees, erreurs API et doublons ;
- export de la couche en GeoPackage, Shapefile, GeoJSON ou CSV ;
- export d'un rapport CSV de traitement.

## Champs conseilles

La selection conseillee cree une table lisible pour l'utilisateur :

- `id`
- `numero`
- `voie`
- `adresse`
- `resultat_adresse`
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

Les anciens champs techniques `statut_adresse` et `type_lieu` ne sont plus crees par defaut. Les informations utiles sont regroupees dans `resultat_adresse`, `source` et `commentaire`.

## Rapport CSV

Le rapport exportable contient notamment :

- date du traitement ;
- nombre total de points traites ;
- nombre de points mis a jour ;
- adresses trouvees ;
- communes seules ;
- adresses non trouvees ;
- erreurs API ;
- doublons ;
- resultats deja presents ;
- points sans geometrie exploitable.

## Installation locale

Copier le dossier `tables_attributaires_france` dans le repertoire des plugins QGIS, puis activer le plugin dans le gestionnaire d'extensions.

Chemin Windows typique :

```text
%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\tables_attributaires_france
```

## Notes

Le geocodage depend de services externes. En cas de coupure reseau, timeout ou limitation temporaire, le plugin renseigne `source` et `commentaire` avec l'erreur rencontree au lieu de bloquer toute la couche.
