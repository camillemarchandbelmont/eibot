"""Client Discord : commandes slash et publication quotidienne."""

from __future__ import annotations

import asyncio
import io
import json
import logging
from decimal import Decimal

import discord
from discord import app_commands

from src import settings
from src.acces import acces_autorise
from src.commandes import (
    administrateur,
    lister_fourchettes,
    permissions_manquantes,
)
from src.db import Store
from src.journal import Journal
from src.modules import decouvrir, greffer
from src.modules import filiales as module_filiales
from src.modules import promos as module_promos
from src.promos import Building, Meta, find_promos, parse_csv
from src.publish import construire_embeds, envoyer, message_aucune_promo
from src.schedule import boucle_planning, maintenant_local
from src.source import (
    URL_API_DEFAUT,
    ApiSource,
    CsvFileSource,
    DataSource,
    SourceError,
    decrire,
    diagnostiquer,
)
from src.tournee import faire_la_tournee
from src.template import PLACEHOLDERS, TemplateError, placeholders_inconnus, valider_template

log = logging.getLogger(__name__)


def creer_source() -> DataSource:
    """Choisit l'API du jeu ou le fichier local.

    L'API l'emporte dès qu'une clé (`EMPIRE_API_KEY`) ou une URL explicite est
    fournie. Sans rien, on lit le CSV du dépôt : le bot reste utilisable hors
    ligne et pour les tests.
    """
    url = settings.EMPIRE_API_URL or settings.CSV_URL

    if settings.EMPIRE_API_KEY or url:
        source = ApiSource(url or URL_API_DEFAUT, cle=settings.EMPIRE_API_KEY)
        log.info("Source : API Empire Immo (%s)", source.url_masquee)
        return source

    log.info("Source : fichier %s", settings.CSV_PATH)
    return CsvFileSource(settings.CSV_PATH)


class ArbreProtege(app_commands.CommandTree):
    """`CommandTree` dont **toutes** les commandes sont réservées.

    Le contrôle vit ici, pas dans chaque callback : une commande ajoutée plus
    tard est protégée d'office. La version précédente vérifiait commande par
    commande, et sept d'entre elles étaient restées ouvertes à tout le serveur.
    """

    def __init__(self, client: "EmpireBot"):
        super().__init__(client)
        self.store = client.store

    async def autorisation(self, interaction: discord.Interaction) -> bool:
        """Vrai si l'auteur peut utiliser les commandes ; refuse sinon.

        Nom distinct de `interaction_check` pour rester appelable en test sans
        passer par discord.py.

        La décision est déléguée à `src.acces` : le site web applique la même
        règle, donc un membre ajouté par `/config acces ajouter` obtient les deux
        accès d'un coup.
        """
        permissions = getattr(interaction.user, "guild_permissions", None)
        if acces_autorise(
            est_admin=bool(permissions and permissions.administrator),
            membre_id=getattr(interaction.user, "id", None),
            autorises=await self.store.autorises(),
        ):
            return True

        await interaction.response.send_message(
            "❌ Réservé aux administrateurs et aux membres autorisés.\n"
            "-# Un administrateur peut t'ajouter avec `/config acces ajouter`.",
            ephemeral=True,
        )
        return False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self.autorisation(interaction)


