"""Les commandes doivent apparaître sur **chacun** des serveurs déclarés.

Synchroniser sur un seul serveur ne lève aucune erreur : les commandes sont
simplement absentes ailleurs, ce qu'on ne remarque qu'en les cherchant dans
Discord.
"""

import importlib

import pytest


def _settings_avec(monkeypatch, **variables):
    """Recharge `src.settings` avec ces variables d'environnement.

    Les constantes sont lues à l'import : sans rechargement, `monkeypatch`
    n'aurait aucun effet.
    """
    # GUILD_IDS : si non fournie, la supprimer pour que le repli fonctionne
    if "GUILD_IDS" in variables:
        monkeypatch.setenv("GUILD_IDS", variables["GUILD_IDS"])
    else:
        monkeypatch.delenv("GUILD_IDS", raising=False)

    # GUILD_ID : si non fournie, la vider pour empêcher load_dotenv() de charger depuis .env
    if "GUILD_ID" in variables:
        monkeypatch.setenv("GUILD_ID", variables["GUILD_ID"])
    else:
        monkeypatch.setenv("GUILD_ID", "")

    import src.settings

    return importlib.reload(src.settings)


def test_guild_ids_lit_une_liste(monkeypatch):
    settings = _settings_avec(monkeypatch, GUILD_IDS="111,222")
    assert settings.GUILD_IDS == ["111", "222"]


def test_guild_ids_tolere_les_espaces(monkeypatch):
    """La valeur est recopiée à la main dans Render : « 111, 222 » est probable."""
    settings = _settings_avec(monkeypatch, GUILD_IDS=" 111 , 222 ")
    assert settings.GUILD_IDS == ["111", "222"]


def test_guild_id_seul_sert_de_repli(monkeypatch):
    """Le `.env` local et la variable Render existants ne doivent pas casser."""
    settings = _settings_avec(monkeypatch, GUILD_ID="111")
    assert settings.GUILD_IDS == ["111"]


def test_aucune_variable_donne_une_liste_vide(monkeypatch):
    """Liste vide = synchronisation globale, le comportement d'avant."""
    settings = _settings_avec(monkeypatch)
    assert settings.GUILD_IDS == []


def test_virgule_seule_ne_cree_pas_de_serveur_vide(monkeypatch):
    """`discord.Object(id=int(""))` lèverait au démarrage du bot."""
    settings = _settings_avec(monkeypatch, GUILD_IDS=",")
    assert settings.GUILD_IDS == []


class ArbreFactice:
    """Compte les menus construits et les synchronisations, par serveur."""

    def __init__(self):
        #: Les serveurs dont le menu a été vidé avant d'être rebâti. Un serveur
        #: qui n'y figure pas a reçu la synchronisation d'un menu qui n'est pas
        #: le sien.
        self.menus: list[int] = []
        self.syncs: list[int | None] = []

    def get_commands(self, guild=None):
        return []

    def clear_commands(self, *, guild, type=None):
        self.menus.append(guild.id)

    def add_command(self, commande, guild=None):
        pass

    async def sync(self, guild=None):
        self.syncs.append(guild.id if guild is not None else None)


async def _bot_avec_arbre(monkeypatch, guild_ids: list[str]):
    from src.bot import EmpireBot
    from src import bot as module_bot
    from src.db import Store

    monkeypatch.setattr(module_bot.settings, "GUILD_IDS", guild_ids)

    store = Store(dsn="")
    await store.connect()

    bot = object.__new__(EmpireBot)
    bot.tree = ArbreFactice()
    # Le menu d'un serveur se construit en lisant ses modules éteints : sans
    # magasin, `setup_hook` n'irait pas jusqu'à la synchronisation.
    bot.store = store
    bot.module_des_commandes = {}
    return bot


async def test_synchronise_sur_chaque_serveur(monkeypatch):
    """La propriété qui fait l'intérêt de la liste : deux serveurs, deux syncs."""
    bot = await _bot_avec_arbre(monkeypatch, ["111", "222"])

    await bot.setup_hook()

    assert bot.tree.syncs == [111, 222]
    assert bot.tree.menus == [111, 222]


async def test_liste_vide_synchronise_globalement(monkeypatch):
    bot = await _bot_avec_arbre(monkeypatch, [])

    await bot.setup_hook()

    assert bot.tree.syncs == [None]
    assert bot.tree.menus == []


async def test_id_non_numerique_est_ignore(monkeypatch):
    """Un caractère parasite dans Render (« 111;222 » au lieu de « 111,222 »)
    ne doit pas empêcher le bot de démarrer. Le serveur invalide est simplement ignoré.
    """
    bot = await _bot_avec_arbre(monkeypatch, ["111", "abc"])

    await bot.setup_hook()

    assert bot.tree.syncs == [111]
    assert bot.tree.menus == [111]


async def test_aucun_id_valide_ne_synchronise_rien(monkeypatch):
    """Si tous les ids sont invalides, on ne synchronise rien — surtout pas
    globalement, ce qui serait l'inverse de ce que l'opérateur a demandé.
    """
    bot = await _bot_avec_arbre(monkeypatch, ["abc"])

    await bot.setup_hook()

    assert bot.tree.syncs == []
    assert bot.tree.menus == []


# --- Publication : chaque salon mentionne le rôle de son serveur ------------

from decimal import Decimal

from src.bot import EmpireBot
from src.db import Store

