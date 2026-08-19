"""Affichage correct de `/reglages voir` et `/reglages mention` avec role_id plat."""

from src.bot import EmpireBot
from src.db import Store


def _commande(bot: EmpireBot, nom: str):
    for commande in bot.tree.walk_commands():
        if commande.qualified_name == nom:
            return commande
    raise AssertionError(f"commande introuvable : {nom}")


class Reponse:
    def __init__(self):
        self.messages = []

    async def send_message(self, contenu=None, *, embed=None, **_):
        self.messages.append({"contenu": contenu, "embed": embed})


class Utilisateur:
    def __init__(self, admin: bool = True, membre_id: int = 1):
        self.id = membre_id
        self.top_role = type("Role", (), {"position": 10 if admin else 1})()


class ServeurFactice:
    def __init__(self, serveur_id: int = 111):
        self.id = serveur_id
        self.name = f"Serveur {serveur_id}"


class InteractionFactice:
    def __init__(self, admin: bool = True, membre_id: int = 1, serveur_id: int = 111):
        self.user = Utilisateur(admin, membre_id)
        self.response = Reponse()
        self.guild = ServeurFactice(serveur_id)

    @property
    def textes(self) -> list[str]:
        return [
            message["contenu"]
            for message in self.response.messages
            if isinstance(message.get("contenu"), str)
        ]

    @property
    def embeds(self) -> list:
        return [message["embed"] for message in self.response.messages if message.get("embed")]


CSV = """# nom: Empire Immo - M8
# mise_a_jour: 2026-07-29 12:00:07
type,nom,niveau,valeur,loyer,charge,impot,promotion,construction,embellissement,reparation
zones,"Technopôle",0,2710572934559948,0,0,0,17,0,0,0
"""


async def _bot() -> EmpireBot:
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".csv", delete=False) as f:
        f.write(CSV)
        chemin = f.name

    store = Store(dsn="")
    await store.connect()

    from src.source import CsvFileSource

    bot = EmpireBot(store, CsvFileSource(chemin))
    return bot


# --- DEFECT B: /reglages voir n'affiche pas le repli plat ---------------------


async def test_reglages_voir_affiche_les_roles_par_serveur():
    """Le cas neuf : roles() renvoie une table, et /reglages voir l'affiche."""
    bot = await _bot()
    await bot.store.definir_role("111", "42")
    await bot.store.definir_role("222", "43")
    interaction = InteractionFactice()

    await _commande(bot, "reglages voir").callback(interaction)

    rendu = repr(interaction.embeds[0].to_dict())
    assert "<@&42>" in rendu
    assert "<@&43>" in rendu


async def test_reglages_voir_affiche_le_role_id_plat_comme_repli():
    """Defect B : avec roles()={} mais role_id="7", /reglages voir affiche "aucune"
    alors que le bot pingue <@&7> partout.

    Le repli doit être rendu visible, avec un texte explicatif.
    """
    bot = await _bot()
    # Config d'avant : role_id plat, pas de roles
    await bot.store.set("config", {"role_id": "7"})
    interaction = InteractionFactice()

    await _commande(bot, "reglages voir").callback(interaction)

    rendu = repr(interaction.embeds[0].to_dict())
    # Doit afficher le rôle
    assert "<@&7>" in rendu
    # Et non "*aucune*"
    assert "*aucune*" not in rendu.lower()


async def test_reglages_voir_affiche_aucune_quand_vraiment_rien():
    """Quand ni roles ni role_id ne sont définis."""
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "reglages voir").callback(interaction)

    rendu = repr(interaction.embeds[0].to_dict())
    assert "*aucune*" in rendu.lower() or "aucune" in rendu.lower()


# --- DEFECT C: /reglages mention sous-estime la portée de l'effacement --------


class RoleFactice:
    def __init__(self, role_id: int):
        self.id = role_id
        self.mention = f"<@&{role_id}>"


async def test_reglages_mention_efface_un_role_par_serveur():
    """Le cas neuf : un rôle dans roles[serveur_id]."""
    bot = await _bot()
    await bot.store.definir_role("111", "42")
    interaction = InteractionFactice(serveur_id=111)

    await _commande(bot, "reglages mention").callback(interaction, role=None)

    texte = interaction.textes[0]
    # Doit dire "sur ce serveur"
    assert "sur ce serveur" in texte.lower()
    # Et ne doit PAS dire "sur tous les serveurs"
    assert "tous les serveurs" not in texte.lower()


async def test_reglages_mention_efface_le_role_id_plat_pour_tous():
    """Defect C : effacer role_id="7" affecte tous les serveurs, mais le
    message dit "sur ce serveur".

    Le message doit clarifier que c'était un réglage global.
    """
    bot = await _bot()
    # Config d'avant : role_id plat, pas de roles
    await bot.store.set("config", {"role_id": "7"})
    interaction = InteractionFactice(serveur_id=111)

    await _commande(bot, "reglages mention").callback(interaction, role=None)

    texte = interaction.textes[0]
    # Doit dire "sur tous les serveurs" ou équivalent
    assert "tous les serveurs" in texte.lower() or "tous" in texte.lower()


async def test_reglages_mention_sans_rien_a_effacer():
    """Quand ni roles ni role_id ne sont définis."""
    bot = await _bot()
    interaction = InteractionFactice(serveur_id=111)

    await _commande(bot, "reglages mention").callback(interaction, role=None)

    texte = interaction.textes[0]
    assert "aucune mention" in texte.lower()
