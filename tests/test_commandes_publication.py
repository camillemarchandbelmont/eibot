"""Le vocabulaire commun à toute publication : `heure`, `apercu`, `publier`, `salon`.

Ces quatre mots sont ceux des deux publications d'aujourd'hui, et ce sont ceux
qu'héritera la troisième. C'est le vrai intérêt du contrat : un module qui ajoute
un post n'a pas à inventer les noms de ses réglages, ni à réécrire ce que ces
commandes font.

Éprouvé ici sur une publication d'essai, et non sur les promotions ou le tableau
des frais : ce qui doit tenir, c'est le cas général. Une commande qui ne marcherait
que sur les deux publications historiques ne se verrait qu'au module suivant.
"""

from discord import app_commands

from src.commandes import ajouter_les_commandes_de_publication
from src.modules import Envoi, Publication, Tournee
from src.schedule import maintenant_local

from tests.test_commandes_fourchettes import (
    InteractionFactice,
    SalonFactice,
    ServeurFactice,
    _bot,
    _magasin,
)


async def _aujourdhui(bot) -> str:
    """La date du jour telle que le moteur l'écrira, pas telle qu'on l'écrit ici."""
    return maintenant_local((await _magasin(bot).config())["fuseau"]).strftime("%Y-%m-%d")


def _publication(tournee: Tournee | None = None, **surcharges) -> Publication:
    async def preparer(bot, magasin, maintenant):
        return tournee if tournee is not None else Tournee(raison="rien à dire")

    return Publication(
        cle=surcharges.pop("cle", "essai"),
        titre=surcharges.pop("titre", "l'essai"),
        preparer=preparer,
        **surcharges,
    )


def _tournee(*etiquettes: str, salons: tuple[str, ...] = ("1",)) -> Tournee:
    envois = []
    for etiquette in etiquettes:
        async def envoyer(cible, ephemere=False, etiquette=etiquette):
            await cible.send(f"contenu de {etiquette}", ephemeral=ephemere)

        envois.append(Envoi(etiquette=etiquette, salons=salons, envoyer=envoyer))
    return Tournee(envois=tuple(envois), compte=len(etiquettes), resume="un essai")


async def _groupe(bot, publication: Publication, salons: bool = True):
    """Le groupe d'une publication d'essai, greffé sur l'arbre du bot.

    Greffé pour de vrai : `walk_commands` est la seule façon de vérifier les noms
    que Discord affichera, et une commande enregistrée autrement ne prouverait
    rien du menu.

    « essai » et non un nom plausible : le bot est un vrai `EmpireBot`, donc ses
    modules sont déjà greffés. Un double qui s'appellerait comme la commande d'un
    module — présent ou à venir — ferait lever `CommandAlreadyRegistered`, et la
    panne se lirait comme un défaut du module qu'on vient d'écrire.
    """
    groupe = app_commands.Group(name="essai", description="Une publication d'essai")
    ajouter_les_commandes_de_publication(groupe, bot, publication, salons=salons)
    bot.tree.add_command(groupe)
    return groupe


def _commande(bot, nom: str):
    for commande in bot.tree.walk_commands():
        if commande.qualified_name == nom:
            return commande
    raise AssertionError(f"commande introuvable : {nom}")


# --- Les noms ---------------------------------------------------------------


async def test_une_publication_recoit_le_vocabulaire_complet():
    """Les mêmes mots que les deux publications historiques, sans les redéclarer."""
    bot = await _bot()
    await _groupe(bot, _publication())

    noms = {c.qualified_name for c in bot.tree.walk_commands()}

    assert {
        "essai heure",
        "essai apercu",
        "essai publier",
        "essai salon ajouter",
        "essai salon retirer",
    } <= noms


async def test_une_publication_peut_se_passer_des_commandes_de_salon():
    """Les promotions attachent leurs salons à une fourchette, pas à la publication.

    Leur `/promos salon ajouter` prend un nom de fourchette : la version
    générique cohabiterait avec elle sous le même nom, en écrivant ailleurs.
    """
    bot = await _bot()
    await _groupe(bot, _publication(), salons=False)

    noms = {c.qualified_name for c in bot.tree.walk_commands()}

    assert "essai heure" in noms
    assert "essai salon ajouter" not in noms


# --- heure ------------------------------------------------------------------


