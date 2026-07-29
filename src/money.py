"""Échelle monétaire d'Empire Immo (notation Ø).

Le jeu utilise ses propres symboles, qui ne suivent PAS les préfixes SI :
`G` vaut un milliard, `E` vaut 10^18, `D` vaut 10^45. D'où cette table
explicite plutôt qu'une bibliothèque de formatage générique.

Ce module ne dépend de rien d'autre que la stdlib.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, getcontext

# Les montants du jeu atteignent 21 chiffres et plus : la précision par défaut
# de Decimal (28) ne suffit pas pour les divisions du calcul de remise.
getcontext().prec = 60

DEVISE = "Ø"

#: (exposant décimal, symbole), du plus grand au plus petit.
ECHELLE: list[tuple[int, str]] = [
    (45, "D"),  # septilliard
    (42, "N"),  # septillion
    (39, "X"),  # sextilliard
    (36, "S"),  # sextillion
    (33, "U"),  # quintilliard
    (30, "Q"),  # quintillion
    (27, "R"),  # quadrilliard
    (24, "Y"),  # quadrillion
    (21, "Z"),  # trilliard
    (18, "E"),  # trillion
    (15, "P"),  # billiard
    (12, "T"),  # billion
    (9, "G"),   # milliard
    (6, "M"),   # million
    (3, "K"),   # mille
]

NOMS = {
    "K": "mille", "M": "million", "G": "milliard", "T": "billion",
    "P": "billiard", "E": "trillion", "Z": "trilliard", "Y": "quadrillion",
    "R": "quadrilliard", "Q": "quintillion", "U": "quintilliard",
    "S": "sextillion", "X": "sextilliard", "N": "septillion",
    "D": "septilliard",
}

#: symbole -> multiplicateur
_MULTIPLICATEURS = {symbole: Decimal(10) ** exposant for exposant, symbole in ECHELLE}

#: Plus grand palier de la table ; au-delà, on replie sur la notation
#: scientifique plutôt que d'afficher un montant faux.
_EXPOSANT_MAX = ECHELLE[0][0]

_ESPACES = "   \t"
_NBSP = " "


class MoneyError(ValueError):
    """Saisie monétaire inintelligible."""


def _grouper(chiffres: str) -> str:
    """1234567 -> '1 234 567' (espaces insécables)."""
    morceaux = []
    while len(chiffres) > 3:
        morceaux.append(chiffres[-3:])
        chiffres = chiffres[:-3]
    morceaux.append(chiffres)
    return _NBSP.join(reversed(morceaux))


def format_money(montant: Decimal) -> str:
    """Notation courte du jeu : 2710572934559948 -> '2,71 PØ'.

    Arrondi au plus proche sur 2 décimales, virgule comme séparateur
    décimal, espace insécable avant le symbole.
    """
    signe = "-" if montant < 0 else ""
    valeur = abs(Decimal(montant))

    if valeur < 1000:
        # Pas de symbole d'échelle sous le millier.
        entier = valeur.quantize(Decimal(1), rounding=ROUND_HALF_UP)
        return f"{signe}{_grouper(str(entier))}{_NBSP}{DEVISE}"

    for rang, (exposant, symbole) in enumerate(ECHELLE):
        if valeur < Decimal(10) ** exposant:
            continue

        mantisse = (valeur / Decimal(10) ** exposant).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        # L'arrondi peut pousser la mantisse au palier supérieur :
        # 999,996 GØ doit s'afficher 1,00 TØ, pas 1000,00 GØ.
        if mantisse >= 1000:
            if rang == 0:
                # Plus rien au-dessus dans la table : notation scientifique,
                # honnête plutôt qu'un symbole inventé.
                return f"{signe}{valeur:.4E}{_NBSP}{DEVISE}"
            exposant, symbole = ECHELLE[rang - 1]
            mantisse = (valeur / Decimal(10) ** exposant).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

        texte = f"{mantisse:.2f}".replace(".", ",")
        return f"{signe}{texte}{_NBSP}{symbole}{DEVISE}"

    # Inatteignable : valeur >= 1000 trouve toujours au moins le palier K.
    return f"{signe}{valeur:.4E}{_NBSP}{DEVISE}"  # pragma: no cover


def format_money_long(montant: Decimal) -> str:
    """Tous les chiffres, séparés par milliers : '2 710 572 934 559 948 Ø'."""
    entier = Decimal(montant).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    signe = "-" if entier < 0 else ""
    return f"{signe}{_grouper(str(abs(entier)))}{_NBSP}{DEVISE}"


def format_money_brut(montant: Decimal) -> str:
    """Chiffres seuls, sans séparateur ni devise : '2710572934559948'."""
    entier = Decimal(montant).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return str(entier)


def _symboles_valides() -> str:
    return ", ".join(f"{s} ({NOMS[s]})" for _, s in reversed(ECHELLE))


def parse_money(texte: str) -> Decimal:
    """Lit un montant saisi dans Discord.

    Tolérant par conception : `50 6P`, `12,25M`, `1.5 gø`, `840`, ou un
    montant recopié depuis un message du bot. Les espaces sont traités
    comme des séparateurs de milliers, d'où `50 6P` == `506 PØ`.
    """
    if texte is None:
        raise MoneyError("Montant vide.")

    brut = str(texte).strip()
    if not brut:
        raise MoneyError("Montant vide.")

    # Normalisation : espaces exotiques -> espace simple, virgule -> point.
    normalise = brut
    for espace in _ESPACES:
        normalise = normalise.replace(espace, " ")
    normalise = normalise.replace(",", ".").upper()

    signe = Decimal(1)
    if normalise.startswith("-"):
        signe = Decimal(-1)
        normalise = normalise[1:].strip()
    elif normalise.startswith("+"):
        normalise = normalise[1:].strip()

    # Retire la devise finale. 'O' et '0' sont acceptés comme des 'Ø' mal
    # tapés, mais uniquement juste après un symbole d'échelle (ex. '1,5 g0') :
    # sinon '10' deviendrait '1' et '840 O' serait ambigu.
    normalise = re.sub(r"Ø+$", "", normalise).strip()
    symboles = "".join(s for _, s in ECHELLE)
    normalise = re.sub(rf"(?<=[{symboles}])[O0]$", "", normalise).strip()

    # Symbole d'échelle : dernière lettre, éventuellement séparée du nombre.
    multiplicateur = Decimal(1)
    correspondance = re.search(r"([A-Z])\s*$", normalise)
    if correspondance:
        symbole = correspondance.group(1)
        if symbole not in _MULTIPLICATEURS:
            raise MoneyError(
                f"Symbole monétaire inconnu : « {symbole} ». "
                f"Symboles valides : {_symboles_valides()}."
            )
        multiplicateur = _MULTIPLICATEURS[symbole]
        normalise = normalise[: correspondance.start()].strip()

    # Ce qui reste doit être un nombre, espaces de milliers retirés.
    chiffres = normalise.replace(" ", "")
    if not chiffres:
        raise MoneyError(
            f"Aucun nombre dans « {brut} ». "
            f"Exemples valides : 840, 12,25M, 100T, 50 6P."
        )
    if not re.fullmatch(r"\d+(\.\d+)?", chiffres):
        raise MoneyError(
            f"Montant illisible : « {brut} ». "
            f"Exemples valides : 840, 12,25M, 100T, 50 6P."
        )

    try:
        nombre = Decimal(chiffres)
    except InvalidOperation as exc:  # pragma: no cover - garde-fou
        raise MoneyError(f"Montant illisible : « {brut} ».") from exc

    return signe * nombre * multiplicateur
