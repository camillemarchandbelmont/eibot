"""Tests de `/promos` sans argument et de `/fourchette apercu`.

Ces deux commandes lisaient la fourchette unique de la racine, qui n'existe
plus. Chacune répond à une question différente, d'où deux comportements
différents :

- `/promos` sans argument : « qu'est-ce qui est en promo dans ce que je
  surveille ? » — l'**union** des bornes, une seule liste.
- `/fourchette apercu` : « qu'est-ce que le bot va poster ? » — **un post par
  fourchette**, puisque c'est exactement ce que fera la publication.

Un aperçu qui montrerait l'union mentirait sur le contenu de chaque salon, et
c'est précisément ce qu'on vient prévisualiser.

L'aperçu vient du vocabulaire commun des publications (`src.commandes`), éprouvé
là-bas sur une publication d'essai. Ce qui se vérifie ici est ce que ce
vocabulaire donne **appliqué aux promotions** : le découpage par fourchette et
les noms de commandes cités dans ses messages.
"""

from decimal import Decimal

import pytest

from src.bot import EmpireBot
from src.db import Store

CSV = """# nom: Empire Immo - M8
# mise_a_jour: 2026-07-29 12:00:07
type,nom,niveau,valeur,loyer,charge,impot,promotion,construction,embellissement,reparation
zones,"Technopôle",0,2710572934559948,0,0,0,17,0,0,0
zones,"Zone portuaire",0,124467906332,0,0,0,17,0,0,0
industriels,"Entrepôt",0,302620,0,0,283,17,611961,87354,62063
"""


class SourceFactice:
    def __init__(self, texte: str = CSV):
        self.texte = texte

    async def fetch(self) -> str:
        return self.texte


class Reponse:
    def __init__(self):
        self.messages: list[dict] = []
        self.differee = False

    async def defer(self, ephemeral: bool = False) -> None:
        self.differee = True

    async def send_message(self, contenu=None, **options) -> None:
        self.messages.append({"contenu": contenu, **options})


class Followup:
    def __init__(self):
        self.messages: list[dict] = []

    async def send(self, contenu=None, **options) -> None:
        self.messages.append({"contenu": contenu, **options})


class Utilisateur:
    id = 1

    class guild_permissions:  # noqa: N801 - imite l'attribut de discord.Member
        administrator = True


#: Le serveur où ces commandes sont tapées. Il y en a un, parce qu'il en faut
#: un : chaque serveur a sa configuration, et des fourchettes rangées dans la
#: configuration commune ne seraient lues par aucune commande.
SERVEUR = 555


class ServeurFactice:
    def __init__(self, serveur_id: int = SERVEUR):
        self.id = serveur_id
        self.name = f"Serveur {serveur_id}"


class InteractionFactice:
    def __init__(self):
        self.user = Utilisateur()
        self.response = Reponse()
        self.followup = Followup()
        self.guild = ServeurFactice()

    @property
    def textes(self) -> list[str]:
        return [
            message["contenu"]
            for message in [*self.response.messages, *self.followup.messages]
            if isinstance(message.get("contenu"), str)
        ]

    @property
    def titres(self) -> list[str]:
        """Titres des embeds envoyés, pour savoir *quels bâtiments* sont montrés."""
        trouves = []
        for message in [*self.response.messages, *self.followup.messages]:
            for embed in message.get("embeds") or []:
                titre = getattr(embed, "title", None) or embed.to_dict().get("title")
                if titre:
                    trouves.append(titre)
        return trouves


def _commande(bot: EmpireBot, nom: str):
    for commande in bot.tree.walk_commands():
        if commande.qualified_name == nom:
            return commande
    raise AssertionError(f"commande introuvable : {nom}")


async def _bot() -> EmpireBot:
    store = Store(dsn="")
    await store.connect()
    return EmpireBot(store, SourceFactice())


def _magasin(bot: EmpireBot):
    """La configuration du serveur où les commandes ci-dessous sont tapées.

    Le montage passe par elle, comme les commandes : réglé ailleurs, rien de ce
    qui suit ne serait visible.
    """
    return bot.store.pour(SERVEUR)


# --- /promos sans argument ---------------------------------------------------


async def test_promos_sans_argument_couvre_toutes_les_fourchettes():
    """Le minimum le plus bas et le maximum le plus haut, en une seule liste.

    Interroger une seule fourchette obligerait à en nommer une, alors que la
    commande sert justement à voir « ce qui bouge » sans réfléchir.
    """
    bot = await _bot()
    await _magasin(bot).ajouter_fourchette("grosses", Decimal("1e15"), Decimal("6e15"))
    await _magasin(bot).ajouter_fourchette("petits", Decimal("1e5"), Decimal("1e6"))
    interaction = InteractionFactice()

    await _commande(bot, "promos").callback(interaction)

    titres = " ".join(interaction.titres)
    assert "Technopôle" in titres      # de « grosses »
    assert "Entrepôt" in titres        # de « petits »


