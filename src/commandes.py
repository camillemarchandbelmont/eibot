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
from src.modules import Publication
from src.money import ECHELLE, NOMS, format_money, parse_money
from src.schedule import doit_publier, maintenant_local
from src.source import SourceError
from src.tournee import (
    ajouter_un_salon,
    derniere_de,
    ecrire_l_heure,
    heure_de,
    marquer_le_jour,
    retirer_un_salon,
    salons_de,
)


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


# --- Le vocabulaire commun des publications ---------------------------------


class ReponsePrivee:
    """Le fil de réponse d'une commande, `ephemeral=True` forcé sur tout envoi.

    L'aperçu passe ceci à un module en guise de salon. Le module reçoit déjà
    `ephemere=True` et peut s'en servir ; s'il l'oublie, son contenu partirait en
    clair dans le salon où la commande a été tapée — un aperçu public vaut
    publication, soit l'inverse de ce qu'on demande. La garde est donc ici, du
    côté qui ne dépend pas de la bonne volonté du module.
    """

    def __init__(self, fil: Any):
        self._fil = fil

    async def send(self, contenu: Any = None, **options: Any) -> None:
        options["ephemeral"] = True
        # Le contenu n'est passé que s'il existe : `send(None, embeds=…)` n'est pas
        # la même requête que `send(embeds=…)` pour l'API de Discord.
        if contenu is None:
            await self._fil.send(**options)
            return
        await self._fil.send(contenu, **options)


