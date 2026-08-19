"""`/reglages` : tout ce qui règle le bot, à un seul endroit.

**Ce fichier n'est pas un module.** Les modules de `src/modules/` apportent des
fonctionnalités et peuvent être retirés ou éteints ; `/reglages` est le noyau,
toujours présent. Sans lui, un serveur dont on aurait éteint le dernier module ne
pourrait plus rien régler — ni rendre la main.

Le rangement suit une règle simple : **une commande qui configure le bot vit ici,
une commande qui s'en sert vit dans son module.** C'est ce qui distingue
`/reglages source tester` (est-ce que l'export arrive ?) de `/fourchette apercu`
(qu'est-ce qui partirait ce soir ?), alors que les deux touchent aux mêmes
données.

Trois sous-groupes — `acces`, `source`, `template` — parce que ce sont des sujets
entiers, et pas un de plus : Discord n'accepte que trois niveaux, si bien que
`/reglages template charger` est déjà au fond.
"""

from __future__ import annotations

import io
import json
from decimal import Decimal
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import discord
from discord import app_commands

from src.commandes import administrateur, lister_fourchettes, permissions_manquantes
from src.publish import envoyer
from src.schedule import maintenant_local
from src.source import ApiSource, SourceError, diagnostiquer
from src.template import (
    PLACEHOLDERS,
    TemplateError,
    placeholders_inconnus,
    valider_template,
)

if TYPE_CHECKING:  # pragma: no cover - uniquement pour les annotations
    from src.bot import EmpireBot


