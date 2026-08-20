"""`/reglages importer` : le pont entre l'ancienne config et les tiroirs.

Le cloisonnement n'a pas de repli. Au déploiement, chaque serveur se réveille
donc vide : il ne publie nulle part, ne mentionne personne et laisse son salon de
logs muet. `/reglages importer`, tapé une fois dans chaque serveur, est le seul
chemin de retour — si cette commande est cassée, la seule façon de récupérer deux
ans de réglages est de tout ressaisir à la main.

Le calcul est éprouvé à part, sur des dictionnaires nus
(`tests/test_importation.py`). Ce qui se joue ici est le raccordement, et il a
ses propres façons de rater :

- écrire dans la configuration commune au lieu du tiroir du serveur — l'import
  ne changerait alors rien du tout, et le referait dans l'autre serveur
  écraserait ce qu'on vient de faire ;
- demander à Discord les salons du mauvais serveur, ou ne pas les demander du
  tout — et tout serait repris, y compris les salons du voisin ;
- ne rien dire de ce qui a été écarté, ce qui laisserait chercher longtemps
  pourquoi une fourchette ne publie plus.

Le dernier test est celui du plan : « compter les messages : un par salon, pas
deux ». C'est le seul qui prouve que la chaîne entière tient, de l'import à
l'envoi.
"""

import tempfile

from decimal import Decimal

from src.bot import EmpireBot
from src.db import Store
from src.source import CsvFileSource

CSV = """# nom: Empire Immo - M8
# mise_a_jour: 2026-07-29 12:00:07
type,nom,niveau,valeur,loyer,charge,impot,promotion,construction,embellissement,reparation
zones,"Technopôle",0,2710572934559948,0,0,0,17,0,0,0
"""


def _commande(bot: EmpireBot, nom: str):
    for commande in bot.tree.walk_commands():
        if commande.qualified_name == nom:
            return commande
    raise AssertionError(f"commande introuvable : {nom}")


class SalonFactice:
    def __init__(self, salon_id: int, serveur=None, nom="promos"):
        self.id = salon_id
        self.name = nom
        self.guild = serveur
        self.mention = f"<#{salon_id}>"
        self.envois: list[dict] = []

    async def send(self, contenu=None, **options):
        self.envois.append({"contenu": contenu, **options})


class ServeurFactice:
    """Serveur Discord, avec ses salons — c'est eux que l'import filtre."""

    def __init__(self, serveur_id: int, nom: str, salons=()):
        self.id = serveur_id
        self.name = nom
        self.channels = list(salons)


class Utilisateur:
    def __init__(self, admin: bool = True, membre_id: int = 1):
        self.id = membre_id
        self.guild_permissions = type(
            "Permissions", (), {"administrator": admin}
        )()


class Reponse:
    def __init__(self):
        self.messages = []

    async def send_message(self, contenu=None, *, embed=None, **_):
        self.messages.append({"contenu": contenu, "embed": embed})


class InteractionFactice:
    def __init__(self, serveur: ServeurFactice, admin: bool = True):
        self.user = Utilisateur(admin)
        self.response = Reponse()
        self.guild = serveur

    @property
    def textes(self) -> list[str]:
        return [
            message["contenu"]
            for message in self.response.messages
            if isinstance(message.get("contenu"), str)
        ]

    @property
    def rendu(self) -> str:
        """Tout ce que la commande a répondu, texte et embed confondus.

        Le compte rendu peut être un embed ou un message : ce qui compte est que
        l'information y soit, pas la forme choisie.
        """
        morceaux = []
        for message in self.response.messages:
            if isinstance(message.get("contenu"), str):
                morceaux.append(message["contenu"])
            if message.get("embed") is not None:
                morceaux.append(repr(message["embed"].to_dict()))
        return "\n".join(morceaux)


class JournalFactice:
    def __init__(self):
        self.rapports: list[tuple] = []

    async def publication(self, promos, reussis, echecs):
        self.rapports.append((promos, reussis, echecs))

    async def erreur(self, message):
        self.rapports.append(("erreur", message, {}))


