"""Chaque serveur allume les modules qu'il veut.

Dernière étape du plan. Les trois précédentes ont donné des modules (un fichier
chacun), un vocabulaire commun et une configuration par serveur ; il manque de
pouvoir dire « chez moi, pas de tableau des frais » sans l'enlever aux autres.

Deux règles gouvernent le stockage, et expliquent qu'on retienne les modules
**éteints** plutôt que les allumés :

- **tout est allumé par défaut**, donc rien ne bouge tant qu'on ne demande rien ;
- un module qui arrive par un déploiement est allumé partout d'office. Retenir
  les allumés obligerait à visiter chaque serveur pour l'y ajouter, et personne
  ne saurait qu'il faut le faire.

Un module éteint ne publie plus rien dans ce serveur, et ses commandes quittent
son menu. Ce fichier éprouve les deux, plus le refus d'éteindre le dernier : un
serveur sans aucun module ressemblerait trait pour trait à une panne du bot.
"""

from src.db import PREFIXE_SERVEUR, Store

from tests.test_publication_par_serveur import (
    EMPIRE,
    FILIALE,
    SalonFactice,
    _bot,
    _fourchette_dans,
    _module_dessai,
)

EMPIRE_ID = str(EMPIRE.id)
FILIALE_ID = str(FILIALE.id)


async def _store() -> Store:
    store = Store(dsn="")
    await store.connect()
    return store


# --- Le stockage : les éteints, et rien d'autre ------------------------------


async def test_tout_est_allume_dans_un_serveur_neuf():
    """Le déploiement ne doit rien éteindre. Un serveur qui se réveille sans
    aucun module aurait l'air en panne, et personne ne devinerait quoi rallumer.
    """
    magasin = (await _store()).pour(EMPIRE_ID)

    assert await magasin.modules_eteints() == []
    assert await magasin.module_actif("filiales") is True


async def test_un_module_arrive_apres_coup_est_allume_partout():
    """C'est pour ça que la base retient les éteints et non les allumés.

    Un module posé par un déploiement n'est écrit dans aucune configuration :
    listé comme allumé, il faudrait passer dans chaque serveur pour l'y ajouter,
    sans que rien ne dise qu'il faut le faire.
    """
    magasin = (await _store()).pour(EMPIRE_ID)
    await magasin.eteindre_module("filiales")

    assert await magasin.module_actif("bonjour") is True


async def test_eteindre_un_module_ne_leteint_que_dans_son_serveur():
    store = await _store()
    await store.pour(EMPIRE_ID).eteindre_module("filiales")

    assert await store.pour(EMPIRE_ID).module_actif("filiales") is False
    assert await store.pour(FILIALE_ID).module_actif("filiales") is True
    assert await store.module_actif("filiales") is True


async def test_eteindre_un_module_deja_eteint_le_dit():
    """La commande s'en sert pour répondre « il était déjà éteint » au lieu d'un
    « ✅ » qui ferait croire à un changement."""
    magasin = (await _store()).pour(EMPIRE_ID)
    await magasin.eteindre_module("filiales")

    assert await magasin.eteindre_module("filiales") is False
    assert await magasin.modules_eteints() == ["filiales"]


async def test_rallumer_un_module():
    magasin = (await _store()).pour(EMPIRE_ID)
    await magasin.eteindre_module("filiales")

    assert await magasin.rallumer_module("filiales") is True
    assert await magasin.modules_eteints() == []


async def test_rallumer_un_module_deja_allume_le_dit():
    magasin = (await _store()).pour(EMPIRE_ID)

    assert await magasin.rallumer_module("filiales") is False


async def test_la_liste_videe_est_bien_enregistree():
    """Rallumer le dernier module écrit une liste vide, et doit l'écrire.

    Sauter les valeurs vides est une économie qui se fait tout seule en relisant
    le code : le module resterait rallumé jusqu'au redémarrage, puis s'éteindrait
    de lui-même là où personne ne regarde.
    """
    store = await _store()
    magasin = store.pour(EMPIRE_ID)
    await magasin.eteindre_module("filiales")
    await magasin.rallumer_module("filiales")

    # Relu dans la base, et non par l'accesseur : une liste vide et une clé
    # jamais écrite se lisent pareil à travers `modules_eteints`.
    config = await store.get(f"{PREFIXE_SERVEUR}:{EMPIRE_ID}:config")
    assert config["modules_eteints"] == []


# --- Un module éteint ne publie plus ----------------------------------------


async def test_un_module_eteint_ne_publie_plus_dans_ce_serveur():
    """La moitié visible de l'extinction : le post du soir ne sort plus.

    Sans elle, `desactiver` ne ferait que retirer les commandes du menu, et le
    tableau continuerait de tomber chaque jour dans le salon.
    """
    module, envoyes = _module_dessai(("essai",))
    salons = {1: SalonFactice(1, EMPIRE)}
    bot = await _bot([EMPIRE], salons, [module])
    await _fourchette_dans(bot.store.pour(EMPIRE_ID), "1")
    await bot.store.pour(EMPIRE_ID).eteindre_module("essai")

    await bot.publier_tout(forcer=True)

    assert envoyes == []
    assert salons[1].envois == []


async def test_un_module_eteint_chez_lun_publie_encore_chez_lautre():
    """Tout l'intérêt de l'étape : éteindre chez soi n'éteint pas chez le voisin.
    """
    module, envoyes = _module_dessai(("essai",), salons=("1", "2"))
    salons = {1: SalonFactice(1, EMPIRE), 2: SalonFactice(2, FILIALE)}
    bot = await _bot([EMPIRE, FILIALE], salons, [module])
    await _fourchette_dans(bot.store.pour(EMPIRE_ID), "1")
    await _fourchette_dans(bot.store.pour(FILIALE_ID), "2")
    await bot.store.pour(EMPIRE_ID).eteindre_module("essai")

    await bot.publier_tout(forcer=True)

    assert envoyes == [(FILIALE_ID, "essai")]


async def test_un_autre_module_du_meme_serveur_publie_toujours():
    """Éteindre un module n'est pas éteindre le bot : ce qui reste allumé sort.
    """
    eteint, jamais = _module_dessai(("eteint",), nom="eteint")
    allume, envoyes = _module_dessai(("allume",), nom="allume")
    salons = {1: SalonFactice(1, EMPIRE)}
    bot = await _bot([EMPIRE], salons, [eteint, allume])
    await _fourchette_dans(bot.store.pour(EMPIRE_ID), "1")
    await bot.store.pour(EMPIRE_ID).eteindre_module("eteint")

    await bot.publier_tout(forcer=True)

    assert jamais == []
    assert envoyes == [(EMPIRE_ID, "allume")]


async def test_le_compte_rendu_avoue_un_serveur_sans_module_allume():
    """Un nom de serveur suivi de rien se lirait comme « tout va bien ».

    La commande refuse d'éteindre le dernier module, mais la base peut arriver
    dans cet état — un module retiré du dépôt, un tiroir repris à la main.
    """
    module, _ = _module_dessai(("essai",))
    bot = await _bot([EMPIRE], {1: SalonFactice(1, EMPIRE)}, [module])
    await bot.store.pour(EMPIRE_ID).eteindre_module("essai")

    assert "aucune publication" in await bot.publier_tout(forcer=True)