async def test_promos_union_ne_se_reduit_pas_a_la_premiere_fourchette():
    """Les bornes viennent de fourchettes *différentes*, dans les deux sens.

    Prendre les bornes de la première suffirait à faire passer un test où elle
    est justement la plus large : ici la plus haute est la seconde et la plus
    basse la première, donc une seule fourchette consultée fait disparaître le
    Technopôle de la liste.
    """
    bot = await _bot()
    await _magasin(bot).ajouter_fourchette("moyennes", Decimal("1e11"), Decimal("1e12"))
    await _magasin(bot).ajouter_fourchette("grosses", Decimal("1e15"), Decimal("6e15"))
    interaction = InteractionFactice()

    await _commande(bot, "promos").callback(interaction)

    titres = " ".join(interaction.titres)
    assert "Technopôle" in titres        # borne haute de « grosses »
    assert "Zone portuaire" in titres    # borne basse de « moyennes »


async def test_promos_avec_arguments_ignore_les_fourchettes():
    """Une recherche ponctuelle ne dépend pas de la config : comportement conservé."""
    bot = await _bot()
    await _magasin(bot).ajouter_fourchette("grosses", Decimal("1e15"), Decimal("6e15"))
    interaction = InteractionFactice()

    await _commande(bot, "promos").callback(interaction, min="0", max="1M")

    titres = " ".join(interaction.titres)
    assert "Entrepôt" in titres
    assert "Technopôle" not in titres


async def test_promos_sans_fourchette_le_dit_au_lieu_de_ne_rien_montrer():
    """Un bot neuf n'a pas de fourchette : montrer une liste vide ferait croire
    à une absence de promotions."""
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "promos").callback(interaction)

    texte = " ".join(interaction.textes)
    assert "fourchette" in texte.lower()
    assert "/fourchette ajouter" in texte


# --- /fourchette apercu -----------------------------------------------------


async def test_apercu_montre_un_post_par_fourchette():
    """C'est ce que la publication fera : un post par fourchette, pas une union."""
    bot = await _bot()
    await _magasin(bot).ajouter_fourchette("grosses", Decimal("1e15"), Decimal("6e15"))
    await _magasin(bot).ajouter_salon_fourchette("grosses", "111")
    await _magasin(bot).ajouter_fourchette("petits", Decimal("1e5"), Decimal("1e6"))
    await _magasin(bot).ajouter_salon_fourchette("petits", "222")
    interaction = InteractionFactice()

    await _commande(bot, "fourchette apercu").callback(interaction)

    textes = " ".join(interaction.textes)
    assert "grosses" in textes and "petits" in textes


async def test_apercu_nomme_la_fourchette_de_chaque_post():
    """Sans le nom, deux posts d'affilée ne se distingueraient pas."""
    bot = await _bot()
    await _magasin(bot).ajouter_fourchette("grosses", Decimal("1e15"), Decimal("6e15"))
    await _magasin(bot).ajouter_salon_fourchette("grosses", "111")
    interaction = InteractionFactice()

    await _commande(bot, "fourchette apercu").callback(interaction)

    assert any("grosses" in texte for texte in interaction.textes)


async def test_apercu_signale_une_fourchette_sans_salon():
    """Elle ne publiera rien : l'aperçu doit le dire, pas la montrer comme les
    autres."""
    bot = await _bot()
    await _magasin(bot).ajouter_fourchette(
        "orpheline", Decimal("1e15"), Decimal("6e15")
    )
    interaction = InteractionFactice()

    await _commande(bot, "fourchette apercu").callback(interaction)

    textes = " ".join(interaction.textes)
    assert "orpheline" in textes
    assert "⚠️" in textes or "aucun salon" in textes.lower()


async def test_apercu_sans_fourchette_explique_quoi_faire():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "fourchette apercu").callback(interaction)

    assert "/fourchette ajouter" in " ".join(interaction.textes)


async def test_apercu_lit_lexport_une_seule_fois():
    """Même raison qu'à la publication : N fourchettes ne doivent pas faire N
    appels à l'API du jeu pour des données identiques."""
    class SourceComptee(SourceFactice):
        def __init__(self):
            super().__init__()
            self.lectures = 0

        async def fetch(self) -> str:
            self.lectures += 1
            return self.texte

    store = Store(dsn="")
    await store.connect()
    source = SourceComptee()
    bot = EmpireBot(store, source)

    for index, nom in enumerate(("a", "b", "c"), start=1):
        await _magasin(bot).ajouter_fourchette(nom, Decimal("0"), Decimal("6e15"))
        await _magasin(bot).ajouter_salon_fourchette(nom, str(index))

    await _commande(bot, "fourchette apercu").callback(InteractionFactice())

    assert source.lectures == 1