class EmpireBot(discord.Client):
    def __init__(self, store: Store, source: DataSource):
        super().__init__(intents=discord.Intents.default())
        self.store = store
        self.source = source
        self.journal = Journal(self, store)
        self.tree = ArbreProtege(self)
        self._planning: asyncio.Task | None = None

        # Le balayage une seule fois, au démarrage : il importe des fichiers,
        # donc exécute du code. Le refaire à chaque commande multiplierait les
        # occasions de tomber sans rien apporter — un module n'apparaît qu'après
        # un déploiement.
        self.modules, self.modules_refuses = decouvrir()
        # Les deux refus dans le même dictionnaire : de l'extérieur, « ce module
        # n'est pas là » est un seul fait, qu'il ait échoué à se déclarer ou à
        # greffer ses commandes. Aucune clé ne se recouvre, un module refusé au
        # chargement n'atteignant jamais la greffe.
        self.modules_refuses.update(greffer(self, self.modules))
        enregistrer_commandes(self)

    async def on_ready(self) -> None:
        """Démarre la boucle de planification, une seule fois.

        `on_ready` peut être rappelé après une reconnexion Gateway, d'où la
        garde : deux boucles publieraient... une seule fois quand même (grâce à
        `doit_publier`), mais doubleraient les appels pour rien.
        """
        if self._planning is None or self._planning.done():
            self._planning = self.loop.create_task(
                boucle_planning(self.publier_tout, self.is_closed)
            )
            log.info("Planification interne active (vérification chaque minute).")
        log.info("Connecté en tant que %s.", self.user)

        # Ici et non dans `setup_hook` : le salon de logs se résout par l'API, ce
        # qui demande une connexion établie. Signalé à chaque reconnexion plutôt
        # qu'une seule fois : le message est rare, et le taire après une coupure
        # laisserait le bot amputé sans trace visible.
        await self.signaler_les_modules_refuses()

    async def setup_hook(self) -> None:
        if settings.GUILD_IDS:
            # Filtrer les ids non numériques : une faute de frappe dans Render
            # (un point-virgule au lieu d'une virgule, un caractère invisible
            # collé) ne doit jamais empêcher le bot de démarrer et de publier.
            # Un id invalide coûte les commandes sur ce serveur ; le bot entier
            # lever au démarrage coûte la publication quotidienne, découverte
            # le lendemain seulement.
            serveurs_valides = []
            for serveur_id in settings.GUILD_IDS:
                if serveur_id.isdigit():
                    serveurs_valides.append(int(serveur_id))
                else:
                    log.warning(
                        "Id de serveur invalide ignoré : %r (pas un nombre)",
                        serveur_id,
                    )

            if serveurs_valides:
                for guild_id in serveurs_valides:
                    guild = discord.Object(id=guild_id)
                    self.tree.copy_global_to(guild=guild)
                    await self.tree.sync(guild=guild)
                log.info(
                    "Commandes synchronisées sur %d serveur(s) : %s",
                    len(serveurs_valides),
                    ", ".join(str(g) for g in serveurs_valides),
                )
            else:
                log.error(
                    "Aucun serveur valide dans GUILD_IDS=%r : "
                    "commandes non synchronisées.",
                    settings.GUILD_IDS,
                )
        else:
            await self.tree.sync()
            log.info("Commandes synchronisées globalement.")

    # --- Cœur partagé par /promos, l'aperçu et la publication --------------

    def decrire_source(self) -> str:
        """Provenance des données, pour `/config voir`. Jamais la clé d'API."""
        return decrire(self.source)

    async def charger(self):
        texte = await self.source.fetch()
        return parse_csv(texte)

    async def construire_publication(
        self,
        prix_min: Decimal,
        prix_max: Decimal,
        donnees: tuple[Meta, list[Building]] | None = None,
        tolere_min: Decimal | None = None,
        tolere_max: Decimal | None = None,
    ) -> tuple[list[dict], str, str]:
        """Renvoie (embeds, contenu, message de repli si aucune promo).

        `donnees` permet de réutiliser un export déjà chargé : avec plusieurs
        fourchettes, recharger à chaque tour multiplierait les appels à l'API du
        jeu pour des données identiques.

        `tolere_min`/`tolere_max` décrivent la zone de tolérance de la
        fourchette, où l'on cherche avant de repêcher au hasard de la distance.
        """
        meta, batiments = donnees if donnees is not None else await self.charger()
        promos = find_promos(
            batiments, prix_min, prix_max,
            tolere_min=tolere_min, tolere_max=tolere_max,
        )
        modele = await self.store.template()
        date = maintenant_local((await self.store.config())["fuseau"]).strftime("%Y-%m-%d")

        if not promos:
            return [], "", message_aucune_promo(prix_min, prix_max, meta)

        # Une promo repêchée hors fourchette n'est signalée nulle part : ni note
        # globale sous le message, ni marqueur dans son embed.
        embeds, contenu = construire_embeds(promos, meta, modele, date)
        return embeds, contenu, ""

    async def resoudre_salon(self, salon_id: str):
        """Salon Discord depuis son id, via le cache puis l'API.

        Traverse tous les serveurs où le bot est présent : c'est ce qui rend le
        multi-serveurs possible sans rien changer à la publication.

        Mémorise au passage le nom du salon et de son serveur, pour le site. Ici
        plutôt qu'au seul réglage : un salon renommé garderait sinon son ancien
        nom indéfiniment, alors qu'il se corrige de lui-même au premier post.
        """
        salon = self.get_channel(int(salon_id))
        if salon is None:
            salon = await self.fetch_channel(int(salon_id))

        serveur = getattr(salon, "guild", None)
        if serveur is not None and getattr(salon, "name", None):
            try:
                await self.store.memoriser_salon(
                    salon_id, salon.name, serveur.id, getattr(serveur, "name", "")
                )
            except Exception:
                # Un cache de noms cosmétique ne doit jamais empêcher un post :
                # si Postgres est indisponible, on log et on continue.
                log.warning(
                    "Impossible de mémoriser le nom du salon %s.", salon_id, exc_info=True
                )
        return salon

    async def publier_si_lheure(self, forcer: bool = False) -> str:
        """Les promotions, si c'est l'heure. Appelée par `/tick` et la boucle.

        Ne fait plus que déléguer : la mécanique est dans `src.tournee`, ce qui
        est propre aux promotions dans `src.modules.promos`. Le nom reste pour
        `/fourchette apercu`, le site de contrôle et les tests, qui l'appellent
        tel quel.
        """
        return await self.faire_publication(module_promos.PUBLICATION, forcer=forcer)

    async def publier_filiales_si_lheure(self, forcer: bool = False) -> str:
        """Le tableau des frais, si c'est son heure. Même délégation."""
        return await self.faire_publication(
            module_filiales.PUBLICATION, forcer=forcer
        )

    async def faire_publication(self, publication, forcer: bool = False) -> str:
        """Une publication quelconque, la même mécanique pour toutes.

        Le fuseau vient de la configuration, et non de l'horloge du serveur : Render
        tourne en UTC, où « 09:00 » n'est pas la même heure qu'à Paris.
        """
        config = await self.store.config()
        maintenant = maintenant_local(config["fuseau"])
        return await faire_la_tournee(
            publication, self, self.store, maintenant, forcer=forcer
        )

    async def publier_tout(self, forcer: bool = False) -> str:
        """Le tour complet appelé par `/tick` et la boucle interne.

        Les deux publications sont **isolées** : la panne de l'export du jeu ne
        doit pas faire taire un tableau dont les données sont saisies à la main,
        et réciproquement. Chaque panne reste dans le compte rendu — avalée en
        silence, on croirait que tout est normal.
        """
        comptes = []
        for publier, quoi in (
            (self.publier_si_lheure, "promotions"),
            (self.publier_filiales_si_lheure, "filiales"),
        ):
            try:
                comptes.append(await publier(forcer=forcer))
            except Exception as erreur:
                log.warning("Publication des %s impossible : %s", quoi, erreur)
                comptes.append(f"{quoi} : {type(erreur).__name__} : {erreur}")
        return " · ".join(comptes)

    # --- Journal : un observateur ne doit jamais bloquer l'essentiel --------

    async def journaliser_publication(
        self, promos: int, reussis: list[str], echecs: dict[str, str]
    ) -> None:
        """Publique : c'est par là que le moteur de tournée rend ses comptes.

        La panne est avalée ici, à l'unique endroit qui appelle le journal — un
        observateur ne doit jamais bloquer ce qu'il observe, et dupliquer la garde
        dans le moteur en ferait deux à maintenir.
        """
        try:
            await self.journal.publication(promos=promos, reussis=reussis, echecs=echecs)
        except Exception:
            log.warning("Journal Discord indisponible.", exc_info=True)

    async def signaler_les_modules_refuses(self) -> None:
        """Nomme dans le salon de logs les modules écartés au démarrage.

        Muette quand tout va bien : un « 0 module refusé » à chaque démarrage
        apprendrait à ne plus lire ce salon, et le vrai signalement passerait avec
        le reste.
        """
        if not self.modules_refuses:
            return
        lignes = "\n".join(
            f"• `{nom}` — {raison}"
            for nom, raison in sorted(self.modules_refuses.items())
        )
        await self.journaliser_erreur(
            f"{len(self.modules_refuses)} module(s) écarté(s) au démarrage :\n{lignes}"
        )

    async def journaliser_erreur(self, message: str) -> None:
        """Publique, comme sa voisine : les modules signalent leurs pannes par ici."""
        try:
            await self.journal.erreur(message)
        except Exception:
            log.warning("Journal Discord indisponible.", exc_info=True)


