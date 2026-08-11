"""Frais de gestion par filiale : cœur pur, sans Discord ni base.

Deux règles viennent du jeu et non de nous :

  - les frais valent 7 % des bénéfices (`money.frais_de_gestion`), sans
    décimales — le jeu ne facture pas de fraction d'Ø ;
  - une filiale qui ne gagne rien ne paie rien. Le jeu ne rembourse pas : des
    frais négatifs seraient un montant inventé. Zéro compte comme une perte,
    puisqu'il n'y a rien à prélever dessus.

Le **nom est la clé d'import du jeu** : il est conservé caractère pour
caractère, doubles espaces compris (`ARMEE  DE TERRE`). Seuls les espaces de
bordure sont retirés, parce qu'ils sont invisibles et viennent d'un copier-coller.
La comparaison, elle, ignore la casse : deux lignes que rien ne distingue à
l'œil seraient pires qu'un remplacement.

Les montants ne traversent jamais un `float` : ils atteignent dix-sept chiffres,
et JSONB n'a pas d'entier de cette taille — d'où le stockage en chaîne.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from src.money import frais_de_gestion


class FilialeError(ValueError):
    """Saisie de filiale inutilisable."""


@dataclass(frozen=True)
class Filiale:
    """Un relevé : ce qu'une filiale a rapporté, et ce qu'elle coûte."""

    nom: str          #: tel que saisi, espaces internes compris
    benefices: Decimal
    frais: Decimal    #: 7 % des bénéfices, ou 0 quand elle ne gagne rien
    date: str         #: 'AAAA-MM-JJ' de la saisie

    @property
    def en_perte(self) -> bool:
        """Vrai quand il n'y a rien à prélever (bénéfices nuls ou négatifs)."""
        return self.benefices <= 0


def _cle(nom: str) -> str:
    """Forme comparable d'un nom : casse ignorée, bordures retirées.

    Les espaces **internes** restent : le jeu les compte dans sa clé d'import.
    """
    return str(nom).strip().casefold()


def calculer(nom: str, benefices: Decimal, date: str) -> Filiale:
    """Relevé d'une filiale à partir de ses bénéfices.

    Lève `FilialeError` si le nom est vide : une ligne anonyme serait
    impossible à retrouver dans le tableau comme à remplacer par une ressaisie.
    """
    propre = str(nom).strip()
    if not propre:
        raise FilialeError("Le nom de la filiale ne peut pas être vide.")

    montant = Decimal(benefices)
    # `frais_de_gestion` est appelée et non recopiée : le taux affiché par
    # `/frais` et celui appliqué ici ne peuvent donc pas divergier.
    frais = frais_de_gestion(montant) if montant > 0 else Decimal(0)
    return Filiale(nom=propre, benefices=montant, frais=frais, date=str(date))


def index_de(filiales: list[Filiale], nom: str) -> int:
    """Position d'une filiale par son nom, -1 si absente."""
    cible = _cle(nom)
    for index, filiale in enumerate(filiales):
        if _cle(filiale.nom) == cible:
            return index
    return -1


def enregistrer(filiales: list[Filiale], filiale: Filiale) -> list[Filiale]:
    """Liste augmentée du relevé, en remplaçant celui de la même filiale.

    Le remplacement conserve la **place** de la filiale : la faire remonter en
    fin de liste ferait danser le tableau d'un jour à l'autre.

    Renvoie une nouvelle liste : la liste d'entrée vient de la base, et la muter
    en place ferait divergier ce qui est affiché de ce qui est enregistré si
    l'écriture échouait ensuite.
    """
    index = index_de(filiales, filiale.nom)
    if index < 0:
        return [*filiales, filiale]
    return [*filiales[:index], filiale, *filiales[index + 1 :]]


def retirer(filiales: list[Filiale], nom: str) -> list[Filiale]:
    """Liste privée d'une filiale. Inchangée si elle n'y était pas."""
    index = index_de(filiales, nom)
    if index < 0:
        return list(filiales)
    return [*filiales[:index], *filiales[index + 1 :]]


def total_frais(filiales: list[Filiale]) -> Decimal:
    """Somme des frais. `Decimal` de bout en bout : dix-sept chiffres."""
    return sum((filiale.frais for filiale in filiales), Decimal(0))


def vers_json(filiales: list[Filiale]) -> list[dict]:
    """Forme stockable en JSONB. Les montants sont des **chaînes**.

    Un nombre JSON passerait par un flottant et perdrait ses derniers chiffres.
    """
    return [
        {
            "nom": filiale.nom,
            "benefices": str(filiale.benefices),
            "frais": str(filiale.frais),
            "date": filiale.date,
        }
        for filiale in filiales
    ]


def depuis_json(brut: Any) -> list[Filiale]:
    """Relit ce que `vers_json` a écrit, en sautant les lignes illisibles.

    La config est du JSON retouchable à la main : une ligne cassée doit coûter
    sa filiale, pas le tableau du jour.

    Les frais sont **recalculés** depuis les bénéfices plutôt que relus : s'ils
    ne collaient plus (retouche à la main, taux modifié), le tableau annoncerait
    un montant que le jeu ne réclamera pas.
    """
    filiales: list[Filiale] = []
    for ligne in brut or []:
        if not isinstance(ligne, dict):
            continue
        try:
            filiales.append(
                calculer(
                    ligne.get("nom", ""),
                    Decimal(str(ligne.get("benefices", ""))),
                    str(ligne.get("date", "")),
                )
            )
        except (FilialeError, InvalidOperation, ArithmeticError):
            continue
    return filiales