class BotDeTest(EmpireBot):
    """`EmpireBot` réel — donc son arbre de commandes — dont on choisit les
    serveurs.

    `guilds` est une propriété en lecture seule de `discord.Client` : la
    redéfinir ici est le seul moyen de la garnir sans se connecter.
    """

    @property
    def guilds(self):
        return self._serveurs


async def _bot(serveurs=None, salons=None) -> BotDeTest:
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".csv", delete=False
    ) as f:
        f.write(CSV)
        chemin = f.name

    store = Store(dsn="")
    await store.connect()

    bot = BotDeTest(store, CsvFileSource(chemin))
    bot._serveurs = list(serveurs or [])
    bot.journal = JournalFactice()
    bot.get_channel = (salons or {}).get
    return bot


async def _importer(bot: BotDeTest, interaction: InteractionFactice) -> None:
    await _commande(bot, "reglages importer").callback(interaction)


# --- Où l'import écrit ------------------------------------------------------


async def test_limport_ecrit_dans_le_tiroir_du_serveur():
    """Le besoin même : ce qui était réglé se retrouve dans ce serveur.

    Écrit dans la configuration commune, l'import ne changerait rien — le
    serveur continuerait de ne rien publier, sans que rien ne le dise.
    """
    empire = ServeurFactice(111, "Empire Immo", [SalonFactice(1)])
    bot = await _bot([empire])
    await bot.store.maj_config(heure="21:00")
    await bot.store.set("template", {"embeds": [{"title": "Promos"}]})

    await _importer(bot, InteractionFactice(empire))

    magasin = bot.store.pour("111")
    assert (await magasin.config())["heure"] == "21:00"
    assert (await magasin.template())["embeds"] == [{"title": "Promos"}]


async def test_la_configuration_commune_nest_pas_touchee():
    """« Si le résultat ne va pas, rien n'est perdu » : l'ancienne config reste
    lisible, et un import raté se refait après correction."""
    empire = ServeurFactice(111, "Empire Immo", [SalonFactice(1)])
    bot = await _bot([empire])
    await bot.store.maj_config(heure="21:00")

    await _importer(bot, InteractionFactice(empire))

    assert (await bot.store.config())["heure"] == "21:00"


async def test_seuls_les_salons_de_ce_serveur_sont_repris():
    """Le point de vigilance du plan, vu depuis la commande : c'est Discord qui
    dit quels salons sont à ce serveur, et il faut le lui demander."""
    empire = ServeurFactice(111, "Empire Immo", [SalonFactice(1)])
    bot = await _bot([empire])
    await bot.store.ajouter_fourchette("a", Decimal("0"), Decimal("6e15"))
    await bot.store.ajouter_salon_fourchette("a", "1")
    await bot.store.ajouter_salon_fourchette("a", "2")

    await _importer(bot, InteractionFactice(empire))

    fourchettes = await bot.store.pour("111").fourchettes()
    assert fourchettes[0]["salons"] == ["1"]


async def test_les_salons_ecartes_sont_nommes_dans_le_compte_rendu():
    """Sans ça, on chercherait longtemps pourquoi une fourchette ne publie plus
    là où elle publiait la veille."""
    empire = ServeurFactice(111, "Empire Immo", [SalonFactice(1)])
    bot = await _bot([empire])
    await bot.store.ajouter_fourchette("a", Decimal("0"), Decimal("6e15"))
    await bot.store.ajouter_salon_fourchette("a", "2")
    interaction = InteractionFactice(empire)

    await _importer(bot, interaction)

    assert "2" in interaction.rendu
    # Nommé comme un salon, et non comme un nombre perdu au milieu d'une phrase.
    assert "<#2>" in interaction.rendu


async def test_le_compte_rendu_dit_ce_qui_est_repris():
    """Un « ✅ » seul ne permettrait pas de constater qu'il ne manque rien —
    notamment les relevés des filiales, saisis un par un."""
    empire = ServeurFactice(111, "Empire Immo", [SalonFactice(1)])
    bot = await _bot([empire])
    await bot.store.maj_config(heure="21:00")
    await bot.store.set("filiales", [{"nom": "ARMEE", "montant": "1000"}])
    interaction = InteractionFactice(empire)

    await _importer(bot, interaction)

    rendu = interaction.rendu.lower()
    assert "réglages" in rendu
    assert "filiales" in rendu