CSV = """# nom: Empire Immo - M8
# mise_a_jour: 2026-07-29 12:00:07
type,nom,niveau,valeur,loyer,charge,impot,promotion,construction,embellissement,reparation
zones,"Technopôle",0,2710572934559948,0,0,0,17,0,0,0
zones,"Zone portuaire",0,124467906332,0,0,0,17,0,0,0
"""


class ServeurFactice:
    def __init__(self, serveur_id: int):
        self.id = serveur_id
        self.name = f"Serveur {serveur_id}"


class SalonFactice:
    """Salon qui connaît son serveur, comme un vrai `TextChannel`."""

    def __init__(self, salon_id: int, serveur_id: int, nom: str = "promos"):
        self.id = salon_id
        self.name = nom
        self.guild = ServeurFactice(serveur_id)
        self.mention = f"<#{salon_id}>"
        self.envois: list[dict] = []

    async def send(self, contenu=None, **options):
        self.envois.append({"contenu": contenu, **options})

    @property
    def mentions(self) -> list[str]:
        """Contenu des messages reçus, pour voir *qui* a été mentionné."""
        return [envoi.get("content") or "" for envoi in self.envois]


class SourceFactice:
    async def fetch(self) -> str:
        return CSV


class JournalFactice:
    async def publication(self, promos, reussis, echecs):
        pass

    async def erreur(self, message):
        pass


async def _bot(salons: dict[int, SalonFactice]) -> EmpireBot:
    store = Store(dsn="")
    await store.connect()

    bot = object.__new__(EmpireBot)
    bot.store = store
    bot.source = SourceFactice()
    bot.journal = JournalFactice()
    bot.get_channel = salons.get
    return bot


async def test_publie_dans_deux_serveurs():
    """Le besoin de base : une fourchette, deux salons, deux serveurs."""
    salons = {1: SalonFactice(1, 111), 2: SalonFactice(2, 222)}
    bot = await _bot(salons)
    await bot.store.ajouter_fourchette("a", Decimal("0"), Decimal("6e15"))
    await bot.store.ajouter_salon_fourchette("a", "1")
    await bot.store.ajouter_salon_fourchette("a", "2")

    await bot.publier_si_lheure(forcer=True)

    assert salons[1].envois and salons[2].envois


async def test_chaque_salon_mentionne_le_role_de_son_serveur():
    """La propriété qui fait l'intérêt du changement.

    Mentionner le rôle de A dans un salon de B afficherait `@deleted-role`
    dans le post — sans erreur, sans log, visible seulement en le lisant.
    """
    salons = {1: SalonFactice(1, 111), 2: SalonFactice(2, 222)}
    bot = await _bot(salons)
    await bot.store.definir_role("111", "42")
    await bot.store.definir_role("222", "43")
    await bot.store.ajouter_fourchette("a", Decimal("0"), Decimal("6e15"))
    await bot.store.ajouter_salon_fourchette("a", "1")
    await bot.store.ajouter_salon_fourchette("a", "2")

    await bot.publier_si_lheure(forcer=True)

    assert "<@&42>" in salons[1].mentions[0]
    assert "<@&43>" not in salons[1].mentions[0]
    assert "<@&43>" in salons[2].mentions[0]
    assert "<@&42>" not in salons[2].mentions[0]


async def test_serveur_sans_role_ne_mentionne_personne():
    """Pas de mention vide non plus : le post part sans ping."""
    salons = {1: SalonFactice(1, 111), 2: SalonFactice(2, 222)}
    bot = await _bot(salons)
    await bot.store.definir_role("111", "42")
    await bot.store.ajouter_fourchette("a", Decimal("0"), Decimal("6e15"))
    await bot.store.ajouter_salon_fourchette("a", "1")
    await bot.store.ajouter_salon_fourchette("a", "2")

    await bot.publier_si_lheure(forcer=True)

    assert "<@&" in salons[1].mentions[0]
    assert "<@&" not in salons[2].mentions[0]


async def test_role_id_plat_mentionne_partout():
    """Compatibilité : une config d'avant garde son comportement."""
    salons = {1: SalonFactice(1, 111), 2: SalonFactice(2, 222)}
    bot = await _bot(salons)
    await bot.store.set("config", {"role_id": "7"})
    await bot.store.ajouter_fourchette("a", Decimal("0"), Decimal("6e15"))
    await bot.store.ajouter_salon_fourchette("a", "1")
    await bot.store.ajouter_salon_fourchette("a", "2")

    await bot.publier_si_lheure(forcer=True)

    assert "<@&7>" in salons[1].mentions[0]
    assert "<@&7>" in salons[2].mentions[0]


async def test_publication_memorise_les_noms_des_deux_serveurs():
    """Après un post, le site sait nommer les salons des deux serveurs."""
    salons = {1: SalonFactice(1, 111, "promos"), 2: SalonFactice(2, 222, "annonces")}
    bot = await _bot(salons)
    await bot.store.ajouter_fourchette("a", Decimal("0"), Decimal("6e15"))
    await bot.store.ajouter_salon_fourchette("a", "1")
    await bot.store.ajouter_salon_fourchette("a", "2")

    await bot.publier_si_lheure(forcer=True)

    connus = await bot.store.salons_connus()
    assert connus["1"] == {"nom": "promos", "serveur": "111"}
    assert connus["2"] == {"nom": "annonces", "serveur": "222"}
    assert len(await bot.store.serveurs()) == 2
