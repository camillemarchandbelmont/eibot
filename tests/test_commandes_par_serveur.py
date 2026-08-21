"""Chaque commande lit et écrit la configuration de **son** serveur.

Le stockage est cloisonné (`tests/test_cloisonnement.py`), la tournée quotidienne
passe une fois par serveur (`tests/test_publication_par_serveur.py`) et
`/reglages importer` reprend l'ancienne configuration
(`tests/test_reglages_importer.py`). Il manque le dernier maillon : les commandes.

Tant qu'elles lisent la configuration commune, le cloisonnement est pire que
l'ancien état. Régler l'heure dans une entreprise la changerait pour toutes — ce
qu'on vient de défaire — **et** ne servirait à rien : la tournée ne lit plus cette
heure-là. `/promos liste` montrerait des fourchettes qui ne publient nulle
part, et `/promos salon ajouter` un salon dont le post ne sortira jamais.

Deux serveurs, et la même assertion partout : ce qui est réglé ici n'apparaît ni
chez le voisin, ni dans la configuration commune — celle que le site de contrôle
continue de lire, faute de dire de quel serveur il parle.
"""

from decimal import Decimal

from src.db import bornes_tolerees
from src.modules import Publication, Tournee
from src.schedule import maintenant_local

from tests.test_commandes_fourchettes import (
    InteractionFactice,
    SalonFactice,
    ServeurFactice,
    _bot,
    _commande,
)
from tests.test_commandes_frais import _fichiers, _octets
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


def _propositions(commande, parametre: str):
    """Le rappel d'autocomplétion d'un paramètre.

    `Parameter.autocomplete` publique ne dit que *s'il y en a un* ; l'appeler
    passe par `_params`.
    """
    return commande._params[parametre].autocomplete


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

    await _commande(bot, "essai heure").callback(
        _interaction(EMPIRE), heure="21:30"
    )

    assert await bot.store.pour(EMPIRE).get("publication:essai:heure") == "21:30"
    assert await bot.store.pour(VOISIN).get("publication:essai:heure") is None
    assert await bot.store.get("publication:essai:heure") is None


async def test_lheure_affichee_est_celle_du_serveur_ou_lon_demande():
    """Deux entreprises publient à deux heures : consulter depuis l'une ne doit
    pas montrer le réglage de l'autre."""
    bot = await _bot()
    await _groupe(bot, _publication(heure_par_defaut="07:45"))
    await bot.store.pour(EMPIRE).set("publication:essai:heure", "21:30")

    ici, ailleurs = _interaction(EMPIRE), _interaction(VOISIN)
    await _commande(bot, "essai heure").callback(ici, heure=None)
    await _commande(bot, "essai heure").callback(ailleurs, heure=None)

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

    await _commande(bot, "essai heure").callback(interaction, heure=None)

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
            "publication:essai:derniere", "2026-08-19"
        )

    await _commande(bot, "essai heure").callback(
        _interaction(EMPIRE), heure="21:30"
    )

    assert await bot.store.pour(EMPIRE).get("publication:essai:derniere") is None
    assert (
        await bot.store.pour(VOISIN).get("publication:essai:derniere")
        == "2026-08-19"
    )


async def test_un_salon_est_ajoute_au_serveur_ou_la_commande_est_tapee():
    bot = await _bot()
    await _groupe(bot, _publication())

    await _commande(bot, "essai salon ajouter").callback(
        _interaction(EMPIRE), salon=SalonFactice(4242)
    )

    assert await bot.store.pour(EMPIRE).get("publication:essai:salons") == ["4242"]
    assert await bot.store.pour(VOISIN).get("publication:essai:salons") is None
    assert await bot.store.get("publication:essai:salons") is None


async def test_un_salon_ne_se_retire_pas_depuis_un_autre_serveur():
    """Sinon une entreprise couperait la publication d'une autre, en croyant
    couper la sienne."""
    bot = await _bot()
    await _groupe(bot, _publication())
    salon = SalonFactice(4242)
    await _commande(bot, "essai salon ajouter").callback(
        _interaction(EMPIRE), salon=salon
    )

    ailleurs = _interaction(VOISIN)
    await _commande(bot, "essai salon retirer").callback(ailleurs, salon=salon)

    assert "❌" in " ".join(ailleurs.textes)
    assert await bot.store.pour(EMPIRE).get("publication:essai:salons") == ["4242"]


