"""Un module éteint quitte le menu de son serveur — et refuse d'être appelé.

Dernière moitié de l'étape. La tournée saute déjà les modules éteints
(`tests/test_modules_par_serveur.py`) et `/reglages modules` les allume
(`tests/test_reglages_modules.py`) ; il reste les commandes, qui sont ce qu'on
voit. Un `/frais` toujours dans la liste après `desactiver frais` se lirait
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

from src.modules import Module, decouvrir

from tests.test_commandes_fourchettes import _bot, _commande
from tests.test_commandes_par_serveur import EMPIRE, VOISIN, _interaction

#: Le menu complet, celui que voit un serveur qui n'a rien éteint.
MENU_COMPLET = {
    "convertir",
    "promos",
    "frais",
    "reglages",
}

#: Les modules du dossier, dans l'ordre de leur rang. Nommés ici pour que l'ajout
#: d'un quatrième casse ce fichier plutôt que de passer inaperçu.
TOUS_LES_MODULES = ("conversion", "promos", "frais")


#: Les deux commandes nues que le module d'essai pose à la racine.
ESSAIS = ("essai-un", "essai-deux")


def _module_dessai() -> Module:
    """Un module qui pose deux commandes **nues** à la racine.

    Plus aucun module livré ne le fait : tout ce que le dossier apporte est un
    groupe, depuis que les calculatrices sont rangées sous `/convertir`. Le
    contrat l'autorise pourtant, et deux mécanismes en dépendent — l'attribution
    prise à la greffe, qui doit retenir *toutes* les commandes d'un module, et le
    gardien, qui remonte d'une sous-commande à sa racine ou prend la commande
    elle-même quand il n'y a pas de groupe.

    Éprouvés seulement sur les modules du dossier, ces deux mécanismes ne le
    seraient plus que sur des groupes, et la panne n'apparaîtrait qu'au module
    qui reviendrait à une commande nue.
    """

    def enregistrer(bot) -> None:
        for nom in ESSAIS:
            @bot.tree.command(name=nom, description="Commande d'essai à la racine")
            async def commande_dessai(interaction) -> None:
                await interaction.response.send_message("essai", ephemeral=True)

    return Module(
        nom="essai",
        titre="Module d'essai",
        description="Deux commandes nues à la racine, pour éprouver le cas général.",
        # Après tous les autres : l'ordre décide de la place dans le menu, et ce
        # module n'a pas à déplacer ceux du dossier.
        ordre=999,
        enregistrer=enregistrer,
    )


async def _bot_avec_essai(monkeypatch):
    """Le vrai bot, plus le module d'essai, greffé comme les autres.

    Le balayage est détourné plutôt que le relevé écrit à la main : ce qui doit
    être éprouvé est justement ce que la greffe retient, et le lui souffler
    rendrait le test vrai quoi que fasse la greffe.
    """
    vrais, _ = decouvrir()
    monkeypatch.setattr("src.bot.decouvrir", lambda: ([*vrais, _module_dessai()], {}))
    return await _bot()


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
        "conversion": ("convertir",),
        "promos": ("promos",),
        "frais": ("frais",),
    }


async def test_le_tableau_des_frais_seteint_sous_le_nom_quon_tape():
    """`desactiver frais` doit nommer ce qui va disparaître : `/frais`.

    Le module s'appelait `filiales` et posait `/filiales` — le nom collait. Rebaptiser
    la commande sans rebaptiser le module aurait cassé ce lien, et `/reglages modules
    liste` aurait cité un `filiales` qu'on ne trouve plus dans le menu.
    """
    bot = await _bot()

    assert bot.commandes_des_modules["frais"] == ("frais",)


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

    menu = _noms(bot.commandes_du_menu(["frais"]))

    assert menu == MENU_COMPLET - {"frais"}


async def test_eteindre_un_module_retire_toutes_ses_commandes(monkeypatch):
    """Un module peut poser plusieurs commandes à la racine.

    Oublier la seconde laisserait dans le menu d'un serveur une commande dont le
    module est éteint, et que plus rien ne pourrait en retirer.
    """
    bot = await _bot_avec_essai(monkeypatch)

    menu = _noms(bot.commandes_du_menu(["essai"]))

    assert menu == MENU_COMPLET
    assert not set(ESSAIS) & menu


async def test_reglages_reste_dans_le_menu_quoi_quil_arrive():
    """La porte de sortie. La commande refuse d'éteindre le dernier module, mais
    la base peut arriver là — un module retiré du dépôt, un tiroir repris."""
    bot = await _bot()

    menu = _noms(bot.commandes_du_menu(TOUS_LES_MODULES))

    assert menu == {"reglages"}


# --- L'installation, serveur par serveur ------------------------------------


async def test_chaque_serveur_recoit_son_propre_menu():
    """Le cœur de l'étape : le voisin garde ce que celui-ci a éteint."""
    bot = await _bot()
    pousses = _sans_reseau(bot)
    await bot.store.pour(EMPIRE).eteindre_module("frais")

    await bot.synchroniser_les_menus([EMPIRE, VOISIN])

    assert _menu_de(bot, EMPIRE) == MENU_COMPLET - {"frais"}
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
    await magasin.eteindre_module("frais")
    await bot.synchroniser_le_menu(EMPIRE)

    assert "frais" not in _menu_de(bot, EMPIRE)


