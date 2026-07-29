"""Tests du contrôle d'accès aux commandes.

Toutes les commandes sont réservées aux administrateurs du serveur et aux
membres explicitement autorisés. Le contrôle est fait **une fois** dans
`autorisation` du `CommandTree`, pas commande par commande : c'est cette
centralisation qui garantit qu'une commande ajoutée plus tard est protégée
d'office, sans qu'on ait à y penser.
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
    def __init__(self, administrator: bool = False, manage_guild: bool = False):
        self.administrator = administrator
        self.manage_guild = manage_guild


class Membre:
    def __init__(self, membre_id: int, **permissions):
        self.id = membre_id
        self.guild_permissions = Permissions(**permissions)


class Reponse:
    def __init__(self):
        self.messages: list[dict] = []

    async def send_message(self, contenu=None, **options) -> None:
        self.messages.append({"contenu": contenu, **options})

    async def defer(self, ephemeral: bool = False) -> None:
        pass


class Followup:
    def __init__(self):
        self.messages: list[dict] = []

    async def send(self, contenu=None, **options) -> None:
        self.messages.append({"contenu": contenu, **options})


class InteractionFactice:
    def __init__(self, membre: Membre, commande: str = "config voir"):
        self.user = membre
        self.response = Reponse()
        self.followup = Followup()
        self.command = type("Commande", (), {"qualified_name": commande})()

    @property
    def textes(self) -> list[str]:
        return [
            message["contenu"]
            for message in [*self.response.messages, *self.followup.messages]
            if isinstance(message.get("contenu"), str)
        ]


async def _bot(tmp_path) -> EmpireBot:
    chemin = tmp_path / "export.csv"
    chemin.write_text(CSV, encoding="utf-8")
    store = Store(dsn="")
    await store.connect()
    return EmpireBot(store, CsvFileSource(chemin))


# --- Qui passe la porte -----------------------------------------------------

async def test_administrateur_autorise(tmp_path):
    bot = await _bot(tmp_path)
    interaction = InteractionFactice(Membre(1, administrator=True))
    assert await bot.tree.autorisation(interaction) is True


async def test_membre_lambda_refuse(tmp_path):
    bot = await _bot(tmp_path)
    interaction = InteractionFactice(Membre(1))
    assert await bot.tree.autorisation(interaction) is False


async def test_membre_explicitement_autorise(tmp_path):
    bot = await _bot(tmp_path)
    await bot.store.autoriser("42")
    interaction = InteractionFactice(Membre(42))
    assert await bot.tree.autorisation(interaction) is True


async def test_gerer_le_serveur_ne_suffit_plus(tmp_path):
    """« Gérer le serveur » ouvrait la configuration ; désormais il faut être
    administrateur ou figurer dans la liste."""
    bot = await _bot(tmp_path)
    interaction = InteractionFactice(Membre(1, manage_guild=True))
    assert await bot.tree.autorisation(interaction) is False


async def test_membre_retire_perd_lacces(tmp_path):
    bot = await _bot(tmp_path)
    await bot.store.autoriser("42")
    await bot.store.retirer_autorise("42")
    interaction = InteractionFactice(Membre(42))
    assert await bot.tree.autorisation(interaction) is False


async def test_administrateur_reste_autorise_meme_retire(tmp_path):
    """Un administrateur ne peut pas se verrouiller dehors."""
    bot = await _bot(tmp_path)
    await bot.store.retirer_autorise("42")
    interaction = InteractionFactice(Membre(42, administrator=True))
    assert await bot.tree.autorisation(interaction) is True


async def test_message_de_refus_ephemere(tmp_path):
    """Le refus ne doit pas polluer le salon pour les autres membres."""
    bot = await _bot(tmp_path)
    interaction = InteractionFactice(Membre(1))

    await bot.tree.autorisation(interaction)

    message = interaction.response.messages[0]
    assert message["ephemeral"] is True
    assert "réservé" in message["contenu"].lower()


async def test_message_de_refus_hors_serveur(tmp_path):
    """En message privé, `guild_permissions` n'existe pas : refus, pas de crash."""
    bot = await _bot(tmp_path)

    class SansPermissions:
        id = 1

    interaction = InteractionFactice(SansPermissions())
    assert await bot.tree.autorisation(interaction) is False


# --- Aucune commande n'échappe au contrôle ----------------------------------

async def test_toutes_les_commandes_sont_protegees(tmp_path):
    """Le contrôle est au niveau du tree : il couvre tout l'arbre d'un coup.

    Ce test échouerait si une commande était enregistrée sur un autre tree, ou
    si le contrôle redevenait local à quelques commandes.
    """
    bot = await _bot(tmp_path)
    commandes = [c.qualified_name for c in bot.tree.walk_commands()]

    assert "promos" in commandes and "config voir" in commandes
    for nom in commandes:
        interaction = InteractionFactice(Membre(1), commande=nom)
        assert await bot.tree.autorisation(interaction) is False, nom
