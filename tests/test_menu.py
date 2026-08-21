"""La forme du menu : ce que voit quelqu'un qui tape « / » dans Discord.

Chaque commande est éprouvée là où elle vit. Ce qui se vérifie ici n'appartient à
aucune d'elles : c'est le rangement. Peu d'entrées à la racine, et **tout ce qui
règle le bot** sous `/reglages` — sans quoi le prochain module posera son propre
groupe de réglages à côté, et le menu repartira de travers comme la première fois.

`/bonjour` et `/bonsoir` viennent du module d'épreuve `src/modules/politesse.py`,
jetable : ils s'en vont avec lui.
"""

from src.bot import EmpireBot
from src.db import Store


class SourceFactice:
    async def fetch(self) -> str:
        return ""


async def _bot() -> EmpireBot:
    store = Store(dsn="")
    await store.connect()
    return EmpireBot(store, SourceFactice())


async def test_la_racine_ne_montre_que_les_entrees_du_menu():
    """Trois calculatrices, deux domaines, un tiroir à réglages — et l'épreuve.

    L'égalité est stricte à dessein : un groupe oublié à la racine ne se
    remarquerait jamais dans une assertion « contient ». C'est aussi ce qui fait
    que le module d'épreuve casse ce test au lieu de s'y glisser sans bruit.
    """
    bot = await _bot()

    racine = {commande.name for commande in bot.tree.get_commands()}

    assert racine == {
        "convertir",
        "frais",
        "promos",
        "fourchette",
        "filiales",
        "reglages",
        # Le module jetable, et ses deux publications dans un seul fichier.
        "bonjour",
        "bonsoir",
    }


async def test_reglages_rassemble_tout_ce_qui_configure_le_bot():
    """Un seul endroit à ouvrir pour régler quoi que ce soit.

    Quatre sous-groupes plutôt qu'une liste plate : `modules`, `acces`, `source`
    et `template` sont des sujets entiers, et Discord n'accepte pas un quatrième
    niveau — c'est donc la profondeur maximale, et elle est atteinte.
    """
    bot = await _bot()

    sous_reglages = {
        commande.qualified_name.removeprefix("reglages ")
        for commande in bot.tree.walk_commands()
        if commande.qualified_name.startswith("reglages ")
    }

    assert sous_reglages == {
        "voir",
        "mention",
        "logs",
        "fuseau",
        # Le pont vers la configuration par serveur : chaque serveur a la sienne,
        # sans repli sur la commune, et c'est cette commande qui la reprend.
        "importer",
        # L'allumage par serveur : un module éteint ne publie plus et quitte le
        # menu de ce serveur seul.
        "modules",
        "modules liste",
        "modules activer",
        "modules desactiver",
        "acces",
        "acces ajouter",
        "acces retirer",
        "acces liste",
        "source",
        "source voir",
        "source tester",
        "template",
        "template charger",
        "template voir",
        "template champs",
    }


async def test_les_anciens_groupes_de_reglages_ont_disparu():
    """`/config`, `/source` et `/template` ne sont plus des groupes à part.

    Rangés côte à côte à la racine, rien ne disait lequel réglait quoi : `/config
    voir` ne montrait pas la source, alors que `/source voir` la montrait. Trois
    portes pour une seule pièce.
    """
    bot = await _bot()

    racine = {commande.name for commande in bot.tree.get_commands()}

    assert "config" not in racine
    assert "source" not in racine
    assert "template" not in racine
