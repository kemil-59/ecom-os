"""Reconnaissance géographique française documentée (départements, régions, préfectures, codes).

Règles (testées dans tests/test_intel_v2.py) :
1. Normalisation : casse, accents, tirets, apostrophes typographiques et espaces multiples ignorés
   (« Finistere » = « Finistère », « Cote D'or » = « Côte-d'Or », « Bouches Du Rhone » = « Bouches-du-Rhône »).
2. Un code de département n'est reconnu qu'entre parenthèses ou derrière un libellé de profil explicite
   (« (35) », « Dept : 35 », « Département 2A ») ; un nombre isolé dans le texte n'est jamais une adresse.
3. La résidence n'est jamais déduite de la langue française, d'un domaine .fr ou d'un nom de lieu cité
   dans le message : seul le champ de profil de l'en-tête ou une phrase explicite (« j'habite à … ») vaut
   (voir web_capture._resolve_geo).
"""
from __future__ import annotations

import re
import unicodedata

DEPARTMENTS = {
    "01": "Ain", "02": "Aisne", "03": "Allier", "04": "Alpes-de-Haute-Provence", "05": "Hautes-Alpes",
    "06": "Alpes-Maritimes", "07": "Ardèche", "08": "Ardennes", "09": "Ariège", "10": "Aube", "11": "Aude",
    "12": "Aveyron", "13": "Bouches-du-Rhône", "14": "Calvados", "15": "Cantal", "16": "Charente",
    "17": "Charente-Maritime", "18": "Cher", "19": "Corrèze", "2A": "Corse-du-Sud", "2B": "Haute-Corse",
    "21": "Côte-d'Or", "22": "Côtes-d'Armor", "23": "Creuse", "24": "Dordogne", "25": "Doubs", "26": "Drôme",
    "27": "Eure", "28": "Eure-et-Loir", "29": "Finistère", "30": "Gard", "31": "Haute-Garonne", "32": "Gers",
    "33": "Gironde", "34": "Hérault", "35": "Ille-et-Vilaine", "36": "Indre", "37": "Indre-et-Loire", "38": "Isère",
    "39": "Jura", "40": "Landes", "41": "Loir-et-Cher", "42": "Loire", "43": "Haute-Loire", "44": "Loire-Atlantique",
    "45": "Loiret", "46": "Lot", "47": "Lot-et-Garonne", "48": "Lozère", "49": "Maine-et-Loire", "50": "Manche",
    "51": "Marne", "52": "Haute-Marne", "53": "Mayenne", "54": "Meurthe-et-Moselle", "55": "Meuse", "56": "Morbihan",
    "57": "Moselle", "58": "Nièvre", "59": "Nord", "60": "Oise", "61": "Orne", "62": "Pas-de-Calais",
    "63": "Puy-de-Dôme", "64": "Pyrénées-Atlantiques", "65": "Hautes-Pyrénées", "66": "Pyrénées-Orientales",
    "67": "Bas-Rhin", "68": "Haut-Rhin", "69": "Rhône", "70": "Haute-Saône", "71": "Saône-et-Loire", "72": "Sarthe",
    "73": "Savoie", "74": "Haute-Savoie", "75": "Paris", "76": "Seine-Maritime", "77": "Seine-et-Marne",
    "78": "Yvelines", "79": "Deux-Sèvres", "80": "Somme", "81": "Tarn", "82": "Tarn-et-Garonne", "83": "Var",
    "84": "Vaucluse", "85": "Vendée", "86": "Vienne", "87": "Haute-Vienne", "88": "Vosges", "89": "Yonne",
    "90": "Territoire de Belfort", "91": "Essonne", "92": "Hauts-de-Seine", "93": "Seine-Saint-Denis",
    "94": "Val-de-Marne", "95": "Val-d'Oise", "971": "Guadeloupe", "972": "Martinique", "973": "Guyane",
    "974": "La Réunion", "976": "Mayotte",
}
REGIONS = ("France", "Île-de-France", "Bretagne", "Normandie", "Occitanie", "Nouvelle-Aquitaine",
           "Auvergne-Rhône-Alpes", "Grand Est", "Hauts-de-France", "Bourgogne-Franche-Comté", "Centre-Val de Loire",
           "Pays de la Loire", "Provence-Alpes-Côte d'Azur", "PACA", "Corse", "Alsace", "Lorraine", "Picardie",
           "Champagne-Ardenne", "Aquitaine", "Limousin", "Poitou-Charentes", "Languedoc-Roussillon",
           "Midi-Pyrénées", "Auvergne", "Rhône-Alpes", "Franche-Comté", "Bourgogne", "Haute-Normandie",
           "Basse-Normandie", "Nord-Pas-de-Calais", "Centre")