async def test_lapercu_prepare_sur_la_configuration_du_serveur():
    """L'aperçu répond à « qu'est-ce qui sortira ici ? » : préparé sur la
    configuration commune, il montrerait un post qui ne sortira jamais."""
    bot = await _bot()
    vus: list[str | None] = []

    async def preparer(bot_, magasin, maintenant):
        vus.append(getattr(magasin, "serveur_id", None))
        return Tournee(raison="rien à dire")

    await _groupe(
        bot, Publication(cle="essai", titre="l'essai", preparer=preparer)
    )

    await _commande(bot, "essai apercu").callback(_interaction(EMPIRE))

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
        bot, Publication(cle="essai", titre="l'essai", preparer=preparer)
    )
    interaction = _interaction(EMPIRE)

    await _commande(bot, "essai publier").callback(interaction)

    assert vus == [str(EMPIRE)]
    assert cible.envois == ["contenu de matin"]
    aujourdhui = maintenant_local(
        (await bot.store.pour(EMPIRE).config())["fuseau"]
    ).strftime("%Y-%m-%d")
    assert (
        await bot.store.pour(EMPIRE).get("publication:essai:derniere") == aujourdhui
    )
    assert await bot.store.get("publication:essai:derniere") is None
    # L'avertissement porte sur la journée de ce serveur, et elle est bien
    # consommée : sans lui, un `publier` du matin ferait croire à une panne le soir.
    assert "remplace" in " ".join(interaction.textes).lower()


# --- /promos : les fourchettes appartiennent à leur serveur ------------------


async def test_une_fourchette_est_creee_dans_le_serveur_ou_on_la_cree():
    """Créée dans le commun, elle n'enverrait rien — la tournée de ce serveur ne
    lit pas cette liste-là — et apparaîtrait pourtant chez tous les voisins."""
    bot = await _bot()

    await _commande(bot, "promos ajouter").callback(
        _interaction(EMPIRE), fourchette="grosses", min="100T", max="6P"
    )

    assert [f["nom"] for f in await bot.store.pour(EMPIRE).fourchettes()] == ["grosses"]
    assert await bot.store.pour(VOISIN).fourchettes() == []
    assert await bot.store.fourchettes() == []


async def test_la_liste_ne_montre_que_les_fourchettes_du_serveur():
    """Deux entreprises surveillent deux marchés : voir la fourchette de l'autre
    ne renseigne sur rien et fait croire à un réglage qu'on n'a pas ici."""
    bot = await _bot()
    await bot.store.pour(EMPIRE).ajouter_fourchette(
        "chez-nous", Decimal("1e14"), Decimal("6e15")
    )
    await bot.store.pour(VOISIN).ajouter_fourchette(
        "chez-le-voisin", Decimal("1e5"), Decimal("1e6")
    )
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos liste").callback(interaction)

    description = interaction.embeds[0].description
    assert "chez-nous" in description
    assert "chez-le-voisin" not in description


async def test_supprimer_une_fourchette_laisse_celle_du_voisin():
    """Le même nom des deux côtés est le cas normal entre entreprises d'un même
    propriétaire : supprimer ici ne doit pas faire taire le post de là-bas."""
    bot = await _bot()
    for serveur in (EMPIRE, VOISIN):
        await bot.store.pour(serveur).ajouter_fourchette(
            "grosses", Decimal("1e14"), Decimal("6e15")
        )

    await _commande(bot, "promos supprimer").callback(
        _interaction(EMPIRE), fourchette="grosses"
    )

    assert await bot.store.pour(EMPIRE).fourchettes() == []
    assert [f["nom"] for f in await bot.store.pour(VOISIN).fourchettes()] == ["grosses"]


async def test_les_bornes_se_reglent_sur_la_fourchette_du_serveur():
    bot = await _bot()
    await bot.store.pour(EMPIRE).ajouter_fourchette(
        "grosses", Decimal("1e14"), Decimal("6e15")
    )

    await _commande(bot, "promos prix").callback(
        _interaction(EMPIRE), fourchette="grosses", min="1M", max="2M"
    )

    fourchette = (await bot.store.pour(EMPIRE).fourchettes())[0]
    assert Decimal(fourchette["prix_min"]) == Decimal("1e6")


