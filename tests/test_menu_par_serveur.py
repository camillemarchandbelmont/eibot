"""Un module éteint quitte le menu de son serveur — et refuse d'être appelé.

Dernière moitié de l'étape. La tournée saute déjà les modules éteints
(`tests/test_modules_par_serveur.py`) et `/reglages modules` les allume
(`tests/test_reglages_modules.py`) ; il reste les commandes, qui sont ce qu'on
voit. Un `/filiales` toujours dans la liste après `desactiver filiales` se lirait
comme une commande qui n'a pas marché.

**Deux verrous, et il en faut deux.** Le menu par serveur est le bon : chaque
serveur reçoit la liste des commandes de ses modules allumés. Mais Discord garde
cette liste en cache chez le client, et sans `GUILD_IDS` la synchronisation est
globale — il n'y a alors pas de menu par serveur du tout. Le gardien de l'arbre
refuse donc aussi, à l'exécution, la commande d'un module éteint ici.

Ce qui ne s'éteint jamais : `/reglages`. Il n'appartient à aucun module, et c'est
la seule porte de sortie d'un serveur qui a tout éteint.
"""

import discord

from tests.test_commandes_fourchettes import _bot, _commande
from tests.test_commandes_par_serveur import EMPIRE, VOISIN, _interaction

#: Le menu complet, celui que voit un serveur qui n'a rien éteint.
MENU_COMPLET = {"convertir", "frais", "promos", "fourchette", "filiales", "reglages"}


def _noms(commandes) -> set[str]:
    return {commande.name for commande in commandes}


def _menu_de(bot, serveur_id: int) -> set[str]:
    """Les commandes installées pour ce serveur, et non les globales."""
    return _noms(bot.tree.get_commands(guild=discord.Object(id=serveur_id)))


def _sans_reseau(bot) -> list[int]:
    """Neutralise la poussée à Discord et rend la liste des serveurs poussés.

    `tree.sync` est le seul appel réseau du chemin : sans connexion il lève, et
    ce qui est éprouvé ici est le menu construit, pas la requête HTTP.
    """
    pousses: list[int] = []

    async def sync(guild=None):
        pousses.append(guild.id if guild is not None else None)

    bot.tree.sync = sync
    return pousses


def _tape(bot, nom: str, serveur_id: int):
    """Une interaction sur une vraie commande, comme Discord la livre.

    `interaction.command` est ce que le gardien lit pour savoir de quel module
    relève ce qu'on vient de taper.
    """
    interaction = _interaction(serveur_id)
    interaction.command = _commande(bot, nom)
    return interaction


# --- Quelle commande appartient à quel module -------------------------------


async def test_chaque_module_est_associe_a_ses_commandes():
    """Le lien sans lequel rien de tout ceci n'est possible.

    Il est relevé à la greffe, en regardant ce qui apparaît dans l'arbre pendant
    qu'un module s'enregistre : c'est le seul moment où l'on sait à qui attribuer
    une commande. Une liste écrite à la main oublierait le module suivant.
    """
    bot = await _bot()

    assert bot.commandes_des_modules == {
        "conversion": ("convertir", "frais"),
        "promos": ("promos", "fourchette"),
        "filiales": ("filiales",),
    }


async def test_reglages_nappartient_a_aucun_module():
    """C'est ce qui le rend inextinguible, et c'est voulu : sans lui, un serveur
    qui a tout éteint ne pourrait plus rien rallumer."""
    bot = await _bot()

    assert "reglages" not in bot.module_des_commandes


# --- Le menu d'un serveur ---------------------------------------------------


async def test_le_menu_dun_serveur_neuf_montre_tout():
    """Tout est allumé par défaut : le déploiement ne doit rien retirer."""
    bot = await _bot()

    assert _noms(bot.commandes_du_menu()) == MENU_COMPLET


async def test_un_module_eteint_quitte_le_menu():
    """La moitié visible de l'extinction, et celle que le plan fait vérifier."""
    bot = await _bot()

    menu = _noms(bot.commandes_du_menu(["filiales"]))

    assert menu == MENU_COMPLET - {"filiales"}


