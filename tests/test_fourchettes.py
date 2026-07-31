"""Tests des fourchettes multiples : lecture, migration, écriture.

Le cas le plus risqué est la **migration** : la prod tourne avec une config
plate (`prix_min`/`prix_max`/`salons` à la racine). Une migration ratée ne lève
rien — elle fait juste taire un salon déjà configuré, ce qui ne se remarque que
le lendemain à 09:00.
"""

from decimal import Decimal

import pytest

from src.db import Store


@pytest.fixture
def store():
    return Store(dsn="")


# --- Lecture et migration ---------------------------------------------------


@pytest.mark.asyncio
async def test_bot_neuf_aucune_fourchette(store):
    """Rien de configuré : aucune fourchette, donc rien à publier.

    Inventer une fourchette par défaut serait pire : le bot posterait dans un
    salon que personne n'a choisi.
    """
    assert await store.fourchettes() == []


@pytest.mark.asyncio
async def test_config_plate_lue_comme_fourchette_principale(store):
    """Le cas de la prod : prix et salons à la racine."""
    await store.set(
        "config",
        {"prix_min": "1e14", "prix_max": "6e15", "salons": ["111", "222"]},
    )

    fourchettes = await store.fourchettes()

    assert len(fourchettes) == 1
    assert fourchettes[0]["nom"] == "principale"
    assert Decimal(fourchettes[0]["prix_min"]) == Decimal("1e14")
    assert Decimal(fourchettes[0]["prix_max"]) == Decimal("6e15")
    assert fourchettes[0]["salons"] == ["111", "222"]


@pytest.mark.asyncio
async def test_config_pre_multi_salon_migre_aussi(store):
    """`salon_id` unique, d'avant la migration multi-salon.

    Les deux migrations doivent s'enchaîner : `salon_id` -> `salons` ->
    `fourchettes`. Si la seconde ignore la première, ce salon disparaît.
    """
    await store.set("config", {"prix_min": "1e14", "prix_max": "6e15", "salon_id": "999"})

    fourchettes = await store.fourchettes()

    assert len(fourchettes) == 1
    assert fourchettes[0]["salons"] == ["999"]


@pytest.mark.asyncio
async def test_config_plate_sans_salon_donne_une_fourchette_vide(store):
    """La fourchette existe mais n'a pas de destination : elle est conservée.

    La supprimer ferait perdre les bornes déjà réglées.
    """
    await store.set("config", {"prix_min": "1e14", "prix_max": "6e15"})

    fourchettes = await store.fourchettes()

    assert len(fourchettes) == 1
    assert fourchettes[0]["salons"] == []


@pytest.mark.asyncio
async def test_fourchettes_existantes_font_foi(store):
    """Dès que `fourchettes` existe, la racine n'est plus consultée.

    Sinon une fourchette supprimée reviendrait, ressuscitée par les vieux
    champs plats restés en base.
    """
    await store.set(
        "config",
        {
            "prix_min": "1e14",
            "prix_max": "6e15",
            "salons": ["111"],
            "fourchettes": [
                {"nom": "petits", "prix_min": "0", "prix_max": "1e12", "salons": ["333"]}
            ],
        },
    )

    fourchettes = await store.fourchettes()

    assert [f["nom"] for f in fourchettes] == ["petits"]
    assert fourchettes[0]["salons"] == ["333"]


@pytest.mark.asyncio
async def test_liste_vide_explicite_reste_vide(store):
    """`fourchettes: []` est un choix, pas une absence.

    C'est l'état après avoir supprimé la dernière fourchette : la migration ne
    doit pas la recréer depuis les champs plats.
    """
    await store.set(
        "config",
        {"prix_min": "1e14", "prix_max": "6e15", "salons": ["111"], "fourchettes": []},
    )

    assert await store.fourchettes() == []


@pytest.mark.asyncio
async def test_regler_lheure_ne_cree_pas_de_fourchette(store):
    """`maj_config` recopie les défauts d'usine dans la config enregistrée.

    Ceux-ci contiennent `prix_min`/`prix_max`/`salons` : la config prend alors la
    signature d'une config plate, et la migration fabrique une `principale` que
    personne n'a demandée — avec pour bornes des valeurs d'usine et pour salon
    celui de `SALON_ID`. Le bot se mettrait à publier de lui-même.
    """
    await store.maj_config(heure="09:30")

    assert await store.fourchettes() == []