# Villes (préfectures et grandes villes) ; liste fermée, extensible par revue.
CITIES = ("Paris", "Lyon", "Marseille", "Toulouse", "Nice", "Nantes", "Strasbourg", "Montpellier", "Bordeaux",
          "Lille", "Rennes", "Reims", "Toulon", "Grenoble", "Dijon", "Angers", "Nîmes", "Brest", "Limoges", "Tours",
          "Amiens", "Metz", "Perpignan", "Besançon", "Orléans", "Rouen", "Caen", "Nancy", "Clermont-Ferrand",
          "Le Havre", "Le Mans", "Saint-Étienne", "Villeurbanne", "Aix-en-Provence", "Mulhouse", "Poitiers",
          "Pau", "La Rochelle", "Avignon", "Annecy", "Chambéry", "Valence", "Bayonne", "Quimper", "Vannes",
          "Lorient", "Saint-Brieuc", "Laval", "Cholet", "Niort", "Angoulême", "Périgueux", "Agen", "Tarbes",
          "Albi", "Rodez", "Cahors", "Montauban", "Carcassonne", "Béziers", "Ajaccio", "Bastia", "Troyes",
          "Charleville-Mézières", "Châlons-en-Champagne", "Colmar", "Épinal", "Bar-le-Duc", "Belfort", "Vesoul",
          "Lons-le-Saunier", "Mâcon", "Auxerre", "Nevers", "Bourges", "Châteauroux", "Blois", "Chartres",
          "Évreux", "Alençon", "Saint-Lô", "Cherbourg", "Beauvais", "Laon", "Arras", "Calais", "Dunkerque",
          "Boulogne-sur-Mer", "Versailles", "Créteil", "Nanterre", "Bobigny", "Évry", "Cergy", "Melun",
          "Gap", "Digne-les-Bains", "Privas", "Mende", "Le Puy-en-Velay", "Aurillac", "Moulins", "Guéret",
          "Tulle", "Mont-de-Marsan", "Auch", "Foix", "Saint-Denis", "Fort-de-France", "Pointe-à-Pitre", "Cayenne")


def norm_place(text: str) -> str:
    """Forme canonique : sans accents, minuscule, tirets/apostrophes → espaces, espaces compactés."""
    t = unicodedata.normalize("NFKD", str(text or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.replace("’", "'").replace("‘", "'").replace("-", " ").replace("'", " ").replace("_", " ")
    return " ".join(t.casefold().split())


PLACES = {norm_place(x) for x in (*DEPARTMENTS.values(), *REGIONS, *CITIES)}
CODES = {k.casefold(): v for k, v in DEPARTMENTS.items()}
_CODE_IN_PROFILE = re.compile(r"(?:\((\d{2,3}|2[ab])\)|\b(?:dept|departement|dpt)\s*:?\s*(\d{2,3}|2[ab])\b)")


def is_fr_place(claim: str) -> bool:
    c = norm_place(claim)
    return c in PLACES or c in CODES


def claim_in_profile(claim: str, profile_zone: str) -> bool:
    """Le lieu revendiqué figure-t-il dans le champ de profil de l'en-tête (nom ou code de département) ?"""
    c, zone = norm_place(claim), norm_place(profile_zone)
    if not c or not zone:
        return False
    if c in PLACES and re.search(r"(?<!\w)" + re.escape(c) + r"(?!\w)", zone):
        return True
    codes = {m.group(1) or m.group(2) for m in _CODE_IN_PROFILE.finditer(zone)}
    if c in CODES:
        return c in codes
    # Nom revendiqué = département dont le code apparaît entre parenthèses dans le profil.
    return any(norm_place(CODES[k]) == c for k in codes if k in CODES)


def stated_residence(claim: str, block: str) -> bool:
    """Phrase explicite de l'auteur (« j'habite à Lyon », « je vis en Bretagne »), jamais un mot isolé."""
    c, b = norm_place(claim), norm_place(block)
    if c not in PLACES:
        return False
    return bool(re.search(r"(?:j ?habite|je vis|j ?habitais|habitant|je suis)\s+(?:a|en|dans l[ae]?|dans le|au|aux|sur|pres de)\s*"
                          + re.escape(c) + r"(?!\w)", b))