async def test_eteindre_un_module_retire_toutes_ses_commandes():
    """Un module peut poser plusieurs commandes à la racine : les oublier
    laisserait `/frais` dans le menu d'un serveur sans calculatrices."""
    bot = await _bot()

    menu = _noms(bot.commandes_du_menu(["conversion"]))

    assert menu == MENU_COMPLET - {"convertir", "frais"}


async def test_reglages_reste_dans_le_menu_quoi_quil_arrive():
    """La porte de sortie. La commande refuse d'éteindre le dernier module, mais
    la base peut arriver là — un module retiré du dépôt, un tiroir repris."""
    bot = await _bot()

    menu = _noms(bot.commandes_du_menu(["conversion", "promos", "filiales"]))

    assert menu == {"reglages"}


# --- L'installation, serveur par serveur ------------------------------------


async def test_chaque_serveur_recoit_son_propre_menu():
    """Le cœur de l'étape : le voisin garde ce que celui-ci a éteint."""
    bot = await _bot()
    pousses = _sans_reseau(bot)
    await bot.store.pour(EMPIRE).eteindre_module("filiales")

    await bot.synchroniser_les_menus([EMPIRE, VOISIN])

    assert _menu_de(bot, EMPIRE) == MENU_COMPLET - {"filiales"}
    assert _menu_de(bot, VOISIN) == MENU_COMPLET
    # Poussé aux deux : un menu construit et jamais envoyé ne change rien à ce
    # qu'on voit dans Discord.
    assert pousses == [EMPIRE, VOISIN]


async def test_le_menu_est_reconstruit_et_non_complete():
    """Rallumer doit rendre la commande, mais éteindre doit la retirer.

    Ajouter sans effacer d'abord laisserait la commande d'un module éteint dans
    le menu jusqu'au redémarrage — et `desactiver` semblerait sans effet.
    """
    bot = await _bot()
    _sans_reseau(bot)
    magasin = bot.store.pour(EMPIRE)

    await bot.synchroniser_le_menu(EMPIRE)
    await magasin.eteindre_module("filiales")
    await bot.synchroniser_le_menu(EMPIRE)

    assert "filiales" not in _menu_de(bot, EMPIRE)


async def test_les_commandes_globales_restent_completes():
    """Le menu d'un serveur est une copie, pas un déménagement.

    Vidées de l'arbre global, les commandes d'un module éteint quelque part
    disparaîtraient de *tous* les serveurs à la synchronisation suivante.
    """
    bot = await _bot()
    _sans_reseau(bot)
    await bot.store.pour(EMPIRE).eteindre_module("filiales")

    await bot.synchroniser_le_menu(EMPIRE)

    assert _noms(bot.tree.get_commands()) == MENU_COMPLET


async def test_une_poussee_qui_echoue_est_avouee_et_non_fatale():
    """Discord limite le débit des synchronisations. Le réglage est déjà écrit :
    le perdre pour une requête refusée serait pire que le menu en retard."""
    bot = await _bot()

    async def sync(guild=None):
        raise RuntimeError("429 Too Many Requests")

    bot.tree.sync = sync

    assert await bot.synchroniser_le_menu(EMPIRE) is False


# --- Après /reglages modules, tout de suite ---------------------------------


async def test_desactiver_rafraichit_le_menu_sans_redemarrage():
    """Le plan le demande explicitement : l'activation est immédiate.

    Sans ce rappel, la commande resterait dans le menu jusqu'au prochain
    déploiement, et l'extinction se lirait comme un réglage sans effet.
    """
    bot = await _bot()
    pousses = _sans_reseau(bot)

    await _commande(bot, "reglages modules desactiver").callback(
        _interaction(EMPIRE), module="filiales"
    )

    assert _menu_de(bot, EMPIRE) == MENU_COMPLET - {"filiales"}
    assert pousses == [EMPIRE]