# --- Création ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_ajouter_fourchette(store):
    await store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))

    fourchettes = await store.fourchettes()
    assert len(fourchettes) == 1
    assert fourchettes[0]["nom"] == "grosses"
    assert fourchettes[0]["salons"] == []


@pytest.mark.asyncio
async def test_ajouter_survit_a_la_relecture(store):
    """Écrite en base, pas seulement en mémoire de l'appel."""
    await store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))

    config = await store.get("config")

    assert [f["nom"] for f in config["fourchettes"]] == ["grosses"]


@pytest.mark.asyncio
async def test_ajouter_migre_avant_d_ecrire(store):
    """Ajouter sur une config plate ne doit pas effacer l'existant.

    Sans migration au moment de l'écriture, la fourchette de la prod
    disparaîtrait au premier `/fourchette ajouter`.
    """
    await store.set("config", {"prix_min": "1e14", "prix_max": "6e15", "salons": ["111"]})

    await store.ajouter_fourchette("petits", Decimal("0"), Decimal("1e12"))

    noms = [f["nom"] for f in await store.fourchettes()]
    assert noms == ["principale", "petits"]


@pytest.mark.asyncio
async def test_ecrire_efface_les_champs_plats(store):
    """Après migration, la racine ne doit plus porter prix ni salons.

    Les laisser ne casse rien tout de suite — c'est bien le problème : la base
    garderait deux vérités, dont une périmée que le prochain lecteur (une
    version antérieure du bot en rollback, un correctif écrit de mémoire)
    prendrait pour la bonne.
    """
    await store.set(
        "config",
        {"prix_min": "1e14", "prix_max": "6e15", "salons": ["111"], "salon_id": "222"},
    )

    await store.ajouter_fourchette("petits", Decimal("0"), Decimal("1e12"))

    enregistree = await store.get("config")
    for ancien in ("prix_min", "prix_max", "salons", "salon_id"):
        assert ancien not in enregistree, ancien


@pytest.mark.asyncio
async def test_nom_duplique_refuse(store):
    await store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))

    with pytest.raises(ValueError, match="existe déjà"):
        await store.ajouter_fourchette("grosses", Decimal("0"), Decimal("1e12"))


@pytest.mark.asyncio
async def test_nom_duplique_insensible_a_la_casse(store):
    """`Grosses` et `grosses` seraient indistinguables à l'œil dans une liste."""
    await store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))

    with pytest.raises(ValueError, match="existe déjà"):
        await store.ajouter_fourchette("GROSSES", Decimal("0"), Decimal("1e12"))


@pytest.mark.asyncio
async def test_nom_vide_refuse(store):
    with pytest.raises(ValueError, match="nom"):
        await store.ajouter_fourchette("   ", Decimal("0"), Decimal("1e12"))


@pytest.mark.asyncio
async def test_nom_est_nettoye(store):
    """Les espaces de bord viennent du copier-coller, pas d'une intention."""
    await store.ajouter_fourchette("  grosses  ", Decimal("1e14"), Decimal("6e15"))

    assert (await store.fourchettes())[0]["nom"] == "grosses"


@pytest.mark.asyncio
async def test_bornes_inversees_remises_dans_l_ordre(store):
    """Une fourchette dont le min dépasse le max ne contiendrait jamais rien."""
    await store.ajouter_fourchette("grosses", Decimal("6e15"), Decimal("1e14"))

    fourchette = (await store.fourchettes())[0]
    assert Decimal(fourchette["prix_min"]) == Decimal("1e14")
    assert Decimal(fourchette["prix_max"]) == Decimal("6e15")


# --- Suppression et modification -------------------------------------------


@pytest.mark.asyncio
async def test_supprimer_fourchette(store):
    await store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    await store.ajouter_fourchette("petits", Decimal("0"), Decimal("1e12"))

    assert await store.supprimer_fourchette("grosses") is True

    assert [f["nom"] for f in await store.fourchettes()] == ["petits"]


