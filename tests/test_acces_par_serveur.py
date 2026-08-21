"""La liste d'accès appartient au serveur, comme le reste de la configuration.

Le plan le dit : « sa liste d'accès ». C'est le dernier réglage encore commun, et
le plus lourd de conséquences — **inviter le bot dans un serveur donnait les clés
de tous les autres**, puisque `est_admin` se lit dans le serveur où la commande
est tapée et que la liste, elle, valait partout.

Deux moitiés inséparables, d'où un fichier pour les deux :

- le **gardien**, `ArbreProtege.autorisation`, qui laisse entrer ou refuse ;
- les **commandes** `/reglages acces ajouter|retirer|liste`, qui tiennent la
  liste que le gardien relit.

Cloisonner l'une sans l'autre serait pire que de ne rien faire : une liste écrite
dans le tiroir du serveur mais relue dans le commun n'autoriserait plus personne,
et l'inverse afficherait une liste que le gardien n'applique pas.

Il n'y a **pas de repli** ici non plus : un serveur qui n'a pas tapé
`/reglages importer` n'a personne dans sa liste. Ses administrateurs passent
toujours — c'est ce qui laisse `/reglages importer` typable, et ce serait sans
issue autrement.

Une conséquence assumée, à savoir : le site de contrôle lit la liste **commune**,
faute de dire de quel serveur il parle. `/reglages importer` recopie la liste
d'alors, donc personne ne perd le site aujourd'hui ; mais un membre ajouté
désormais dans un serveur n'ouvre plus le site. Le raccorder est le chantier à
part que le plan garde pour plus tard.
"""

import importlib
import inspect

from src.commandes import pour_ce_serveur
from src.modules import noms_de_modules

from tests.test_commandes_acces import Membre
from tests.test_commandes_fourchettes import InteractionFactice, ServeurFactice, _bot
from tests.test_commandes_par_serveur import EMPIRE, VOISIN, _commande, _interaction


def _tape_par(serveur_id: int, membre_id: int, admin: bool = False):
    """Une commande tapée dans `serveur_id` par un membre qui n'est pas admin.

    L'auteur compte ici, contrairement au reste des tests par serveur : c'est lui
    que le gardien cherche dans la liste.
    """
    interaction = InteractionFactice(admin=admin, membre_id=membre_id)
    interaction.guild = ServeurFactice(serveur_id)
    return interaction


# --- Le gardien lit la liste du serveur où l'on tape ------------------------


async def test_le_gardien_laisse_entrer_qui_est_autorise_dans_ce_serveur():
    """Le cas de base, et celui qui casse tout s'il est manqué : la liste écrite
    par la commande doit être celle que le gardien relit, sinon plus personne
    n'entre — sauf les administrateurs."""
    bot = await _bot()
    await bot.store.pour(EMPIRE).autoriser("42")

    assert await bot.tree.autorisation(_tape_par(EMPIRE, 42)) is True


async def test_un_membre_autorise_ailleurs_est_refuse_ici():
    """Le vrai danger de l'ancien état : inviter le bot dans un serveur donnait
    ses clés à tous les autres.

    Un administrateur d'une entreprise pouvait autoriser qui il voulait, et
    l'autorisé se servait des commandes de toutes les entreprises.
    """
    bot = await _bot()
    await bot.store.pour(EMPIRE).autoriser("42")
    # Le voisin a sa liste, non vide : ce qui est en jeu est *laquelle* est lue,
    # pas le refus quand il n'y en a aucune.
    await bot.store.pour(VOISIN).autoriser("7")
    interaction = _tape_par(VOISIN, 42)

    assert await bot.tree.autorisation(interaction) is False
    assert "❌" in interaction.textes[0]


async def test_la_liste_commune_nouvre_plus_les_commandes_dun_serveur():
    """Pas de repli : la liste d'avant le cloisonnement n'autorise plus rien.

    C'est le prix de `/reglages importer`, assumé pour la liste comme pour les
    fourchettes — un repli laisserait entrer dans un serveur neuf des membres
    autorisés par une autre entreprise, ce qui est exactement le trou qu'on
    ferme.
    """
    bot = await _bot()
    await bot.store.autoriser("42")

    assert await bot.tree.autorisation(_tape_par(EMPIRE, 42)) is False


