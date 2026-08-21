"""L'épreuve finale du contrat de module : deux publications dans un fichier.

Le plan la demande nommément, et le module qu'elle éprouve est **jetable** —
`src/modules/politesse.py`, un « bonjour » le matin et un « bonsoir » le soir. Ce
qui est en jeu n'est pas la politesse : c'est de savoir si un module écrit *après*
le chantier obtient tout ce que les trois modules historiques ont, sans qu'on
touche à quoi que ce soit d'autre.

Ces trois-là ne peuvent pas répondre à la question. Chacun fournit ses accès à
l'heure, aux salons et à la trace de passage, parce que ses données existaient
avant les modules — aucun n'emprunte donc le tiroir générique. Aucun ne déclare
deux publications. Aucun ne pose deux commandes de publication à la racine. Un
module neuf fait les trois d'un coup, et c'est la seule façon de voir ce qui
manque avant le jour où l'on en aura besoin.

Ce fichier part avec le module qu'il éprouve : c'est un `git revert`, une fois la
vérification faite dans Discord.
"""

from datetime import datetime

from src.modules import decouvrir
from src.modules import politesse as module_politesse
from src.tournee import faire_la_tournee

from tests.test_commandes_fourchettes import _bot, _commande
from tests.test_commandes_par_serveur import EMPIRE, VOISIN, _interaction
from tests.test_menu_par_serveur import _menu_de, _sans_reseau, _tape
from tests.test_publication_par_serveur import EMPIRE as SERVEUR_EMPIRE
from tests.test_publication_par_serveur import SalonFactice
from tests.test_publication_par_serveur import _bot as _bot_publiant
from tests.test_reglages_modules import _lignes

#: Les deux commandes que le module pose à la racine, une par publication.
LES_DEUX = {"bonjour", "bonsoir"}


async def _publiant(matin: SalonFactice, soir: SalonFactice):
    """Un bot qui ne porte que ce module, et ses deux salons de destination.

    Un salon par publication, et non un seul : c'est ce qui permet de voir que le
    post du matin ne part pas dans le salon du soir. Une liste partagée rendrait
    l'assertion vraie sans rien dire des salons.
    """
    bot = await _bot_publiant(
        [SERVEUR_EMPIRE], {1: matin, 2: soir}, [module_politesse.MODULE]
    )
    magasin = bot.store.pour(EMPIRE)
    await magasin.set("publication:bonjour:salons", ["1"])
    await magasin.set("publication:bonsoir:salons", ["2"])
    return bot, magasin


# --- Un fichier posé dans le dossier suffit ---------------------------------


def test_le_dossier_livre_le_quatrieme_module():
    """Le balayage réel, et non une liste : c'est la promesse du système.

    Un fichier qui refuserait de se charger apparaîtrait dans `refuses` sans
    qu'aucun autre test ne le voie — le bot démarre quand même, par construction.
    """
    charges, refuses = decouvrir()

    assert refuses == {}
    assert "politesse" in {module.nom for module in charges}


def test_un_seul_fichier_declare_deux_publications():
    """Le cœur de l'épreuve : rien ne plafonne le nombre de posts quotidiens."""
    assert [p.cle for p in module_politesse.MODULE.publications] == [
        "bonjour",
        "bonsoir",
    ]


def test_les_deux_publications_nempruntent_que_le_tiroir_generique():
    """Aucun accès déclaré : ni heure, ni salons, ni trace de passage.

    C'est ce qu'aucun module historique n'éprouve, et c'est le vrai coût d'une
    publication de plus. Un seul accès déclaré ici, et tout ce fichier ne dirait
    plus rien du module que quelqu'un écrira sans reprise de données à faire.
    """
    accesseurs = (
        "lire_heure",
        "ecrire_heure",
        "lire_derniere",
        "marquer",
        "lire_salons",
        "ajouter_salon",
        "retirer_salon",
    )
    for publication in module_politesse.MODULE.publications:
        for nom in accesseurs:
            assert getattr(publication, nom) is None, f"{publication.cle}.{nom}"


# --- Il apparaît dans /reglages modules liste --------------------------------


async def test_la_liste_des_modules_le_cite():
    """L'épreuve telle que le plan l'écrit. Trouvé mais absent de la liste, il ne
    pourrait ni s'allumer ni s'éteindre, et rien ne dirait qu'il est là."""
    bot = await _bot()
    interaction = _interaction(EMPIRE)

    await _commande(bot, "reglages modules liste").callback(interaction)

    rendu = _lignes(interaction.embeds[0])
    assert "politesse" in rendu, rendu
    assert module_politesse.MODULE.titre in rendu, rendu


# --- Il reçoit ses propres commandes ----------------------------------------


async def test_chaque_publication_recoit_le_vocabulaire_complet():
    """`/bonjour heure` et `/bonjour apercu`, sans un mot inventé ni réécrit.

    Les deux, et pas seulement la première : c'est le point où un contrat qui ne
    saurait porter qu'une publication par module lâcherait.
    """
    bot = await _bot()

    noms = {commande.qualified_name for commande in bot.tree.walk_commands()}

    for racine in sorted(LES_DEUX):
        assert {
            f"{racine} heure",
            f"{racine} apercu",
            f"{racine} publier",
            f"{racine} salon ajouter",
            f"{racine} salon retirer",
        } <= noms, racine


async def test_ses_deux_commandes_racine_lui_sont_attribuees():
    """Le relevé pris à la greffe doit tenir pour un module qui en pose deux.

    Une seule retenue, l'autre resterait dans le menu d'un serveur qui a éteint
    le module, sans que rien ne puisse l'en retirer.
    """
    bot = await _bot()

    assert bot.commandes_des_modules["politesse"] == ("bonjour", "bonsoir")


