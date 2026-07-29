"""Tests des commandes `/config acces`.

Gérer la liste reste **réservé aux administrateurs**, même si un membre
autorisé peut utiliser toutes les autres commandes : sinon il pourrait
s'ajouter des complices, ou retirer celui qui l'a nommé.
"""

import pytest

from src.bot import EmpireBot
from src.db import Store
from src.source import CsvFileSource

CSV = """# nom: Empire Immo - M8
# mise_a_jour: 2026-07-29 12:00:07
type,nom,niveau,valeur,loyer,charge,impot,promotion,construction,embellissement,reparation
zones,"Technopôle",0,2710572934559948,0,0,0,17,0,0,0
"""


class Permissions:
    def __init__(self, administrator: bool = False):
        self.administrator = administrator
        self.manage_guild = administrator


class Membre:
    """Remplace `discord.Member` comme auteur ou comme argument de commande."""

    def __init__(self, membre_id: int, nom: str = "Camille", admin: bool = False):
        self.id = membre_id
        self.display_name = nom
        self.mention = f"<@{membre_id}>"
        self.guild_permissions = Permissions(admin)
        self.bot = False


class Bot(Membre):
    def __init__(self, membre_id: int = 999):
        super().__init__(membre_id, nom="EmpireBot")
        self.bot = True


class Reponse:
    def __init__(self):
        self.messages: list[dict] = []

    async def send_message(self, contenu=None, **options) -> None:
        self.messages.append({"contenu": contenu, **options})

    async def defer(self, ephemeral: bool = False) -> None:
        pass


class InteractionFactice:
    def __init__(self, auteur: Membre):
        self.user = auteur
        self.response = Reponse()

    @property
    def textes(self) -> list[str]:
        return [
            m["contenu"] for m in self.response.messages
            if isinstance(m.get("contenu"), str)
        ]

    @property
    def embeds(self) -> list:
        return [m["embed"] for m in self.response.messages if m.get("embed")]


def _commande(bot: EmpireBot, nom: str):
    for commande in bot.tree.walk_commands():
        if commande.qualified_name == nom:
            return commande
    raise AssertionError(f"commande introuvable : {nom}")


async def _bot(tmp_path) -> EmpireBot:
    chemin = tmp_path / "export.csv"
    chemin.write_text(CSV, encoding="utf-8")
    store = Store(dsn="")
    await store.connect()
    return EmpireBot(store, CsvFileSource(chemin))


ADMIN = lambda: Membre(1, nom="Admin", admin=True)


# --- Ajouter ----------------------------------------------------------------

async def test_ajouter_un_membre(tmp_path):
    bot = await _bot(tmp_path)
    interaction = InteractionFactice(ADMIN())

    await _commande(bot, "config acces ajouter").callback(interaction, Membre(42))

    assert await bot.store.autorises() == ["42"]
    assert "✅" in interaction.textes[0]


async def test_ajouter_deux_fois_le_dit(tmp_path):
    bot = await _bot(tmp_path)
    membre = Membre(42)
    await _commande(bot, "config acces ajouter").callback(
        InteractionFactice(ADMIN()), membre
    )

    interaction = InteractionFactice(ADMIN())
    await _commande(bot, "config acces ajouter").callback(interaction, membre)

    assert await bot.store.autorises() == ["42"]
    assert "déjà" in interaction.textes[0]


async def test_ajouter_un_bot_refuse(tmp_path):
    """Un bot ne tape pas de commandes : l'autoriser ne peut être qu'une erreur
    de clic dans la liste Discord."""
    bot = await _bot(tmp_path)
    interaction = InteractionFactice(ADMIN())

    await _commande(bot, "config acces ajouter").callback(interaction, Bot())

    assert await bot.store.autorises() == []
    assert "bot" in interaction.textes[0].lower()


async def test_ajouter_un_administrateur_le_dit(tmp_path):
    """Il a déjà accès : le laisser croire qu'on vient de lui donner un droit
    masquerait le fait qu'il le perdra en perdant son rôle d'admin."""
    bot = await _bot(tmp_path)
    interaction = InteractionFactice(ADMIN())

    await _commande(bot, "config acces ajouter").callback(
        interaction, Membre(7, admin=True)
    )

    assert "administrateur" in interaction.textes[0].lower()


# --- Retirer ----------------------------------------------------------------

async def test_retirer_un_membre(tmp_path):
    bot = await _bot(tmp_path)
    await bot.store.autoriser("42")
    interaction = InteractionFactice(ADMIN())

    await _commande(bot, "config acces retirer").callback(interaction, Membre(42))

    assert await bot.store.autorises() == []
    assert "✅" in interaction.textes[0]


async def test_retirer_un_membre_absent_le_dit(tmp_path):
    bot = await _bot(tmp_path)
    interaction = InteractionFactice(ADMIN())

    await _commande(bot, "config acces retirer").callback(interaction, Membre(42))

    assert "pas" in interaction.textes[0].lower()


# --- Liste ------------------------------------------------------------------

async def test_liste_vide_dit_que_les_admins_gardent_lacces(tmp_path):
    """Sans cette mention, une liste vide se lirait comme « personne », alors que
    les administrateurs passent toujours.

    L'assertion porte sur la description, pas sur l'embed entier : le pied de
    page cite lui aussi les administrateurs et masquerait la disparition de
    cette phrase.
    """
    bot = await _bot(tmp_path)
    interaction = InteractionFactice(ADMIN())

    await _commande(bot, "config acces liste").callback(interaction)

    embed = interaction.embeds[0]
    assert "dministrateur" in (embed.description or "")
    assert "Aucun" in repr(embed.to_dict())


async def test_liste_affiche_les_membres(tmp_path):
    bot = await _bot(tmp_path)
    await bot.store.autoriser("42")
    await bot.store.autoriser("43")
    interaction = InteractionFactice(ADMIN())

    await _commande(bot, "config acces liste").callback(interaction)

    champs = repr(interaction.embeds[0].to_dict()["fields"])
    assert "42" in champs and "43" in champs
    assert "(2)" in champs   # le compte, pour repérer un membre oublié


# --- Réservé aux administrateurs -------------------------------------------

async def test_un_membre_autorise_ne_peut_pas_ajouter(tmp_path):
    """Sinon il s'ajouterait des complices sans passer par un administrateur."""
    bot = await _bot(tmp_path)
    await bot.store.autoriser("42")
    interaction = InteractionFactice(Membre(42))

    await _commande(bot, "config acces ajouter").callback(interaction, Membre(43))

    assert await bot.store.autorises() == ["42"]
    assert "administrateur" in interaction.textes[0].lower()


async def test_un_membre_autorise_ne_peut_pas_retirer(tmp_path):
    """Sinon il pourrait retirer les autres et rester seul aux commandes."""
    bot = await _bot(tmp_path)
    await bot.store.autoriser("42")
    await bot.store.autoriser("43")
    interaction = InteractionFactice(Membre(42))

    await _commande(bot, "config acces retirer").callback(interaction, Membre(43))

    assert await bot.store.autorises() == ["42", "43"]
    assert "administrateur" in interaction.textes[0].lower()


async def test_un_membre_autorise_peut_voir_la_liste(tmp_path):
    """Consulter ne présente pas le même risque que modifier."""
    bot = await _bot(tmp_path)
    await bot.store.autoriser("42")
    interaction = InteractionFactice(Membre(42))

    await _commande(bot, "config acces liste").callback(interaction)

    assert interaction.embeds or interaction.textes
