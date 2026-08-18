"""Tests du déménagement d'une base à l'autre.

Deux `Store` en mémoire suffisent : `copier` ne parle qu'à l'interface `tout` /
`get` / `set`, la même en mémoire et sur Postgres. Aucun faux, donc, et aucun
risque qu'un mock valide une copie qui n'aurait pas lieu.
"""

import pytest

from src.db import Store
from src.migration import (
    MigrationError,
    copier,
    ecarts,
    sans_mot_de_passe,
    verifier_deux_bases,
)


@pytest.fixture
def source():
    return Store(dsn="")


@pytest.fixture
def cible():
    return Store(dsn="")


async def _remplir(store: Store) -> None:
    """L'état d'un bot en service : les cinq clés, marques de publication comprises."""
    await store.set("config", {"heure": "20:30", "salons": ["123"]})
    await store.set("template", {"embeds": [{"title": "{nom}"}]})
    await store.set("filiales", [{"nom": "MEGAPOLE", "benefices": "1000", "frais": "70"}])
    await store.set("derniere_publication", "2026-08-17")
    await store.set("derniere_publication_filiales", "2026-08-17")


# --- copier -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_copie_toutes_les_cles(source, cible):
    await _remplir(source)

    await copier(source, cible)

    assert await cible.tout() == await source.tout()


@pytest.mark.asyncio
async def test_copie_les_marques_de_publication(source, cible):
    """Elles font partie du déménagement, sinon le bot republierait le jour même.

    Un tableau de frais publié deux fois passerait pour une double facturation
    auprès des filiales, et rien dans les logs ne dirait pourquoi.
    """
    await _remplir(source)

    await copier(source, cible)

    assert await cible.derniere_publication() == "2026-08-17"
    assert await cible.get("derniere_publication_filiales") == "2026-08-17"


@pytest.mark.asyncio
async def test_copie_une_cle_inconnue_du_code(source, cible):
    """La copie lit ce qu'il y a, pas ce qu'elle sait nommer.

    Une clé ajoutée après l'écriture de `copier` serait sinon laissée derrière,
    et le manque ne se verrait qu'une fois l'ancienne base éteinte.
    """
    await source.set("cle_ajoutee_plus_tard", {"quoi": "que ce soit"})

    await copier(source, cible)

    assert await cible.get("cle_ajoutee_plus_tard") == {"quoi": "que ce soit"}


@pytest.mark.asyncio
async def test_une_cible_non_vide_est_refusee(source, cible):
    """Deux bots, deux configs : écraser serait irréversible.

    La base d'arrivée peut déjà servir — un autre bot, un essai. Sans garde, la
    copie remplacerait sa config sans qu'on puisse la retrouver.
    """
    await _remplir(source)
    await cible.set("config", {"heure": "09:00"})

    with pytest.raises(MigrationError):
        await copier(source, cible)


@pytest.mark.asyncio
async def test_la_cible_refusee_reste_intacte(source, cible):
    """Refuser à moitié serait pire que ne pas refuser : rien n'est écrit."""
    await _remplir(source)
    await cible.set("config", {"heure": "09:00"})

    with pytest.raises(MigrationError):
        await copier(source, cible)

    assert await cible.tout() == {"config": {"heure": "09:00"}}


@pytest.mark.asyncio
async def test_forcer_ecrase_une_cible_non_vide(source, cible):
    """Une deuxième passe doit rester possible : un premier essai a pu échouer.

    Sans ça, il faudrait vider la table à la main entre deux tentatives — au
    risque de la vider une fois la copie réussie.
    """
    await _remplir(source)
    await cible.set("config", {"heure": "09:00"})

    await copier(source, cible, forcer=True)

    assert (await cible.get("config"))["heure"] == "20:30"


@pytest.mark.asyncio
async def test_la_source_n_est_pas_touchee(source, cible):
    """Rien n'est déplacé, tout est recopié : l'ancienne base reste un recours.

    Elle est le seul filet si la nouvelle se révèle inutilisable après coup.
    """
    await _remplir(source)
    avant = await source.tout()

    await copier(source, cible)

    assert await source.tout() == avant


@pytest.mark.asyncio
async def test_une_cible_vide_est_acceptee(source, cible):
    """Le cas normal : une base fraîche, aucun avertissement à donner."""
    await _remplir(source)
    assert await copier(source, cible) == []


@pytest.mark.asyncio
async def test_une_ecriture_perdue_est_signalee(source, cible):
    """La copie se relit : annoncer une réussite sans vérifier ne prouve rien.

    Une base peut accepter une écriture et ne rien garder — droits, réplique en
    lecture seule. Le bot démarrerait alors sur une config d'usine, et
    republierait tout.
    """
    await _remplir(source)

    async def _avaler(cle, valeur):
        return None

    cible.set = _avaler

    manquantes = await copier(source, cible)

    assert sorted(manquantes) == sorted(await source.tout())


