"""`/reglages` : tout ce qui règle le bot, à un seul endroit.

**Ce fichier n'est pas un module.** Les modules de `src/modules/` apportent des
fonctionnalités et peuvent être retirés ou éteints ; `/reglages` est le noyau,
toujours présent. Sans lui, un serveur dont on aurait éteint le dernier module ne
pourrait plus rien régler — ni rendre la main.

Le rangement suit une règle simple : **une commande qui configure le bot vit ici,
une commande qui s'en sert vit dans son module.** C'est ce qui distingue
`/reglages source tester` (est-ce que l'export arrive ?) de `/promos apercu`
(qu'est-ce qui partirait ce soir ?), alors que les deux touchent aux mêmes
données.

Quatre sous-groupes — `modules`, `acces`, `source`, `template` — parce que ce sont
des sujets entiers, et pas un de plus : Discord n'accepte que trois niveaux, si
bien que `/reglages template charger` est déjà au fond.
"""

from __future__ import annotations

import io
import json
from decimal import Decimal
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import discord
from discord import app_commands

from src.commandes import (
    administrateur,
    lister_fourchettes,
    permissions_manquantes,
    pour_ce_serveur,
)
from src.importation import nommer, preparer
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


def _choix(modules, saisie: str, garder) -> list[app_commands.Choice[str]]:
    """Les modules retenus par `garder`, en propositions Discord.

    L'ordre reçu est conservé : c'est celui du menu, donc celui qu'on a sous les
    yeux quand on cherche le nom à taper.
    """
    debut = (saisie or "").strip().casefold()
    return [
        app_commands.Choice(name=f"{module.nom} — {module.titre}", value=module.nom)
        for module in modules
        if garder(module) and debut in module.nom.casefold()
    ][:25]  # limite Discord


