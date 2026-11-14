"""Chiffres cités par les agents et absents du dossier.

Constaté (séance 54b71524) : un modèle a calculé un « ROI de 646 % » (8,6 / 1,03 donne 835 %), un autre a inventé
un test de charge à 30 k€ et une capacité de 1 000 utilisateurs simultanés ; le contradicteur a ensuite jugé
résolue une objection sur la foi de ces chiffres. Ce module repère, dans n'importe quel dossier, les montants,
pourcentages et quantités qu'une contribution avance sans qu'ils figurent dans le dossier. Un chiffre repéré
n'est pas forcément faux (un calcul juste n'est pas écrit dans le dossier) : il est à vérifier, pas à croire.
"""
import json
import re
from functools import lru_cache

from .dossier import REF

_ESPACES = "   "
_NOMBRE = rf"\d{{1,3}}(?:[{_ESPACES}]\d{{3}})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?"
_UNITE = r"Md€|M€|k€|K€|€|%|milliards?|millions?"
CHIFFRE = re.compile(
    rf"(?<![\w§.,/-])(?P<a>{_NOMBRE})(?:\s*[–-]\s*(?P<b>{_NOMBRE}))?(?:\s*(?P<u>{_UNITE}))?(?![\w§])")
ECHELLE = {"md€": 1e9, "milliard": 1e9, "milliards": 1e9, "m€": 1e6, "million": 1e6, "millions": 1e6,
           "k€": 1e3}
MAX_PAR_CONTRIBUTION = 10


def _valeur(nombre: str) -> float:
    return float(re.sub(f"[{_ESPACES}]", "", nombre).replace(",", "."))


def _nature(unite: str) -> str:
    return "%" if unite == "%" else ("n" if not unite else "€")


def _demi_unite(nombre: str, echelle: float) -> float:
    """Moitié de la dernière unité écrite : « 3,4 M€ » est précis à 0,05 M€ près, « 5 M€ » à 0,5 M€ près."""
    decimales = len(re.split(r"[.,]", nombre)[1]) if re.search(r"[.,]\d+$", nombre) else 0
    return 0.5 * 10 ** -decimales * echelle


def _lus(texte: str, avec_precision: bool = False):
    """(texte du chiffre, valeurs, nature, position) pour chaque chiffre significatif du texte."""
    texte = REF.sub(lambda m: " " * len(m.group(0)), texte)          # D1-§2 n'est pas un chiffre (positions gardées)
    for m in CHIFFRE.finditer(texte):
        unite = (m.group("u") or "").lower()
        nombres = [m.group("a")] + ([m.group("b")] if m.group("b") else [])
        valeurs = [_valeur(n) * ECHELLE.get(unite, 1) for n in nombres]
        nature = _nature(unite)
        if nature == "n" and all(v < 10 or (1900 <= v <= 2100 and v.is_integer()) for v in valeurs):
            continue                                                 # petits entiers, années
        if avec_precision:
            yield m.group(0).strip(), valeurs, nature, m.start(), [_demi_unite(n, ECHELLE.get(unite, 1)) for n in nombres]
        else:
            yield m.group(0).strip(), valeurs, nature, m.start()


@lru_cache(maxsize=8)
def valeurs_du_dossier(texte_dossier: str) -> frozenset:
    return frozenset((round(v, 6), nature) for _, valeurs, nature, _ in _lus(texte_dossier) for v in valeurs)


def _connu(valeur: float, nature: str, connues: frozenset) -> bool:
    if nature == "n":                                                # « 864 000 » ou « 2 400 » sans unité
        return any(abs(valeur - v) <= 1e-6 * max(1, abs(v)) for v, _ in connues)
    return any(n == nature and abs(valeur - v) <= 1e-6 * max(1, abs(v)) for v, n in connues)


def _textes(valeur) -> list[str]:
    if isinstance(valeur, str):
        return [valeur]
    if isinstance(valeur, dict):
        return [t for k, v in valeur.items() if k != "_meta" for t in _textes(v)]
    if isinstance(valeur, list):
        return [t for v in valeur for t in _textes(v)]
    return []


HYPOTHESE = "hypothèse de calcul"