@pytest.mark.asyncio
async def test_copier_une_source_vide_ne_fait_rien(source, cible):
    """Sans données, il n'y a rien à déménager — et rien à signaler non plus."""
    assert await copier(source, cible) == []
    assert await cible.tout() == {}


# --- ecarts -----------------------------------------------------------------


def test_ecarts_vide_quand_tout_correspond():
    etat = {"config": {"heure": "20:30"}, "filiales": []}
    assert ecarts(etat, dict(etat)) == []


def test_ecarts_nomme_la_cle_absente():
    assert ecarts({"config": 1, "template": 2}, {"config": 1}) == ["template"]


def test_ecarts_nomme_la_cle_dont_la_valeur_differe():
    """Présente ne suffit pas : une valeur tronquée passerait pour copiée."""
    assert ecarts({"config": {"heure": "20:30"}}, {"config": {}}) == ["config"]


def test_ecarts_ignore_ce_que_la_cible_a_en_plus():
    """La copie ne promet pas de vider la cible, seulement d'y porter la source.

    Compter ses clés en plus ferait échouer un `forcer` parfaitement valide.
    """
    assert ecarts({"config": 1}, {"config": 1, "autre": 2}) == []


# --- sans_mot_de_passe ------------------------------------------------------


def test_le_mot_de_passe_ne_sort_pas():
    """Le déménagement se raconte à l'écran : le mot de passe n'y a pas sa place.

    Dire de quelle base on parle est nécessaire — deux DSN se ressemblent — mais
    l'affichage part dans le terminal, donc dans son historique et dans tout
    copier-coller vers un rapport.
    """
    masque = sans_mot_de_passe(
        "postgresql://postgres.abcdefgh:tres-secret@aws-0-eu-central-1"
        ".pooler.supabase.com:5432/postgres"
    )
    assert "tres-secret" not in masque


def test_l_hote_reste_lisible():
    """Sans l'hôte, on ne saurait pas laquelle des deux bases a échoué."""
    masque = sans_mot_de_passe("postgresql://postgres.abc:x@db.exemple.co:5432/postgres")
    assert "db.exemple.co" in masque
    assert "postgres.abc" in masque


def test_un_dsn_sans_mot_de_passe_passe_tel_quel():
    assert sans_mot_de_passe("postgresql://ou.exemple/base") == (
        "postgresql://ou.exemple/base"
    )


def test_un_dsn_illisible_est_entierement_masque():
    """Illisible veut dire « je ne sais pas où est le secret » : tout part.

    Le laisser passer parce que la découpe a échoué serait exactement le cas où
    la fuite ne serait pas remarquée.
    """
    assert "secret" not in sans_mot_de_passe("nimporte:quoi@secret")


# --- verifier_deux_bases ----------------------------------------------------


def test_la_meme_chaine_deux_fois_est_refusee():
    """Le pire scénario du déménagement, et le plus facile à provoquer.

    Deux DSN se ressemblent ; collée deux fois, la même chaîne donnerait une copie
    de la base sur elle-même, zéro écart, et un rapport de réussite. L'ancienne
    base serait éteinte ensuite, avec les données dedans.
    """
    dsn = "postgresql://postgres:x@ou.exemple:5432/postgres"
    with pytest.raises(MigrationError):
        verifier_deux_bases(dsn, dsn)


def test_deux_chaines_differentes_passent():
    verifier_deux_bases(
        "postgresql://a:x@ancienne.exemple:5432/postgres",
        "postgresql://b:y@nouvelle.exemple:5432/postgres",
    )


def test_une_chaine_vide_est_refusee():
    """Vide, `Store` retombe en mémoire sans rien dire : la copie n'irait nulle part.

    Elle rendrait pourtant zéro écart, la cible en mémoire relisant fidèlement ce
    qu'on vient d'y écrire — puis tout disparaîtrait à la fin du processus.
    """
    with pytest.raises(MigrationError):
        verifier_deux_bases("", "postgresql://b:y@nouvelle.exemple:5432/postgres")
    with pytest.raises(MigrationError):
        verifier_deux_bases("postgresql://a:x@ancienne.exemple:5432/postgres", "")


def test_le_message_de_refus_ne_porte_pas_le_mot_de_passe():
    """L'erreur remonte à l'écran, et peut finir dans un rapport ou un ticket."""
    dsn = "postgresql://postgres:tres-secret@ou.exemple:5432/postgres"
    with pytest.raises(MigrationError) as echec:
        verifier_deux_bases(dsn, dsn)
    assert "tres-secret" not in str(echec.value)
