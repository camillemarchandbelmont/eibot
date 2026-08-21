"""Rendu d'un export Discohook avec des placeholders `{champ}`.

Le template décrit UN bâtiment. `publish.py` l'applique à chaque promotion
pour produire un embed par bâtiment.

Ce module ne connaît que du JSON et un dictionnaire de chaînes : aucune
dépendance à Discord ni à la base.
"""

from __future__ import annotations

import re
from typing import Any

from src.money import format_money, format_money_brut, format_money_long
from src.promos import Meta, Promo

#: Champs monétaires : chacun donne aussi `_long` et `_brut`.
CHAMPS_MONETAIRES = (
    "prix", "prix_origine", "economie", "loyer", "charge", "impot",
    "loyer_net", "construction", "embellissement", "reparation",
    "ecart",
)

#: Champs textuels et numériques simples.
#:
#: Une promo repêchée hors fourchette n'est pas marquée : elle apparaît comme
#: les autres. `{ecart}` reste disponible pour qui veut afficher la distance
#: à la fourchette.
CHAMPS_SIMPLES = (
    "nom", "type", "niveau", "remise", "rang", "total",
    "monde", "taux_promoteur", "mise_a_jour", "date",
)

#: Marqueurs de repêchage supprimés. Rendus vides plutôt qu'inconnus : un
#: template chargé avant leur retrait les afficherait sinon littéralement dans
#: le post, et `/reglages template charger` les signalerait comme des fautes de frappe.
#: Volontairement absents de `PLACEHOLDERS` : plus proposés à l'usage.
CHAMPS_OBSOLETES = ("hors_fourchette", "dans_fourchette")

#: Tous les placeholders reconnus, pour l'aide et la détection de fautes.
PLACEHOLDERS: tuple[str, ...] = CHAMPS_SIMPLES + tuple(
    nom + suffixe
    for nom in CHAMPS_MONETAIRES
    for suffixe in ("", "_long", "_brut")
)

_MOTIF = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

#: Limites de l'API Discord.
MAX_EMBEDS_PAR_MESSAGE = 10
MAX_CARACTERES_PAR_MESSAGE = 6000


class TemplateError(ValueError):
    """Template Discohook inutilisable."""


TEMPLATE_DEFAUT: dict[str, Any] = {
    "embeds": [
        {
            "title": "🏷️ {nom}",
            "description": "**{type}** · niveau {niveau}",
            "color": 3066993,
            # Une seule ligne de trois champs. `{embellissement}`,
            # `{reparation}` et `{loyer_net}` restent disponibles pour un
            # template personnalisé.
            "fields": [
                {"name": "Prix promo", "value": "**{prix}**", "inline": True},
                {"name": "Avant remise", "value": "~~{prix_origine}~~", "inline": True},
                {"name": "Économie", "value": "💰 {economie} (−{remise})", "inline": True},
            ],
            "footer": {"text": "{rang}/{total} • {monde} • MAJ {mise_a_jour}"},
        }
    ]
}


def champs_promo(promo: Promo, meta: Meta, date: str) -> dict[str, str]:
    """Construit le dictionnaire de substitution pour une promotion."""
    batiment = promo.building
    montants = {
        "prix": promo.prix,
        "prix_origine": promo.prix_origine,
        "economie": promo.economie,
        "loyer": batiment.loyer,
        "charge": batiment.charge,
        "impot": batiment.impot,
        "loyer_net": promo.loyer_net,
        "construction": batiment.construction,
        "embellissement": batiment.embellissement,
        "reparation": batiment.reparation,
        "ecart": promo.ecart,
    }

    champs: dict[str, str] = {
        "nom": batiment.nom,
        "type": batiment.type,
        "niveau": str(batiment.niveau),
        "remise": f"{promo.remise.normalize():f} %",
        "rang": str(promo.rang),
        "total": str(promo.total),
        "monde": meta.monde,
        "taux_promoteur": meta.taux_promoteur,
        "mise_a_jour": meta.mise_a_jour,
        "date": date,
    }

    for nom, valeur in montants.items():
        champs[nom] = format_money(valeur)
        champs[f"{nom}_long"] = format_money_long(valeur)
        champs[f"{nom}_brut"] = format_money_brut(valeur)

    for obsolete in CHAMPS_OBSOLETES:
        champs[obsolete] = ""

    return champs


def _remplacer(texte: str, champs: dict[str, str]) -> str:
    """Remplace les `{champ}` connus ; laisse les autres intacts."""
    return _MOTIF.sub(
        lambda m: champs.get(m.group(1), m.group(0)),
        texte,
    )


def rendre(modele: Any, champs: dict[str, str]) -> Any:
    """Parcourt le JSON et substitue dans toute chaîne rencontrée."""
    if isinstance(modele, str):
        return _remplacer(modele, champs)
    if isinstance(modele, dict):
        return {cle: rendre(valeur, champs) for cle, valeur in modele.items()}
    if isinstance(modele, list):
        return [rendre(element, champs) for element in modele]
    return modele


def _chaines(modele: Any):
    if isinstance(modele, str):
        yield modele
    elif isinstance(modele, dict):
        for valeur in modele.values():
            yield from _chaines(valeur)
    elif isinstance(modele, list):
        for element in modele:
            yield from _chaines(element)


def placeholders_inconnus(modele: Any) -> set[str]:
    """Placeholders présents dans le template mais non reconnus.

    Sert à te signaler une faute de frappe au moment de `/reglages template
    charger`, plutôt
    que de laisser un `{prixx}` traîner dans le post quotidien.
    """
    trouves: set[str] = set()
    for texte in _chaines(modele):
        trouves.update(_MOTIF.findall(texte))
    return trouves - set(PLACEHOLDERS) - set(CHAMPS_OBSOLETES)


def valider_template(modele: Any) -> None:
    """Vérifie qu'un export Discohook est utilisable. Lève `TemplateError`."""
    if not isinstance(modele, dict):
        raise TemplateError(
            "Le fichier doit contenir un objet JSON (l'export Discohook), "
            "pas une liste ou une valeur simple."
        )

    embeds = modele.get("embeds")
    contenu = modele.get("content")

    if embeds is None and not contenu:
        raise TemplateError(
            "Le template doit contenir au moins un embed ou un `content`. "
            "Copie l'export JSON complet depuis Discohook."
        )

    if embeds is not None:
        if not isinstance(embeds, list):
            raise TemplateError("`embeds` doit être une liste.")
        if len(embeds) > 1:
            raise TemplateError(
                "Le template ne doit contenir qu'**un seul** embed : il décrit "
                "un bâtiment, et le bot en génère un par promotion trouvée."
            )
        if embeds and not isinstance(embeds[0], dict):
            raise TemplateError("L'embed doit être un objet JSON.")