def _resultats(operandes: list) -> list:
    """Résultats des opérations simples entre chiffres d'un même texte : (valeur, nature, chiffres utilisés).

    Constaté (séance be1d5427) : « avec 40 % d'adoption, la valeur tombe à 3,4 M€ » (40 % × 8,6 M€) était signalé
    comme absent du dossier. Seules les opérations qu'un membre écrit en une phrase sont reconnues : part d'un
    montant, somme, écart, rapport et coût unitaire (annuel ou mensuel)."""
    resultats = []
    for i, (a, na, ba) in enumerate(operandes):
        for j, (b, nb, bb) in enumerate(operandes):
            if i == j or b == 0:
                continue
            avec = {ba, bb}
            if na in ("€", "n") and nb == "%":
                resultats.append((a * b / 100, na, avec))
            if na == nb == "%":
                resultats.append((a * b / 100, "%", avec))
            if na == nb == "€":
                resultats += [(a + b, "€", avec), (a - b, "€", avec), (a / b * 100, "%", avec)]
            if na == "€" and nb == "n":
                resultats += [(a / b, "€", avec), (a / b / 12, "€", avec)]
            if na == nb == "n":
                resultats.append((a / b * 100, "%", avec))
    return resultats


def _proche(valeur: float, cible: float, demi: float) -> bool:
    """Égalité à la précision écrite près, bornée à 5 % pour qu'un chiffre arrondi ne passe pas pour n'importe quoi."""
    return abs(valeur - cible) <= min(max(demi, 0.01 * abs(cible)), 0.05 * abs(cible))


def chiffres_hors_dossier(contribution, texte_dossier: str, maxi: int = MAX_PAR_CONTRIBUTION) -> list[str]:
    """Chiffres de la contribution absents du dossier, chacun avec un court extrait de son contexte.

    Un chiffre calculé à partir d'autres chiffres du même texte n'est pas signalé ; les chiffres hors dossier
    qui servent à ce calcul (« 40 % d'adoption ») sont signalés comme hypothèses de calcul."""
    connues, vus, trouves = valeurs_du_dossier(texte_dossier), set(), []
    for texte in _textes(contribution):
        texte = texte.replace("**", "")
        lus = list(_lus(texte, avec_precision=True))
        # Données d'un calcul : chiffres du dossier, ou pourcentages posés en hypothèse. Un montant hors dossier
        # n'en est pas une, sinon « 3,4 M€ » justifierait « 40 % » (3,4 / 8,6) et réciproquement.
        operandes = [(v, nature, brut) for brut, valeurs, nature, _, _ in lus for v in valeurs
                     if nature == "%" or all(_connu(x, nature, connues) for x in valeurs)]
        resultats = _resultats(operandes)
        hypotheses = set()
        inconnus = []
        for brut, valeurs, nature, debut, demis in lus:
            if all(_connu(v, nature, connues) for v in valeurs):
                continue
            utilises = [next((avec for r, nr, avec in resultats
                              if nr == nature and brut not in avec and _proche(v, r, d)), None)
                        for v, d in zip(valeurs, demis)]
            if all(utilises):
                hypotheses.update(*utilises)                     # calcul reconnu : ses données sont des hypothèses
                continue
            inconnus.append((brut, debut))
        for brut, debut in inconnus:
            if brut in vus:
                continue
            vus.add(brut)
            extrait = " ".join(texte[max(0, debut - 70):debut + len(brut) + 25].split())
            etiquette = f" ({HYPOTHESE})" if brut in hypotheses else ""
            trouves.append(f"{brut}{etiquette} — « …{extrait}… »")
            if len(trouves) >= maxi:
                return trouves
    return trouves


def releve(contributions: dict, texte_dossier: str) -> dict:
    """{auteur: [chiffres hors dossier]} pour un ensemble de contributions, auteurs sans chiffre omis."""
    return {auteur: c for auteur, contenu in contributions.items()
            if (c := chiffres_hors_dossier(contenu, texte_dossier))}


if __name__ == "__main__":                                           # diagnostic rapide sur un résultat
    import sys
    from pathlib import Path
    from .dossier import charger_dossier
    etat = json.load(open(sys.argv[2], encoding="utf-8"))["etat"]
    texte = charger_dossier(Path(sys.argv[1])).texte
    for cle in ("avis", "reponses"):
        for auteur, chiffres in releve(etat.get(cle, {}), texte).items():
            print(cle, auteur, *chiffres, sep="\n  ")
