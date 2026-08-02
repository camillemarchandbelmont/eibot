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
    """Compte les synchronisations, par serveur."""

    def __init__(self):
        self.copies: list[int | None] = []
        self.syncs: list[int | None] = []

    def copy_global_to(self, guild):
        self.copies.append(guild.id)

    async def sync(self, guild=None):
        self.syncs.append(guild.id if guild is not None else None)


async def _bot_avec_arbre(monkeypatch, guild_ids: list[str]):
    from src.bot import EmpireBot
    from src import bot as module_bot

    monkeypatch.setattr(module_bot.settings, "GUILD_IDS", guild_ids)

    bot = object.__new__(EmpireBot)
    bot.tree = ArbreFactice()
    return bot


async def test_synchronise_sur_chaque_serveur(monkeypatch):
    """La propriété qui fait l'intérêt de la liste : deux serveurs, deux syncs."""
    bot = await _bot_avec_arbre(monkeypatch, ["111", "222"])

    await bot.setup_hook()

    assert bot.tree.syncs == [111, 222]
    assert bot.tree.copies == [111, 222]


async def test_liste_vide_synchronise_globalement(monkeypatch):
    bot = await _bot_avec_arbre(monkeypatch, [])

    await bot.setup_hook()

    assert bot.tree.syncs == [None]
    assert bot.tree.copies == []


async def test_id_non_numerique_est_ignore(monkeypatch):
    """Un caractère parasite dans Render (« 111;222 » au lieu de « 111,222 »)
    ne doit pas empêcher le bot de démarrer. Le serveur invalide est simplement ignoré.
    """
    bot = await _bot_avec_arbre(monkeypatch, ["111", "abc"])

    await bot.setup_hook()

    assert bot.tree.syncs == [111]
    assert bot.tree.copies == [111]


async def test_aucun_id_valide_ne_synchronise_rien(monkeypatch):
    """Si tous les ids sont invalides, on ne synchronise rien — surtout pas
    globalement, ce qui serait l'inverse de ce que l'opérateur a demandé.
    """
    bot = await _bot_avec_arbre(monkeypatch, ["abc"])

    await bot.setup_hook()

    assert bot.tree.syncs == []
    assert bot.tree.copies == []