@pytest.mark.asyncio
async def test_supprimer_inconnue_renvoie_faux(store):
    assert await store.supprimer_fourchette("fantome") is False


@pytest.mark.asyncio
async def test_supprimer_la_derniere_ne_la_fait_pas_ressusciter(store):
    """Le piège que `_ecrire_salons` corrigeait déjà pour `salon_id`.

    Après suppression, la config plate d'origine est toujours en base : si elle
    reste consultée, la fourchette revient au redémarrage.
    """
    await store.set("config", {"prix_min": "1e14", "prix_max": "6e15", "salons": ["111"]})
    await store.supprimer_fourchette("principale")

    assert await store.fourchettes() == []


@pytest.mark.asyncio
async def test_modifier_les_bornes(store):
    await store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))

    assert await store.majprix_fourchette("grosses", Decimal("0"), Decimal("1e12")) is True

    fourchette = (await store.fourchettes())[0]
    assert Decimal(fourchette["prix_min"]) == Decimal("0")
    assert Decimal(fourchette["prix_max"]) == Decimal("1e12")


@pytest.mark.asyncio
async def test_modifier_conserve_les_salons(store):
    """Changer les bornes ne doit pas détacher les salons."""
    await store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    await store.ajouter_salon_fourchette("grosses", "111")

    await store.majprix_fourchette("grosses", Decimal("0"), Decimal("1e12"))

    assert (await store.fourchettes())[0]["salons"] == ["111"]


@pytest.mark.asyncio
async def test_modifier_inconnue_renvoie_faux(store):
    assert await store.majprix_fourchette("fantome", Decimal("0"), Decimal("1")) is False


# --- Salons d'une fourchette ------------------------------------------------


@pytest.mark.asyncio
async def test_ajouter_salon_a_une_fourchette(store):
    await store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))

    assert await store.ajouter_salon_fourchette("grosses", "111") is True

    assert (await store.fourchettes())[0]["salons"] == ["111"]


@pytest.mark.asyncio
async def test_salon_deja_present_renvoie_faux(store):
    await store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    await store.ajouter_salon_fourchette("grosses", "111")

    assert await store.ajouter_salon_fourchette("grosses", "111") is False
    assert (await store.fourchettes())[0]["salons"] == ["111"]


@pytest.mark.asyncio
async def test_un_salon_peut_servir_deux_fourchettes(store):
    """Rien ne l'interdit : deux fourchettes peuvent viser le même salon."""
    await store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    await store.ajouter_fourchette("petits", Decimal("0"), Decimal("1e12"))

    await store.ajouter_salon_fourchette("grosses", "111")
    await store.ajouter_salon_fourchette("petits", "111")

    assert all(f["salons"] == ["111"] for f in await store.fourchettes())


@pytest.mark.asyncio
async def test_retirer_salon_d_une_fourchette(store):
    await store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    await store.ajouter_salon_fourchette("grosses", "111")
    await store.ajouter_salon_fourchette("grosses", "222")

    assert await store.retirer_salon_fourchette("grosses", "111") is True

    assert (await store.fourchettes())[0]["salons"] == ["222"]


@pytest.mark.asyncio
async def test_retirer_salon_absent_renvoie_faux(store):
    await store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))

    assert await store.retirer_salon_fourchette("grosses", "111") is False


@pytest.mark.asyncio
async def test_retirer_le_dernier_salon_ne_le_fait_pas_ressusciter(store):
    """Une liste vidée doit être écrite ; `maj_config` ignore les valeurs vides."""
    await store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    await store.ajouter_salon_fourchette("grosses", "111")
    await store.retirer_salon_fourchette("grosses", "111")

    assert (await store.fourchettes())[0]["salons"] == []


@pytest.mark.asyncio
async def test_salon_sur_fourchette_inconnue_renvoie_faux(store):
    assert await store.ajouter_salon_fourchette("fantome", "111") is False


@pytest.mark.asyncio
async def test_nom_retrouve_insensible_a_la_casse(store):
    """Tu tapes `Grosses`, la fourchette s'appelle `grosses` : ça doit marcher.

    Refuser sur la casse serait un piège, puisque l'unicité l'ignore déjà.
    """
    await store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))

    assert await store.ajouter_salon_fourchette("GROSSES", "111") is True