async def test_activer_rafraichit_le_menu_sans_redemarrage():
    bot = await _bot()
    _sans_reseau(bot)
    await bot.store.pour(EMPIRE).eteindre_module("filiales")
    await bot.synchroniser_le_menu(EMPIRE)

    await _commande(bot, "reglages modules activer").callback(
        _interaction(EMPIRE), module="filiales"
    )

    assert _menu_de(bot, EMPIRE) == MENU_COMPLET


async def test_desactiver_previent_quand_le_menu_na_pas_suivi():
    """Le réglage est pris, le menu non : le taire ferait retaper la commande.
    """
    bot = await _bot()

    async def sync(guild=None):
        raise RuntimeError("429 Too Many Requests")

    bot.tree.sync = sync
    interaction = _interaction(EMPIRE)

    await _commande(bot, "reglages modules desactiver").callback(
        interaction, module="filiales"
    )

    texte = " ".join(interaction.textes)
    assert "✅" in texte and "⚠️" in texte
    # Le réglage, lui, est bien écrit : c'est ce qui rend l'avertissement
    # supportable plutôt qu'inquiétant.
    assert await bot.store.pour(EMPIRE).module_actif("filiales") is False


# --- Le second verrou : le gardien de l'arbre -------------------------------


async def test_une_commande_dun_module_eteint_est_refusee():
    """Le menu ne suffit pas : Discord le garde en cache chez le client, et sans
    `GUILD_IDS` la synchronisation est globale — il n'y a alors aucun menu par
    serveur. Sans ce refus, la commande éteinte resterait utilisable."""
    bot = await _bot()
    await bot.store.pour(EMPIRE).eteindre_module("filiales")
    interaction = _tape(bot, "filiales liste", EMPIRE)

    assert await bot.tree.interaction_check(interaction) is False
    texte = " ".join(interaction.textes)
    assert "Tableau des frais" in texte
    assert "/reglages modules activer" in texte


async def test_une_commande_racine_dun_module_eteint_est_refusee():
    """`/frais` n'est pas dans un groupe : sans remonter au module, le gardien la
    laisserait passer alors que sa calculatrice est éteinte."""
    bot = await _bot()
    await bot.store.pour(EMPIRE).eteindre_module("conversion")

    assert await bot.tree.interaction_check(_tape(bot, "frais", EMPIRE)) is False


async def test_une_commande_dun_module_allume_passe():
    bot = await _bot()
    await bot.store.pour(EMPIRE).eteindre_module("filiales")

    assert await bot.tree.interaction_check(_tape(bot, "promos", EMPIRE)) is True


async def test_une_commande_eteinte_ailleurs_passe_ici():
    """Le refus est propre au serveur, comme l'extinction."""
    bot = await _bot()
    await bot.store.pour(VOISIN).eteindre_module("filiales")

    assert await bot.tree.interaction_check(_tape(bot, "filiales liste", EMPIRE)) is True


async def test_reglages_passe_meme_si_tout_est_eteint():
    """La porte de sortie, au niveau du gardien cette fois : refusée ici, plus
    rien ne pourrait rallumer quoi que ce soit."""
    bot = await _bot()
    magasin = bot.store.pour(EMPIRE)
    for nom in ("conversion", "promos", "filiales"):
        await magasin.eteindre_module(nom)

    passe = await bot.tree.interaction_check(
        _tape(bot, "reglages modules liste", EMPIRE)
    )

    assert passe is True


async def test_hors_dun_serveur_le_gardien_ne_regarde_pas_les_modules():
    """En message privé il n'y a pas de serveur, donc pas de liste d'éteints :
    lever ici ferait échouer la commande au lieu de la laisser répondre."""
    bot = await _bot()
    interaction = _tape(bot, "frais", EMPIRE)
    interaction.guild = None

    assert await bot.tree.interaction_check(interaction) is True


async def test_le_gardien_verifie_toujours_lacces():
    """Les deux contrôles se composent : ajouter celui des modules ne doit pas
    avoir remplacé celui de la liste d'accès."""
    bot = await _bot()
    interaction = _tape(bot, "promos", EMPIRE)
    interaction.user.guild_permissions.administrator = False

    assert await bot.tree.interaction_check(interaction) is False
    assert "autorisés" in " ".join(interaction.textes)