def enregistrer_les_reglages(bot: EmpireBot) -> None:
    """Greffe `/reglages` sur l'arbre du bot.

    Appelée directement par `EmpireBot`, et non par la découverte des modules :
    ces commandes doivent être là même si aucun module ne se charge.
    """
    groupe = app_commands.Group(name="reglages", description="Réglages du bot")

    # --- /reglages voir -----------------------------------------------------

    @groupe.command(name="voir", description="Affiche la configuration courante")
    async def reglages_voir(interaction: discord.Interaction):
        config = await bot.store.config()
        fourchettes = await bot.store.fourchettes()
        logs = await bot.store.salon_logs()
        roles = await bot.store.roles()
        # Une ligne par serveur : une valeur unique laisserait croire que tous
        # les serveurs pinguent, ou qu'aucun ne le fait.
        if roles:
            noms = await bot.store.serveurs()
            role = "\n".join(
                f"{noms.get(serveur, serveur)} : <@&{role_id}>"
                for serveur, role_id in roles.items()
            )
        elif config.get("role_id"):
            # Repli plat : role_id existe, mais roles est vide. Ce rôle est
            # mentionné sur tous les serveurs (config d'avant le multi-serveurs).
            role = (
                f"<@&{config['role_id']}>\n"
                "-# Réglage d'avant le multi-serveurs, appliqué à tous les serveurs. "
                "Utilise `/reglages mention` pour le rendre par serveur."
            )
        else:
            role = "*aucune*"
        stockage = "Postgres" if bot.store.persistant else "⚠️ mémoire (perdue au redémarrage)"

        embed = discord.Embed(title="Configuration", color=0x5865F2)
        # L'heure vue par le bot, pour rendre visible un décalage de fuseau.
        embed.add_field(
            name="Heure",
            value=f"{config['heure']} ({config['fuseau']})\n"
                  f"-# il est {maintenant_local(config['fuseau']).strftime('%H:%M')}",
        )
        embed.add_field(name="Mention", value=role)
        # Les salons sont listés *sous leur fourchette* : séparés, on ne saurait
        # plus quel salon reçoit quelles promotions.
        embed.add_field(
            name=f"Fourchettes ({len(fourchettes)})",
            value=lister_fourchettes(bot, fourchettes),
            inline=False,
        )
        embed.add_field(
            name="Journal",
            value=f"<#{logs}>" if logs else "*désactivé*",
        )
        embed.add_field(name="Stockage", value=stockage, inline=False)
        embed.add_field(name="Données", value=bot.decrire_source(), inline=False)
        embed.set_footer(text=f"Dernière publication : {await bot.store.derniere_publication() or 'jamais'}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- /reglages fuseau ---------------------------------------------------

    @groupe.command(
        name="fuseau", description="Fuseau horaire de toutes les publications"
    )
    @app_commands.describe(fuseau="Ex: Europe/Paris")
    async def reglages_fuseau(interaction: discord.Interaction, fuseau: str):
        """Le seul réglage d'horloge commun aux publications.

        L'heure de chacune se règle chez elle (`/fourchette heure`, `/filiales
        heure`). Le fuseau, lui, est partagé : le régler depuis l'une déplacerait
        l'autre, surprise qui ne se découvrirait que le lendemain. D'où sa propre
        commande, à l'endroit des réglages communs.
        """
        try:
            ZoneInfo(fuseau)
        except Exception:
            # Écrit tel quel, il ferait échouer chaque lecture de l'heure ensuite,
            # donc les publications. Un exemple, sinon rien ne dit à quoi
            # ressemble un nom accepté.
            await interaction.response.send_message(
                f"❌ Fuseau inconnu : `{fuseau}`. Ex : `Europe/Paris`.", ephemeral=True
            )
            return

        config = await bot.store.maj_config(fuseau=fuseau)

        # Les marques du jour ne sont pas effacées : corriger l'horloge n'est pas
        # demander un nouveau post, et il n'y aurait aucune raison de choisir
        # laquelle des deux publications repartirait.
        maintenant = maintenant_local(config["fuseau"])
        await interaction.response.send_message(
            f"✅ Fuseau : **{config['fuseau']}**.\n"
            f"-# Il est {maintenant.strftime('%H:%M')} — "
            f"promotions à {config['heure']}, "
            f"tableau des frais à {await bot.store.heure_filiales()}.",
            ephemeral=True,
        )

    # --- /reglages mention --------------------------------------------------

    @groupe.command(name="mention", description="Rôle mentionné à chaque post")
    async def reglages_mention(
        interaction: discord.Interaction, role: discord.Role | None = None
    ):
        if role is None:
            # Lire avant d'effacer pour savoir si on tape dans le repli plat
            roles = await bot.store.roles()
            config = await bot.store.config()
            etait_plat = not roles and config.get("role_id")

            if await bot.store.effacer_role(str(interaction.guild.id)):
                if etait_plat:
                    message = (
                        "✅ Mention désactivée **sur tous les serveurs** : les posts ne "
                        "pingueront plus personne.\n"
                        "-# Ce réglage datait d'avant le multi-serveurs. "
                        "Utilise `/reglages mention` pour régler par serveur."
                    )
                else:
                    message = (
                        "✅ Mention désactivée **sur ce serveur** : les posts n'y "
                        "pingueront plus personne."
                    )
            else:
                message = "ℹ️ Aucune mention n'était réglée sur ce serveur."
            await interaction.response.send_message(message, ephemeral=True)
            return

        await bot.store.definir_role(str(interaction.guild.id), str(role.id))
        await interaction.response.send_message(
            f"✅ {role.mention} sera mentionné à chaque post **sur ce serveur**.\n"
            "-# Les autres serveurs gardent leur propre réglage.",
            ephemeral=True,
        )

    # --- /reglages logs -----------------------------------------------------

    @groupe.command(
        name="logs", description="Salon où le bot raconte ce qu'il fait"
    )
    async def reglages_logs(
        interaction: discord.Interaction, salon: discord.TextChannel | None = None
    ):
        if salon is None:
            await bot.store.desactiver_logs()
            await interaction.response.send_message(
                "✅ Journal désactivé.", ephemeral=True
            )
            return

        manquantes = permissions_manquantes(interaction, salon)
        if manquantes:
            await interaction.response.send_message(
                f"❌ Je n'ai pas la permission {manquantes} dans {salon.mention}.",
                ephemeral=True,
            )
            return

        await bot.store.maj_config(logs_salon_id=str(salon.id))
        await interaction.response.send_message(
            f"✅ Journal dans {salon.mention} : publications et erreurs y seront "
            f"rapportées.",
            ephemeral=True,
        )

    # Rien qui ressemble à l'ancien `/config retester` : elle effaçait la marque
    # du jour des promotions seules, sous un nom qui ne nommait aucune
    # publication — illisible sur un bot qui en a deux et pourra en avoir plus.
    # Pour republier tout de suite, `/fourchette publier` et `/filiales publier`
    # le font sans détour, et préviennent que le post de l'heure prévue ne
    # repassera pas. Pour éprouver la source, `/reglages source tester`.

    # --- /reglages acces ----------------------------------------------------

    acces_groupe = app_commands.Group(
        name="acces",
        description="Qui peut utiliser les commandes du bot",
        parent=groupe,
    )

    #: Gérer la liste reste réservé aux administrateurs, alors que le reste des
    #: commandes est ouvert aux membres autorisés : sans ça, un membre autorisé
    #: pourrait s'ajouter des complices ou retirer celui qui l'a nommé.
    REFUS_ADMIN = "❌ Seul un administrateur peut modifier la liste d'accès."

    @acces_groupe.command(
        name="ajouter", description="Autorise un membre à utiliser les commandes"
    )
    async def acces_ajouter(interaction: discord.Interaction, membre: discord.Member):
        if not administrateur(interaction):
            await interaction.response.send_message(REFUS_ADMIN, ephemeral=True)
            return

        # Un bot ne tape pas de commandes : c'est forcément un mauvais clic dans
        # la liste Discord.
        if getattr(membre, "bot", False):
            await interaction.response.send_message(
                f"❌ {membre.mention} est un bot : il n'a pas de commandes à saisir.",
                ephemeral=True,
            )
            return

        # Dire qu'il est déjà admin, plutôt que de laisser croire qu'on vient de
        # lui donner un droit : le sien disparaîtra avec son rôle.
        permissions = getattr(membre, "guild_permissions", None)
        if permissions is not None and permissions.administrator:
            await interaction.response.send_message(
                f"ℹ️ {membre.mention} est **administrateur** : il a déjà accès à "
                f"tout.\n-# Ajoute-le quand même si tu comptes lui retirer son "
                f"rôle d'admin plus tard.",
                ephemeral=True,
            )
            return

        if not await bot.store.autoriser(str(membre.id)):
            await interaction.response.send_message(
                f"ℹ️ {membre.mention} est déjà autorisé.", ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"✅ {membre.mention} peut maintenant utiliser les commandes du bot.",
            ephemeral=True,
        )

    @acces_groupe.command(
        name="retirer", description="Retire l'accès aux commandes à un membre"
    )
    async def acces_retirer(interaction: discord.Interaction, membre: discord.Member):
        if not administrateur(interaction):
            await interaction.response.send_message(REFUS_ADMIN, ephemeral=True)
            return

        if not await bot.store.retirer_autorise(str(membre.id)):
            await interaction.response.send_message(
                f"ℹ️ {membre.mention} n'était pas dans la liste.", ephemeral=True
            )
            return

        message = f"✅ {membre.mention} n'a plus accès aux commandes."
        permissions = getattr(membre, "guild_permissions", None)
        if permissions is not None and permissions.administrator:
            # Sinon on croirait l'avoir mis dehors alors qu'il passe toujours.
            message += (
                "\n⚠️ Il reste **administrateur** : il garde l'accès à tout "
                "jusqu'à ce que ce rôle lui soit retiré."
            )
        await interaction.response.send_message(message, ephemeral=True)

    @acces_groupe.command(name="liste", description="Qui peut utiliser les commandes")
    async def acces_liste(interaction: discord.Interaction):
        autorises = await bot.store.autorises()
        embed = discord.Embed(
            title="Accès aux commandes",
            # Les administrateurs sont cités même s'ils ne sont pas dans la
            # liste : sinon celle-ci se lirait comme exhaustive.
            description="Les **administrateurs** du serveur ont toujours accès.",
            color=0x5865F2,
        )
        embed.add_field(
            name=f"Membres autorisés ({len(autorises)})",
            value="\n".join(f"• <@{membre}>" for membre in autorises)
                  or "*Aucun.* Seuls les administrateurs peuvent s'en servir.",
            inline=False,
        )
        embed.set_footer(text="/reglages acces ajouter — réservé aux administrateurs.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- /reglages source ---------------------------------------------------

    source_groupe = app_commands.Group(
        name="source",
        description="Provenance des données (API du jeu ou fichier)",
        parent=groupe,
    )

    @source_groupe.command(
        name="tester", description="Teste la récupération des données maintenant"
    )
    async def source_tester(interaction: discord.Interaction):
        # Un appel réseau de 30 s au maximum : on diffère pour ne pas laisser
        # Discord expirer l'interaction au bout de 3 s.
        await interaction.response.defer(ephemeral=True)
        rapport = await diagnostiquer(bot.source)

        if rapport.ok:
            embed = discord.Embed(
                title="✅ Données accessibles",
                description=rapport.source,
                color=0x2ECC71,
            )
            taille = f"{rapport.taille:,}".replace(",", " ")
            embed.add_field(name="Réponse", value=f"{rapport.duree_ms} ms · {taille} caractères")
            embed.add_field(name="Bâtiments", value=str(rapport.batiments))
            embed.add_field(
                name="En promotion",
                value=str(rapport.promos) if rapport.promos else "aucune aujourd'hui",
            )
            if rapport.monde or rapport.mise_a_jour:
                embed.add_field(
                    name="Export",
                    value=f"{rapport.monde or '?'}\n-# mise à jour {rapport.mise_a_jour or '?'}",
                    inline=False,
                )
            if rapport.exemples:
                # Bornées : 116 bâtiments peuvent tous être en promo un jour.
                noms = ", ".join(rapport.exemples[:10])
                if len(rapport.exemples) > 10:
                    noms += f", … (+{len(rapport.exemples) - 10})"
                embed.add_field(name="Promotions trouvées", value=noms, inline=False)
            embed.set_footer(
                text="Teste la source, pas la fourchette : "
                "/fourchette apercu pour le post du jour."
            )
        else:
            embed = discord.Embed(
                title="❌ Données inaccessibles",
                description=rapport.source,
                color=0xE74C3C,
            )
            embed.add_field(name="Erreur", value=rapport.erreur or "inconnue", inline=False)
            if isinstance(bot.source, ApiSource):
                embed.add_field(
                    name="À vérifier",
                    value="`EMPIRE_API_KEY` (clé valide et non révoquée) et "
                          "`EMPIRE_API_URL` si tu l'as définie.",
                    inline=False,
                )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @source_groupe.command(name="voir", description="Affiche la source utilisée")
    async def source_voir(interaction: discord.Interaction):
        api = isinstance(bot.source, ApiSource)
        embed = discord.Embed(
            title="Source des données",
            description=bot.decrire_source(),
            color=0x5865F2,
        )
        embed.add_field(
            name="Clé d'API",
            value="✅ configurée" if api and bot.source.cle else
                  ("dans l'URL" if api else "—"),
        )
        embed.add_field(
            name="Bascule",
            value="Renseigne `EMPIRE_API_KEY` puis redémarre pour passer sur l'API."
                  if not api else
                  "Vide `EMPIRE_API_KEY` puis redémarre pour revenir au fichier.",
            inline=False,
        )
        embed.set_footer(
            text="/reglages source tester pour vérifier que les données arrivent."
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- /reglages template -------------------------------------------------

    template_groupe = app_commands.Group(
        name="template",
        description="Embed personnalisé (Discohook)",
        parent=groupe,
    )

    @template_groupe.command(name="charger", description="Charge un export Discohook (.json)")
    async def template_charger(
        interaction: discord.Interaction, fichier: discord.Attachment
    ):
        await interaction.response.defer(ephemeral=True)

        try:
            modele = json.loads((await fichier.read()).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as erreur:
            await interaction.followup.send(
                f"❌ JSON illisible : {erreur}", ephemeral=True
            )
            return

        try:
            valider_template(modele)
        except TemplateError as erreur:
            await interaction.followup.send(f"❌ {erreur}", ephemeral=True)
            return

        inconnus = placeholders_inconnus(modele)
        await bot.store.set_template(modele)

        message = "✅ Template enregistré."
        if inconnus:
            message += (
                f"\n⚠️ Placeholders non reconnus (laissés tels quels) : "
                f"{', '.join('`{' + p + '}`' for p in sorted(inconnus))}"
            )

        config = await bot.store.config()
        try:
            embeds, contenu, repli = await bot.construire_publication(
                Decimal(config["prix_min"]), Decimal(config["prix_max"])
            )
        except SourceError as erreur:
            await interaction.followup.send(
                f"{message}\n⚠️ Aperçu impossible : {erreur}", ephemeral=True
            )
            return

        await interaction.followup.send(message, ephemeral=True)
        if repli:
            await interaction.followup.send(
                f"Aperçu impossible : {repli}", ephemeral=True
            )
        else:
            await envoyer(interaction.followup, embeds[:1], contenu, ephemere=True)

    @template_groupe.command(name="voir", description="Renvoie le template actuel")
    async def template_voir(interaction: discord.Interaction):
        modele = await bot.store.template()
        contenu = json.dumps(modele, indent=2, ensure_ascii=False)
        fichier = discord.File(
            fp=io.BytesIO(contenu.encode("utf-8")),
            filename="template.json",
        )
        await interaction.response.send_message(file=fichier, ephemeral=True)

    @template_groupe.command(name="champs", description="Liste les placeholders disponibles")
    async def template_champs(interaction: discord.Interaction):
        embed = discord.Embed(
            title="Placeholders disponibles",
            description="À utiliser entre accolades dans ton export Discohook.",
            color=0x5865F2,
        )
        embed.add_field(
            name="Bâtiment",
            value="`{nom}` `{type}` `{niveau}` `{remise}` `{rang}` `{total}`",
            inline=False,
        )
        embed.add_field(
            name="Monde",
            value="`{monde}` `{taux_promoteur}` `{mise_a_jour}` `{date}`",
            inline=False,
        )
        embed.add_field(
            name="Montants",
            value="`{prix}` `{prix_origine}` `{economie}` `{loyer}` `{charge}` "
                  "`{impot}` `{loyer_net}` `{construction}` `{embellissement}` "
                  "`{reparation}` `{ecart}`\n"
                  "-# `{ecart}` : distance au bord de la fourchette, `0 Ø` si "
                  "la promo est dedans.",
            inline=False,
        )
        embed.add_field(
            name="Variantes",
            value="Ajoute `_long` pour tous les chiffres (`302 620 Ø`) ou "
                  "`_brut` pour les chiffres seuls (`302620`).\n"
                  "Ex : `{prix_long}`, `{economie_brut}`.",
            inline=False,
        )
        embed.set_footer(text=f"{len(PLACEHOLDERS)} placeholders reconnus")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    bot.tree.add_command(groupe)