# --- Commandes --------------------------------------------------------------

def enregistrer_commandes(bot: EmpireBot) -> None:
    tree = bot.tree

    # --- /config ----------------------------------------------------------

    config_groupe = app_commands.Group(
        name="config", description="Réglages du bot"
    )

    @config_groupe.command(name="voir", description="Affiche la configuration courante")
    async def config_voir(interaction: discord.Interaction):
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
                "Utilise `/config mention` pour le rendre par serveur."
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

    @config_groupe.command(
        name="fuseau", description="Fuseau horaire de toutes les publications"
    )
    @app_commands.describe(fuseau="Ex: Europe/Paris")
    async def config_fuseau(interaction: discord.Interaction, fuseau: str):
        """Le seul réglage d'horloge commun aux publications.

        L'heure de chacune se règle chez elle (`/fourchette heure`, `/filiales
        heure`). Le fuseau, lui, est partagé : le régler depuis l'une déplacerait
        l'autre, surprise qui ne se découvrirait que le lendemain. D'où sa propre
        commande, à l'endroit des réglages communs.
        """
        from zoneinfo import ZoneInfo

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

    # --- /config acces ----------------------------------------------------

    acces_groupe = app_commands.Group(
        name="acces",
        description="Qui peut utiliser les commandes du bot",
        parent=config_groupe,
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
        embed.set_footer(text="/config acces ajouter — réservé aux administrateurs.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @config_groupe.command(name="mention", description="Rôle mentionné à chaque post")
    async def config_mention(
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
                        "Utilise `/config mention` pour régler par serveur."
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

    @config_groupe.command(
        name="logs", description="Salon où le bot raconte ce qu'il fait"
    )
    async def config_logs(
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

    # Plus de `/config retester` : elle effaçait la marque du jour des promotions
    # seules, sous un nom qui ne nommait aucune publication — impossible à lire
    # sur un bot qui en a deux et pourra en avoir plus. Pour republier tout de
    # suite, `/fourchette publier` et `/filiales publier` le font sans détour, et
    # préviennent que le post de l'heure prévue ne repassera pas. Pour éprouver la
    # source de données, `/source tester`.

    tree.add_command(config_groupe)

    # --- /source ----------------------------------------------------------

    source_groupe = app_commands.Group(
        name="source", description="Provenance des données (API du jeu ou fichier)"
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
            taille = f"{rapport.taille:,}".replace(",", " ")
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
        embed.set_footer(text="/source tester pour vérifier que les données arrivent.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    tree.add_command(source_groupe)

    # --- /template --------------------------------------------------------

    template_groupe = app_commands.Group(
        name="template", description="Embed personnalisé (Discohook)"
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

    tree.add_command(template_groupe)
