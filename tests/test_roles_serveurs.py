"""Un rôle appartient à un serveur, pas au bot.

`config["role_id"]` était global : le bot enverrait `<@&123>` dans un second
serveur où ce rôle n'existe pas, et Discord y afficherait `@deleted-role` — une
faute visible seulement en lisant le post.
"""

import pytest

from src import settings
from src.db import Store


@pytest.fixture(autouse=True)
def sans_role_denv(monkeypatch):
    """Neutralise `ROLE_ID`.

    `config_par_defaut()` déclare `role_id` depuis la variable d'env
    (`src/settings.py:90`). Elle est vide dans le `.env` de cette machine, mais un
    poste où elle serait réglée ferait échouer les tests du repli — pour une
    raison invisible dans le code testé.
    """
    monkeypatch.setattr(settings, "ROLE_DEFAUT", "")


async def _store() -> Store:
    store = Store(dsn="")
    await store.connect()
    return store


async def test_aucun_role_par_defaut():
    """Un bot neuf ne mentionne personne, et `roles` n'est pas materialise."""
    store = await _store()
    assert await store.roles() == {}
    assert await store.role_du_serveur("999") is None


async def test_role_enregistre_pour_son_serveur():
    store = await _store()
    await store.definir_role("999", "42")

    assert await store.roles() == {"999": "42"}
    assert await store.role_du_serveur("999") == "42"


async def test_un_serveur_sans_role_ne_mentionne_personne():
    """Le cas central : régler A ne doit pas pinguer dans B."""
    store = await _store()
    await store.definir_role("999", "42")

    assert await store.role_du_serveur("888") is None


async def test_role_id_plat_sapplique_partout():
    """Comportement actuel préservé : avec un seul serveur, une seule réponse
    possible. Convertir demanderait de résoudre un salon pour connaître son
    serveur — donc un accès à Discord, que `Store` n'a pas."""
    store = await _store()
    await store.set("config", {"role_id": "7"})

    assert await store.role_du_serveur("999") == "7"
    assert await store.role_du_serveur("888") == "7"


async def test_roles_regles_ignorent_le_role_id_plat():
    """Le cas mixte, le plus piégeux : `roles` réglé pour A alors que `role_id`
    traîne encore en base. B ne doit **rien** en hériter, sinon un rôle qu'on
    croit remplacé continuerait d'être mentionné ailleurs."""
    store = await _store()
    await store.set("config", {"role_id": "7", "roles": {"999": "42"}})

    assert await store.role_du_serveur("999") == "42"
    assert await store.role_du_serveur("888") is None


async def test_definir_role_efface_le_role_id_plat():
    """Sinon le repli continuerait de s'appliquer aux serveurs non réglés."""
    store = await _store()
    await store.set("config", {"role_id": "7"})
    await store.definir_role("999", "42")

    enregistree = await store.get("config", {})
    assert "role_id" not in enregistree
    assert await store.role_du_serveur("888") is None


async def test_effacer_role_ne_touche_que_son_serveur():
    store = await _store()
    await store.definir_role("999", "42")
    await store.definir_role("888", "43")

    assert await store.effacer_role("999") is True
    assert await store.roles() == {"888": "43"}


async def test_effacer_role_absent_renvoie_faux():
    store = await _store()
    assert await store.effacer_role("999") is False


async def test_dernier_role_efface_reste_efface():
    """`maj_config` ignore les valeurs vides : un dict vidé ne serait jamais
    enregistré, et le rôle reviendrait au redémarrage."""
    store = await _store()
    await store.definir_role("999", "42")
    await store.effacer_role("999")

    assert await store.roles() == {}
    assert (await store.get("config", {})).get("roles") == {}


async def test_effacer_role_avec_role_id_plat_le_neutralise():
    """Le défaut découvert : avec `role_id` plat, `effacer_role` revenait False
    sans toucher à rien — et le bot continuait de pinguer.

    Pire qu'un crash : une commande qui ment silencieusement.
    """
    store = await _store()
    await store.set("config", {"role_id": "7"})

    # Avant : le bot mentionne <@&7>
    assert await store.role_du_serveur("999") == "7"

    # L'utilisateur veut arrêter les pings sur ce serveur
    resultat = await store.effacer_role("999")

    # Le défaut : renvoie False ("rien n'était réglé")
    # alors que le bot va continuer de pinguer.
    assert resultat is True, "doit signaler qu'il a fait quelque chose"

    # Le vrai test : le rôle doit disparaître
    assert await store.role_du_serveur("999") is None


async def test_effacer_role_id_plat_neutralise_tous_les_serveurs():
    """Avec un `role_id` plat : impossible de savoir à quel serveur il appartient
    (ça demanderait de résoudre un salon, donc Discord, que `Store` n'a pas).

    Comportement choisi : effacer un `role_id` plat l'efface PARTOUT, car c'était
    déjà global. Sinon un `/config mention` (sans arg) sur 999 laisserait 888
    continuer de pinguer un rôle qu'on croyait supprimé — pire que de tout couper.
    """
    store = await _store()
    await store.set("config", {"role_id": "7"})

    # Le rôle plat s'applique partout
    assert await store.role_du_serveur("999") == "7"
    assert await store.role_du_serveur("888") == "7"

    # On l'efface depuis n'importe quel serveur
    await store.effacer_role("999")

    # Il doit disparaître PARTOUT
    assert await store.role_du_serveur("888") is None
