"""Chaque commande lit et écrit la configuration de **son** serveur.

Le stockage est cloisonné (`tests/test_cloisonnement.py`), la tournée quotidienne
passe une fois par serveur (`tests/test_publication_par_serveur.py`) et
`/reglages importer` reprend l'ancienne configuration
(`tests/test_reglages_importer.py`). Il manque le dernier maillon : les commandes.

Tant qu'elles lisent la configuration commune, le cloisonnement est pire que
l'ancien état. Régler l'heure dans une entreprise la changerait pour toutes — ce
qu'on vient de défaire — **et** ne servirait à rien : la tournée ne lit plus cette
heure-là. `/fourchette liste` montrerait des fourchettes qui ne publient nulle
part, et `/fourchette salon ajouter` un salon dont le post ne sortira jamais.

Deux serveurs, et la même assertion partout : ce qui est réglé ici n'apparaît ni
chez le voisin, ni dans la configuration commune — celle que le site de contrôle
continue de lire, faute de dire de quel serveur il parle.
"""

from src.modules import Publication, Tournee
from src.schedule import maintenant_local

from tests.test_commandes_fourchettes import (
    InteractionFactice,
    SalonFactice,
    ServeurFactice,
    _bot,
    _commande,
)
from tests.test_commandes_publication import _groupe, _publication, _tournee

#: Les deux entreprises de l'histoire. Des ids, et non des noms : c'est par l'id
#: du serveur où la commande est tapée que la configuration est choisie.
EMPIRE = 111
VOISIN = 222


def _interaction(serveur_id: int) -> InteractionFactice:
    """Une commande tapée dans le serveur `serveur_id`."""
    interaction = InteractionFactice()
    interaction.guild = ServeurFactice(serveur_id)
    return interaction


class Cible:
    """Un salon résolu à l'envoi, rattaché à son serveur.

    Le rattachement compte : `src.tournee` écarte un salon qui n'est pas dans le
    serveur dont il lit la configuration, et un salon sans serveur serait écarté
    comme les autres.
    """

    def __init__(self, salon_id: int, serveur_id: int):
        self.id = salon_id
        self.guild = ServeurFactice(serveur_id)
        self.envois: list[str] = []

    async def send(self, contenu=None, **options):
        self.envois.append(contenu)


# --- Le vocabulaire commun des publications ---------------------------------


async def test_lheure_dune_publication_est_reglee_dans_son_serveur():
    """Le cas le plus cher : l'heure réglée dans le commun n'est plus lue par
    personne, et celle du voisin serait déplacée au passage."""
    bot = await _bot()
    await _groupe(bot, _publication())

    await _commande(bot, "bonjour heure").callback(
        _interaction(EMPIRE), heure="21:30"
    )

    assert await bot.store.pour(EMPIRE).get("publication:bonjour:heure") == "21:30"
    assert await bot.store.pour(VOISIN).get("publication:bonjour:heure") is None
    assert await bot.store.get("publication:bonjour:heure") is None


async def test_lheure_affichee_est_celle_du_serveur_ou_lon_demande():
    """Deux entreprises publient à deux heures : consulter depuis l'une ne doit
    pas montrer le réglage de l'autre."""
    bot = await _bot()
    await _groupe(bot, _publication(heure_par_defaut="07:45"))
    await bot.store.pour(EMPIRE).set("publication:bonjour:heure", "21:30")

    ici, ailleurs = _interaction(EMPIRE), _interaction(VOISIN)
    await _commande(bot, "bonjour heure").callback(ici, heure=None)
    await _commande(bot, "bonjour heure").callback(ailleurs, heure=None)

    assert "21:30" in " ".join(ici.textes)
    assert "07:45" in " ".join(ailleurs.textes)


async def test_le_fuseau_affiche_est_celui_du_serveur():
    """Une heure sans son fuseau ne dit rien : montrer celui du commun ferait
    lire « 21:30 » dans le mauvais décalage, et attendre le post à côté."""
    bot = await _bot()
    await _groupe(bot, _publication())
    await bot.store.maj_config(fuseau="Pacific/Kiritimati")
    await bot.store.pour(EMPIRE).maj_config(fuseau="Europe/Paris")
    interaction = _interaction(EMPIRE)

    await _commande(bot, "bonjour heure").callback(interaction, heure=None)

    assert "Europe/Paris" in " ".join(interaction.textes)


