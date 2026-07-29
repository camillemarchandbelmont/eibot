"""Construit et envoie les messages Discord.

Seul module à parler à l'API Discord. Il applique le template à chaque
promotion (un embed par bâtiment) et découpe en plusieurs messages pour
respecter les limites de l'API.
"""

from __future__ import annotations

import copy
import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # `discord` n'est importé qu'à l'envoi : le reste du module
    import discord  # (assemblage, découpage) est ainsi testable sans la lib.

from src.money import format_money
from src.promos import Meta, Promo
from src.template import (
    MAX_CARACTERES_PAR_MESSAGE,
    MAX_EMBEDS_PAR_MESSAGE,
    champs_promo,
    rendre,
)

log = logging.getLogger(__name__)

#: Limites de l'API Discord par embed.
_LIMITES = {
    "title": 256,
    "description": 4096,
}
_LIMITE_NOM_CHAMP = 256
_LIMITE_VALEUR_CHAMP = 1024
_LIMITE_FOOTER = 2048
_MAX_CHAMPS = 25


def _tronquer(texte: str, limite: int) -> str:
    if len(texte) <= limite:
        return texte
    return texte[: limite - 1] + "…"


def _assainir(embed_json: dict[str, Any]) -> dict[str, Any]:
    """Rogne ce qui dépasse les limites Discord plutôt que subir un rejet 400."""
    propre = copy.deepcopy(embed_json)

    for cle, limite in _LIMITES.items():
        if isinstance(propre.get(cle), str):
            propre[cle] = _tronquer(propre[cle], limite)

    champs = propre.get("fields")
    if isinstance(champs, list):
        rognes = []
        for champ in champs[:_MAX_CHAMPS]:
            if not isinstance(champ, dict):
                continue
            nom = _tronquer(str(champ.get("name", "​")) or "​", _LIMITE_NOM_CHAMP)
            valeur = _tronquer(
                str(champ.get("value", "​")) or "​", _LIMITE_VALEUR_CHAMP
            )
            rognes.append({"name": nom, "value": valeur, "inline": bool(champ.get("inline"))})
        propre["fields"] = rognes

    pied = propre.get("footer")
    if isinstance(pied, dict) and isinstance(pied.get("text"), str):
        pied["text"] = _tronquer(pied["text"], _LIMITE_FOOTER)

    # Discord refuse les clés qu'il ne connaît pas dans un embed.
    autorisees = {
        "title", "description", "url", "color", "timestamp",
        "footer", "image", "thumbnail", "author", "fields",
    }
    return {cle: valeur for cle, valeur in propre.items() if cle in autorisees}


def _taille(embed_json: dict[str, Any]) -> int:
    """Longueur cumulée du texte d'un embed, au sens du quota Discord."""
    total = len(str(embed_json.get("title", ""))) + len(str(embed_json.get("description", "")))
    for champ in embed_json.get("fields", []) or []:
        total += len(str(champ.get("name", ""))) + len(str(champ.get("value", "")))
    pied = embed_json.get("footer") or {}
    total += len(str(pied.get("text", "")))
    auteur = embed_json.get("author") or {}
    total += len(str(auteur.get("name", "")))
    return total


def construire_embeds(
    promos: list[Promo], meta: Meta, modele: dict, date: str
) -> tuple[list[dict], str]:
    """Applique le template à chaque promo.

    Renvoie (liste d'embeds JSON, contenu texte du premier message).
    Le `content` du template n'est utilisé qu'une fois, sur le premier
    message : le répéter à chaque bâtiment serait du bruit.
    """
    embeds: list[dict] = []
    contenu = ""

    for promo in promos:
        rendu = rendre(modele, champs_promo(promo, meta, date))
        if not contenu and isinstance(rendu.get("content"), str):
            contenu = rendu["content"]
        for embed_json in rendu.get("embeds") or []:
            if isinstance(embed_json, dict):
                embeds.append(_assainir(embed_json))

    return embeds, contenu


def grouper_messages(embeds: list[dict]) -> list[list[dict]]:
    """Découpe en paquets respectant les 10 embeds / 6000 caractères."""
    paquets: list[list[dict]] = []
    courant: list[dict] = []
    taille_courante = 0

    for embed_json in embeds:
        taille = _taille(embed_json)
        depasse = (
            len(courant) >= MAX_EMBEDS_PAR_MESSAGE
            or (courant and taille_courante + taille > MAX_CARACTERES_PAR_MESSAGE)
        )
        if depasse:
            paquets.append(courant)
            courant, taille_courante = [], 0
        courant.append(embed_json)
        taille_courante += taille

    if courant:
        paquets.append(courant)
    return paquets


def message_aucune_promo(prix_min: Decimal, prix_max: Decimal, meta: Meta) -> str:
    """Confirme que le bot a bien tourné, même sans résultat."""
    return (
        f"Aucune promotion entre **{format_money(prix_min)}** et "
        f"**{format_money(prix_max)}** aujourd'hui.\n"
        f"-# {meta.monde} • mise à jour {meta.mise_a_jour}"
    )


async def envoyer(
    destination: "discord.abc.Messageable",
    embeds: list[dict],
    contenu: str = "",
    role_id: str | None = None,
    ephemere: bool = False,
) -> int:
    """Envoie les embeds en autant de messages que nécessaire.

    `destination` est un salon ou un `interaction.followup`. La mention de
    rôle et le `content` ne figurent que sur le premier message. Renvoie le
    nombre de messages envoyés.
    """
    import discord

    entete_parts = []
    if role_id:
        entete_parts.append(f"<@&{role_id}>")
    if contenu:
        entete_parts.append(contenu)
    entete = " ".join(entete_parts)

    paquets = grouper_messages(embeds)
    envoyes = 0

    for index, paquet in enumerate(paquets):
        options: dict[str, Any] = {
            "embeds": [discord.Embed.from_dict(e) for e in paquet],
            "allowed_mentions": discord.AllowedMentions(roles=bool(role_id)),
        }
        if index == 0 and entete:
            options["content"] = entete
        # Seuls les webhooks d'interaction acceptent `ephemeral`.
        if ephemere and isinstance(destination, discord.Webhook):
            options["ephemeral"] = True

        await destination.send(**options)
        envoyes += 1

    return envoyes
