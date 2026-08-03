"""Cœur métier : lecture de l'export Empire Immo et sélection des promotions.

Ce module ne connaît ni Discord ni la base de données : il ne manipule que
des `Decimal` et des dataclasses. Tout est testable sans I/O.

Mécanique du jeu :
    - `promotion` est un pourcentage de remise (17 = -17 %), 0 = pas de promo.
    - `valeur` est le prix DÉJÀ remisé, c'est-à-dire ce que l'on paie.
    - donc prix_origine = valeur / (1 - promotion/100).
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

CENT = Decimal(100)

#: Colonnes numériques attendues dans l'export.
_CHAMPS_NUMERIQUES = (
    "valeur", "loyer", "charge", "impot", "promotion",
    "construction", "embellissement", "reparation",
)


@dataclass(frozen=True)
class Meta:
    """En-tête commentée de l'export (`# clé: valeur`)."""

    monde: str = ""
    description: str = ""
    taux_promoteur: str = ""
    mise_a_jour: str = ""


@dataclass(frozen=True)
class Building:
    type: str
    nom: str
    niveau: int
    valeur: Decimal           # prix payé (déjà remisé)
    loyer: Decimal
    charge: Decimal
    impot: Decimal
    promotion: Decimal        # % de remise
    construction: Decimal
    embellissement: Decimal
    reparation: Decimal


#: Nombre de promotions que l'on cherche à présenter. En dessous, on complète
#: avec les promos les plus proches de la fourchette (voir `find_promos`).
CIBLE_MINIMUM = 2


@dataclass(frozen=True)
class Promo:
    """Un bâtiment en promotion, avec les montants dérivés."""

    building: Building
    prix: Decimal             # = building.valeur
    prix_origine: Decimal     # avant remise
    economie: Decimal         # prix_origine - prix
    remise: Decimal           # % de remise
    loyer_net: Decimal        # loyer - charge - impôt
    rang: int = 0
    total: int = 0
    #: Faux quand la promo a été repêchée hors de la fourchette demandée.
    dans_fourchette: bool = True
    #: Écart au plus proche bord de la fourchette (0 si dedans).
    ecart: Decimal = Decimal(0)


def _nombre(brut: str | None) -> Decimal:
    """Lit un nombre de l'export : entier géant ou notation scientifique.

    Un champ vide vaut zéro. On passe par `Decimal` et jamais par `float` :
    l'export contient des entiers de 21 chiffres qu'un float tronquerait.
    """
    if brut is None:
        return Decimal(0)
    texte = brut.strip()
    if not texte:
        return Decimal(0)
    try:
        return Decimal(texte)
    except InvalidOperation:
        return Decimal(0)


def parse_csv(texte: str) -> tuple[Meta, list[Building]]:
    """Lit l'export complet : en-tête commentée + lignes de bâtiments."""
    entete: dict[str, str] = {}
    lignes_donnees: list[str] = []

    for ligne in texte.splitlines():
        depouille = ligne.strip()
        if not depouille:
            continue
        if depouille.startswith("#"):
            commentaire = depouille.lstrip("#").strip()
            if ":" in commentaire:
                cle, _, valeur = commentaire.partition(":")
                entete[cle.strip()] = valeur.strip()
            continue
        lignes_donnees.append(ligne)

    meta = Meta(
        monde=entete.get("nom", ""),
        description=entete.get("description", ""),
        taux_promoteur=entete.get("taux_promoteur", ""),
        mise_a_jour=entete.get("mise_a_jour", ""),
    )

    batiments: list[Building] = []
    for rangee in csv.DictReader(io.StringIO("\n".join(lignes_donnees))):
        if not rangee.get("nom"):
            continue
        nombres = {champ: _nombre(rangee.get(champ)) for champ in _CHAMPS_NUMERIQUES}
        batiments.append(
            Building(
                type=(rangee.get("type") or "").strip(),
                nom=rangee["nom"].strip(),
                niveau=int(_nombre(rangee.get("niveau"))),
                **nombres,
            )
        )

    return meta, batiments