async def test_regler_une_fourchette_du_commun_est_refuse():
    """Aucun repli sur la configuration commune : `/reglages importer` est le seul
    pont, et il est explicite.

    Réglée ici, une fourchette restée dans le commun ne publierait toujours rien —
    la tournée de ce serveur ne la lit pas — mais la commande aurait répondu
    « ✅ ». Mieux vaut un refus qui nomme les fourchettes de ce serveur : aucune.
    """
    bot = await _bot()
    await bot.store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos prix").callback(
        interaction, fourchette="grosses", min="1M", max="2M"
    )

    texte = " ".join(interaction.textes)
    assert "❌" in texte and "aucune" in texte
    intacte = (await bot.store.fourchettes())[0]
    assert Decimal(intacte["prix_min"]) == Decimal("1e14")


async def test_la_tolerance_se_regle_sur_la_fourchette_du_serveur():
    bot = await _bot()
    await bot.store.pour(EMPIRE).ajouter_fourchette(
        "grosses", Decimal("1e14"), Decimal("6e15")
    )

    # Plus large que la fourchette : le magasin refuse une zone plus étroite,
    # qui n'ajouterait aucun candidat.
    await _commande(bot, "promos tolerance").callback(
        _interaction(EMPIRE), fourchette="grosses", min="50T", max="8P"
    )

    fourchette = (await bot.store.pour(EMPIRE).fourchettes())[0]
    assert bornes_tolerees(fourchette) == (Decimal("5e13"), Decimal("8e15"))


async def test_un_salon_est_attache_a_la_fourchette_de_ce_serveur():
    """Attaché à la fourchette du voisin, le salon recevrait ses promos — pas
    celles d'ici, qui ne partiraient nulle part."""
    bot = await _bot()
    for serveur in (EMPIRE, VOISIN):
        await bot.store.pour(serveur).ajouter_fourchette(
            "grosses", Decimal("1e14"), Decimal("6e15")
        )

    await _commande(bot, "promos salon ajouter").callback(
        _interaction(EMPIRE), fourchette="grosses", salon=SalonFactice(4242)
    )

    assert (await bot.store.pour(EMPIRE).fourchettes())[0]["salons"] == ["4242"]
    assert (await bot.store.pour(VOISIN).fourchettes())[0]["salons"] == []


async def test_un_salon_se_retire_de_la_fourchette_de_ce_serveur():
    bot = await _bot()
    magasin = bot.store.pour(EMPIRE)
    await magasin.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    await magasin.ajouter_salon_fourchette("grosses", "4242")

    await _commande(bot, "promos salon retirer").callback(
        _interaction(EMPIRE), fourchette="grosses", salon=SalonFactice(4242)
    )

    assert (await magasin.fourchettes())[0]["salons"] == []


async def test_lautocompletion_ne_propose_que_les_fourchettes_du_serveur():
    """Proposer celle du voisin ferait choisir un nom que la commande refuse
    ensuite — et dirait au passage ce que l'autre entreprise surveille."""
    bot = await _bot()
    await bot.store.pour(EMPIRE).ajouter_fourchette(
        "chez-nous", Decimal("1e14"), Decimal("6e15")
    )
    await bot.store.pour(VOISIN).ajouter_fourchette(
        "chez-le-voisin", Decimal("1e5"), Decimal("1e6")
    )
    completer = _propositions(_commande(bot, "promos supprimer"), "fourchette")

    choix = await completer(_interaction(EMPIRE), "")

    assert [c.value for c in choix] == ["chez-nous"]


# --- /frais : les relevés appartiennent à leur serveur -------------------


async def test_un_releve_est_enregistre_dans_son_serveur():
    """Le tableau du soir est celui de l'entreprise : des relevés mélangés
    donneraient à chacune les frais de l'autre, et un total faux des deux côtés."""
    bot = await _bot()

    await _commande(bot, "frais releve").callback(
        _interaction(EMPIRE), filiale="ARMEE", montant="1000"
    )

    assert [f.nom for f in await bot.store.pour(EMPIRE).filiales()] == ["ARMEE"]
    assert await bot.store.pour(VOISIN).filiales() == []
    assert await bot.store.filiales() == []


