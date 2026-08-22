"""Le plafond est rangé **dans** la fourchette, comme ses bornes et ses salons.

Là plutôt que dans la configuration du serveur, parce que le besoin y vit : une
fourchette large sur les petits prix trouve quarante bâtiments et l'on n'en veut
que cinq, tandis que la fourchette des très gros n'en trouve que deux et qu'on
les veut tous. Un plafond commun aux deux ne pourrait servir qu'à l'une.

Deux conséquences que ces tests figent : il survit à un changement de bornes —
`/promos prix` ne doit pas effacer un réglage qu'il ne mentionne pas — et il est
lu défensivement, la configuration étant du JSON retouchable à la main.
"""

from decimal import Decimal

from src.db import Store, plafond_fourchette

from tests.test_publication_par_serveur import EMPIRE, FILIALE

EMPIRE_ID = str(EMPIRE.id)
FILIALE_ID = str(FILIALE.id)


async def _store() -> Store:
    store = Store(dsn="")
    await store.connect()
    return store


async def _magasin(serveur_id: str = EMPIRE_ID):
    magasin = (await _store()).pour(serveur_id)
    await magasin.ajouter_fourchette("grosses", Decimal(100), Decimal(900))
    return magasin


async def _grosses(magasin) -> dict:
    return (await magasin.fourchettes())[0]


# --- Une fourchette neuve n'a pas de plafond --------------------------------


async def test_une_fourchette_neuve_ne_plafonne_rien():
    """Le défaut est le comportement d'avant : un déploiement ne doit pas
    raccourcir les posts de tout le monde sans que personne l'ait demandé."""
    magasin = await _magasin()

    assert plafond_fourchette(await _grosses(magasin)) is None


async def test_un_plafond_retouche_a_la_main_ne_casse_rien():
    """`plafond` peut valoir n'importe quoi : la config est du JSON éditable.

    Une valeur illisible est lue comme « aucun plafond », jamais levée : une
    faute de frappe doit coûter le plafond du jour, pas la publication.
    """
    for absurde in ("abc", 0, -3, "", None, [1]):
        assert plafond_fourchette({"plafond": absurde}) is None, absurde


async def test_un_plafond_ecrit_en_texte_est_lu_comme_un_nombre():
    """Le site et les versions antérieures écrivent volontiers des chaînes.
    Lu tel quel, `"5"` comparerait mal et ne plafonnerait rien."""
    assert plafond_fourchette({"plafond": "5"}) == 5


# --- Le régler, le lire, l'effacer ------------------------------------------


async def test_regler_puis_lire_le_plafond():
    magasin = await _magasin()

    assert await magasin.regler_plafond_fourchette("grosses", 5) is True

    assert plafond_fourchette(await _grosses(magasin)) == 5


async def test_regler_le_plafond_dune_fourchette_inconnue():
    """False plutôt qu'une exception : la commande doit pouvoir dire « inconnue »
    en citant les noms qui existent."""
    magasin = await _magasin()

    assert await magasin.regler_plafond_fourchette("petits", 5) is False


async def test_un_plafond_sous_un_est_refuse():
    """Zéro promotion, c'est une fourchette qui ne publie rien — indiscernable
    d'une panne, et déjà obtenable en retirant ses salons."""
    magasin = await _magasin()

    for absurde in (0, -1):
        try:
            await magasin.regler_plafond_fourchette("grosses", absurde)
        except ValueError:
            continue
        raise AssertionError(f"plafond {absurde} accepté")


async def test_effacer_le_plafond():
    magasin = await _magasin()
    await magasin.regler_plafond_fourchette("grosses", 5)

    assert await magasin.effacer_plafond_fourchette("grosses") is True

    assert plafond_fourchette(await _grosses(magasin)) is None


async def test_effacer_un_plafond_absent_le_dit():
    """Sans ce False, la commande annoncerait un effacement imaginaire et l'on
    croirait avoir changé quelque chose."""
    magasin = await _magasin()

    assert await magasin.effacer_plafond_fourchette("grosses") is False


# --- Ce qui ne doit pas l'effacer -------------------------------------------