def to_promo(batiment: Building) -> Promo:
    """Calcule les montants dérivés d'un bâtiment en promotion."""
    prix = batiment.valeur
    remise = batiment.promotion
    facteur = (CENT - remise) / CENT
    # Une remise de 100 % serait une division par zéro ; le jeu ne la produit
    # pas, mais mieux vaut retomber sur le prix payé que planter la publication.
    prix_origine = prix / facteur if facteur > 0 else prix
    return Promo(
        building=batiment,
        prix=prix,
        prix_origine=prix_origine,
        economie=prix_origine - prix,
        remise=remise,
        loyer_net=batiment.loyer - batiment.charge - batiment.impot,
    )


def _ecart_a_la_fourchette(
    valeur: Decimal, prix_min: Decimal, prix_max: Decimal
) -> Decimal:
    """Distance au bord le plus proche ; 0 si la valeur est dans la fourchette.

    Reste une distance en Ø : c'est le `{ecart}` du template Discohook, donc un
    montant. Le **classement** du repêchage, lui, passe par `_facteur_ecart`.
    """
    if valeur < prix_min:
        return prix_min - valeur
    if valeur > prix_max:
        return valeur - prix_max
    return Decimal(0)


def _facteur_ecart(
    valeur: Decimal, prix_min: Decimal, prix_max: Decimal
) -> Decimal:
    """Combien de fois le prix rate le bord le plus proche ; 1 s'il est dedans.

    Une distance en Ø n'a pas le même sens en bas et en haut de l'échelle des
    prix du jeu, qui couvre plus de vingt ordres de grandeur. Sur 100 TØ → 6 PØ,
    un bâtiment à 1 Ø est « à 100 TØ » du bord bas, soit la même distance qu'un
    bâtiment à 6,1 PØ du bord haut — alors qu'il est cent mille milliards de fois
    trop petit, quand l'autre ne dépasse le budget que de 1,7 %. Classer sur la
    différence faisait donc repêcher l'inutilisable et écarter l'intéressant.

    Le facteur corrige ça : ×100 000 000 000 000 contre ×1,017.
    """
    if valeur < prix_min:
        # `valeur <= 0` : le jeu n'affiche pas de bâtiment gratuit, mais un export
        # corrompu en produirait un, et une division par zéro couperait la
        # publication du matin. L'infini le relègue en dernier sans lever.
        if valeur <= 0:
            return Decimal("Infinity")
        return prix_min / valeur
    if valeur > prix_max:
        # `prix_max <= 0` est impossible ici : valeur > prix_max et valeur > 0.
        return valeur / prix_max
    return Decimal(1)


def find_promos(
    batiments: list[Building],
    prix_min: Decimal,
    prix_max: Decimal,
    minimum: int = CIBLE_MINIMUM,
) -> list[Promo]:
    """Promotions dont le prix payé tombe dans [prix_min, prix_max].

    Triées du plus cher au moins cher, bornes incluses.

    Si la fourchette en contient moins que `minimum`, on complète avec les
    promotions les plus proches d'un de ses bords — mieux vaut proposer un
    bâtiment un peu hors budget que de poster une liste quasi vide. Ces
    repêchées portent `dans_fourchette=False` et leur `ecart`.

    `minimum=0` désactive le repêchage (filtre strict).
    """
    en_promo = [b for b in batiments if b.promotion > 0]

    dedans = [b for b in en_promo if prix_min <= b.valeur <= prix_max]
    dedans.sort(key=lambda b: b.valeur, reverse=True)

    dehors: list[Building] = []
    if len(dedans) < minimum:
        candidats = [b for b in en_promo if not (prix_min <= b.valeur <= prix_max)]
        # Le plus proche **en proportion** d'abord ; à facteur égal, le plus cher.
        candidats.sort(
            key=lambda b: (_facteur_ecart(b.valeur, prix_min, prix_max), -b.valeur)
        )
        dehors = candidats[: minimum - len(dedans)]

    retenus = dedans + dehors
    total = len(retenus)
    # Identité plutôt qu'égalité : deux lignes du CSV peuvent avoir les mêmes
    # valeurs sans être le même bâtiment.
    ids_dedans = {id(b) for b in dedans}

    return [
        Promo(
            **{
                **to_promo(b).__dict__,
                "rang": index,
                "total": total,
                "dans_fourchette": id(b) in ids_dedans,
                "ecart": _ecart_a_la_fourchette(b.valeur, prix_min, prix_max),
            }
        )
        for index, b in enumerate(retenus, start=1)
    ]