def ajouter_les_commandes_de_publication(
    groupe: app_commands.Group,
    bot: Any,
    publication: Publication,
    salons: bool = True,
) -> None:
    """Greffe `heure`, `apercu`, `publier` et `salon` sur le groupe d'un module.

    Écrites une seule fois, pour toute publication présente ou à venir : le module
    qui en déclare une troisième hérite des mêmes mots sans avoir à inventer les
    siens, et sans réécrire ce qu'ils font. C'est l'autre moitié du contrat — la
    première étant `src.tournee`, qui porte la mécanique d'envoi.

    `salons=False` pour une publication dont les salons ne lui appartiennent pas :
    les promotions attachent les leurs à une fourchette, et un `/fourchette salon
    ajouter` générique cohabiterait avec le vrai sous le même nom en écrivant
    ailleurs.
    """
    titre = publication.titre

    @groupe.command(name="heure", description=f"Heure de publication ({titre})"[:100])
    @app_commands.describe(heure="Format HH:MM — laisse vide pour l'afficher")
    async def commande_heure(
        interaction: discord.Interaction, heure: str | None = None
    ) -> None:
        config = await bot.store.config()
        maintenant = maintenant_local(config["fuseau"])

        # Consulter sans changer : demander l'heure ne doit pas obliger à la
        # régler, sous peine de décaler le post pour avoir voulu le vérifier.
        if heure is None:
            actuelle = await heure_de(publication, bot.store)
            await interaction.response.send_message(
                f"🕒 {titre.capitalize()} : **{actuelle}** ({config['fuseau']}).\n"
                f"-# Il est {maintenant.strftime('%H:%M')}.",
                ephemeral=True,
            )
            return

        propre = heure_valide(heure)
        if propre is None:
            await interaction.response.send_message(
                "❌ Heure invalide. Format attendu : `HH:MM` (ex: `09:00`).",
                ephemeral=True,
            )
            return

        await ecrire_l_heure(publication, bot.store, propre)

        # Régler l'heure exprime l'intention de publier à la nouvelle heure : on
        # oublie la marque du jour, sinon un post déjà sorti bloquerait ce nouvel
        # horaire jusqu'à demain.
        await marquer_le_jour(publication, bot.store, None)

        message = (
            f"✅ {titre.capitalize()} : publication à **{propre}** "
            f"({config['fuseau']}).\n"
            f"-# Il est {maintenant.strftime('%H:%M')}."
        )
        if not doit_publier(maintenant, propre, None):
            attendu = "aujourd'hui" if maintenant.strftime("%H:%M") < propre else "demain"
            message += f" Prochain post {attendu}."
        else:
            message += " Publication imminente (dans la minute)."
        if salons and not await salons_de(publication, bot.store):
            message += "\n⚠️ Aucun salon configuré : rien ne sortira."

        await interaction.response.send_message(message, ephemeral=True)

    @groupe.command(
        name="apercu", description=f"Prévisualise sans publier ({titre})"[:100]
    )
    async def commande_apercu(interaction: discord.Interaction) -> None:
        """Le post tel qu'il sortira, sans consommer la journée.

        Un envoi par contenu, comme à la publication : montrer tout en un seul
        message serait plus court et mensonger — aucun salon ne recevrait ça.
        """
        # Différé : préparer peut coûter un appel à l'API du jeu, et Discord
        # n'accorde que trois secondes.
        await interaction.response.defer(ephemeral=True)
        prive = ReponsePrivee(interaction.followup)
        maintenant = maintenant_local((await bot.store.config())["fuseau"])

        try:
            tournee = await publication.preparer(bot, bot.store, maintenant)
        except SourceError as erreur:
            # Message déjà lisible et clé d'API masquée : le préfixer du nom de la
            # classe n'ajouterait que du bruit.
            await prive.send(f"❌ {erreur}")
            return
        except Exception as erreur:
            # Panne imprévue : seul le type est montré. Le texte d'une exception
            # inattendue peut porter une URL, donc la clé d'API.
            await prive.send(
                f"❌ Aperçu impossible ({type(erreur).__name__}). "
                "Le salon de logs en dit plus."
            )
            return

        # Ce qui est écarté d'abord, et nommé : un aperçu qui montrerait seulement
        # ce qui part laisserait croire que tout part.
        for etiquette, pourquoi in tournee.ecartes:
            await prive.send(
                f"⚠️ **{etiquette}** — {pourquoi} : ne sera pas publié."
            )

        if not tournee.envois:
            # La raison, et pas seulement « rien » : sinon il faudrait deviner
            # entre « aucun salon », « rien à dire » et une panne.
            await prive.send(f"❌ Rien ne serait publié : {tournee.raison}")
            return

        for envoi in tournee.envois:
            # L'étiquette en tête de chaque aperçu : deux contenus d'affilée
            # seraient sinon indistinguables.
            entete = f"**{envoi.etiquette}**"
            if not envoi.salons:
                entete += " ⚠️ aucun salon : ne sera pas publié"
            await prive.send(entete)
            await envoi.envoyer(prive, ephemere=True)

    @groupe.command(
        name="publier", description=f"Publie maintenant, sans attendre l'heure ({titre})"[:100]
    )
    async def commande_publier(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        compte_rendu = await bot.faire_publication(publication, forcer=True)

        # La marque relue plutôt que le compte rendu interprété : elle dit si la
        # journée a réellement été consommée, y compris quand tout a échoué.
        aujourdhui = maintenant_local(
            (await bot.store.config())["fuseau"]
        ).strftime("%Y-%m-%d")
        parti = await derniere_de(publication, bot.store) == aujourdhui

        message = f"📣 {compte_rendu}"
        if parti:
            message += (
                "\n-# Publier maintenant **remplace** le post de l'heure prévue : "
                "il ne repassera pas aujourd'hui."
            )
        await interaction.followup.send(message, ephemeral=True)

    if not salons:
        return

    salon_groupe = app_commands.Group(
        name="salon", description=f"Salons où publier ({titre})"[:100], parent=groupe
    )

    @salon_groupe.command(name="ajouter", description=f"Publier dans un salon ({titre})"[:100])
    async def commande_salon_ajouter(
        interaction: discord.Interaction, salon: discord.TextChannel
    ) -> None:
        # Vérifié à l'attachement : une permission manquante découverte à l'heure
        # du post est un post perdu.
        manquantes = permissions_manquantes(interaction, salon)
        if manquantes:
            await interaction.response.send_message(
                f"❌ Je n'ai pas la permission {manquantes} dans {salon.mention}.\n"
                f"-# Ajoute-la puis relance la commande.",
                ephemeral=True,
            )
            return

        if not await ajouter_un_salon(publication, bot.store, str(salon.id)):
            await interaction.response.send_message(
                f"ℹ️ {salon.mention} reçoit déjà {titre}.", ephemeral=True
            )
            return

        # Mémorisé pour le site, qui n'a pas accès à Discord et ne pourrait
        # afficher qu'un id nu.
        await bot.store.memoriser_salon(
            str(salon.id), salon.name, str(interaction.guild.id), interaction.guild.name
        )

        await interaction.response.send_message(
            f"✅ {titre.capitalize()} sera publié dans {salon.mention} à "
            f"**{await heure_de(publication, bot.store)}**.",
            ephemeral=True,
        )

    @salon_groupe.command(
        name="retirer", description=f"Ne plus publier dans un salon ({titre})"[:100]
    )
    async def commande_salon_retirer(
        interaction: discord.Interaction, salon: discord.TextChannel
    ) -> None:
        if not await retirer_un_salon(publication, bot.store, str(salon.id)):
            await interaction.response.send_message(
                f"❌ {titre.capitalize()} n'était pas publié dans {salon.mention}.",
                ephemeral=True,
            )
            return

        # Pas de `oublier_salons_orphelins` ici : sa notion de « salon encore
        # servi » ne connaît que les fourchettes, si bien qu'appelée depuis une
        # autre publication elle oublierait des salons toujours utilisés. Le
        # cache de noms grossit donc un peu ; il est cosmétique, et l'étape du
        # cloisonnement par serveur le reprendra.
        await interaction.response.send_message(
            f"✅ {titre.capitalize()} ne sera plus publié dans {salon.mention}.",
            ephemeral=True,
        )