async def test_un_releve_est_date_dans_le_fuseau_du_serveur():
    """La date de saisie vient du fuseau de **ce** serveur.

    Deux fuseaux à un jour d'écart, ce que le tableau affiche en tête : datée
    d'ailleurs, la ligne se lirait « relevé d'hier » un jour sur deux, et la
    remise à zéro daterait le nouveau cycle d'un jour qui n'est pas le sien.
    """
    bot = await _bot()
    await bot.store.maj_config(fuseau="Pacific/Kiritimati")
    await bot.store.pour(EMPIRE).maj_config(fuseau="Pacific/Niue")

    await _commande(bot, "frais releve").callback(
        _interaction(EMPIRE), filiale="ARMEE", montant="1000"
    )

    # Les deux fuseaux sont à 25 heures l'un de l'autre : leurs dates ne
    # coïncident jamais, donc l'assertion ne dépend pas de l'heure du test.
    attendu = maintenant_local("Pacific/Niue").strftime("%Y-%m-%d")
    assert (await bot.store.pour(EMPIRE).filiales())[0].date == attendu


async def test_la_liste_ne_montre_que_les_filiales_du_serveur():
    bot = await _bot()
    await bot.store.pour(EMPIRE).enregistrer_filiale(
        "CHEZ-NOUS", Decimal(1000), "2026-08-20"
    )
    await bot.store.pour(VOISIN).enregistrer_filiale(
        "CHEZ-LE-VOISIN", Decimal(2000), "2026-08-20"
    )
    interaction = _interaction(EMPIRE)

    await _commande(bot, "frais liste").callback(interaction)

    rendu = repr(interaction.embeds[0].to_dict())
    assert "CHEZ-NOUS" in rendu
    assert "CHEZ-LE-VOISIN" not in rendu


async def test_retirer_une_filiale_ne_touche_que_son_serveur():
    """Retirée chez le voisin, elle reviendrait dans son tableau du soir et
    manquerait dans celui d'ici : deux erreurs pour un seul geste."""
    bot = await _bot()
    for serveur in (EMPIRE, VOISIN):
        await bot.store.pour(serveur).enregistrer_filiale(
            "ARMEE", Decimal(1000), "2026-08-20"
        )

    await _commande(bot, "frais retirer").callback(
        _interaction(EMPIRE), filiales="ARMEE"
    )

    assert await bot.store.pour(EMPIRE).filiales() == []
    assert [f.nom for f in await bot.store.pour(VOISIN).filiales()] == ["ARMEE"]


async def test_vider_ne_remet_a_zero_que_son_serveur():
    """Le cycle d'une entreprise n'est pas celui de l'autre : vider ici ne doit
    pas effacer les relevés que le voisin n'a pas encore publiés."""
    bot = await _bot()
    for serveur in (EMPIRE, VOISIN):
        await bot.store.pour(serveur).enregistrer_filiale(
            "ARMEE", Decimal(1000), "2026-08-20"
        )

    await _commande(bot, "frais vider").callback(
        _interaction(EMPIRE), confirmer=True
    )

    assert (await bot.store.pour(EMPIRE).filiales())[0].benefices == Decimal(0)
    assert (await bot.store.pour(VOISIN).filiales())[0].benefices == Decimal(1000)


async def test_lexport_ne_sort_que_les_filiales_du_serveur():
    """Le fichier part dans le champ d'import du jeu : une ligne d'une autre
    entreprise y serait importée pour de bon."""
    bot = await _bot()
    await bot.store.pour(EMPIRE).enregistrer_filiale(
        "CHEZ-NOUS", Decimal(1000), "2026-08-20"
    )
    await bot.store.pour(VOISIN).enregistrer_filiale(
        "CHEZ-LE-VOISIN", Decimal(2000), "2026-08-20"
    )
    interaction = _interaction(EMPIRE)

    await _commande(bot, "frais export").callback(interaction)

    contenu = _octets(_fichiers(interaction)[0]).decode("utf-8")
    assert "CHEZ-NOUS" in contenu
    assert "CHEZ-LE-VOISIN" not in contenu


async def test_lautocompletion_ne_propose_que_les_filiales_du_serveur():
    """Le nom est la clé d'import du jeu : proposé depuis un autre serveur, il
    ferait saisir un relevé sur une filiale qu'on ne possède pas ici."""
    bot = await _bot()
    await bot.store.pour(EMPIRE).enregistrer_filiale(
        "CHEZ-NOUS", Decimal(1000), "2026-08-20"
    )
    await bot.store.pour(VOISIN).enregistrer_filiale(
        "CHEZ-LE-VOISIN", Decimal(2000), "2026-08-20"
    )
    completer = _propositions(_commande(bot, "frais releve"), "filiale")

    choix = await completer(_interaction(EMPIRE), "")

    assert [c.value for c in choix] == ["CHEZ-NOUS"]