async def test_heure_sans_argument_affiche_l_heure_courante():
    """Consulter sans changer : demander l'heure ne doit pas obliger à la régler."""
    bot = await _bot()
    await _groupe(bot, _publication(heure_par_defaut="07:45"))
    interaction = InteractionFactice()

    await _commande(bot, "essai heure").callback(interaction, heure=None)

    assert "07:45" in " ".join(interaction.textes)


async def test_heure_enregistre_et_confirme():
    bot = await _bot()
    publication = _publication()
    await _groupe(bot, publication)
    interaction = InteractionFactice()

    await _commande(bot, "essai heure").callback(interaction, heure="21:30")

    assert "21:30" in " ".join(interaction.textes)
    assert await _magasin(bot).get("publication:essai:heure") == "21:30"


async def test_heure_est_normalisee_avant_d_etre_rangee():
    """`doit_publier` compare des chaînes : « 9:00 » se rangerait après « 20:30 »."""
    bot = await _bot()
    await _groupe(bot, _publication())
    interaction = InteractionFactice()

    await _commande(bot, "essai heure").callback(interaction, heure="9:5")

    assert await _magasin(bot).get("publication:essai:heure") == "09:05"


async def test_une_heure_illisible_est_refusee_sans_rien_ecrire():
    bot = await _bot()
    await _groupe(bot, _publication())
    interaction = InteractionFactice()

    await _commande(bot, "essai heure").callback(interaction, heure="midi")

    assert "❌" in " ".join(interaction.textes)
    assert await _magasin(bot).get("publication:essai:heure") is None


async def test_regler_l_heure_oublie_la_marque_du_jour():
    """Régler l'heure exprime l'intention de publier à cette heure-là.

    La marque gardée, un post déjà sorti bloquerait le nouvel horaire jusqu'au
    lendemain — et l'utilisateur conclurait que la commande n'a rien fait.
    """
    bot = await _bot()
    await _groupe(bot, _publication())
    await _magasin(bot).set("publication:essai:derniere", "2026-08-19")

    await _commande(bot, "essai heure").callback(
        InteractionFactice(), heure="21:30"
    )

    assert await _magasin(bot).get("publication:essai:derniere") is None


async def test_heure_ecrit_ou_la_publication_le_demande():
    """Les publications historiques rangent leur heure ailleurs qu'au tiroir générique.

    Sans cette dérivation, `/promos heure` écrirait dans un tiroir que la
    publication ne lit pas : la commande confirmerait, et l'heure ne changerait pas.
    """
    bot = await _bot()
    ecrites: list[str] = []

    async def ecrire_heure(magasin, heure):
        ecrites.append(heure)

    async def lire_heure(magasin):
        return ecrites[-1] if ecrites else "07:00"

    await _groupe(
        bot, _publication(lire_heure=lire_heure, ecrire_heure=ecrire_heure)
    )
    interaction = InteractionFactice()

    await _commande(bot, "essai heure").callback(interaction, heure="21:30")

    assert ecrites == ["21:30"]
    assert await _magasin(bot).get("publication:essai:heure") is None


async def test_heure_previent_quand_aucun_salon_ne_recoit_le_post():
    """Régler l'heure d'un post qui ne part nulle part n'a aucun effet visible.

    Sans l'avertissement, on attendrait le post à l'heure dite, puis on
    chercherait la panne du côté du bot — alors qu'il manque un `salon ajouter`.
    """
    bot = await _bot()
    await _groupe(bot, _publication())
    interaction = InteractionFactice()

    await _commande(bot, "essai heure").callback(interaction, heure="21:30")

    texte = " ".join(interaction.textes)
    assert "✅" in texte
    assert "Aucun salon" in texte, texte


async def test_heure_ne_previent_pas_quand_les_salons_sont_declares_ailleurs():
    """Le tableau des frais garde ses salons dans son ancienne liste.

    Cherchés dans le tiroir générique, ils seraient introuvables et `/frais
    heure` annoncerait que rien ne sortira — alors que le post part chaque soir.
    C'est le mensonge le plus coûteux possible : il ferait défaire un réglage
    qui marche.
    """
    bot = await _bot()
    ailleurs = ["4242"]

    async def lire_salons(magasin):
        return list(ailleurs)

    async def ajouter_salon(magasin, salon_id):
        ailleurs.append(str(salon_id))
        return True

    async def retirer_salon(magasin, salon_id):
        ailleurs.remove(str(salon_id))
        return True

    # Les trois accès et pas seulement la lecture : le contrat refuse une
    # publication qui lirait ses salons ailleurs qu'elle ne les écrit.
    await _groupe(
        bot,
        _publication(
            lire_salons=lire_salons,
            ajouter_salon=ajouter_salon,
            retirer_salon=retirer_salon,
        ),
    )
    interaction = InteractionFactice()

    await _commande(bot, "essai heure").callback(interaction, heure="21:30")

    texte = " ".join(interaction.textes)
    assert "✅" in texte
    assert "Aucun salon" not in texte, texte


