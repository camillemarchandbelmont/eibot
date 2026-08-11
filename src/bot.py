"""Client Discord : commandes slash et publication quotidienne."""

from __future__ import annotations

import asyncio
# Les paramètres des commandes s'appellent `min` et `max` (ce que Discord
# affiche) : les fonctions natives ne sont donc joignables que par `builtins`.
import builtins
import json
import logging
from decimal import Decimal

import discord
from discord import app_commands

from src import settings
from src.acces import acces_autorise, gere_la_liste
from src.db import Store, bornes_tolerees
from src.filiales import FilialeError, index_de, total_frais
from src.journal import Journal
from src.money import (
    ECHELLE,
    NOMS,
    TAUX_GESTION,
    MoneyError,
    convertir,
    format_money,
    format_money_long,
    frais_de_gestion,
    parse_money,
)
from src.promos import Building, Meta, find_promos, parse_csv
from src.publish import construire_embeds, envoyer, message_aucune_promo
from src.publish_filiales import embed_filiales
from src.schedule import (
    FENETRE_RATTRAPAGE,
    boucle_planning,
    doit_publier,
    maintenant_local,
)
from src.source import (
    URL_API_DEFAUT,
    ApiSource,
    CsvFileSource,
    DataSource,
    SourceError,
    decrire,
    diagnostiquer,
)
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

    # --- Cœur partagé par /promos, /apercu et la publication ---------------

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
        """Appelée par /tick et la boucle interne.

        Chaque fourchette publie son propre post dans ses propres salons.
        L'isolation est à deux niveaux : une fourchette dont tous les salons
        échouent n'empêche pas les suivantes, et un salon cassé ne prive pas les
        autres salons de sa fourchette.

        Renvoie un compte rendu pour la réponse HTTP.
        """
        config = await self.store.config()
        maintenant = maintenant_local(config["fuseau"])

        if not forcer:
            derniere = await self.store.derniere_publication()
            if not doit_publier(maintenant, config["heure"], derniere):
                return "rien à faire"

        fourchettes = await self.store.fourchettes()
        if not fourchettes:
            return "aucune fourchette configurée (/fourchette ajouter)"

        servies = [f for f in fourchettes if f["salons"]]
        if not servies:
            return "aucun salon configuré (/fourchette salon ajouter)"

        # L'export d'abord, une seule fois pour toutes les fourchettes : si
        # l'API est en panne, on lève avant d'avoir touché à Discord, et surtout
        # avant `marquer_publie` — sinon la panne de 09:00 annulerait la
        # publication de toute la journée.
        try:
            donnees = await self.charger()
        except SourceError as erreur:
            await self._journaliser_erreur(str(erreur))
            raise
        except Exception as erreur:
            # Panne non prévue (CSV corrompu) : elle doit rester visible dans
            # Discord, pas seulement dans les logs du serveur.
            await self._journaliser_erreur(f"{type(erreur).__name__} : {erreur}")
            raise

        promos = 0
        reussis: list[str] = []
        echecs: dict[str, str] = {}

        for fourchette in servies:
            try:
                tolere_min, tolere_max = bornes_tolerees(fourchette)
                embeds, contenu, repli = await self.construire_publication(
                    Decimal(fourchette["prix_min"]),
                    Decimal(fourchette["prix_max"]),
                    donnees=donnees,
                    tolere_min=tolere_min,
                    tolere_max=tolere_max,
                )
            except Exception as erreur:
                # Rendu impossible pour *cette* fourchette (template appliqué à
                # des valeurs inattendues) : les autres doivent quand même
                # partir, alors qu'une panne de l'export les condamnait toutes.
                log.warning("Rendu impossible pour « %s » : %s", fourchette["nom"], erreur)
                await self._journaliser_erreur(
                    f"Fourchette « {fourchette['nom']} » : "
                    f"{type(erreur).__name__} : {erreur}"
                )
                continue

            promos += 0 if repli else len(embeds)

            for salon_id in fourchette["salons"]:
                try:
                    salon = await self.resoudre_salon(salon_id)
                    if repli:
                        await salon.send(repli)
                    else:
                        # Le rôle du serveur **du salon**, et non un rôle global :
                        # un rôle n'existe que dans son serveur, et `<@&123>`
                        # envoyé ailleurs s'affiche en `@deleted-role`.
                        serveur = getattr(salon, "guild", None)
                        role_id = await self.store.role_du_serveur(
                            getattr(serveur, "id", None)
                        )
                        await envoyer(salon, embeds, contenu, role_id)
                except Exception as erreur:
                    # Un salon cassé ne doit pas priver les autres : on note et
                    # on continue. Le détail part dans le salon de logs.
                    log.warning("Publication impossible dans %s : %s", salon_id, erreur)
                    # La fourchette est nommée : un même salon peut servir deux
                    # fourchettes, et « <#111> a échoué » serait ambigu.
                    echecs[f"<#{salon_id}> ({fourchette['nom']})"] = (
                        f"{type(erreur).__name__}: {erreur}"
                    )
                else:
                    reussis.append(f"<#{salon_id}> ({fourchette['nom']})")

        await self._journaliser_publication(promos, reussis, echecs)

        if not reussis:
            log.error("Publication échouée dans les %d envois.", len(echecs))
            return f"échec dans les {len(echecs)} envoi(s)"

        # Marqué dès qu'un salon a reçu le post : sinon le passage suivant
        # reposterait là où ça avait marché.
        await self.store.marquer_publie(maintenant.strftime("%Y-%m-%d"))
        total = sum(len(f["salons"]) for f in servies)
        log.info(
            "Publication effectuée (%d/%d envois, %d fourchettes).",
            len(reussis), total, len(servies),
        )
        # « Envois » et non « salons » : un salon servant deux fourchettes reçoit
        # deux posts, et le compter une fois annoncerait moins que ce qui est
        # parti.
        return (
            f"publié ({len(reussis)}/{total} envois, "
            f"{len(servies)} fourchette{'s' if len(servies) > 1 else ''})"
        )

    async def publier_filiales_si_lheure(self, forcer: bool = False) -> str:
        """Le tableau des frais, une fois par jour, à son heure et dans ses salons.

        Ne touche **pas** à l'export du jeu : les relevés sont saisis à la main,
        et une API en panne ne doit pas empêcher le tableau de sortir. C'est
        toute la raison d'en faire une publication séparée plutôt qu'un embed de
        plus dans celle des promotions.

        Renvoie un compte rendu pour la réponse HTTP.
        """
        config = await self.store.config()
        maintenant = maintenant_local(config["fuseau"])

        if not forcer:
            derniere = await self.store.derniere_publication_filiales()
            if not doit_publier(maintenant, await self.store.heure_filiales(), derniere):
                return "rien à faire"

        salons = await self.store.salons_filiales()
        if not salons:
            return "aucun salon pour le tableau des frais (/filiales salon ajouter)"

        aujourdhui = maintenant.strftime("%Y-%m-%d")
        # Publié même vide : l'absence de post ne se distinguerait pas d'une
        # panne du bot, et l'embed vide dit comment le remplir.
        filiales = await self.store.filiales()
        embed = embed_filiales(filiales, aujourdhui)

        reussis: list[str] = []
        echecs: dict[str, str] = {}

        for salon_id in salons:
            try:
                salon = await self.resoudre_salon(salon_id)
                await salon.send(embed=embed)
            except Exception as erreur:
                # Un salon cassé ne prive pas les autres, comme pour les promos.
                log.warning("Tableau des frais impossible dans %s : %s", salon_id, erreur)
                echecs[f"<#{salon_id}> (filiales)"] = f"{type(erreur).__name__}: {erreur}"
            else:
                reussis.append(f"<#{salon_id}> (filiales)")

        await self._journaliser_publication(len(filiales), reussis, echecs)

        if not reussis:
            log.error("Tableau des frais échoué dans les %d envois.", len(echecs))
            return f"tableau des frais : échec dans les {len(echecs)} envoi(s)"

        # Marqué dès qu'un salon a reçu le tableau : sinon le passage suivant
        # reposterait là où ça avait marché.
        await self.store.marquer_publie_filiales(aujourdhui)
        log.info(
            "Tableau des frais publié (%d/%d envois, %d filiales).",
            len(reussis), len(salons), len(filiales),
        )
        return (
            f"tableau des frais publié ({len(reussis)}/{len(salons)} envois, "
            f"{len(filiales)} filiale{'s' if len(filiales) > 1 else ''})"
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

    async def _journaliser_publication(
        self, promos: int, reussis: list[str], echecs: dict[str, str]
    ) -> None:
        try:
            await self.journal.publication(promos=promos, reussis=reussis, echecs=echecs)
        except Exception:
            log.warning("Journal Discord indisponible.", exc_info=True)

    async def _journaliser_erreur(self, message: str) -> None:
        try:
            await self.journal.erreur(message)
        except Exception:
            log.warning("Journal Discord indisponible.", exc_info=True)


# --- Commandes --------------------------------------------------------------

def _administrateur(interaction: discord.Interaction) -> bool:
    """Vrai si l'auteur est administrateur du serveur.

    `ArbreProtege` laisse déjà passer les membres autorisés : ce contrôle
    supplémentaire ne sert qu'à la gestion de la liste d'accès elle-même, pour
    qu'un membre autorisé ne puisse ni s'ajouter des complices ni retirer celui
    qui l'a nommé.
    """
    permissions = getattr(interaction.user, "guild_permissions", None)
    return gere_la_liste(est_admin=bool(permissions and permissions.administrator))


def _permissions_manquantes(
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


def _lister_salons(bot: EmpireBot, salons: list[str]) -> str:
    """Liste à puces des salons, avec un ⚠️ sur ceux devenus inaccessibles."""
    if not salons:
        return "*Aucun salon configuré.* Le post quotidien ne sortira pas."

    lignes = []
    for salon_id in salons:
        introuvable = bot.get_channel(int(salon_id)) is None
        suffixe = " ⚠️ introuvable" if introuvable else ""
        lignes.append(f"• <#{salon_id}>{suffixe}")
    return "\n".join(lignes)


def _lister_fourchettes(bot: EmpireBot, fourchettes: list[dict]) -> str:
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


class _AucuneFourchette(Exception):
    """Rien n'est configuré : le message porte la commande qui y remédie."""


async def _bornes_demandees(
    bot: EmpireBot, min: str | None, max: str | None
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
            raise _AucuneFourchette(
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


def _heure_valide(heure: str) -> str | None:
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


def _aide_montants() -> str:
    return (
        "Formats acceptés : `840`, `12,25M`, `100T`, `6P`, `50 6P`, `2,71 PØ`.\n"
        "Symboles : K M G T P E Z Y R Q U S X N D."
    )


def _choix_symboles() -> list[app_commands.Choice]:
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


def enregistrer_commandes(bot: EmpireBot) -> None:
    tree = bot.tree

    # --- /convertir et /frais : deux calculatrices, sans état -------------

    @tree.command(
        name="convertir", description="Exprime un montant dans un autre palier (P → T, Z → M…)"
    )
    @app_commands.describe(
        montant="Montant de départ (ex: 2,71P, 50 6P, 840)",
        vers="Palier d'arrivée",
    )
    @app_commands.choices(vers=_choix_symboles())
    async def convertir_commande(
        interaction: discord.Interaction, montant: str, vers: str
    ):
        try:
            valeur = parse_money(montant)
        except MoneyError as erreur:
            await interaction.response.send_message(
                f"❌ {erreur}\n{_aide_montants()}", ephemeral=True
            )
            return

        try:
            rendu = convertir(valeur, vers)
        except MoneyError as erreur:
            await interaction.response.send_message(f"❌ {erreur}", ephemeral=True)
            return

        # Le montant de départ est rappelé sous sa forme comprise : c'est le
        # seul moyen de vérifier que `50 6P` a bien été lu comme 506 PØ.
        await interaction.response.send_message(
            f"**{format_money(valeur)}** = **{rendu}**\n"
            f"-# {format_money_long(valeur)}",
            ephemeral=True,
        )

    async def _completer_filiale(
        interaction: discord.Interaction, saisie: str
    ) -> list[app_commands.Choice[str]]:
        """Propose les filiales déjà saisies.

        Le nom est la clé du jeu : retapé de mémoire, une faute de frappe
        créerait une **seconde** filiale au lieu de mettre la première à jour, et
        le tableau compterait deux fois la même.
        """
        debut = saisie.strip().casefold()
        return [
            app_commands.Choice(name=f.nom, value=f.nom)
            for f in await bot.store.filiales()
            if debut in f.nom.casefold()
        ][:25]  # limite Discord

    @tree.command(
        name="frais", description="Frais de gestion sur un montant (7 %, sans décimales)"
    )
    @app_commands.describe(
        montant="Montant sur lequel calculer (ex: 2,71P, 100T)",
        filiale="Nom de la filiale : enregistre le relevé pour le tableau du jour",
    )
    @app_commands.autocomplete(filiale=_completer_filiale)
    async def frais_commande(
        interaction: discord.Interaction, montant: str, filiale: str | None = None
    ):
        """Une commande, deux usages.

        Sans `filiale`, c'est la calculatrice : elle n'écrit rien. Avec, le
        montant est compris comme les **bénéfices** de cette filiale, et le
        relevé rejoint le tableau quotidien.

        Une seule commande plutôt qu'un groupe : c'est le même calcul, et
        `/filiales frais` obligerait à réapprendre où taper 7 %.
        """
        try:
            valeur = parse_money(montant)
        except MoneyError as erreur:
            # Avant tout enregistrement : une filiale retenue à un montant faux
            # figurerait dans le tableau et fausserait le total.
            await interaction.response.send_message(
                f"❌ {erreur}\n{_aide_montants()}", ephemeral=True
            )
            return

        if filiale is not None:
            await _enregistrer_frais(interaction, valeur, filiale)
            return

        frais = frais_de_gestion(valeur)
        # Les deux formes : la courte pour lire, la longue pour recopier dans le
        # jeu — on ne paie pas « 189,70 TØ ».
        await interaction.response.send_message(
            f"Frais de gestion sur **{format_money(valeur)}** "
            f"({TAUX_GESTION.normalize():f} %) :\n"
            f"**{format_money(frais)}**\n"
            f"-# {format_money_long(frais)}",
            ephemeral=True,
        )

    async def _enregistrer_frais(
        interaction: discord.Interaction, benefices: Decimal, nom: str
    ) -> None:
        """Calcule, enregistre et rend compte — éphémère comme la calculatrice.

        Les résultats de l'entreprise n'ont pas à s'afficher dans le salon :
        seul le tableau du jour est public.
        """
        existait = index_de(await bot.store.filiales(), nom) >= 0
        aujourdhui = maintenant_local((await bot.store.config())["fuseau"]).strftime(
            "%Y-%m-%d"
        )

        try:
            releve = await bot.store.enregistrer_filiale(nom, benefices, aujourdhui)
        except FilialeError as erreur:
            # Discord accepte une chaîne d'espaces : la ligne serait anonyme.
            await interaction.response.send_message(f"❌ {erreur}", ephemeral=True)
            return

        filiales = await bot.store.filiales()
        verbe = "mise à jour" if existait else "enregistrée"

        if releve.en_perte:
            # Le jeu ne rembourse pas : dit explicitement, sinon un 0 Ø se
            # lirait comme une saisie ratée.
            corps = (
                f"**{releve.nom}** {verbe} : "
                f"**{format_money(releve.benefices)}** de bénéfices, "
                f"donc **rien à payer** (en perte)."
            )
        else:
            corps = (
                f"**{releve.nom}** {verbe} : "
                f"{format_money(releve.benefices)} de bénéfices "
                f"→ **{format_money(releve.frais)}** de frais "
                f"({TAUX_GESTION.normalize():f} %).\n"
                f"-# {format_money_long(releve.frais)}"
            )

        total = total_frais(filiales)
        corps += (
            f"\nTotal des {len(filiales)} filiale{'s' if len(filiales) > 1 else ''} : "
            f"**{format_money(total)}**\n"
            f"-# {format_money_long(total)}"
        )

        if not await bot.store.salons_filiales():
            # Une saisie qui n'ira nulle part doit se voir maintenant, pas au
            # moment où l'on s'étonne de ne rien recevoir.
            corps += "\n⚠️ Aucun salon pour le tableau : `/filiales salon ajouter`."

        await interaction.response.send_message(corps, ephemeral=True)

    # --- /promos ----------------------------------------------------------

    @tree.command(name="promos", description="Meilleures promotions dans une fourchette de prix")
    @app_commands.describe(
        min="Prix minimum (ex: 100T). Par défaut : la fourchette configurée.",
        max="Prix maximum (ex: 6P).",
    )
    async def promos(
        interaction: discord.Interaction,
        min: str | None = None,
        max: str | None = None,
    ):
        await interaction.response.defer()
        try:
            prix_min, prix_max = await _bornes_demandees(bot, min, max)
        except MoneyError as erreur:
            await interaction.followup.send(f"❌ {erreur}\n{_aide_montants()}", ephemeral=True)
            return
        except _AucuneFourchette as erreur:
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
            value=_lister_fourchettes(bot, fourchettes),
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

    @config_groupe.command(name="heure", description="Heure du post quotidien")
    @app_commands.describe(heure="Format HH:MM", fuseau="Ex: Europe/Paris")
    async def config_heure(
        interaction: discord.Interaction, heure: str, fuseau: str | None = None
    ):
        heure_propre = _heure_valide(heure)
        if heure_propre is None:
            await interaction.response.send_message(
                "❌ Heure invalide. Format attendu : `HH:MM` (ex: `09:00`).", ephemeral=True
            )
            return

        if fuseau:
            from zoneinfo import ZoneInfo
            try:
                ZoneInfo(fuseau)
            except Exception:
                await interaction.response.send_message(
                    f"❌ Fuseau inconnu : `{fuseau}`. Ex : `Europe/Paris`.", ephemeral=True
                )
                return

        config = await bot.store.maj_config(heure=heure_propre, fuseau=fuseau)

        # Changer l'heure exprime l'intention de publier à la nouvelle heure :
        # on oublie la marque du jour, sinon un post déjà sorti (ou rattrapé à
        # l'ancienne heure) bloquerait ce nouvel horaire jusqu'à demain.
        await bot.store.oublier_publication()

        maintenant = maintenant_local(config["fuseau"])
        message = (
            f"✅ Publication quotidienne à **{config['heure']}** ({config['fuseau']}).\n"
            f"-# Il est {maintenant.strftime('%H:%M')}."
        )
        if not doit_publier(maintenant, config["heure"], None):
            attendu = "aujourd'hui" if maintenant.strftime("%H:%M") < config["heure"] else "demain"
            message += f" Prochain post {attendu}."
        else:
            message += " Publication imminente (dans la minute)."

        await interaction.response.send_message(message, ephemeral=True)

    # --- /fourchette ------------------------------------------------------
    #
    # Remplace `/config prix` et `/config salon` : ceux-ci ne pouvaient plus
    # rien signifier sans dire *de quelle* fourchette il s'agit. Une commande
    # qui agit sur une cible implicite est exactement ce qui fait publier au
    # mauvais endroit.

    fourchette_groupe = app_commands.Group(
        name="fourchette", description="Fourchettes de prix et leurs salons"
    )

    async def _completer_nom(
        interaction: discord.Interaction, saisie: str
    ) -> list[app_commands.Choice[str]]:
        """Propose les fourchettes existantes.

        Sans ça le nom serait retapé à chaque commande, et une faute de frappe
        ne se verrait qu'au message d'erreur.
        """
        debut = saisie.strip().casefold()
        return [
            app_commands.Choice(name=f["nom"], value=f["nom"])
            for f in await bot.store.fourchettes()
            if debut in f["nom"].casefold()
        ][:25]  # limite Discord

    async def _refuser_nom_inconnu(interaction: discord.Interaction, nom: str) -> None:
        """Refuse en listant les noms valides.

        Sans la liste, impossible de savoir si c'est une faute de frappe ou une
        fourchette jamais créée.
        """
        noms = [f["nom"] for f in await bot.store.fourchettes()]
        connues = ", ".join(f"`{n}`" for n in noms) if noms else "*aucune*"
        await interaction.response.send_message(
            f"❌ Aucune fourchette nommée « {nom} ». Fourchettes : {connues}.",
            ephemeral=True,
        )

    @fourchette_groupe.command(name="ajouter", description="Crée une fourchette de prix")
    @app_commands.describe(
        nom="Nom court, ex: grosses-affaires",
        min="Prix minimum (ex: 100T)",
        max="Prix maximum (ex: 6P)",
    )
    async def fourchette_ajouter(
        interaction: discord.Interaction, nom: str, min: str, max: str
    ):
        try:
            prix_min, prix_max = parse_money(min), parse_money(max)
        except MoneyError as erreur:
            await interaction.response.send_message(
                f"❌ {erreur}\n{_aide_montants()}", ephemeral=True
            )
            return

        try:
            fourchette = await bot.store.ajouter_fourchette(nom, prix_min, prix_max)
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

    @fourchette_groupe.command(name="supprimer", description="Supprime une fourchette")
    @app_commands.autocomplete(nom=_completer_nom)
    async def fourchette_supprimer(interaction: discord.Interaction, nom: str):
        if not await bot.store.supprimer_fourchette(nom):
            await _refuser_nom_inconnu(interaction, nom)
            return

        restantes = await bot.store.fourchettes()
        message = f"✅ Fourchette **{nom.strip()}** supprimée."
        if not restantes:
            message += "\n⚠️ Plus aucune fourchette : le post quotidien ne sortira plus."
        await interaction.response.send_message(message, ephemeral=True)

    @fourchette_groupe.command(name="prix", description="Modifie les bornes d'une fourchette")
    @app_commands.describe(min="Prix minimum (ex: 100T)", max="Prix maximum (ex: 6P)")
    @app_commands.autocomplete(nom=_completer_nom)
    async def fourchette_prix(
        interaction: discord.Interaction, nom: str, min: str, max: str
    ):
        try:
            prix_min, prix_max = parse_money(min), parse_money(max)
        except MoneyError as erreur:
            await interaction.response.send_message(
                f"❌ {erreur}\n{_aide_montants()}", ephemeral=True
            )
            return

        avant = await bot.store.fourchettes()
        index_avant = bot.store._index(avant, nom)
        zone_avant = (
            bornes_tolerees(avant[index_avant]) if index_avant >= 0 else (None, None)
        )

        if not await bot.store.majprix_fourchette(nom, prix_min, prix_max):
            await _refuser_nom_inconnu(interaction, nom)
            return

        if prix_min > prix_max:
            prix_min, prix_max = prix_max, prix_min
        message = (
            f"✅ **{nom.strip()}** : **{format_money(prix_min)}** → "
            f"**{format_money(prix_max)}**"
        )

        # Les nouvelles bornes ont pu repousser la zone de tolérance. Le taire
        # laisserait croire qu'elle est restée là où on l'avait réglée.
        apres = await bot.store.fourchettes()
        zone_apres = bornes_tolerees(apres[bot.store._index(apres, nom)])
        if zone_apres != zone_avant and zone_apres[0] is not None:
            message += (
                f"\n-# Zone de tolérance élargie d'autant : "
                f"{format_money(zone_apres[0])} → {format_money(zone_apres[1])}"
            )

        await interaction.response.send_message(message, ephemeral=True)

    @fourchette_groupe.command(
        name="tolerance",
        description="Zone acceptée quand la fourchette est trop pauvre (sans bornes : efface)",
    )
    @app_commands.describe(
        min="Prix minimum toléré (ex: 50T) ; laisser vide pour effacer la zone",
        max="Prix maximum toléré (ex: 8P) ; laisser vide pour effacer la zone",
    )
    @app_commands.autocomplete(nom=_completer_nom)
    async def fourchette_tolerance(
        interaction: discord.Interaction,
        nom: str,
        min: str | None = None,
        max: str | None = None,
    ):
        """Règle ou efface la zone de tolérance d'une fourchette.

        Les deux bornes ou aucune : une seule ne décrit pas une plage, et
        `find_promos` ignorerait la zone à moitié réglée — la commande aurait
        alors confirmé un réglage sans effet.
        """
        if (min is None) != (max is None):
            await interaction.response.send_message(
                "❌ Donne les **deux** bornes, ou aucune pour effacer la zone.\n"
                "-# `/fourchette tolerance nom:… min:50T max:8P`",
                ephemeral=True,
            )
            return

        if min is None:
            if not await bot.store.effacer_tolerance_fourchette(nom):
                fourchettes = await bot.store.fourchettes()
                if bot.store._index(fourchettes, nom) < 0:
                    await _refuser_nom_inconnu(interaction, nom)
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
                f"❌ {erreur}\n{_aide_montants()}", ephemeral=True
            )
            return

        try:
            regle = await bot.store.majtolerance_fourchette(nom, tolere_min, tolere_max)
        except ValueError as erreur:
            await interaction.response.send_message(f"❌ {erreur}", ephemeral=True)
            return

        if not regle:
            await _refuser_nom_inconnu(interaction, nom)
            return

        if tolere_min > tolere_max:
            tolere_min, tolere_max = tolere_max, tolere_min
        await interaction.response.send_message(
            f"✅ **{nom.strip()}** tolère **{format_money(tolere_min)}** → "
            f"**{format_money(tolere_max)}**.\n"
            "-# Cherché là en priorité quand la fourchette n'a pas assez de promos.",
            ephemeral=True,
        )

    @fourchette_groupe.command(name="liste", description="Liste les fourchettes et leurs salons")
    async def fourchette_liste(interaction: discord.Interaction):
        fourchettes = await bot.store.fourchettes()
        embed = discord.Embed(
            title="Fourchettes de prix",
            description=_lister_fourchettes(bot, fourchettes),
            color=0x5865F2,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    fourchette_salon_groupe = app_commands.Group(
        name="salon",
        description="Salons d'une fourchette",
        parent=fourchette_groupe,
    )

    @fourchette_salon_groupe.command(
        name="ajouter", description="Publie cette fourchette dans un salon"
    )
    @app_commands.autocomplete(nom=_completer_nom)
    async def fourchette_salon_ajouter(
        interaction: discord.Interaction, nom: str, salon: discord.TextChannel
    ):
        # Vérifié tout de suite : sinon l'erreur n'apparaîtrait qu'à l'heure du
        # post, le lendemain.
        manquantes = _permissions_manquantes(interaction, salon)
        if manquantes:
            await interaction.response.send_message(
                f"❌ Je n'ai pas la permission {manquantes} dans {salon.mention}.\n"
                f"-# Ajoute-la puis relance la commande.",
                ephemeral=True,
            )
            return

        if bot.store._index(await bot.store.fourchettes(), nom) < 0:
            await _refuser_nom_inconnu(interaction, nom)
            return

        if not await bot.store.ajouter_salon_fourchette(nom, str(salon.id)):
            await interaction.response.send_message(
                f"ℹ️ {salon.mention} reçoit déjà **{nom.strip()}**.", ephemeral=True
            )
            return

        # Mémorisé pour le site, qui n'a pas accès à Discord et ne pourrait
        # afficher qu'un id nu.
        await bot.store.memoriser_salon(
            str(salon.id), salon.name, str(interaction.guild.id), interaction.guild.name
        )

        await interaction.response.send_message(
            f"✅ **{nom.strip()}** sera publiée dans {salon.mention}.", ephemeral=True
        )

    @fourchette_salon_groupe.command(
        name="retirer", description="Ne plus publier cette fourchette dans un salon"
    )
    @app_commands.autocomplete(nom=_completer_nom)
    async def fourchette_salon_retirer(
        interaction: discord.Interaction, nom: str, salon: discord.TextChannel
    ):
        if bot.store._index(await bot.store.fourchettes(), nom) < 0:
            await _refuser_nom_inconnu(interaction, nom)
            return

        if not await bot.store.retirer_salon_fourchette(nom, str(salon.id)):
            await interaction.response.send_message(
                f"❌ **{nom.strip()}** n'était pas publiée dans {salon.mention}.",
                ephemeral=True,
            )
            return

        # Le salon n'est peut-être plus servi par aucune fourchette : son nom
        # n'a alors plus à occuper la config.
        await bot.store.oublier_salons_orphelins()

        await interaction.response.send_message(
            f"✅ **{nom.strip()}** ne sera plus publiée dans {salon.mention}.",
            ephemeral=True,
        )

    tree.add_command(fourchette_groupe)

    # --- /filiales --------------------------------------------------------
    #
    # Les réglages du tableau et son entretien — jamais l'ajout, qui appartient à
    # `/frais` : le calcul et l'enregistrement sont le même geste, et les séparer
    # obligerait à taper deux commandes pour une filiale.

    filiales_groupe = app_commands.Group(
        name="filiales", description="Tableau des frais de gestion par filiale"
    )

    @filiales_groupe.command(name="liste", description="Filiales enregistrées et total")
    async def filiales_liste(interaction: discord.Interaction):
        filiales = await bot.store.filiales()
        aujourdhui = maintenant_local((await bot.store.config())["fuseau"]).strftime(
            "%Y-%m-%d"
        )
        await interaction.response.send_message(
            embed=embed_filiales(filiales, aujourdhui), ephemeral=True
        )

    @filiales_groupe.command(name="retirer", description="Oublie une filiale")
    @app_commands.describe(filiale="Nom de la filiale à retirer du tableau")
    @app_commands.autocomplete(filiale=_completer_filiale)
    async def filiales_retirer(interaction: discord.Interaction, filiale: str):
        if not await bot.store.retirer_filiale(filiale):
            # Listées : sinon on ne sait pas si c'est une faute de frappe ou une
            # filiale jamais saisie.
            noms = [f.nom for f in await bot.store.filiales()]
            connues = ", ".join(f"`{n}`" for n in noms) if noms else "*aucune*"
            await interaction.response.send_message(
                f"❌ Aucune filiale nommée « {filiale.strip()} ». Filiales : {connues}.",
                ephemeral=True,
            )
            return

        restantes = await bot.store.filiales()
        await interaction.response.send_message(
            f"✅ **{filiale.strip()}** retirée du tableau.\n"
            f"-# Reste {len(restantes)} filiale(s), "
            f"{format_money(total_frais(restantes))} de frais.",
            ephemeral=True,
        )

    @filiales_groupe.command(name="heure", description="Heure du tableau des frais")
    @app_commands.describe(heure="Format HH:MM")
    async def filiales_heure(interaction: discord.Interaction, heure: str):
        heure_propre = _heure_valide(heure)
        if heure_propre is None:
            await interaction.response.send_message(
                "❌ Heure invalide. Format attendu : `HH:MM` (ex: `20:30`).",
                ephemeral=True,
            )
            return

        # `filiales_heure` et non `heure` : deux posts, deux horaires. Régler
        # l'un en déplaçant l'autre serait une surprise découverte le lendemain.
        config = await bot.store.maj_config(filiales_heure=heure_propre)

        # Comme pour `/config heure` : régler l'heure exprime l'intention de
        # publier à la nouvelle heure, et un post déjà sorti la bloquerait
        # jusqu'à demain.
        await bot.store.oublier_publication_filiales()

        maintenant = maintenant_local(config["fuseau"])
        message = (
            f"✅ Tableau des frais publié à **{heure_propre}** ({config['fuseau']}).\n"
            f"-# Il est {maintenant.strftime('%H:%M')}."
        )
        if not doit_publier(maintenant, heure_propre, None):
            attendu = (
                "aujourd'hui" if maintenant.strftime("%H:%M") < heure_propre else "demain"
            )
            message += f" Prochain tableau {attendu}."
        else:
            message += " Publication imminente (dans la minute)."

        await interaction.response.send_message(message, ephemeral=True)

    filiales_salon_groupe = app_commands.Group(
        name="salon",
        description="Salons où publier le tableau des frais",
        parent=filiales_groupe,
    )

    @filiales_salon_groupe.command(
        name="ajouter", description="Publie le tableau des frais dans un salon"
    )
    async def filiales_salon_ajouter(
        interaction: discord.Interaction, salon: discord.TextChannel
    ):
        # Vérifié au réglage : une permission manquante découverte à l'heure du
        # post est un tableau perdu.
        manquantes = _permissions_manquantes(interaction, salon)
        if manquantes:
            await interaction.response.send_message(
                f"❌ Je n'ai pas la permission {manquantes} dans {salon.mention}.\n"
                f"-# Ajoute-la puis relance la commande.",
                ephemeral=True,
            )
            return

        if not await bot.store.ajouter_salon_filiales(str(salon.id)):
            await interaction.response.send_message(
                f"ℹ️ {salon.mention} reçoit déjà le tableau des frais.", ephemeral=True
            )
            return

        # Mémorisé pour le site, qui n'a pas accès à Discord et ne pourrait
        # afficher qu'un id nu.
        await bot.store.memoriser_salon(
            str(salon.id), salon.name, str(interaction.guild.id), interaction.guild.name
        )

        await interaction.response.send_message(
            f"✅ Le tableau des frais sera publié dans {salon.mention} à "
            f"**{await bot.store.heure_filiales()}**.",
            ephemeral=True,
        )

    @filiales_salon_groupe.command(
        name="retirer", description="Ne plus publier le tableau dans un salon"
    )
    async def filiales_salon_retirer(
        interaction: discord.Interaction, salon: discord.TextChannel
    ):
        if not await bot.store.retirer_salon_filiales(str(salon.id)):
            await interaction.response.send_message(
                f"❌ Le tableau des frais n'était pas publié dans {salon.mention}.",
                ephemeral=True,
            )
            return

        await bot.store.oublier_salons_orphelins()

        await interaction.response.send_message(
            f"✅ Le tableau des frais ne sera plus publié dans {salon.mention}.",
            ephemeral=True,
        )

    @filiales_groupe.command(
        name="apercu", description="Prévisualise le tableau des frais sans publier"
    )
    async def filiales_apercu(interaction: discord.Interaction):
        """Le tableau tel qu'il sortira, sans consommer le post du jour."""
        filiales = await bot.store.filiales()
        aujourdhui = maintenant_local((await bot.store.config())["fuseau"]).strftime(
            "%Y-%m-%d"
        )
        entete = f"Tableau prévu à **{await bot.store.heure_filiales()}**"
        if not await bot.store.salons_filiales():
            entete += " ⚠️ aucun salon : ne sera pas publié"
        await interaction.response.send_message(
            entete, embed=embed_filiales(filiales, aujourdhui), ephemeral=True
        )

    tree.add_command(filiales_groupe)

    # --- /config (suite) --------------------------------------------------

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
        if not _administrateur(interaction):
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
        if not _administrateur(interaction):
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

        manquantes = _permissions_manquantes(interaction, salon)
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

    @config_groupe.command(
        name="retester",
        description="Oublie la publication du jour pour retester le déclenchement",
    )
    async def config_retester(interaction: discord.Interaction):
        await bot.store.oublier_publication()
        config = await bot.store.config()
        await interaction.response.send_message(
            f"✅ Marque du jour effacée. Le bot republiera à **{config['heure']}** "
            f"({config['fuseau']}).\n"
            f"-# Règle l'heure *avant* de lancer cette commande : le rattrapage "
            f"n'accepte que {FENETRE_RATTRAPAGE} minutes de retard.",
            ephemeral=True,
        )

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
            embed.set_footer(text="Teste la source, pas la fourchette : /apercu pour le post du jour.")
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
            fp=__import__("io").BytesIO(contenu.encode("utf-8")),
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

    # --- /apercu ----------------------------------------------------------

    @tree.command(name="apercu", description="Prévisualise les posts du jour sans publier")
    async def apercu(interaction: discord.Interaction):
        """Un aperçu **par fourchette**, comme la publication.

        Montrer l'union des bornes en un seul post serait plus court et
        mensonger : aucun salon ne recevrait ça.
        """
        await interaction.response.defer(ephemeral=True)
        fourchettes = await bot.store.fourchettes()
        if not fourchettes:
            await interaction.followup.send(
                "❌ Aucune fourchette configurée : rien ne serait publié.\n"
                "-# `/fourchette ajouter nom:… min:… max:…`",
                ephemeral=True,
            )
            return

        # Chargé une fois pour toutes les fourchettes, comme à la publication.
        try:
            donnees = await bot.charger()
        except SourceError as erreur:
            await interaction.followup.send(f"❌ {erreur}", ephemeral=True)
            return

        for fourchette in fourchettes:
            tolere_min, tolere_max = bornes_tolerees(fourchette)
            embeds, contenu, repli = await bot.construire_publication(
                Decimal(fourchette["prix_min"]),
                Decimal(fourchette["prix_max"]),
                donnees=donnees,
                tolere_min=tolere_min,
                tolere_max=tolere_max,
            )

            # Le nom en tête de chaque aperçu : deux posts d'affilée seraient
            # sinon indistinguables.
            entete = f"**{fourchette['nom']}**"
            if not fourchette["salons"]:
                entete += " ⚠️ aucun salon : ne sera pas publiée"
            await interaction.followup.send(entete, ephemeral=True)

            if repli:
                await interaction.followup.send(repli, ephemeral=True)
                continue
            await envoyer(interaction.followup, embeds, contenu, ephemere=True)
