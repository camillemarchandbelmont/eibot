"""Client Discord : commandes slash et publication quotidienne."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from decimal import Decimal

import discord
from discord import app_commands

from src import settings
from src.acces import acces_autorise
from src.db import Store
from src.journal import Journal
from src.modules import decouvrir, greffer
from src.modules import frais as module_frais
from src.modules import promos as module_promos
from src.promos import Building, Meta, find_promos, parse_csv, types_disponibles
from src.publish import construire_embeds, message_aucune_promo
from src.reglages import enregistrer_les_reglages
from src.schedule import boucle_planning, maintenant_local
from src.source import (
    URL_API_DEFAUT,
    ApiSource,
    CsvFileSource,
    DataSource,
    decrire,
)
from src.tournee import faire_la_tournee

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
        règle, sur la liste **commune** qu'il continue de lire faute de dire de
        quel serveur il parle. Un membre ajouté dans un serveur n'ouvre donc plus
        le site, et c'est le chantier que le plan garde pour plus tard.

        La liste est celle du serveur où l'on tape : la même liste partout
        donnait, en invitant le bot ailleurs, les clés de toutes les entreprises.
        Hors serveur — un message privé — il n'y a pas de liste à lire, et lever
        ici ferait échouer *toutes* les commandes plutôt que de refuser celle-là.
        """
        serveur = getattr(interaction, "guild", None)
        magasin = self.store.pour(serveur.id) if serveur else self.store

        permissions = getattr(interaction.user, "guild_permissions", None)
        if acces_autorise(
            est_admin=bool(permissions and permissions.administrator),
            membre_id=getattr(interaction.user, "id", None),
            autorises=await magasin.autorises(),
        ):
            return True

        await interaction.response.send_message(
            "❌ Réservé aux administrateurs et aux membres autorisés.\n"
            "-# Un administrateur peut t'ajouter avec `/reglages acces ajouter`.",
            ephemeral=True,
        )
        return False

    async def module_allume(self, interaction: discord.Interaction) -> bool:
        """Vrai si ce qu'on vient de taper relève d'un module allumé ici.

        Second verrou, derrière le menu par serveur. Il en faut deux : Discord
        garde la liste des commandes en cache chez le client, et sans
        `GUILD_IDS` la synchronisation est globale — il n'y a alors pas de menu
        par serveur du tout. Sans ce refus, `desactiver` laisserait la commande
        parfaitement utilisable.

        Ce qui n'a pas de module passe toujours : c'est `/reglages`, la seule
        porte de sortie d'un serveur qui a tout éteint. Hors serveur — un message
        privé — il n'y a pas de liste d'éteints à lire, et lever ici ferait
        échouer la commande au lieu de la laisser répondre.
        """
        serveur = getattr(interaction, "guild", None)
        commande = getattr(interaction, "command", None)
        if serveur is None or commande is None:
            return True

        # La racine, et non la sous-commande : c'est elle qui appartient au
        # module. `/frais liste` s'éteint avec `/frais`.
        racine = getattr(commande, "root_parent", None) or commande
        module = self.client.module_des_commandes.get(racine.name)
        if module is None:
            return True

        if await self.store.pour(serveur.id).module_actif(module.nom):
            return True

        await interaction.response.send_message(
            f"❌ Le module **{module.titre}** est éteint dans ce serveur.\n"
            f"-# `/reglages modules activer` avec `{module.nom}` le rallume.",
            ephemeral=True,
        )
        return False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Les deux contrôles, dans cet ordre : à qui n'a pas accès on ne dit
        # rien de la configuration du serveur, pas même quels modules y sont
        # éteints.
        if not await self.autorisation(interaction):
            return False
        return await self.module_allume(interaction)


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
        refuses, self.commandes_des_modules = greffer(self, self.modules)
        self.modules_refuses.update(refuses)
        #: De quel module relève une commande de premier niveau. Le sens inverse
        #: du relevé de la greffe, celui dont on a besoin à chaque interaction :
        #: la question posée est « ce `/frais` qu'on vient de taper, est-il
        #: allumé ici ? ». `/reglages` n'y figure pas — il n'a pas de module,
        #: donc rien ne l'éteint.
        self.module_des_commandes = {
            nom: module
            for module in self.modules
            for nom in self.commandes_des_modules.get(module.nom, ())
        }
        # Après les modules, mais sans dépendre d'eux : `/reglages` est le noyau,
        # et doit répondre même si aucun module ne s'est chargé.
        enregistrer_les_reglages(self)

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
        # De même pour la liste des serveurs, qui n'est garnie qu'une fois la
        # connexion établie.
        await self.signaler_les_serveurs_sans_configuration()

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
                # Un menu par serveur, et non la même copie partout : c'est ici
                # que les modules éteints quittent la liste des commandes.
                await self.synchroniser_les_menus(serveurs_valides)
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
            # Sans liste de serveurs, la synchronisation est globale : le même
            # menu partout, modules éteints compris. C'est `ArbreProtege` qui
            # refuse alors la commande à l'exécution.
            await self.tree.sync()
            log.info("Commandes synchronisées globalement.")

    # --- Le menu de chaque serveur -----------------------------------------

    def commandes_du_menu(self, eteints: Iterable[str] = ()) -> list:
        """Les commandes de premier niveau à montrer dans un serveur.

        `/reglages` en fait toujours partie : il n'appartient à aucun module,
        donc rien ne l'éteint. C'est la seule porte de sortie d'un serveur qui a
        tout éteint — la commande refuse d'en arriver là, mais la base peut y
        arriver, un module retiré du dépôt ou un tiroir repris à la main.
        """
        exclus = set(eteints)
        return [
            commande
            for commande in self.tree.get_commands()
            if getattr(self.module_des_commandes.get(commande.name), "nom", None)
            not in exclus
        ]

    async def synchroniser_le_menu(self, serveur_id) -> bool:
        """Installe le menu de ce serveur et le pousse à Discord.

        Reconstruit et non complété : ajouter sans vider d'abord laisserait la
        commande d'un module qu'on vient d'éteindre dans le menu, et
        `desactiver` semblerait sans effet.

        L'arbre global, lui, garde tout : le menu d'un serveur est une copie. Le
        vider ferait disparaître la commande de **tous** les serveurs à la
        synchronisation suivante.

        Faux si la poussée a échoué — Discord limite le débit des
        synchronisations. Le réglage est déjà écrit en base : le perdre pour une
        requête refusée serait pire que le menu en retard, qui se rattrape au
        prochain démarrage.
        """
        guild = discord.Object(id=int(serveur_id))
        eteints = await self.store.pour(serveur_id).modules_eteints()
        self.tree.clear_commands(guild=guild)
        for commande in self.commandes_du_menu(eteints):
            self.tree.add_command(commande, guild=guild)
        try:
            await self.tree.sync(guild=guild)
        except Exception:
            log.warning(
                "Menu du serveur %s construit mais non synchronisé.",
                serveur_id,
                exc_info=True,
            )
            return False
        return True

    async def synchroniser_les_menus(self, serveurs_ids: Iterable) -> None:
        """Le menu de chaque serveur, un par un.

        Un serveur dont la poussée échoue n'empêche pas les suivants : c'est la
        même règle que partout ailleurs ici, une panne chez l'un ne fait pas
        taire les autres.
        """
        for serveur_id in serveurs_ids:
            await self.synchroniser_le_menu(serveur_id)

    # --- Cœur partagé par /promos, l'aperçu et la publication --------------

    def decrire_source(self) -> str:
        """Provenance des données, pour `/reglages voir`. Jamais la clé d'API."""
        return decrire(self.source)

    async def charger(self):
        """L'export du jeu, lu et découpé — et ses types retenus au passage.

        La mémoire des types sert aux propositions de `/promos types` : Discord
        n'accorde que trois secondes à une frappe, ce qui exclut de charger
        l'export à chaque lettre tapée. Écrite ici plutôt qu'au réglage, elle
        suit d'elle-même un type que le jeu ajouterait.

        Une panne de base n'y fait rien tomber : c'est un cache, et il ne doit
        jamais coûter un post du soir.
        """
        texte = await self.source.fetch()
        meta, batiments = parse_csv(texte)
        try:
            await self.store.memoriser_types(types_disponibles(batiments))
        except Exception:
            log.warning("Impossible de mémoriser les types de l'export.", exc_info=True)
        return meta, batiments

    async def construire_publication(
        self,
        prix_min: Decimal,
        prix_max: Decimal,
        donnees: tuple[Meta, list[Building]] | None = None,
        tolere_min: Decimal | None = None,
        tolere_max: Decimal | None = None,
        magasin=None,
        plafond: int | None = None,
    ) -> tuple[list[dict], str, str]:
        """Renvoie (embeds, contenu, message de repli si aucune promo).

        `donnees` permet de réutiliser un export déjà chargé : avec plusieurs
        fourchettes, recharger à chaque tour multiplierait les appels à l'API du
        jeu pour des données identiques.

        `tolere_min`/`tolere_max` décrivent la zone de tolérance de la
        fourchette, où l'on cherche avant de repêcher au hasard de la distance.

        `magasin` désigne la configuration qui **habille** le post : son template
        et son fuseau. Sans lui, la configuration commune — le site de contrôle ne
        dit pas de quel serveur il parle. Deux entreprises n'ont pas la même
        charte, et c'est tout l'intérêt d'un template par serveur : lu dans le
        commun, il ne changerait jamais rien à ce qui sort.

        C'est aussi lui qui dit quels **types** de bâtiments sont écartés. Posé
        ici, le filtre vaut du même coup pour le post du soir, l'aperçu et
        `/promos chercher` : filtré à la seule publication, l'aperçu montrerait
        des promotions qui ne sortiront pas.

        `plafond` limite le nombre de promotions montrées. Il vient en argument et
        non du magasin, contrairement aux types écartés : il appartient à la
        fourchette et l'appelant sait laquelle il rend, quand cette fonction, elle,
        ne reçoit que des bornes.
        """
        magasin = self.store if magasin is None else magasin
        meta, batiments = donnees if donnees is not None else await self.charger()
        promos = find_promos(
            batiments, prix_min, prix_max,
            tolere_min=tolere_min, tolere_max=tolere_max,
            types_exclus=await magasin.types_exclus(),
            plafond=plafond,
        )
        modele = await magasin.template()
        date = maintenant_local((await magasin.config())["fuseau"]).strftime("%Y-%m-%d")

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
        """Les promotions depuis la configuration **commune**, si c'est l'heure.

        La tournée quotidienne ne passe plus par là : `publier_tout` la fait une
        fois par serveur. Le nom reste pour le site de contrôle, qui ne dit pas de
        quel serveur il parle et continue de lire la configuration commune.
        """
        return await self.faire_publication(module_promos.PUBLICATION, forcer=forcer)

    async def publier_filiales_si_lheure(self, forcer: bool = False) -> str:
        """Le tableau des frais, depuis la configuration commune. Même rôle."""
        return await self.faire_publication(
            module_frais.PUBLICATION, forcer=forcer
        )

    async def faire_publication(
        self, publication, magasin=None, forcer: bool = False
    ) -> str:
        """Une publication quelconque, la même mécanique pour toutes.

        `magasin` désigne la configuration à lire : la vue d'un serveur pour la
        tournée quotidienne, le magasin commun sans lui — le site de contrôle ne
        dit pas de quel serveur il parle.

        Le fuseau vient de la configuration lue, et non de l'horloge du serveur :
        Render tourne en UTC, où « 09:00 » n'est pas la même heure qu'à Paris. De
        celle-ci et non de la commune : deux entreprises peuvent vivre dans deux
        fuseaux, et l'heure réglée dans un serveur n'aurait pas de sens dans
        l'autre.
        """
        magasin = self.store if magasin is None else magasin
        config = await magasin.config()
        maintenant = maintenant_local(config["fuseau"])
        return await faire_la_tournee(
            publication, self, magasin, maintenant, forcer=forcer
        )

    def publications(self, eteints: Iterable[str] = ()) -> list:
        """Les publications déclarées par les modules allumés.

        Lues dans les modules et non écrites en dur : c'est exactement ce qui fait
        qu'un module déclarant une troisième publication la voit partir sans
        qu'on touche à la boucle. L'ordre est celui des modules, donc celui du
        menu.

        `eteints` sont les modules éteints dans le serveur dont on fait la
        tournée : les siennes ne partent plus. Sans ce filtre, `desactiver` ne
        retirerait qu'une commande du menu et le post continuerait de tomber
        chaque jour.
        """
        exclus = set(eteints)
        return [
            publication
            for module in self.modules
            if module.nom not in exclus
            for publication in module.publications
        ]

    async def publier_tout(self, forcer: bool = False) -> str:
        """Le tour complet appelé par `/tick` et la boucle interne.

        Un tour **par serveur**, chacun sur sa propre configuration : c'est ce qui
        donne à chaque entreprise ses fourchettes, son heure, ses salons et sa
        trace de « déjà publié ». Un serveur qui n'a rien réglé ne publie nulle
        part — il n'y a pas de repli sur la configuration commune, et
        `/reglages importer` est le pont.

        Chaque publication est **isolée**, et isolée par serveur : la panne de
        l'export du jeu ne doit pas faire taire un tableau dont les données sont
        saisies à la main, ni les autres entreprises. Chaque panne reste dans le
        compte rendu — avalée en silence, on croirait que tout est normal.
        """
        if not self.guilds:
            # Une chaîne vide se lirait comme une panne du service dans la réponse
            # au cron, qui passe toutes les cinq minutes.
            return "aucun serveur"

        comptes = []
        for serveur in self.guilds:
            magasin = self.store.pour(serveur.id)
            # Relu à chaque serveur : c'est là que se lit quels modules sont
            # allumés chez lui.
            publications = self.publications(await magasin.modules_eteints())
            rendus = []
            for publication in publications:
                try:
                    rendus.append(
                        await self.faire_publication(
                            publication, magasin=magasin, forcer=forcer
                        )
                    )
                except Exception as erreur:
                    log.warning(
                        "Publication « %s » impossible dans %s : %s",
                        publication.titre, serveur.id, erreur,
                    )
                    rendus.append(
                        f"{publication.titre} : {type(erreur).__name__} : {erreur}"
                    )
            # « Aucune publication » et non rien : un nom de serveur suivi du vide
            # se lirait comme « tout va bien », alors que tous les modules ont pu
            # être écartés au démarrage.
            comptes.append(
                f"{serveur.name} — " + (" · ".join(rendus) or "aucune publication")
            )
        return " | ".join(comptes)

    # --- Journal : un observateur ne doit jamais bloquer l'essentiel --------

    async def journaliser_publication(
        self,
        promos: int,
        reussis: list[str],
        echecs: dict[str, str],
        magasin=None,
    ) -> None:
        """Publique : c'est par là que le moteur de tournée rend ses comptes.

        `magasin` est la configuration dont la tournée sort, donc celle qui dit
        dans quel salon de logs raconter. Un serveur a le sien : raconter la
        tournée de l'un dans le salon de l'autre mélangerait deux entreprises dans
        un même fil, et donnerait à chacune les ids de salons de l'autre.

        La panne est avalée ici, à l'unique endroit qui appelle le journal — un
        observateur ne doit jamais bloquer ce qu'il observe, et dupliquer la garde
        dans le moteur en ferait deux à maintenir.
        """
        # Un journal à part pour la vue d'un serveur seulement : `self.journal` est
        # celui de la configuration commune, et c'est lui qui doit continuer de
        # servir le site de contrôle et les modules refusés au démarrage.
        journal = (
            Journal(self, magasin)
            if getattr(magasin, "serveur_id", None) is not None
            else self.journal
        )
        try:
            await journal.publication(promos=promos, reussis=reussis, echecs=echecs)
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

    async def signaler_les_serveurs_sans_configuration(self) -> None:
        """Nomme les serveurs qui n'ont rien réglé, donc où rien ne sortira.

        Chaque serveur a désormais sa configuration, et il n'y a **pas de repli** :
        un serveur qui n'a rien réglé ne publie nulle part, son journal se tait et
        ses membres autorisés perdent l'accès. Le silence ressemble trait pour
        trait à une panne du bot, et se chercherait des jours.

        Dit dans le journal **commun** : celui du serveur en cause est muet par
        définition, c'est justement sa configuration qui manque. Muette quand tout
        est réglé — un rappel à chaque démarrage apprendrait à ne plus lire ce
        salon, et le vrai signalement passerait avec le reste.
        """
        vierges = [
            serveur for serveur in self.guilds
            if await self.store.pour(serveur.id).vierge()
        ]
        if not vierges:
            return
        lignes = "\n".join(f"• {serveur.name} (`{serveur.id}`)" for serveur in vierges)
        await self.journaliser_erreur(
            f"{len(vierges)} serveur(s) sans configuration : rien n'y sera "
            f"publié.\n{lignes}\n"
            "-# `/reglages importer` reprend la configuration commune, à taper une "
            "fois dans chacun."
        )

    async def journaliser_erreur(self, message: str) -> None:
        """Publique, comme sa voisine : les modules signalent leurs pannes par ici."""
        try:
            await self.journal.erreur(message)
        except Exception:
            log.warning("Journal Discord indisponible.", exc_info=True)