async def test_un_administrateur_passe_dans_un_serveur_qui_na_rien_repris():
    """Sans issue autrement : c'est lui qui tape `/reglages importer`.

    Un serveur neuf a une liste vide par construction. Si son administrateur
    était refusé, il ne pourrait plus jamais rien régler — le bot serait muet et
    inatteignable.
    """
    bot = await _bot()

    assert await bot.tree.autorisation(_tape_par(EMPIRE, 1, admin=True)) is True


async def test_hors_dun_serveur_le_gardien_lit_encore_la_liste_commune():
    """En message privé il n'y a pas de serveur dont lire la liste.

    Chercher `interaction.guild.id` y lèverait `AttributeError` dans le gardien,
    donc avant chaque commande : le bot répondrait « une erreur est survenue » à
    tout, au lieu de refuser proprement.
    """
    bot = await _bot()
    await bot.store.autoriser("42")
    interaction = _tape_par(EMPIRE, 42)
    interaction.guild = None

    assert await bot.tree.autorisation(interaction) is True


# --- Les commandes tiennent la liste de leur serveur ------------------------


async def test_ajouter_nautorise_que_dans_son_serveur():
    bot = await _bot()

    await _commande(bot, "reglages acces ajouter").callback(
        _interaction(EMPIRE), Membre(42)
    )

    assert await bot.store.pour(EMPIRE).autorises() == ["42"]
    assert await bot.store.pour(VOISIN).autorises() == []
    assert await bot.store.autorises() == []


async def test_retirer_ne_touche_que_la_liste_de_son_serveur():
    """Deux entreprises peuvent employer la même personne : la retirer de l'une
    ne doit pas la mettre dehors chez l'autre."""
    bot = await _bot()
    await bot.store.pour(EMPIRE).autoriser("42")
    await bot.store.pour(VOISIN).autoriser("42")

    await _commande(bot, "reglages acces retirer").callback(
        _interaction(EMPIRE), Membre(42)
    )

    assert await bot.store.pour(EMPIRE).autorises() == []
    assert await bot.store.pour(VOISIN).autorises() == ["42"]


async def test_la_liste_affichee_est_celle_de_son_serveur():
    """Afficher celle du commun ferait croire à un réglage en place, et personne
    ne comprendrait pourquoi les cités sont refusés."""
    bot = await _bot()
    await bot.store.autoriser("7")
    await bot.store.pour(EMPIRE).autoriser("42")
    interaction = _interaction(EMPIRE)

    await _commande(bot, "reglages acces liste").callback(interaction)

    champs = repr(interaction.embeds[0].to_dict()["fields"])
    assert "42" in champs
    assert "7" not in champs


# --- Plus aucune commande ne touche la configuration commune ----------------


def test_aucune_commande_ne_parle_au_magasin_commun():
    """La règle de `pour_ce_serveur`, vérifiable d'un coup d'œil.

    Chaque commande cloisonnée l'a été à la main, et une commande ajoutée plus
    tard qui écrirait `bot.store` lirait la configuration commune sans que rien
    ne s'en plaigne : elle répondrait « ✅ » à un réglage que la tournée de ce
    serveur ne lira jamais, et déplacerait celui du voisin.

    Ce test structurel nomme le fautif à la place. `src/api.py` n'y est pas : le
    site de contrôle ne dit pas de quel serveur il parle, et reste sur le commun
    par le plan.
    """
    modules = [
        importlib.import_module(nom)
        for nom in ("src.commandes", "src.reglages")
        + tuple(f"src.modules.{nom}" for nom in noms_de_modules())
    ]
    #: La seule porte, et la seule ligne autorisée à la franchir.
    porte = inspect.getsource(pour_ce_serveur)

    fautives = {
        module.__name__: [
            ligne.strip()
            for ligne in inspect.getsource(module).replace(porte, "").splitlines()
            if "bot.store" in ligne or "self.store" in ligne
        ]
        for module in modules
    }
    fautives = {nom: lignes for nom, lignes in fautives.items() if lignes}

    assert not fautives, (
        f"ces lignes parlent à la configuration commune au lieu de passer par "
        f"pour_ce_serveur : {fautives}"
    )