# --- Ce qui est déjà réglé ici -----------------------------------------------


async def test_un_second_import_necrase_rien():
    """La commande est faite pour être retapée : on importe, on corrige à la
    main, et un import de trop ne doit pas ramener l'ancienne heure."""
    empire = ServeurFactice(111, "Empire Immo", [SalonFactice(1)])
    bot = await _bot([empire])
    await bot.store.maj_config(heure="21:00")
    await _importer(bot, InteractionFactice(empire))
    await bot.store.pour("111").maj_config(heure="07:30")

    interaction = InteractionFactice(empire)
    await _importer(bot, interaction)

    assert (await bot.store.pour("111").config())["heure"] == "07:30"
    # Et il le dit : un « ✅ tout est repris » ferait croire à l'inverse.
    assert "déjà" in interaction.rendu.lower()


async def test_sans_rien_a_reprendre_la_commande_le_dit():
    """Un serveur neuf sur un bot neuf : répondre « ✅ » à un import qui n'a
    rien fait laisserait attendre des posts qui ne viendront pas."""
    empire = ServeurFactice(111, "Empire Immo", [SalonFactice(1)])
    bot = await _bot([empire])
    interaction = InteractionFactice(empire)

    await _importer(bot, interaction)

    # La phrase exacte, et non le mot « rien » : le compte rendu d'un import
    # réussi dit lui aussi « rien n'a été effacé ».
    assert "rien à reprendre" in interaction.rendu.lower()


# --- Qui peut l'utiliser -----------------------------------------------------


async def test_limport_est_reserve_aux_administrateurs():
    """L'import recopie la liste d'accès : il décide donc qui pourra se servir
    du bot dans ce serveur, exactement comme `/reglages acces`."""
    empire = ServeurFactice(111, "Empire Immo", [SalonFactice(1)])
    bot = await _bot([empire])
    await bot.store.maj_config(heure="21:00")
    interaction = InteractionFactice(empire, admin=False)

    await _importer(bot, interaction)

    assert "administrateur" in interaction.rendu.lower()
    # Refusé veut dire rien écrit, et pas seulement rien dit.
    assert "heure" not in await bot.store.pour("111").get("config", {})


# --- Le seul vrai juge : compter les messages -------------------------------


async def test_apres_limport_le_serveur_publie_une_fois_dans_son_salon():
    """L'épreuve du plan, de bout en bout.

    Avant l'import, le serveur ne publie nulle part ; après, il publie — une
    seule fois, dans son seul salon. Le salon du voisin, cité par la même
    fourchette commune, ne reçoit rien et n'est pas signalé au journal : il a été
    écarté à l'import, et non rattrapé à l'envoi par la garde de `src.tournee`.
    """
    empire = ServeurFactice(111, "Empire Immo")
    voisin = ServeurFactice(222, "Groupe Nord")
    chez_empire = SalonFactice(1, empire)
    chez_voisin = SalonFactice(2, voisin)
    empire.channels = [chez_empire]
    voisin.channels = [chez_voisin]
    bot = await _bot([empire, voisin], {1: chez_empire, 2: chez_voisin})
    await bot.store.ajouter_fourchette("a", Decimal("0"), Decimal("6e15"))
    await bot.store.ajouter_salon_fourchette("a", "1")
    await bot.store.ajouter_salon_fourchette("a", "2")

    # Rien n'est réglé dans les tiroirs : le tour ne trouve aucun salon.
    await bot.publier_tout(forcer=True)
    assert chez_empire.envois == []

    await _importer(bot, InteractionFactice(empire))
    await bot.publier_tout(forcer=True)

    assert len(chez_empire.envois) == 1
    assert chez_voisin.envois == []
    # Aucun salon étranger n'est arrivé jusqu'à l'envoi.
    assert all(not echecs for _, _, echecs in bot.journal.rapports)
