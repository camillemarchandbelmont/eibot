"""Stockage des filiales et réglages de leur publication (Store en mémoire).

Les filiales vivent sous leur **propre clé** (`filiales`), pas dans `config` :
celle-ci porte encore les champs plats dont la présence déclenche la migration
des fourchettes (voir `Store.fourchettes`), et y greffer une liste sans rapport
rendrait cette signature illisible.

L'heure et les salons du tableau, eux, sont des réglages : ils sont dans
`config`, comme l'heure des promotions.
"""

from decimal import Decimal

import pytest

from src.db import Store


@pytest.fixture
def store():
    return Store(dsn="")


# --- La liste des filiales --------------------------------------------------


async def test_aucune_filiale_au_depart(store):
    assert await store.filiales() == []


async def test_enregistrer_puis_relire_une_filiale(store):
    await store.enregistrer_filiale("ARMEE  DE TERRE", Decimal(1000), "2026-08-11")

    filiales = await store.filiales()
    assert [f.nom for f in filiales] == ["ARMEE  DE TERRE"]
    assert filiales[0].frais == Decimal(70)
    assert filiales[0].date == "2026-08-11"


async def test_enregistrer_renvoie_le_releve_calcule(store):
    """La commande affiche ce qu'elle vient d'enregistrer : le relire serait un
    aller-retour de plus pour la même valeur."""
    filiale = await store.enregistrer_filiale("A", Decimal(1000), "2026-08-11")

    assert filiale.frais == Decimal(70)


async def test_une_ressaisie_ne_cree_pas_de_seconde_ligne(store):
    await store.enregistrer_filiale("A", Decimal(1000), "2026-08-09")
    await store.enregistrer_filiale("A", Decimal(2000), "2026-08-11")

    filiales = await store.filiales()
    assert len(filiales) == 1
    assert filiales[0].benefices == Decimal(2000)


async def test_les_montants_survivent_a_dix_sept_chiffres(store):
    """Le vrai risque du stockage : JSONB n'a pas d'entier de cette taille, et
    un passage par un flottant mangerait les derniers chiffres — sans erreur."""
    await store.enregistrer_filiale("A", Decimal("2710572934559948"), "2026-08-11")

    filiale = (await store.filiales())[0]
    assert filiale.benefices == Decimal("2710572934559948")
    assert filiale.frais == Decimal("189740105419196")


async def test_retirer_une_filiale(store):
    await store.enregistrer_filiale("A", Decimal(1000), "2026-08-11")
    await store.enregistrer_filiale("B", Decimal(2000), "2026-08-11")

    assert await store.retirer_filiale("a") is True
    assert [f.nom for f in await store.filiales()] == ["B"]


async def test_retirer_une_filiale_absente_ne_plante_pas(store):
    assert await store.retirer_filiale("A") is False


async def test_retirer_la_derniere_filiale_laisse_une_liste_vide(store):
    """Le piège du stockage : une liste vidée doit être *écrite*, sinon la
    filiale reviendrait au redémarrage."""
    await store.enregistrer_filiale("A", Decimal(1000), "2026-08-11")
    await store.retirer_filiale("A")

    assert await store.filiales() == []
    assert await store.get("filiales") == []


async def test_les_filiales_ne_polluent_pas_la_config(store):
    """`config` porte les champs plats qui déclenchent la migration des
    fourchettes : une liste sans rapport y rendrait cette signature illisible."""
    await store.enregistrer_filiale("A", Decimal(1000), "2026-08-11")

    assert "filiales" not in (await store.get("config", {}) or {})


# --- Heure du tableau -------------------------------------------------------


async def test_heure_des_filiales_par_defaut(store):
    """Une heure existe toujours : sans salon, elle ne publie rien de toute
    façon, et un `None` obligerait chaque appelant à s'en garder."""
    assert await store.heure_filiales() == "09:00"


async def test_regler_l_heure_des_filiales(store):
    await store.maj_config(filiales_heure="20:30")

    assert await store.heure_filiales() == "20:30"


async def test_l_heure_des_filiales_est_distincte_de_celle_des_promos(store):
    """Deux posts, deux heures : le tableau se lit le soir, les promotions le
    matin."""
    await store.maj_config(filiales_heure="20:30")

    assert await store.heure_filiales() == "20:30"
    assert (await store.config())["heure"] == "09:00"


# --- Salons du tableau ------------------------------------------------------


async def test_aucun_salon_de_filiales_au_depart(store):
    assert await store.salons_filiales() == []


async def test_ajouter_puis_retirer_un_salon_de_filiales(store):
    assert await store.ajouter_salon_filiales("123") is True
    assert await store.salons_filiales() == ["123"]
    assert await store.retirer_salon_filiales("123") is True
    assert await store.salons_filiales() == []


async def test_ajouter_deux_fois_le_meme_salon_de_filiales(store):
    await store.ajouter_salon_filiales("123")

    assert await store.ajouter_salon_filiales("123") is False
    assert await store.salons_filiales() == ["123"]


async def test_retirer_un_salon_de_filiales_absent(store):
    assert await store.retirer_salon_filiales("123") is False


async def test_le_dernier_salon_de_filiales_retire_ne_revient_pas(store):
    """`maj_config` ignore les valeurs vides : une liste vidée par ce chemin ne
    serait jamais écrite, et le salon reviendrait au redémarrage."""
    await store.ajouter_salon_filiales("123")
    await store.retirer_salon_filiales("123")

    assert (await store.get("config", {}))["filiales_salons"] == []


async def test_les_salons_de_filiales_sont_distincts_de_ceux_des_promos(store):
    """Le tableau des frais n'a rien à faire dans le salon des promotions."""
    await store.ajouter_salon_filiales("123")

    assert await store.salons() == []


async def test_les_ids_de_salons_sont_normalises_en_texte(store):
    """Discord donne des int ; JSONB les rendrait tels quels et la comparaison
    avec `str(salon.id)` échouerait en silence."""
    await store.ajouter_salon_filiales(123)

    assert await store.salons_filiales() == ["123"]


# --- Idempotence de la publication -----------------------------------------


async def test_la_marque_du_jour_des_filiales_est_distincte(store):
    """Sinon le post des promotions consommerait le quota du tableau, et l'un
    des deux ne sortirait jamais."""
    await store.marquer_publie("2026-08-11")

    assert await store.derniere_publication_filiales() is None


async def test_marquer_puis_oublier_la_publication_des_filiales(store):
    await store.marquer_publie_filiales("2026-08-11")
    assert await store.derniere_publication_filiales() == "2026-08-11"

    await store.oublier_publication_filiales()
    assert await store.derniere_publication_filiales() is None
