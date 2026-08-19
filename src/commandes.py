"""L'outillage commun des commandes : ce que plusieurs modules réutilisent.

Ces fonctions vivaient dans `bot.py`, au-dessus d'une unique fonction de 1400
lignes qui déclarait toutes les commandes. Les commandes appartiennent désormais
à leur module ; ce qui les sert toutes est ici, une fois.

Rien de tout ceci ne touche à Discord de lui-même : ce sont des mises en forme et
des validations, éprouvables sans connexion.
"""

from __future__ import annotations

# Les paramètres des commandes s'appellent `min` et `max` (ce que Discord
# affiche) : les fonctions natives ne sont donc joignables que par `builtins`.
import builtins
from decimal import Decimal
from typing import Any

import discord
from discord import app_commands

from src.acces import gere_la_liste
from src.db import bornes_tolerees
from src.money import ECHELLE, NOMS, format_money, parse_money


def administrateur(interaction: discord.Interaction) -> bool:
    """Vrai si l'auteur est administrateur du serveur.

    `ArbreProtege` laisse déjà passer les membres autorisés : ce contrôle
    supplémentaire ne sert qu'à la gestion de la liste d'accès elle-même, pour
    qu'un membre autorisé ne puisse ni s'ajouter des complices ni retirer celui
    qui l'a nommé.
    """
    permissions = getattr(interaction.user, "guild_permissions", None)
    return gere_la_liste(est_admin=bool(permissions and permissions.administrator))


def permissions_manquantes(
    interaction: discord.Interaction, salon: discord.TextChannel
) -> str:
    """Nomme la permission qui empêcherait le bot de publier, ou "".

    Vérifié à la configuration plutôt qu'à la publication : une permission
    manquante découverte à 09:00 le lendemain est un post perdu.
    """
    moi = getattr(getattr(interaction, "guild", None), "me", None)
    permissions = salon.permissions_for(moi)
    if not permissions.send_messages:
        return "**Envoyer des messages**"
    if not permissions.embed_links:
        return "**Intégrer des liens**"
    return ""


def lister_salons(bot: Any, salons: list[str]) -> str:
    """Liste à puces des salons, avec un ⚠️ sur ceux devenus inaccessibles."""
    if not salons:
        return "*Aucun salon configuré.* Le post quotidien ne sortira pas."

    lignes = []
    for salon_id in salons:
        introuvable = bot.get_channel(int(salon_id)) is None
        suffixe = " ⚠️ introuvable" if introuvable else ""
        lignes.append(f"• <#{salon_id}>{suffixe}")
    return "\n".join(lignes)


def lister_fourchettes(bot: Any, fourchettes: list[dict]) -> str:
    """Fourchettes avec bornes et salons, ⚠️ sur celles qui ne publieront rien.

    Une fourchette sans salon est silencieuse à la publication : ça doit se voir
    dans la liste, sans avoir à le déduire de l'absence de salon.
    """
    if not fourchettes:
        return (
            "*Aucune fourchette configurée.* Le post quotidien ne sortira pas.\n"
            "-# `/fourchette ajouter nom:… min:… max:…`"
        )

    lignes = []
    for fourchette in fourchettes:
        bornes = (
            f"{format_money(Decimal(fourchette['prix_min']))} → "
            f"{format_money(Decimal(fourchette['prix_max']))}"
        )
        lignes.append(f"**{fourchette['nom']}** — {bornes}")
        # Seul endroit où relire la zone : les posts ne la mentionnent pas.
        tolere_min, tolere_max = bornes_tolerees(fourchette)
        if tolere_min is not None and tolere_max is not None:
            lignes.append(
                f"-# tolérance : {format_money(tolere_min)} → {format_money(tolere_max)}"
            )
        if not fourchette["salons"]:
            lignes.append("⚠️ aucun salon : ne publiera rien")
            continue
        for salon_id in fourchette["salons"]:
            introuvable = bot.get_channel(int(salon_id)) is None
            suffixe = " ⚠️ introuvable" if introuvable else ""
            lignes.append(f"• <#{salon_id}>{suffixe}")
    return "\n".join(lignes)


class AucuneFourchette(Exception):
    """Rien n'est configuré : le message porte la commande qui y remédie."""


async def bornes_demandees(
    bot: Any, min: str | None, max: str | None
) -> tuple[Decimal, Decimal]:
    """Bornes d'une recherche `/promos`, remises dans l'ordre.

    Sans argument, couvre **l'union** de toutes les fourchettes : la commande
    sert à voir ce qui bouge dans tout ce qui est surveillé, pas à en interroger
    une en particulier — sinon il faudrait la nommer.
    """
    if min and max:
        plancher, plafond = parse_money(min), parse_money(max)
    else:
        fourchettes = await bot.store.fourchettes()
        if not fourchettes:
            raise AucuneFourchette(
                "❌ Aucune fourchette configurée : précise `min:` et `max:`, ou "
                "crée-en une avec `/fourchette ajouter`."
            )

        # Une seule borne fournie : l'autre vient de l'union. Refuser une saisie
        # dont l'intention est claire serait gratuit.
        plancher = (
            parse_money(min) if min
            else builtins.min(Decimal(f["prix_min"]) for f in fourchettes)
        )
        plafond = (
            parse_money(max) if max
            else builtins.max(Decimal(f["prix_max"]) for f in fourchettes)
        )

    # `min`/`max` sont ici les paramètres de la commande, pas les fonctions : le
    # tri passe donc par `sorted`.
    return tuple(sorted((plancher, plafond)))  # type: ignore[return-value]


def heure_valide(heure: str) -> str | None:
    """Normalise `HH:MM` en `%02d:%02d`, ou None si la saisie est inutilisable.

    La forme normalisée compte : `doit_publier` compare des chaînes, et « 9:00 »
    se rangerait après « 20:30 ».
    """
    try:
        heures, minutes = (int(part) for part in heure.split(":", 1))
    except ValueError:
        return None
    if not (0 <= heures <= 23 and 0 <= minutes <= 59):
        return None
    return f"{heures:02d}:{minutes:02d}"


def aide_montants() -> str:
    return (
        "Formats acceptés : `840`, `12,25M`, `100T`, `6P`, `50 6P`, `2,71 PØ`.\n"
        "Symboles : K M G T P E Z Y R Q U S X N D."
    )


def choix_symboles() -> list[app_commands.Choice]:
    """Les 15 paliers du jeu, plus l'unité, en menu déroulant.

    Une liste de choix plutôt qu'un champ libre : les symboles ne suivent pas
    les préfixes SI (`G` est un milliard, `E` vaut 10^18), donc personne ne les
    devine, et Discord plafonne à 25 choix — la table en compte 16.
    """
    choix = [
        app_commands.Choice(name=f"{symbole}Ø — {NOMS[symbole]}", value=symbole)
        for _, symbole in reversed(ECHELLE)
    ]
    # L'unité en tête : c'est le palier qu'on demande pour « voir tous les
    # chiffres », et il n'est pas dans la table.
    return [app_commands.Choice(name="Ø — unité", value="Ø"), *choix]