# --- salon ------------------------------------------------------------------


async def test_salon_ajouter_range_le_salon_et_confirme():
    bot = await _bot()
    await _groupe(bot, _publication())
    interaction = InteractionFactice()
    salon = SalonFactice(4242)

    await _commande(bot, "essai salon ajouter").callback(interaction, salon=salon)

    assert "4242" in " ".join(interaction.textes)
    assert await _magasin(bot).get("publication:essai:salons") == ["4242"]


async def test_salon_deja_ajoute_le_dit_sans_le_doubler():
    """Un salon compté deux fois recevrait deux posts identiques."""
    bot = await _bot()
    await _groupe(bot, _publication())
    salon = SalonFactice(4242)

    await _commande(bot, "essai salon ajouter").callback(
        InteractionFactice(), salon=salon
    )
    interaction = InteractionFactice()
    await _commande(bot, "essai salon ajouter").callback(interaction, salon=salon)

    assert await _magasin(bot).get("publication:essai:salons") == ["4242"]
    assert "déjà" in " ".join(interaction.textes)


async def test_un_salon_ou_le_bot_ne_peut_pas_ecrire_est_refuse():
    """Vérifié à l'attachement : découvert à 09:00 le lendemain, c'est un post perdu."""
    bot = await _bot()
    await _groupe(bot, _publication())
    interaction = InteractionFactice()

    await _commande(bot, "essai salon ajouter").callback(
        interaction, salon=SalonFactice(4242, peut_ecrire=False)
    )

    assert "❌" in " ".join(interaction.textes)
    assert await _magasin(bot).get("publication:essai:salons") is None


async def test_salon_retirer_enleve_et_confirme():
    bot = await _bot()
    await _groupe(bot, _publication())
    salon = SalonFactice(4242)
    await _commande(bot, "essai salon ajouter").callback(
        InteractionFactice(), salon=salon
    )

    interaction = InteractionFactice()
    await _commande(bot, "essai salon retirer").callback(interaction, salon=salon)

    assert await _magasin(bot).get("publication:essai:salons") == []
    assert "4242" in " ".join(interaction.textes)


async def test_retirer_un_salon_absent_le_dit():
    bot = await _bot()
    await _groupe(bot, _publication())
    interaction = InteractionFactice()

    await _commande(bot, "essai salon retirer").callback(
        interaction, salon=SalonFactice(4242)
    )

    assert "❌" in " ".join(interaction.textes)


# --- apercu -----------------------------------------------------------------


async def test_apercu_montre_le_contenu_sans_rien_publier():
    bot = await _bot()
    salons = {1: SalonFactice(1)}
    bot.get_channel = salons.get
    await _groupe(bot, _publication(_tournee("matin")))
    interaction = InteractionFactice()

    await _commande(bot, "essai apercu").callback(interaction)

    assert "contenu de matin" in " ".join(interaction.textes)
    assert await _magasin(bot).get("publication:essai:derniere") is None


async def test_l_apercu_reste_prive():
    """Un aperçu public dans le salon vaudrait publication : c'est l'inverse du but."""
    bot = await _bot()
    await _groupe(bot, _publication(_tournee("matin")))
    interaction = InteractionFactice()

    await _commande(bot, "essai apercu").callback(interaction)

    envois = [*interaction.response.messages, *interaction.followup.messages]
    assert envois
    assert all(envoi.get("ephemeral") for envoi in envois)


async def test_l_apercu_nomme_chaque_envoi():
    """Deux aperçus d'affilée seraient sinon indistinguables."""
    bot = await _bot()
    await _groupe(bot, _publication(_tournee("matin", "midi")))
    interaction = InteractionFactice()

    await _commande(bot, "essai apercu").callback(interaction)

    texte = " ".join(interaction.textes)
    assert "matin" in texte and "midi" in texte