def enregistrer_les_reglages(bot: EmpireBot) -> None:
    """Greffe `/reglages` sur l'arbre du bot.

    Appelée directement par `EmpireBot`, et non par la découverte des modules :
    ces commandes doivent être là même si aucun module ne se charge.
    """
    groupe = app_commands.Group(name="reglages", description="Réglages du bot")

    # --- /reglages voir -----------------------------------------------------

    @groupe.command(name="voir", description="Affiche la configuration courante")
    async def reglages_voir(interaction: discord.Interaction):
        """La configuration de **ce** serveur, et l'aveu qu'il n'en a pas.

        Celle du commun ne publie plus rien : l'afficher ici ferait croire ce
        serveur réglé, et cacherait la seule chose à savoir de lui.
        """
        magasin = pour_ce_serveur(bot, interaction)
        config = await magasin.config()
        fourchettes = await magasin.fourchettes()
        logs = await magasin.salon_logs()
        roles = await magasin.roles()
        # Une ligne par serveur : une valeur unique laisserait croire que tous
        # les serveurs pinguent, ou qu'aucun ne le fait.
        if roles:
            noms = await magasin.serveurs()
            role = "\n".join(
                f"{noms.get(serveur, serveur)} : <@&{role_id}>"
                for serveur, role_id in roles.items()
            )
        # Repli plat : `role_id` existe, mais `roles` est vide. Ce rôle est
        # mentionné sur tous les serveurs (config d'avant le multi-serveurs). Lu
        # par `role_du_serveur`, qui le cherche là où il est resté — dans la
        # configuration commune, que `/reglages importer` ne recopie pas faute de
        # savoir à quel serveur il appartenait.
        elif plat := await magasin.role_du_serveur(interaction.guild.id):
            role = (
                f"<@&{plat}>\n"
                "-# Réglage d'avant le multi-serveurs, appliqué à tous les serveurs. "
                "Utilise `/reglages mention` pour le rendre par serveur."
            )
        else:
            role = "*aucune*"
        stockage = "Postgres" if magasin.persistant else "⚠️ mémoire (perdue au redémarrage)"

        embed = discord.Embed(
            title="Configuration",
            # Le vide est le pire des signalements : il ressemble trait pour trait
            # à une panne du bot. Les deux chemins sont nommés, car un serveur
            # tout neuf n'a aucune configuration commune à reprendre.
            description=(
                "⚠️ **Rien n'est réglé dans ce serveur** : il ne publiera nulle "
                "part.\n"
                "-# `/reglages importer` reprend la configuration d'avant, en ne "
                "gardant que les salons de ce serveur. Sinon `/promos ajouter` "
                "puis `/promos salon ajouter`."
            )
            if await magasin.vierge()
            else None,
            color=0x5865F2,
        )
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
        embed.set_footer(
            text="Dernière publication : "
            f"{await magasin.derniere_publication() or 'jamais'}"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- /reglages fuseau ---------------------------------------------------

    @groupe.command(
        name="fuseau", description="Fuseau horaire de toutes les publications"
    )
    @app_commands.describe(fuseau="Ex: Europe/Paris")
    async def reglages_fuseau(interaction: discord.Interaction, fuseau: str):
        """Le seul réglage d'horloge commun aux publications.

        L'heure de chacune se règle chez elle (`/promos heure`, `/frais
        heure`). Le fuseau, lui, est partagé : le régler depuis l'une déplacerait
        l'autre, surprise qui ne se découvrirait que le lendemain. D'où sa propre
        commande, à l'endroit des réglages communs.

        Commun aux publications d'un serveur, pas aux serveurs entre eux : deux
        entreprises peuvent vivre dans deux décalages.
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

        magasin = pour_ce_serveur(bot, interaction)
        config = await magasin.maj_config(fuseau=fuseau)

        # Les marques du jour ne sont pas effacées : corriger l'horloge n'est pas
        # demander un nouveau post, et il n'y aurait aucune raison de choisir
        # laquelle des deux publications repartirait.
        maintenant = maintenant_local(config["fuseau"])
        await interaction.response.send_message(
            f"✅ Fuseau : **{config['fuseau']}**.\n"
            f"-# Il est {maintenant.strftime('%H:%M')} — "
            f"promotions à {config['heure']}, "
            f"tableau des frais à {await magasin.heure_filiales()}.",
            ephemeral=True,
        )

    # --- /reglages mention --------------------------------------------------

    @groupe.command(name="mention", description="Rôle mentionné à chaque post")
    async def reglages_mention(
        interaction: discord.Interaction, role: discord.Role | None = None
    ):
        """Le seul réglage déjà rangé par serveur avant le cloisonnement.

        La table `roles` reste donc dans la configuration commune, et la vue la
        délègue telle quelle : cloisonnée, elle se rangerait deux fois —
        `serveur:111:roles` ne contenant qu'une entrée `111` — et le site de
        contrôle ne la trouverait plus.
        """
        magasin = pour_ce_serveur(bot, interaction)

        if role is None:
            # Lire avant d'effacer pour savoir si on tape dans le repli plat.
            # `role_du_serveur` cherche `role_id` là où il est resté, et ne le rend
            # que si la table est vide : c'est exactement le repli plat.
            roles = await magasin.roles()
            etait_plat = not roles and await magasin.role_du_serveur(
                interaction.guild.id
            )

            if await magasin.effacer_role(str(interaction.guild.id)):
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

        await magasin.definir_role(str(interaction.guild.id), str(role.id))
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
        """Le journal de ce serveur, et de lui seul.

        Un compte rendu de tournée nomme des salons : raconté dans le journal d'une
        autre entreprise, il mélangerait les deux dans un même fil et donnerait à
        chacune les ids de l'autre.
        """
        magasin = pour_ce_serveur(bot, interaction)

        if salon is None:
            await magasin.desactiver_logs()
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

        await magasin.maj_config(logs_salon_id=str(salon.id))
        await interaction.response.send_message(
            f"✅ Journal dans {salon.mention} : publications et erreurs y seront "
            f"rapportées.",
            ephemeral=True,
        )

    # --- /reglages importer -------------------------------------------------

    #: Importer recopie la liste d'accès : la commande décide donc qui pourra se
    #: servir du bot dans ce serveur, exactement comme `/reglages acces`. D'où le
    #: même verrou.
    REFUS_IMPORT = (
        "❌ Seul un administrateur peut importer la configuration.\n"
        "-# L'import reprend la liste d'accès : il décide qui peut se servir du "
        "bot ici."
    )

    @groupe.command(
        name="importer",
        description="Reprend l'ancienne configuration commune dans ce serveur",
    )
    async def reglages_importer(interaction: discord.Interaction):
        """Le pont vers les réglages par serveur, à taper une fois par serveur.

        Chaque serveur a désormais sa propre configuration, et il n'y a pas de
        repli : un serveur qui n'a rien réglé ne publie nulle part. Cette commande
        reprend ce qui était réglé du temps de la configuration commune, en ne
        gardant que les salons de ce serveur-là.

        Le calcul vit dans `src/importation.py`, pour être éprouvé sur des
        dictionnaires nus : ici il ne reste qu'à demander à Discord quels salons
        sont à ce serveur — la seule chose qu'un calcul ne peut pas savoir — et à
        écrire dans le tiroir.
        """
        if not administrateur(interaction):
            await interaction.response.send_message(REFUS_IMPORT, ephemeral=True)
            return

        magasin = pour_ce_serveur(bot, interaction)
        reprise = preparer(
            # Toute la base, et non le tiroir de ce serveur : c'est justement
            # l'ancienne configuration commune qu'on vient reprendre. La vue le
            # permet, `tout()` n'étant pas cloisonné.
            await magasin.tout(),
            interaction.guild.id,
            # Tous les salons, y compris vocaux et catégories : la question posée
            # est « est-il à ce serveur ? », pas « peut-on y publier ? ».
            {str(salon.id) for salon in interaction.guild.channels},
        )

        for cle, valeur in reprise.a_ecrire.items():
            await magasin.set(cle, valeur)

        if not reprise.a_ecrire and not reprise.deja_reglees:
            # Répondre « ✅ » à un import qui n'a rien fait laisserait attendre
            # des posts qui ne viendront jamais.
            await interaction.response.send_message(
                "ℹ️ Il n'y avait **rien à reprendre** : aucune configuration "
                "commune n'est enregistrée.\n"
                "-# Règle ce serveur directement : `/promos ajouter`, "
                "`/promos salon ajouter`, `/promos heure`.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"Configuration reprise dans {interaction.guild.name}",
            description=(
                "Rien n'a été effacé : l'ancienne configuration commune reste en "
                "place, et cet import peut être retapé."
            ),
            color=0x5865F2,
        )
        if reprise.a_ecrire:
            embed.add_field(
                name=f"Repris ({len(reprise.a_ecrire)})",
                value="\n".join(f"• {nommer(cle)}" for cle in reprise.a_ecrire),
                inline=False,
            )
        if reprise.salons_ecartes:
            # Le point de vigilance du plan, rendu visible : sans ce champ, on
            # chercherait longtemps pourquoi une fourchette ne publie plus là où
            # elle publiait la veille.
            embed.add_field(
                name=f"Salons écartés ({len(reprise.salons_ecartes)})",
                value=(
                    " ".join(f"<#{salon}>" for salon in reprise.salons_ecartes)
                    + "\n-# Ils sont dans un autre serveur. Publier dedans depuis "
                    "ici enverrait deux posts par salon."
                ),
                inline=False,
            )
        if reprise.deja_reglees:
            embed.add_field(
                name=f"Déjà réglé ici ({len(reprise.deja_reglees)})",
                value=(
                    "\n".join(f"• {nommer(cle)}" for cle in reprise.deja_reglees)
                    + "\n-# Laissé tel quel : un réglage fait ici a la priorité "
                    "sur l'ancien."
                ),
                inline=False,
            )
        embed.set_footer(text="À vérifier avec /reglages voir et /promos liste.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # Rien qui ressemble à l'ancien `/config retester` : elle effaçait la marque
    # du jour des promotions seules, sous un nom qui ne nommait aucune
    # publication — illisible sur un bot qui en a deux et pourra en avoir plus.
    # Pour republier tout de suite, `/promos publier` et `/frais publier`
    # le font sans détour, et préviennent que le post de l'heure prévue ne
    # repassera pas. Pour éprouver la source, `/reglages source tester`.

    # --- /reglages modules --------------------------------------------------
    #
    # Le seul chemin pour éteindre un module : sans ces commandes, il faudrait
    # écrire dans la base à la main. Elles restent ouvertes aux membres
    # autorisés, comme le reste de `/reglages` — éteindre un module ne donne
    # aucun droit, à la différence de la liste d'accès.

    modules_groupe = app_commands.Group(
        name="modules",
        description="Quels modules sont allumés dans ce serveur",
        parent=groupe,
    )

    def module_nomme(nom: str):
        """Le module de ce nom, ou None. Le nom est une clé, donc en minuscules."""
        cherche = (nom or "").strip().casefold()
        for module in bot.modules:
            if module.nom == cherche:
                return module
        return None

    async def refuser_module_inconnu(
        interaction: discord.Interaction, nom: str
    ) -> None:
        """Refuse en listant les noms trouvés.

        Sans ce refus, un nom mal tapé écrirait dans la liste des éteints un
        module qui n'existe pas : rien ne le rallumerait jamais, et il resterait
        là à faire croire à un réglage.
        """
        noms = ", ".join(f"`{module.nom}`" for module in bot.modules) or "*aucun*"
        await interaction.response.send_message(
            f"❌ Aucun module nommé « {(nom or '').strip()} ».\n"
            f"Modules trouvés : {noms}.",
            ephemeral=True,
        )

    async def rafraichir_le_menu(
        interaction: discord.Interaction, module
    ) -> str:
        """Reconstruit le menu de ce serveur. Rend ce qu'il reste à en dire.

        L'activation est immédiate et ne demande aucun redémarrage : sans ce
        rappel, la commande resterait dans le menu jusqu'au prochain
        déploiement, et l'extinction se lirait comme un réglage sans effet.

        Le réglage est déjà écrit quand on arrive ici. Une poussée refusée —
        Discord limite le débit des synchronisations — est donc dite, pas
        rattrapée : la taire ferait retaper la commande.
        """
        if await bot.synchroniser_le_menu(interaction.guild.id):
            return ""
        return (
            "\n⚠️ Le menu de ce serveur n'a pas pu être rafraîchi tout de "
            "suite ; il le sera au prochain démarrage du bot."
        )

    async def completer_allume(
        interaction: discord.Interaction, saisie: str
    ) -> list[app_commands.Choice[str]]:
        """Propose les modules allumés ici — ceux que `desactiver` peut éteindre.

        Proposer un module déjà éteint ferait choisir un nom pour s'entendre
        répondre qu'il n'y avait rien à faire.
        """
        eteints = await pour_ce_serveur(bot, interaction).modules_eteints()
        return _choix(bot.modules, saisie, lambda module: module.nom not in eteints)

    async def completer_eteint(
        interaction: discord.Interaction, saisie: str
    ) -> list[app_commands.Choice[str]]:
        """Propose les modules éteints ici — ceux que `activer` peut rallumer."""
        eteints = await pour_ce_serveur(bot, interaction).modules_eteints()
        return _choix(bot.modules, saisie, lambda module: module.nom in eteints)

    @modules_groupe.command(
        name="liste", description="Modules trouvés et leur état dans ce serveur"
    )
    async def modules_liste(interaction: discord.Interaction):
        """Tous les modules du dossier, allumés comme éteints.

        Un module absent de cette liste se lirait comme un module jamais
        déployé : ceux qui ont refusé de se charger y sont donc nommés avec leur
        raison, plutôt que de disparaître en silence.
        """
        eteints = await pour_ce_serveur(bot, interaction).modules_eteints()
        embed = discord.Embed(
            title="Modules dans ce serveur",
            description=(
                "Tout est allumé par défaut, et chaque serveur choisit pour lui "
                "seul."
            ),
            color=0x5865F2,
        )
        if bot.modules:
            embed.add_field(
                name=f"Trouvés ({len(bot.modules)})",
                value="\n".join(
                    f"⛔ `{module.nom}` — {module.titre} *(éteint)*"
                    if module.nom in eteints
                    else f"✅ `{module.nom}` — {module.titre}"
                    for module in bot.modules
                ),
                inline=False,
            )
        else:
            embed.add_field(
                name="Trouvés (0)",
                value="*Aucun.* Le bot ne publie rien et n'a que `/reglages`.",
                inline=False,
            )
        if bot.modules_refuses:
            embed.add_field(
                name=f"Refusés au démarrage ({len(bot.modules_refuses)})",
                value="\n".join(
                    f"• `{nom}` — {raison}"
                    for nom, raison in sorted(bot.modules_refuses.items())
                ),
                inline=False,
            )
        embed.set_footer(
            text="/reglages modules activer · /reglages modules desactiver"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @modules_groupe.command(
        name="activer", description="Rallume un module dans ce serveur"
    )
    @app_commands.describe(module="Nom du module")
    @app_commands.autocomplete(module=completer_eteint)
    async def modules_activer(interaction: discord.Interaction, module: str):
        trouve = module_nomme(module)
        if trouve is None:
            await refuser_module_inconnu(interaction, module)
            return

        if not await pour_ce_serveur(bot, interaction).rallumer_module(trouve.nom):
            await interaction.response.send_message(
                f"ℹ️ **{trouve.titre}** est déjà allumé dans ce serveur.",
                ephemeral=True,
            )
            return

        message = f"✅ Module **{trouve.titre}** (`{trouve.nom}`) allumé ici."
        if trouve.publications:
            message += "\n-# Ses publications repartiront à leur heure."
        message += await rafraichir_le_menu(interaction, trouve)
        await interaction.response.send_message(message, ephemeral=True)

    @modules_groupe.command(
        name="desactiver", description="Éteint un module dans ce serveur"
    )
    @app_commands.describe(module="Nom du module")
    @app_commands.autocomplete(module=completer_allume)
    async def modules_desactiver(interaction: discord.Interaction, module: str):
        trouve = module_nomme(module)
        if trouve is None:
            await refuser_module_inconnu(interaction, module)
            return

        magasin = pour_ce_serveur(bot, interaction)
        eteints = await magasin.modules_eteints()
        if trouve.nom in eteints:
            # Dit avant le refus du dernier : « déjà éteint » est la vérité,
            # alors que « c'est le dernier allumé » serait faux.
            await interaction.response.send_message(
                f"ℹ️ **{trouve.titre}** est déjà éteint dans ce serveur.",
                ephemeral=True,
            )
            return

        allumes = [m for m in bot.modules if m.nom not in eteints]
        if len(allumes) <= 1:
            # Un serveur sans aucun module répond encore à `/reglages`, et à rien
            # d'autre : rien à l'écran ne distinguerait ce réglage d'une panne.
            await interaction.response.send_message(
                f"❌ **{trouve.titre}** est le dernier module allumé ici.\n"
                "L'éteindre rendrait le bot muet dans ce serveur, ce qui "
                "ressemble à une panne.\n"
                "-# Allume un autre module d'abord.",
                ephemeral=True,
            )
            return

        await magasin.eteindre_module(trouve.nom)
        message = f"✅ Module **{trouve.titre}** (`{trouve.nom}`) éteint ici."
        if trouve.publications:
            message += "\n-# Ses publications quotidiennes ne sortiront plus ici."
        message += "\n-# `/reglages modules activer` le rallume."
        message += await rafraichir_le_menu(interaction, trouve)
        await interaction.response.send_message(message, ephemeral=True)

    # --- /reglages acces ----------------------------------------------------

    acces_groupe = app_commands.Group(
        name="acces",
        description="Qui peut utiliser les commandes du bot dans ce serveur",
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

        if not await pour_ce_serveur(bot, interaction).autoriser(str(membre.id)):
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

        magasin = pour_ce_serveur(bot, interaction)
        if not await magasin.retirer_autorise(str(membre.id)):
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
        autorises = await pour_ce_serveur(bot, interaction).autorises()
        embed = discord.Embed(
            # « ce serveur » dans le titre : la liste est propre au serveur, et
            # un membre autorisé ailleurs n'y passe pas.
            title="Accès aux commandes dans ce serveur",
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
                "/promos apercu pour le post du jour."
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
        """Le template de ce serveur : deux entreprises n'ont pas la même charte.

        C'est tout l'intérêt d'un template par serveur — et l'aperçu qui suit est
        rendu avec celui qu'on vient d'enregistrer, sans quoi la commande
        confirmerait un réglage en montrant celui du voisin.
        """
        await interaction.response.defer(ephemeral=True)
        magasin = pour_ce_serveur(bot, interaction)

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
        await magasin.set_template(modele)

        message = "✅ Template enregistré."
        if inconnus:
            message += (
                f"\n⚠️ Placeholders non reconnus (laissés tels quels) : "
                f"{', '.join('`{' + p + '}`' for p in sorted(inconnus))}"
            )

        config = await magasin.config()
        try:
            embeds, contenu, repli = await bot.construire_publication(
                Decimal(config["prix_min"]),
                Decimal(config["prix_max"]),
                magasin=magasin,
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
        modele = await pour_ce_serveur(bot, interaction).template()
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