async def test_les_commandes_globales_restent_completes():
    """Le menu d'un serveur est une copie, pas un déménagement.

    Vidées de l'arbre global, les commandes d'un module éteint quelque part
    disparaîtraient de *tous* les serveurs à la synchronisation suivante.
    """
    bot = await _bot()
    _sans_reseau(bot)
    await bot.store.pour(EMPIRE).eteindre_module("frais")

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
        _interaction(EMPIRE), module="frais"
    )

    assert _menu_de(bot, EMPIRE) == MENU_COMPLET - {"frais"}
    assert pousses == [EMPIRE]


async def test_activer_rafraichit_le_menu_sans_redemarrage():
    bot = await _bot()
    _sans_reseau(bot)
    await bot.store.pour(EMPIRE).eteindre_module("frais")
    await bot.synchroniser_le_menu(EMPIRE)

    await _commande(bot, "reglages modules activer").callback(
        _interaction(EMPIRE), module="frais"
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
        interaction, module="frais"
    )

    texte = " ".join(interaction.textes)
    assert "✅" in texte and "⚠️" in texte
    # Le réglage, lui, est bien écrit : c'est ce qui rend l'avertissement
    # supportable plutôt qu'inquiétant.
    assert await bot.store.pour(EMPIRE).module_actif("frais") is False


# --- Le second verrou : le gardien de l'arbre -------------------------------


async def test_une_commande_dun_module_eteint_est_refusee():
    """Le menu ne suffit pas : Discord le garde en cache chez le client, et sans
    `GUILD_IDS` la synchronisation est globale — il n'y a alors aucun menu par
    serveur. Sans ce refus, la commande éteinte resterait utilisable."""
    bot = await _bot()
    await bot.store.pour(EMPIRE).eteindre_module("frais")
    interaction = _tape(bot, "frais liste", EMPIRE)

    assert await bot.tree.interaction_check(interaction) is False
    texte = " ".join(interaction.textes)
    assert "Tableau des frais" in texte
    assert "/reglages modules activer" in texte


async def test_une_commande_racine_dun_module_eteint_est_refusee(monkeypatch):
    """Une commande nue n'a pas de groupe au-dessus d'elle.

    Le gardien remonte d'une sous-commande à sa racine ; sans le cas où il n'y a
    rien à remonter, il chercherait le module d'un `None` et lèverait — la
    commande échouerait au lieu d'être refusée ou acceptée.
    """
    bot = await _bot_avec_essai(monkeypatch)
    await bot.store.pour(EMPIRE).eteindre_module("essai")

    assert await bot.tree.interaction_check(_tape(bot, "essai-un", EMPIRE)) is False


async def test_une_commande_dun_module_allume_passe():
    bot = await _bot()
    await bot.store.pour(EMPIRE).eteindre_module("frais")

    assert await bot.tree.interaction_check(_tape(bot, "promos chercher", EMPIRE)) is True


async def test_une_commande_eteinte_ailleurs_passe_ici():
    """Le refus est propre au serveur, comme l'extinction."""
    bot = await _bot()
    await bot.store.pour(VOISIN).eteindre_module("frais")

    assert await bot.tree.interaction_check(_tape(bot, "frais liste", EMPIRE)) is True


async def test_reglages_passe_meme_si_tout_est_eteint():
    """La porte de sortie, au niveau du gardien cette fois : refusée ici, plus
    rien ne pourrait rallumer quoi que ce soit."""
    bot = await _bot()
    magasin = bot.store.pour(EMPIRE)
    for nom in TOUS_LES_MODULES:
        await magasin.eteindre_module(nom)

    passe = await bot.tree.interaction_check(
        _tape(bot, "reglages modules liste", EMPIRE)
    )

    assert passe is True


async def test_hors_dun_serveur_le_gardien_ne_regarde_pas_les_modules():
    """En message privé il n'y a pas de serveur, donc pas de liste d'éteints :
    lever ici ferait échouer la commande au lieu de la laisser répondre."""
    bot = await _bot()
    interaction = _tape(bot, "convertir frais", EMPIRE)
    interaction.guild = None

    assert await bot.tree.interaction_check(interaction) is True


async def test_le_gardien_verifie_toujours_lacces():
    """Les deux contrôles se composent : ajouter celui des modules ne doit pas
    avoir remplacé celui de la liste d'accès."""
    bot = await _bot()
    interaction = _tape(bot, "promos chercher", EMPIRE)
    interaction.user.guild_permissions.administrator = False

    assert await bot.tree.interaction_check(interaction) is False
    assert "autorisés" in " ".join(interaction.textes)
