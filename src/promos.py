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
from collections.abc import Iterable
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

#: Provenance d'une promo, par ordre de préférence décroissante.
ZONE_IDEALE = "ideale"      # dans [prix_min, prix_max]
ZONE_TOLEREE = "toleree"    # hors fourchette mais dans la zone de tolérance
ZONE_REPECHEE = "repechee"  # au-delà des deux, repêchée faute de mieux


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
    #: D'où vient la promo : `ideale` (dans la fourchette), `toleree` (dans la
    #: zone de tolérance) ou `repechee` (au-delà, faute de mieux).
    zone: str = ZONE_IDEALE


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


def normaliser_type(nom: str | None) -> str:
    """Un type comparable : sans espaces autour, sans casse.

    La liste des types écartés est du JSON retouchable à la main et vient parfois
    d'une saisie. Comparer les chaînes brutes ferait qu'un « Transport » ne
    filtrerait rien — une exclusion silencieuse, donc le pire des cas : le post
    sort inchangé et rien à l'écran ne dit pourquoi.
    """
    return str(nom or "").strip().casefold()


def types_disponibles(batiments: list[Building]) -> list[str]:
    """Les types que l'export contient, dédoublonnés et triés.

    **Tous les bâtiments, et pas les seuls en promotion.** Les promotions
    tournent d'un jour à l'autre, les types du monde non : réduite aux promos du
    moment, la liste ne proposerait `transport` que les jours où il s'en trouve
    un en promotion, c'est-à-dire pas le jour où l'on veut l'exclure.

    Triés parce que cette liste est ce que Discord propose sous le curseur : dans
    l'ordre du fichier, elle changerait de place à chaque export.
    """
    return sorted({b.type.strip() for b in batiments if b.type.strip()})


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
    """Distance au bord le plus proche ; 0 si la valeur est dans la fourchette."""
    if valeur < prix_min:
        return prix_min - valeur
    if valeur > prix_max:
        return valeur - prix_max
    return Decimal(0)


def find_promos(
    batiments: list[Building],
    prix_min: Decimal,
    prix_max: Decimal,
    minimum: int = CIBLE_MINIMUM,
    tolere_min: Decimal | None = None,
    tolere_max: Decimal | None = None,
    types_exclus: Iterable[str] = (),
) -> list[Promo]:
    """Promotions dont le prix payé tombe dans [prix_min, prix_max].

    Triées du plus cher au moins cher, bornes incluses.

    Trois passes, chacune ne se déclenchant que si la précédente n'a pas atteint
    `minimum` :

    1. **idéale** — dans [prix_min, prix_max] ;
    2. **tolérée** — dans la zone de tolérance quand elle est réglée, les plus
       proches de la fourchette idéale d'abord ;
    3. **repêchage** — le reste, par distance à la fourchette idéale.

    Mieux vaut proposer un bâtiment un peu hors budget que de poster une liste
    quasi vide. Les deux dernières passes portent `dans_fourchette=False`, leur
    `ecart` et leur `zone`.

    `minimum=0` désactive tolérance et repêchage (filtre strict).

    `types_exclus` écarte des types de bâtiments (`transport`, `zones`…) **avant
    les trois passes**. Posée sur la seule passe idéale, l'exclusion ne tiendrait
    que les jours où elle ne sert à rien : le repêchage ramènerait le type écarté
    le jour creux, celui où personne ne s'y attend. Un type écarté peut donc
    valoir un post plus court, ou pas de post du tout — c'est le sens d'une
    exclusion, par opposition à une préférence.
    """
    exclus = {t for t in (normaliser_type(nom) for nom in types_exclus) if t}
    en_promo = [
        b
        for b in batiments
        if b.promotion > 0 and normaliser_type(b.type) not in exclus
    ]
    ecart = lambda b: _ecart_a_la_fourchette(b.valeur, prix_min, prix_max)  # noqa: E731

    dedans = [b for b in en_promo if prix_min <= b.valeur <= prix_max]
    dedans.sort(key=lambda b: b.valeur, reverse=True)

    # Les deux passes suivantes puisent dans le même reste, chacune y prenant
    # ce que la précédente a laissé.
    reste = [b for b in en_promo if not (prix_min <= b.valeur <= prix_max)]

    toleres: list[Building] = []
    if len(dedans) < minimum and tolere_min is not None and tolere_max is not None:
        candidats = [b for b in reste if tolere_min <= b.valeur <= tolere_max]
        # Le plus proche de la fourchette idéale d'abord ; à égalité, le plus
        # cher. Même notion de distance que le repêchage : une seule règle.
        candidats.sort(key=lambda b: (ecart(b), -b.valeur))
        toleres = candidats[: minimum - len(dedans)]
        pris = {id(b) for b in toleres}
        reste = [b for b in reste if id(b) not in pris]

    repeches: list[Building] = []
    if len(dedans) + len(toleres) < minimum:
        reste.sort(key=lambda b: (ecart(b), -b.valeur))
        repeches = reste[: minimum - len(dedans) - len(toleres)]

    retenus = dedans + toleres + repeches
    total = len(retenus)
    # Identité plutôt qu'égalité : deux lignes du CSV peuvent avoir les mêmes
    # valeurs sans être le même bâtiment.
    zones = {id(b): ZONE_IDEALE for b in dedans}
    zones.update({id(b): ZONE_TOLEREE for b in toleres})
    zones.update({id(b): ZONE_REPECHEE for b in repeches})

    return [
        Promo(
            **{
                **to_promo(b).__dict__,
                "rang": index,
                "total": total,
                "dans_fourchette": zones[id(b)] == ZONE_IDEALE,
                "ecart": ecart(b),
                "zone": zones[id(b)],
            }
        )
        for index, b in enumerate(retenus, start=1)
    ]