async def test_regler_lheure_noublie_que_la_marque_du_serveur():
    """Régler l'heure oublie la marque du jour, pour que le nouvel horaire prenne
    effet aujourd'hui. Encore faut-il oublier la bonne : celle de ce serveur.

    Oubliée dans le commun, la marque de ce serveur resterait et le post
    n'arriverait que demain ; oubliée chez le voisin, son post repartirait dans la
    minute — un doublon chez quelqu'un qui n'a rien demandé.
    """
    bot = await _bot()
    await _groupe(bot, _publication())
    for serveur in (EMPIRE, VOISIN):
        await bot.store.pour(serveur).set(
            "publication:bonjour:derniere", "2026-08-19"
        )

    await _commande(bot, "bonjour heure").callback(
        _interaction(EMPIRE), heure="21:30"
    )

    assert await bot.store.pour(EMPIRE).get("publication:bonjour:derniere") is None
    assert (
        await bot.store.pour(VOISIN).get("publication:bonjour:derniere")
        == "2026-08-19"
    )


async def test_un_salon_est_ajoute_au_serveur_ou_la_commande_est_tapee():
    bot = await _bot()
    await _groupe(bot, _publication())

    await _commande(bot, "bonjour salon ajouter").callback(
        _interaction(EMPIRE), salon=SalonFactice(4242)
    )

    assert await bot.store.pour(EMPIRE).get("publication:bonjour:salons") == ["4242"]
    assert await bot.store.pour(VOISIN).get("publication:bonjour:salons") is None
    assert await bot.store.get("publication:bonjour:salons") is None


async def test_un_salon_ne_se_retire_pas_depuis_un_autre_serveur():
    """Sinon une entreprise couperait la publication d'une autre, en croyant
    couper la sienne."""
    bot = await _bot()
    await _groupe(bot, _publication())
    salon = SalonFactice(4242)
    await _commande(bot, "bonjour salon ajouter").callback(
        _interaction(EMPIRE), salon=salon
    )

    ailleurs = _interaction(VOISIN)
    await _commande(bot, "bonjour salon retirer").callback(ailleurs, salon=salon)

    assert "❌" in " ".join(ailleurs.textes)
    assert await bot.store.pour(EMPIRE).get("publication:bonjour:salons") == ["4242"]


async def test_lapercu_prepare_sur_la_configuration_du_serveur():
    """L'aperçu répond à « qu'est-ce qui sortira ici ? » : préparé sur la
    configuration commune, il montrerait un post qui ne sortira jamais."""
    bot = await _bot()
    vus: list[str | None] = []

    async def preparer(bot_, magasin, maintenant):
        vus.append(getattr(magasin, "serveur_id", None))
        return Tournee(raison="rien à dire")

    await _groupe(
        bot, Publication(cle="bonjour", titre="le bonjour", preparer=preparer)
    )

    await _commande(bot, "bonjour apercu").callback(_interaction(EMPIRE))

    assert vus == [str(EMPIRE)]


async def test_publier_maintenant_publie_la_configuration_du_serveur():
    """`publier` force la tournée : sur la configuration commune, elle enverrait
    dans les salons de tous les serveurs et consommerait la journée du commun,
    que personne ne lit plus."""
    bot = await _bot()
    cible = Cible(1, EMPIRE)
    vus: list[str | None] = []

    async def preparer(bot_, magasin, maintenant):
        vus.append(getattr(magasin, "serveur_id", None))
        return _tournee("matin")

    async def resoudre_salon(salon_id):
        return cible

    bot.resoudre_salon = resoudre_salon
    await _groupe(
        bot, Publication(cle="bonjour", titre="le bonjour", preparer=preparer)
    )
    interaction = _interaction(EMPIRE)

    await _commande(bot, "bonjour publier").callback(interaction)

    assert vus == [str(EMPIRE)]
    assert cible.envois == ["contenu de matin"]
    aujourdhui = maintenant_local(
        (await bot.store.pour(EMPIRE).config())["fuseau"]
    ).strftime("%Y-%m-%d")
    assert (
        await bot.store.pour(EMPIRE).get("publication:bonjour:derniere") == aujourdhui
    )
    assert await bot.store.get("publication:bonjour:derniere") is None
    # L'avertissement porte sur la journée de ce serveur, et elle est bien
    # consommée : sans lui, un `publier` du matin ferait croire à une panne le soir.
    assert "remplace" in " ".join(interaction.textes).lower()
