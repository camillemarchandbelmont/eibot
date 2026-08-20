"""Les promotions du jeu : un post par fourchette de prix, chaque jour.

Ce fichier ne contient plus de mécanique d'envoi — pas de compte à rebours, pas
de boucle sur les salons, pas de « déjà publié aujourd'hui ? ». Tout cela est
dans `src.tournee`, une fois pour toutes les publications. Ce qui reste ici est
ce qui n'appartient qu'aux promotions : charger l'export du jeu, découper les
promotions par fourchette, et mentionner le rôle.

L'heure et la trace de passage restent lues et écrites **là où elles vivaient
avant les modules**. Le tiroir générique des publications est le défaut, pas une
obligation : les y déménager demanderait une reprise de données, et un
déploiement où la reprise manquerait ferait republier tout un jour à 09:00.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

import discord
from discord import app_commands

from src.commandes import (
    AucuneFourchette,
    aide_montants,
    ajouter_les_commandes_de_publication,
    bornes_demandees,
    lister_fourchettes,
    permissions_manquantes,
    pour_ce_serveur,
)
from src.db import bornes_tolerees
from src.modules import Envoi, Module, Publication, Tournee
from src.money import MoneyError, format_money, parse_money
from src.publish import envoyer
from src.source import SourceError

log = logging.getLogger(__name__)


async def _preparer(bot: Any, magasin: Any, maintenant: Any) -> Tournee:
    """Un envoi par fourchette servie, prêt à partir.

    Peut lever : l'export du jeu est chargé ici, donc **avant** que quoi que ce
    soit soit envoyé ou marqué. Sinon la panne de 09:00 annulerait la publication
    de toute la journée.
    """
    fourchettes = await magasin.fourchettes()
    if not fourchettes:
        return Tournee(raison="aucune fourchette configurée (/fourchette ajouter)")

    servies = [f for f in fourchettes if f["salons"]]
    # Nommées et non comptées : l'aperçu doit dire *lesquelles* ne partiront pas,
    # sinon il faudrait le déduire de la liste des salons.
    ecartes = [(f["nom"], "aucun salon") for f in fourchettes if not f["salons"]]
    if not servies:
        # Le message parle de salon et non de fourchette : celles-ci existent.
        return Tournee(
            raison="aucun salon configuré (/fourchette salon ajouter)",
            ecartes=tuple(ecartes),
        )

    # L'export une seule fois pour toutes les fourchettes : recharger à chaque
    # tour multiplierait les appels à l'API du jeu pour des données identiques.
    try:
        donnees = await bot.charger()
    except SourceError as erreur:
        # Son message est déjà une phrase lisible, clé d'API masquée : la préfixer
        # du nom de la classe n'ajouterait que du bruit dans le salon de logs.
        await bot.journaliser_erreur(str(erreur))
        raise
    except Exception as erreur:
        # Panne non prévue (CSV corrompu) : elle doit rester visible dans Discord,
        # pas seulement dans les logs du serveur.
        await bot.journaliser_erreur(f"{type(erreur).__name__} : {erreur}")
        raise

    envois: list[Envoi] = []
    promos = 0

    for fourchette in servies:
        try:
            tolere_min, tolere_max = bornes_tolerees(fourchette)
            embeds, contenu, repli = await bot.construire_publication(
                Decimal(fourchette["prix_min"]),
                Decimal(fourchette["prix_max"]),
                donnees=donnees,
                tolere_min=tolere_min,
                tolere_max=tolere_max,
            )
        except Exception as erreur:
            # Rendu impossible pour *cette* fourchette (template appliqué à des
            # valeurs inattendues) : les autres doivent quand même partir, alors
            # qu'une panne de l'export les condamnait toutes.
            log.warning("Rendu impossible pour « %s » : %s", fourchette["nom"], erreur)
            await bot.journaliser_erreur(
                f"Fourchette « {fourchette['nom']} » : "
                f"{type(erreur).__name__} : {erreur}"
            )
            ecartes.append((fourchette["nom"], "rendu impossible"))
            continue

        promos += 0 if repli else len(embeds)
        envois.append(
            Envoi(
                etiquette=fourchette["nom"],
                salons=tuple(fourchette["salons"]),
                envoyer=_envoyeur(magasin, embeds, contenu, repli),
            )
        )

    if not envois:
        # Toutes les fourchettes ont échoué au rendu : rien à envoyer, et surtout
        # rien à marquer — le passage suivant réessaiera.
        return Tournee(
            raison=f"rendu impossible pour les {len(servies)} fourchette(s)",
            ecartes=tuple(ecartes),
        )

    return Tournee(
        envois=tuple(envois),
        compte=promos,
        resume=f"{len(envois)} fourchette{'s' if len(envois) > 1 else ''}",
        ecartes=tuple(ecartes),
    )


def _envoyeur(magasin: Any, embeds: list[dict], contenu: str, repli: str):
    """Ce qui part dans un salon donné, une fois le contenu rendu."""

    async def envoyer_dans(salon: Any, ephemere: bool = False) -> None:
        if repli:
            await salon.send(repli)
            return
        # Pas de mention dans un aperçu : celui qui prévisualise ne veut pas
        # réveiller le rôle, et la cible n'est alors pas un salon — son serveur
        # est inconnu, donc le rôle lu serait celui d'ailleurs.
        role_id = None
        if not ephemere:
            # Le rôle du serveur **du salon**, et non un rôle global : un rôle
            # n'existe que dans son serveur, et `<@&123>` envoyé ailleurs
            # s'affiche en `@deleted-role`.
            serveur = getattr(salon, "guild", None)
            role_id = await magasin.role_du_serveur(getattr(serveur, "id", None))
        await envoyer(salon, embeds, contenu, role_id, ephemere=ephemere)

    return envoyer_dans


async def _lire_heure(magasin: Any) -> str:
    return (await magasin.config())["heure"]


async def _ecrire_heure(magasin: Any, heure: str) -> None:
    await magasin.maj_config(heure=heure)


async def _lire_derniere(magasin: Any) -> str | None:
    return await magasin.derniere_publication()


async def _marquer(magasin: Any, date: str | None) -> None:
    await magasin.marquer_publie(date)


# Pas d'accès aux salons : ceux des promotions appartiennent à une **fourchette**,
# pas à la publication. Les commandes génériques `salon` ne sont donc pas greffées
# sur `/fourchette` — elles y cohabiteraient avec les vraies sous le même nom, en
# écrivant ailleurs.
PUBLICATION = Publication(
    cle="promos",
    titre="promotions",
    preparer=_preparer,
    lire_heure=_lire_heure,
    ecrire_heure=_ecrire_heure,
    lire_derniere=_lire_derniere,
    marquer=_marquer,
)

def enregistrer(bot: Any) -> None:
    """Greffe `/promos` et le groupe `/fourchette` sur l'arbre du bot.

    Les fourchettes sont les données de ce module : ce sont elles qui découpent la
    publication, et personne d'autre ne les lit. Les commandes qui y touchent
    vivent donc ici.

    `/promos` reste une commande à part et non un `/fourchette promos` : elle
    n'interroge aucune fourchette en particulier — elle couvre leur union, ou les
    bornes qu'on lui donne — et sa réponse est publique, alors que tout le groupe
    `/fourchette` est de la configuration.
    """
    # --- /promos ------------------------------------------------------------

    @bot.tree.command(
        name="promos",
        description="Meilleures promotions dans une fourchette de prix",
    )
    @app_commands.describe(
        min="Prix minimum (ex: 100T). Par défaut : la fourchette configurée.",
        max="Prix maximum (ex: 6P).",
    )
    async def promos(
        interaction: discord.Interaction,
        min: str | None = None,
        max: str | None = None,
    ) -> None:
        await interaction.response.defer()
        try:
            prix_min, prix_max = await bornes_demandees(
                pour_ce_serveur(bot, interaction), min, max
            )
        except MoneyError as erreur:
            await interaction.followup.send(
                f"❌ {erreur}\n{aide_montants()}", ephemeral=True
            )
            return
        except AucuneFourchette as erreur:
            await interaction.followup.send(str(erreur), ephemeral=True)
            return

        if prix_min > prix_max:
            prix_min, prix_max = prix_max, prix_min

        try:
            embeds, contenu, repli = await bot.construire_publication(prix_min, prix_max)
        except SourceError as erreur:
            await interaction.followup.send(f"❌ {erreur}", ephemeral=True)
            return

        if repli:
            await interaction.followup.send(repli)
            return
        await envoyer(interaction.followup, embeds, contenu)

    # --- /fourchette --------------------------------------------------------
    #
    # Remplace `/config prix` et `/config salon` : ceux-ci ne pouvaient plus rien
    # signifier sans dire *de quelle* fourchette il s'agit. Une commande qui agit
    # sur une cible implicite est exactement ce qui fait publier au mauvais
    # endroit.

    groupe = app_commands.Group(
        name="fourchette", description="Fourchettes de prix et leurs salons"
    )

    async def completer_nom(
        interaction: discord.Interaction, saisie: str
    ) -> list[app_commands.Choice[str]]:
        """Propose les fourchettes existantes.

        Sans ça le nom serait retapé à chaque commande, et une faute de frappe ne
        se verrait qu'au message d'erreur.

        Celles de ce serveur seulement : proposer celles d'un autre ferait choisir
        un nom que la commande refuse ensuite, et dirait au passage ce que l'autre
        entreprise surveille.
        """
        debut = saisie.strip().casefold()
        return [
            app_commands.Choice(name=f["nom"], value=f["nom"])
            for f in await pour_ce_serveur(bot, interaction).fourchettes()
            if debut in f["nom"].casefold()
        ][:25]  # limite Discord

    async def refuser_nom_inconnu(interaction: discord.Interaction, nom: str) -> None:
        """Refuse en listant les noms valides.

        Sans la liste, impossible de savoir si c'est une faute de frappe ou une
        fourchette jamais créée. Les fourchettes de ce serveur : celles d'un autre
        ne pourraient de toute façon pas être réglées d'ici.
        """
        connues = await pour_ce_serveur(bot, interaction).fourchettes()
        noms = [f["nom"] for f in connues]
        connues = ", ".join(f"`{n}`" for n in noms) if noms else "*aucune*"
        await interaction.response.send_message(
            f"❌ Aucune fourchette nommée « {nom} ». Fourchettes : {connues}.",
            ephemeral=True,
        )

    @groupe.command(name="ajouter", description="Crée une fourchette de prix")
    @app_commands.describe(
        nom="Nom court, ex: grosses-affaires",
        min="Prix minimum (ex: 100T)",
        max="Prix maximum (ex: 6P)",
    )
    async def fourchette_ajouter(
        interaction: discord.Interaction, nom: str, min: str, max: str
    ) -> None:
        try:
            prix_min, prix_max = parse_money(min), parse_money(max)
        except MoneyError as erreur:
            await interaction.response.send_message(
                f"❌ {erreur}\n{aide_montants()}", ephemeral=True
            )
            return

        magasin = pour_ce_serveur(bot, interaction)
        try:
            fourchette = await magasin.ajouter_fourchette(nom, prix_min, prix_max)
        except ValueError as erreur:
            await interaction.response.send_message(f"❌ {erreur}", ephemeral=True)
            return

        await interaction.response.send_message(
            f"✅ Fourchette **{fourchette['nom']}** : "
            f"**{format_money(Decimal(fourchette['prix_min']))}** → "
            f"**{format_money(Decimal(fourchette['prix_max']))}**.\n"
            f"-# Reste à lui donner un salon : "
            f"`/fourchette salon ajouter nom:{fourchette['nom']}`",
            ephemeral=True,
        )

    @groupe.command(name="supprimer", description="Supprime une fourchette")
    @app_commands.autocomplete(nom=completer_nom)
    async def fourchette_supprimer(interaction: discord.Interaction, nom: str) -> None:
        magasin = pour_ce_serveur(bot, interaction)
        if not await magasin.supprimer_fourchette(nom):
            await refuser_nom_inconnu(interaction, nom)
            return

        restantes = await magasin.fourchettes()
        message = f"✅ Fourchette **{nom.strip()}** supprimée."
        if not restantes:
            message += "\n⚠️ Plus aucune fourchette : le post quotidien ne sortira plus."
        await interaction.response.send_message(message, ephemeral=True)

    @groupe.command(name="prix", description="Modifie les bornes d'une fourchette")
    @app_commands.describe(min="Prix minimum (ex: 100T)", max="Prix maximum (ex: 6P)")
    @app_commands.autocomplete(nom=completer_nom)
    async def fourchette_prix(
        interaction: discord.Interaction, nom: str, min: str, max: str
    ) -> None:
        try:
            prix_min, prix_max = parse_money(min), parse_money(max)
        except MoneyError as erreur:
            await interaction.response.send_message(
                f"❌ {erreur}\n{aide_montants()}", ephemeral=True
            )
            return

        magasin = pour_ce_serveur(bot, interaction)
        avant = await magasin.fourchettes()
        index_avant = magasin._index(avant, nom)
        zone_avant = (
            bornes_tolerees(avant[index_avant]) if index_avant >= 0 else (None, None)
        )

        if not await magasin.majprix_fourchette(nom, prix_min, prix_max):
            await refuser_nom_inconnu(interaction, nom)
            return

        if prix_min > prix_max:
            prix_min, prix_max = prix_max, prix_min
        message = (
            f"✅ **{nom.strip()}** : **{format_money(prix_min)}** → "
            f"**{format_money(prix_max)}**"
        )

        # Les nouvelles bornes ont pu repousser la zone de tolérance. Le taire
        # laisserait croire qu'elle est restée là où on l'avait réglée.
        apres = await magasin.fourchettes()
        zone_apres = bornes_tolerees(apres[magasin._index(apres, nom)])
        if zone_apres != zone_avant and zone_apres[0] is not None:
            message += (
                f"\n-# Zone de tolérance élargie d'autant : "
                f"{format_money(zone_apres[0])} → {format_money(zone_apres[1])}"
            )

        await interaction.response.send_message(message, ephemeral=True)

    @groupe.command(
        name="tolerance",
        description="Zone acceptée quand la fourchette est trop pauvre (sans bornes : efface)",
    )
    @app_commands.describe(
        min="Prix minimum toléré (ex: 50T) ; laisser vide pour effacer la zone",
        max="Prix maximum toléré (ex: 8P) ; laisser vide pour effacer la zone",
    )
    @app_commands.autocomplete(nom=completer_nom)
    async def fourchette_tolerance(
        interaction: discord.Interaction,
        nom: str,
        min: str | None = None,
        max: str | None = None,
    ) -> None:
        """Règle ou efface la zone de tolérance d'une fourchette.

        Les deux bornes ou aucune : une seule ne décrit pas une plage, et
        `find_promos` ignorerait la zone à moitié réglée — la commande aurait
        alors confirmé un réglage sans effet.
        """
        magasin = pour_ce_serveur(bot, interaction)
        if (min is None) != (max is None):
            await interaction.response.send_message(
                "❌ Donne les **deux** bornes, ou aucune pour effacer la zone.\n"
                "-# `/fourchette tolerance nom:… min:50T max:8P`",
                ephemeral=True,
            )
            return

        if min is None:
            if not await magasin.effacer_tolerance_fourchette(nom):
                fourchettes = await magasin.fourchettes()
                if magasin._index(fourchettes, nom) < 0:
                    await refuser_nom_inconnu(interaction, nom)
                else:
                    await interaction.response.send_message(
                        f"ℹ️ **{nom.strip()}** n'avait pas de zone de tolérance.",
                        ephemeral=True,
                    )
                return

            await interaction.response.send_message(
                f"✅ Zone de tolérance de **{nom.strip()}** effacée.\n"
                "-# Le repêchage reprend au plus proche, dans les deux sens.",
                ephemeral=True,
            )
            return

        try:
            tolere_min, tolere_max = parse_money(min), parse_money(max)
        except MoneyError as erreur:
            await interaction.response.send_message(
                f"❌ {erreur}\n{aide_montants()}", ephemeral=True
            )
            return

        try:
            regle = await magasin.majtolerance_fourchette(nom, tolere_min, tolere_max)
        except ValueError as erreur:
            await interaction.response.send_message(f"❌ {erreur}", ephemeral=True)
            return

        if not regle:
            await refuser_nom_inconnu(interaction, nom)
            return

        if tolere_min > tolere_max:
            tolere_min, tolere_max = tolere_max, tolere_min
        await interaction.response.send_message(
            f"✅ **{nom.strip()}** tolère **{format_money(tolere_min)}** → "
            f"**{format_money(tolere_max)}**.\n"
            "-# Cherché là en priorité quand la fourchette n'a pas assez de promos.",
            ephemeral=True,
        )

    @groupe.command(name="liste", description="Liste les fourchettes et leurs salons")
    async def fourchette_liste(interaction: discord.Interaction) -> None:
        fourchettes = await pour_ce_serveur(bot, interaction).fourchettes()
        embed = discord.Embed(
            title="Fourchettes de prix",
            description=lister_fourchettes(bot, fourchettes),
            color=0x5865F2,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # Les salons appartiennent à une fourchette **nommée**, pas à la publication :
    # c'est pourquoi ce sous-groupe est écrit ici plutôt qu'hérité du vocabulaire
    # commun, et pourquoi chacune de ses commandes exige un `nom`.
    salon_groupe = app_commands.Group(
        name="salon",
        description="Salons d'une fourchette",
        parent=groupe,
    )

    @salon_groupe.command(
        name="ajouter", description="Publie cette fourchette dans un salon"
    )
    @app_commands.autocomplete(nom=completer_nom)
    async def fourchette_salon_ajouter(
        interaction: discord.Interaction, nom: str, salon: discord.TextChannel
    ) -> None:
        # Vérifié tout de suite : sinon l'erreur n'apparaîtrait qu'à l'heure du
        # post, le lendemain.
        manquantes = permissions_manquantes(interaction, salon)
        if manquantes:
            await interaction.response.send_message(
                f"❌ Je n'ai pas la permission {manquantes} dans {salon.mention}.\n"
                f"-# Ajoute-la puis relance la commande.",
                ephemeral=True,
            )
            return

        magasin = pour_ce_serveur(bot, interaction)
        if magasin._index(await magasin.fourchettes(), nom) < 0:
            await refuser_nom_inconnu(interaction, nom)
            return

        if not await magasin.ajouter_salon_fourchette(nom, str(salon.id)):
            await interaction.response.send_message(
                f"ℹ️ {salon.mention} reçoit déjà **{nom.strip()}**.", ephemeral=True
            )
            return

        # Mémorisé pour le site, qui n'a pas accès à Discord et ne pourrait
        # afficher qu'un id nu. Le cache reste commun — un nom de salon ne dépend
        # pas de qui le regarde —, et c'est la vue qui le sait.
        await magasin.memoriser_salon(
            str(salon.id), salon.name, str(interaction.guild.id), interaction.guild.name
        )

        await interaction.response.send_message(
            f"✅ **{nom.strip()}** sera publiée dans {salon.mention}.", ephemeral=True
        )

    @salon_groupe.command(
        name="retirer", description="Ne plus publier cette fourchette dans un salon"
    )
    @app_commands.autocomplete(nom=completer_nom)
    async def fourchette_salon_retirer(
        interaction: discord.Interaction, nom: str, salon: discord.TextChannel
    ) -> None:
        magasin = pour_ce_serveur(bot, interaction)
        if magasin._index(await magasin.fourchettes(), nom) < 0:
            await refuser_nom_inconnu(interaction, nom)
            return

        if not await magasin.retirer_salon_fourchette(nom, str(salon.id)):
            await interaction.response.send_message(
                f"❌ **{nom.strip()}** n'était pas publiée dans {salon.mention}.",
                ephemeral=True,
            )
            return

        # Le salon n'est peut-être plus servi par aucune fourchette : son nom n'a
        # alors plus à occuper la config.
        await magasin.oublier_salons_orphelins()

        await interaction.response.send_message(
            f"✅ **{nom.strip()}** ne sera plus publiée dans {salon.mention}.",
            ephemeral=True,
        )

    # `salons=False` : le générique `salon ajouter` cohabiterait avec le vrai sous
    # le même nom, en écrivant dans la liste de la publication au lieu de celle de
    # la fourchette — un « ✅ » pour un post qui ne partirait nulle part.
    ajouter_les_commandes_de_publication(groupe, bot, PUBLICATION, salons=False)
    bot.tree.add_command(groupe)


MODULE = Module(
    nom="promos",
    titre="Promotions",
    description="Les promotions du jeu, par fourchette de prix.",
    ordre=20,
    enregistrer=enregistrer,
    publications=(PUBLICATION,),
)