async def test_le_plafond_survit_a_un_changement_de_bornes():
    """`/promos prix` ne parle pas du plafond : l'effacer au passage serait une
    perte silencieuse, découverte le lendemain sur un post trop long."""
    magasin = await _magasin()
    await magasin.regler_plafond_fourchette("grosses", 5)

    await magasin.majprix_fourchette("grosses", Decimal(50), Decimal(5000))

    assert plafond_fourchette(await _grosses(magasin)) == 5


async def test_le_plafond_survit_a_un_salon_ajoute():
    magasin = await _magasin()
    await magasin.regler_plafond_fourchette("grosses", 5)

    await magasin.ajouter_salon_fourchette("grosses", "42")

    assert plafond_fourchette(await _grosses(magasin)) == 5


async def test_le_voisin_garde_son_plafond():
    """Les fourchettes sont déjà par serveur ; le plafond suit, étant dedans."""
    magasin = await _magasin()
    voisin = await _magasin(FILIALE_ID)
    await magasin.regler_plafond_fourchette("grosses", 5)

    assert plafond_fourchette(await _grosses(voisin)) is None


# --- Le plafond d'une recherche, qui ne porte sur aucune fourchette ---------


async def test_sans_plafond_partout_la_recherche_nest_pas_plafonnee():
    magasin = await _magasin()

    assert await magasin.plafond_de_recherche() is None


async def test_une_seule_fourchette_sans_plafond_laisse_la_recherche_libre():
    """`/promos chercher` couvre l'**union** des fourchettes.

    Y appliquer le plafond de l'une cacherait des promotions que l'autre publie
    bel et bien : la recherche annoncerait alors moins que ce qui sort le soir,
    et c'est exactement l'inverse de ce qu'on lui demande.
    """
    magasin = await _magasin()
    await magasin.ajouter_fourchette("petits", Decimal(1), Decimal(50))
    await magasin.regler_plafond_fourchette("petits", 3)

    assert await magasin.plafond_de_recherche() is None


async def test_toutes_plafonnees_la_recherche_prend_le_plus_large():
    """Le plus permissif des deux : la recherche ne doit jamais montrer moins
    que la fourchette la plus généreuse ne publiera."""
    magasin = await _magasin()
    await magasin.ajouter_fourchette("petits", Decimal(1), Decimal(50))
    await magasin.regler_plafond_fourchette("grosses", 8)
    await magasin.regler_plafond_fourchette("petits", 3)

    assert await magasin.plafond_de_recherche() == 8


async def test_sans_aucune_fourchette_la_recherche_nest_pas_plafonnee():
    """Le cas d'un serveur neuf, où `/promos chercher` marche déjà avec des
    bornes données à la main. « Toutes plafonnées » ne doit pas être vrai d'un
    ensemble vide, sinon la recherche se plafonnerait à rien."""
    magasin = (await _store()).pour(EMPIRE_ID)

    assert await magasin.plafond_de_recherche() is None


# --- Sérialisation ----------------------------------------------------------


def test_le_plafond_est_expose_au_site():
    """Le site montre les fourchettes : sans le plafond, sa liste promettrait un
    post plus long que celui qui sort."""
    from src.serialisation import fourchette_en_json

    rendu = fourchette_en_json(
        {
            "nom": "grosses",
            "prix_min": "1e14",
            "prix_max": "6e15",
            "salons": [],
            "plafond": 5,
        }
    )

    # Un entier et non une chaîne : c'est un compte, pas un montant, et les
    # montants sont les seuls champs que le bot pré-formate pour le site.
    assert rendu["plafond"] == 5


def test_une_fourchette_sans_plafond_nen_expose_pas():
    """Un `0` se lirait comme « plafonnée à zéro », donc comme une fourchette
    muette — même raison que pour les bornes tolérées absentes."""
    from src.serialisation import fourchette_en_json

    rendu = fourchette_en_json(
        {"nom": "grosses", "prix_min": "1e14", "prix_max": "6e15", "salons": []}
    )

    assert "plafond" not in rendu