async def test_l_apercu_nomme_ce_qui_ne_partira_pas():
    """Un aperçu qui taisait ce qu'il écarte laisserait croire que tout partira.

    C'est même la question à laquelle il sert à répondre : « qu'est-ce que le bot
    va poster ? » — donc aussi ce qu'il ne postera pas, et pourquoi.
    """
    bot = await _bot()
    tournee = _tournee("matin")
    await _groupe(
        bot,
        _publication(
            Tournee(
                envois=tournee.envois,
                compte=1,
                ecartes=(("orpheline", "aucun salon"),),
            )
        ),
    )
    interaction = InteractionFactice()

    await _commande(bot, "essai apercu").callback(interaction)

    texte = " ".join(interaction.textes)
    assert "orpheline" in texte and "aucun salon" in texte
    assert "⚠️" in texte


async def test_ce_qui_est_ecarte_se_voit_meme_quand_rien_ne_part():
    """Le cas du bot neuf : une seule fourchette, sans salon. Ne montrer que
    « rien ne serait publié » obligerait à deviner de laquelle on parle."""
    bot = await _bot()
    await _groupe(
        bot,
        _publication(
            Tournee(
                raison="aucun salon configuré",
                ecartes=(("orpheline", "aucun salon"),),
            )
        ),
    )
    interaction = InteractionFactice()

    await _commande(bot, "essai apercu").callback(interaction)

    texte = " ".join(interaction.textes)
    assert "orpheline" in texte
    assert "aucun salon configuré" in texte


async def test_un_apercu_sans_rien_a_dire_explique_pourquoi():
    """« Rien » sans le pourquoi obligerait à deviner ce qui manque."""
    bot = await _bot()
    await _groupe(bot, _publication(Tournee(raison="aucun salon configuré")))
    interaction = InteractionFactice()

    await _commande(bot, "essai apercu").callback(interaction)

    assert "aucun salon configuré" in " ".join(interaction.textes)


def _resoudre_dans_le_serveur(bot, envoyes: list[str] | None = None) -> None:
    """Branche la résolution des salons sur des cibles **de ce serveur**.

    Le rattachement n'est pas décoratif : la tournée écarte un salon qui n'est pas
    dans le serveur dont elle vient de lire la configuration. Une cible sans
    serveur serait écartée comme telle, et le post ne partirait pas.
    """

    async def resoudre_salon(salon_id):
        class Cible:
            id = int(salon_id)
            guild = ServeurFactice()

            async def send(self, contenu=None, **options):
                if envoyes is not None:
                    envoyes.append(contenu)

        return Cible()

    bot.resoudre_salon = resoudre_salon


# --- publier ----------------------------------------------------------------


async def test_publier_envoie_tout_de_suite():
    bot = await _bot()
    salons = {1: SalonFactice(1)}
    envoyes: list[str] = []

    _resoudre_dans_le_serveur(bot, envoyes)
    await _groupe(bot, _publication(_tournee("matin")))
    interaction = InteractionFactice()

    await _commande(bot, "essai publier").callback(interaction)

    assert envoyes == ["contenu de matin"]


async def test_publier_previent_qu_il_remplace_le_post_du_jour():
    """La marque du jour est posée : le post de l'heure prévue ne repassera pas.

    Sans cet avertissement, un `publier` du matin ferait croire à une panne le
    soir venu.
    """
    bot = await _bot()

    _resoudre_dans_le_serveur(bot)
    await _groupe(bot, _publication(_tournee("matin")))
    interaction = InteractionFactice()

    await _commande(bot, "essai publier").callback(interaction)

    texte = " ".join(interaction.textes).lower()
    assert "remplace" in texte or "ne repassera" in texte
    assert (
        await _magasin(bot).get("publication:essai:derniere")
        == await _aujourdhui(bot)
    )


async def test_publier_rend_le_compte_rendu_du_moteur():
    """Ce que la commande affiche est ce que le planning aurait écrit dans les logs."""
    bot = await _bot()

    _resoudre_dans_le_serveur(bot)
    await _groupe(bot, _publication(_tournee("matin")))
    interaction = InteractionFactice()

    await _commande(bot, "essai publier").callback(interaction)

    texte = " ".join(interaction.textes)
    assert "l'essai" in texte
    assert "1/1" in texte