async def test_son_heure_se_regle_dans_le_serveur_ou_on_la_tape():
    """Le tiroir générique est cloisonné par serveur sans une ligne de plomberie.

    C'est ce que le module ne déclare pas et obtient quand même : la vue du
    serveur préfixe la clé, et la publication n'a rien à en savoir.
    """
    bot = await _bot()

    await _commande(bot, "bonjour heure").callback(
        _interaction(EMPIRE), heure="07:15"
    )

    assert await bot.store.pour(EMPIRE).get("publication:bonjour:heure") == "07:15"
    assert await bot.store.pour(VOISIN).get("publication:bonjour:heure") is None
    assert await bot.store.get("publication:bonjour:heure") is None


async def test_regler_lheure_du_matin_ne_deplace_pas_celle_du_soir():
    """Deux publications, deux horaires indépendants — dans un seul fichier.

    Un tiroir partagé les collerait l'un à l'autre, et le second post ne partirait
    jamais : le premier aurait déjà marqué la journée.
    """
    bot = await _bot()

    await _commande(bot, "bonjour heure").callback(
        _interaction(EMPIRE), heure="07:15"
    )
    await _commande(bot, "bonsoir heure").callback(
        _interaction(EMPIRE), heure="22:45"
    )

    magasin = bot.store.pour(EMPIRE)
    assert await magasin.get("publication:bonjour:heure") == "07:15"
    assert await magasin.get("publication:bonsoir:heure") == "22:45"


async def test_son_apercu_montre_le_post_du_matin():
    """L'autre commande que le plan nomme. Elle prépare vraiment le contenu :
    un aperçu qui montrerait un gabarit vide ne dirait rien de ce qui sortira."""
    bot = await _bot()
    await bot.store.pour(EMPIRE).set("publication:bonjour:salons", ["1"])
    interaction = _interaction(EMPIRE)

    await _commande(bot, "bonjour apercu").callback(interaction)

    titres = [embed.title or "" for embed in interaction.embeds]
    assert any("onjour" in titre for titre in titres), titres


# --- Chaque post part à son heure, dans ses salons --------------------------


async def test_le_matin_seul_part_a_lheure_du_matin():
    """L'heure de chaque publication est la sienne, et son salon aussi.

    L'instant est passé explicitement plutôt que lu à l'horloge : un test qui
    comparerait 08:00 à l'heure réelle serait juste une minute par jour.
    """
    matin, soir = SalonFactice(1, SERVEUR_EMPIRE), SalonFactice(2, SERVEUR_EMPIRE)
    bot, magasin = await _publiant(matin, soir)

    for publication in bot.publications():
        await faire_la_tournee(publication, bot, magasin, datetime(2026, 8, 21, 8, 0))

    assert len(matin.envois) == 1
    assert soir.envois == []


async def test_le_soir_seul_part_a_lheure_du_soir():
    """Le miroir du précédent : sans lui, une publication qui ne partirait jamais
    passerait pour un « chacun à son heure » réussi."""
    matin, soir = SalonFactice(1, SERVEUR_EMPIRE), SalonFactice(2, SERVEUR_EMPIRE)
    bot, magasin = await _publiant(matin, soir)

    for publication in bot.publications():
        await faire_la_tournee(publication, bot, magasin, datetime(2026, 8, 21, 20, 0))

    assert matin.envois == []
    assert len(soir.envois) == 1


async def test_chaque_publication_garde_sa_propre_trace_de_passage():
    """La trace du matin ne doit pas consommer la journée du soir."""
    matin, soir = SalonFactice(1, SERVEUR_EMPIRE), SalonFactice(2, SERVEUR_EMPIRE)
    bot, magasin = await _publiant(matin, soir)

    for publication in bot.publications():
        await faire_la_tournee(publication, bot, magasin, datetime(2026, 8, 21, 8, 0))

    assert await magasin.get("publication:bonjour:derniere") == "2026-08-21"
    assert await magasin.get("publication:bonsoir:derniere") is None


# --- Il s'éteint par serveur ------------------------------------------------


async def test_eteint_il_quitte_le_menu_de_ce_serveur_seul():
    """Ses deux commandes partent ensemble : le module est l'unité qu'on éteint."""
    bot = await _bot()
    _sans_reseau(bot)
    await bot.store.pour(EMPIRE).eteindre_module("politesse")

    await bot.synchroniser_les_menus([EMPIRE, VOISIN])

    assert LES_DEUX & _menu_de(bot, EMPIRE) == set()
    assert LES_DEUX <= _menu_de(bot, VOISIN)


async def test_eteint_ses_commandes_sont_refusees_ici():
    """Le second verrou : Discord garde le menu en cache chez le client."""
    bot = await _bot()
    await bot.store.pour(EMPIRE).eteindre_module("politesse")

    interaction = _tape(bot, "bonsoir heure", EMPIRE)

    assert await bot.tree.interaction_check(interaction) is False


async def test_eteint_ses_deux_posts_quittent_la_tournee():
    """Les deux d'un coup. Une seule retirée, le serveur recevrait encore un post
    d'un module qu'il a éteint — et rien dans `/reglages` ne l'expliquerait."""
    matin, soir = SalonFactice(1, SERVEUR_EMPIRE), SalonFactice(2, SERVEUR_EMPIRE)
    bot, magasin = await _publiant(matin, soir)
    await magasin.eteindre_module("politesse")

    assert bot.publications(await magasin.modules_eteints()) == []
