"""Client Discord : commandes slash et publication quotidienne."""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal

import discord
from discord import app_commands

from src import settings
from src.acces import acces_autorise
from src.db import Store
from src.journal import Journal
from src.modules import decouvrir, greffer
from src.modules import filiales as module_filiales
from src.modules import promos as module_promos
from src.promos import Building, Meta, find_promos, parse_csv
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
        règle, donc un membre ajouté par `/reglages acces ajouter` obtient les deux
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
            "-# Un administrateur peut t'ajouter avec `/reglages acces ajouter`.",
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
        """Provenance des données, pour `/reglages voir`. Jamais la clé d'API."""
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
        """Les promotions depuis la configuration **commune**, si c'est l'heure.

        La tournée quotidienne ne passe plus par là : `publier_tout` la fait une
        fois par serveur. Le nom reste pour le site de contrôle, qui ne dit pas de
        quel serveur il parle et continue de lire la configuration commune.
        """
        return await self.faire_publication(module_promos.PUBLICATION, forcer=forcer)

    async def publier_filiales_si_lheure(self, forcer: bool = False) -> str:
        """Le tableau des frais, depuis la configuration commune. Même rôle."""
        return await self.faire_publication(
            module_filiales.PUBLICATION, forcer=forcer
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

    def publications(self) -> list:
        """Toutes les publications déclarées par les modules chargés.

        Lues dans les modules et non écrites en dur : c'est exactement ce qui fait
        qu'un module déclarant une troisième publication la voit partir sans
        qu'on touche à la boucle. L'ordre est celui des modules, donc celui du
        menu.
        """
        return [
            publication
            for module in self.modules
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

        publications = self.publications()
        comptes = []
        for serveur in self.guilds:
            magasin = self.store.pour(serveur.id)
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

    async def journaliser_erreur(self, message: str) -> None:
        """Publique, comme sa voisine : les modules signalent leurs pannes par ici."""
        try:
            await self.journal.erreur(message)
        except Exception:
            log.warning("Journal Discord indisponible.", exc_info=True)
