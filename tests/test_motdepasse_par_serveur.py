"""Le mot de passe de la page, rangé par entreprise et tiré par Discord.

`tests/test_motdepasse.py` éprouve le calcul : empreinte salée, cookie signé.
Restent les deux bouts qui le relient au reste du bot :

- **le tiroir**, `Store.motdepasse_page` et compagnie. Rangé dans le commun, un
  mot de passe vaudrait pour toutes les entreprises à la fois — celles dont la
  page propose la liste dans un menu déroulant ;
- **la commande**, `/reglages motdepasse`. Le mot de passe est tiré par le bot et
  rendu dans une réponse **éphémère** : passé en argument de commande, Discord
  l'afficherait à tout le salon, et il serait à changer aussitôt que lu.

Le mot de passe en clair ne doit exister qu'entre le tirage et cette réponse :
nulle part en base, et donc nulle part dans une sauvegarde de la base.
"""

import re

from src.db import Store
from src.motdepasse import LONGUEUR_MAXIMALE, verifie
from src.page_frais import CHEMIN

from tests.test_commandes_fourchettes import (
    InteractionFactice,
    ServeurFactice,
    _bot,
    _commande,
)
from tests.test_commandes_par_serveur import EMPIRE, VOISIN, _interaction

#: Un mot de passe tiré, tel qu'il apparaît dans une réponse : quatre groupes de
#: quatre caractères. Sert à le relire dans le message, faute de pouvoir le
#: demander à la base — qui n'en garde que l'empreinte.
MOTIF = re.compile(r"[a-z2-9]{4}(?:-[a-z2-9]{4}){3}")


async def _magasin() -> Store:
    store = Store(dsn="")
    await store.connect()
    return store


def _lire_le_mot_de_passe(interaction: InteractionFactice) -> str:
    """Le mot de passe tel qu'il est montré, extrait de la réponse."""
    trouve = MOTIF.search(" ".join(interaction.textes))
    assert trouve, f"aucun mot de passe dans {interaction.textes}"
    return trouve.group(0)


def _non_admin(serveur_id: int) -> InteractionFactice:
    interaction = InteractionFactice(admin=False)
    interaction.guild = ServeurFactice(serveur_id)
    return interaction


# --- Le tiroir --------------------------------------------------------------


async def test_une_entreprise_neuve_na_pas_de_mot_de_passe():
    """Et sa page est donc fermée en écriture : c'est `verifie` qui le tient,
    mais encore faut-il que l'absence se lise comme une absence."""
    store = await _magasin()

    assert await store.motdepasse_page() is None


async def test_definir_rend_le_mot_de_passe_et_nen_garde_que_lempreinte():
    """Le seul moment où le clair existe : il est rendu, jamais relu."""
    store = await _magasin()

    mdp = await store.definir_motdepasse_page()

    assert verifie(await store.motdepasse_page(), mdp) is True
    assert mdp not in repr(await store.tout())


async def test_definir_accepte_un_mot_de_passe_choisi():
    """Le tirage reste le défaut, mais on peut en imposer un.

    Rendu tel quel, comme le tiré : la commande n'a alors qu'un seul chemin à
    suivre pour confirmer, et le clair ne vit toujours qu'entre l'appel et la
    réponse.
    """
    store = await _magasin()

    rendu = await store.definir_motdepasse_page("frais-du-soir")

    assert rendu == "frais-du-soir"
    assert verifie(await store.motdepasse_page(), "frais-du-soir") is True
    assert "frais-du-soir" not in repr(await store.tout())


async def test_un_mot_de_passe_choisi_remplace_celui_qui_etait_tire():
    """Sinon régler le sien laisserait l'ancien ouvrir la page."""
    store = await _magasin()
    tire = await store.definir_motdepasse_page()

    await store.definir_motdepasse_page("frais-du-soir")

    assert verifie(await store.motdepasse_page(), tire) is False


async def test_le_redefinir_invalide_lancien():
    """C'est ce qu'on attend d'un mot de passe changé, et le seul moyen de couper
    quelqu'un à qui on l'avait donné."""
    store = await _magasin()

    ancien = await store.definir_motdepasse_page()
    nouveau = await store.definir_motdepasse_page()

    assert ancien != nouveau
    assert verifie(await store.motdepasse_page(), ancien) is False
    assert verifie(await store.motdepasse_page(), nouveau) is True


async def test_effacer_referme_la_page():
    """Retirer le mot de passe doit fermer l'écriture, pas l'ouvrir à tous."""
    store = await _magasin()
    mdp = await store.definir_motdepasse_page()

    assert await store.effacer_motdepasse_page() is True
    assert await store.motdepasse_page() is None
    assert verifie(await store.motdepasse_page(), mdp) is False


async def test_effacer_sans_mot_de_passe_ne_dit_pas_le_contraire():
    """La commande répond d'après ce booléen : « ✅ retiré » sur une entreprise
    qui n'en avait pas ferait croire une page refermée qui ne l'était jamais."""
    store = await _magasin()

    assert await store.effacer_motdepasse_page() is False


async def test_une_trace_abimee_se_lit_comme_absente():
    """La base est du JSON qu'on peut retoucher à la main.

    Rendue telle quelle, une valeur qui n'est pas un enregistrement ferait
    échouer la signature du cookie — une panne de la page là où il ne devrait y
    avoir qu'un refus.
    """
    store = await _magasin()
    await store.set("motdepasse_page", "coucou")

    assert await store.motdepasse_page() is None


async def test_chaque_entreprise_a_son_mot_de_passe():
    """Le cœur du réglage : la page propose la liste des entreprises, et un mot
    de passe commun donnerait à chacun l'écriture chez tous."""
    store = await _magasin()

    ici = await store.pour(EMPIRE).definir_motdepasse_page()

    assert await store.pour(VOISIN).motdepasse_page() is None
    assert await store.motdepasse_page() is None
    assert verifie(await store.pour(EMPIRE).motdepasse_page(), ici) is True


async def test_le_mot_de_passe_dune_entreprise_ne_vaut_pas_chez_lautre():
    store = await _magasin()

    ici = await store.pour(EMPIRE).definir_motdepasse_page()
    await store.pour(VOISIN).definir_motdepasse_page()

    assert verifie(await store.pour(VOISIN).motdepasse_page(), ici) is False


# --- /reglages motdepasse : la fenêtre de saisie ----------------------------
#
# Le mot de passe n'est pas un argument de commande : Discord affiche les
# arguments dans le salon, à tout le monde et pour toujours. Il arrive donc par
# une fenêtre de saisie, dont le contenu ne part qu'au bot.


async def _ouvrir(bot, interaction):
    """Tape la commande et rend la fenêtre de saisie qu'elle ouvre."""
    await _commande(bot, "reglages motdepasse").callback(interaction)
    modale = interaction.response.modale
    assert modale is not None, f"aucune fenêtre ouverte : {interaction.textes}"
    return modale


def _taper(modale, valeur: str) -> None:
    """Écrit `valeur` dans le champ, là où Discord l'écrit.

    `TextInput.value` est en lecture seule : c'est Discord qui le remplit à
    l'envoi du formulaire. Le test prend donc sa place plutôt que d'inventer une
    porte de derrière dans le code du bot.
    """
    modale.saisie._value = valeur


async def _envoyer(bot, saisi: str | None = None, serveur: int = EMPIRE, admin=None):
    """Le geste complet : ouvrir la fenêtre, taper (ou non), envoyer.

    Rend l'interaction de l'envoi — c'est elle qui porte la réponse, la première
    n'ayant servi qu'à ouvrir la fenêtre.
    """
    modale = await _ouvrir(bot, _interaction(serveur))
    if saisi is not None:
        _taper(modale, saisi)
    envoi = _interaction(serveur) if admin is None else admin
    await modale.on_submit(envoi)
    return envoi


async def test_la_commande_ouvre_une_fenetre_et_nenregistre_rien():
    """Rien n'est réglé avant l'envoi du formulaire.

    C'est la commande elle-même qui doit être muette : un mot de passe demandé
    en argument s'afficherait dans le salon, et serait à changer aussitôt que lu.
    """
    bot = await _bot()
    interaction = _interaction(EMPIRE)

    await _ouvrir(bot, interaction)

    assert await bot.store.pour(EMPIRE).motdepasse_page() is None
    assert not MOTIF.search(" ".join(interaction.textes))


async def test_le_champ_peut_rester_vide():
    """Le vide est le chemin du tirage.

    Un champ obligatoire — ou une longueur minimale confiée à Discord — grise le
    bouton d'envoi : le tirage deviendrait impossible sans que rien n'explique
    pourquoi, et le refus d'un mot de passe trop court ne dirait plus sa raison.
    """
    bot = await _bot()

    modale = await _ouvrir(bot, _interaction(EMPIRE))

    assert modale.saisie.required is False
    assert modale.saisie.min_length is None


async def test_le_champ_sarrete_a_la_longueur_que_la_regle_accepte():
    """La même borne des deux côtés : sinon Discord laisserait taper ce que le
    bot refuse, ou couperait ce qu'il aurait accepté."""
    bot = await _bot()

    modale = await _ouvrir(bot, _interaction(EMPIRE))

    assert modale.saisie.max_length == LONGUEUR_MAXIMALE


# --- Le mot de passe tiré ---------------------------------------------------


async def test_la_fenetre_vide_tire_un_mot_de_passe_qui_marche():
    """Montré une fois : il n'est pas relisible ensuite, seule son empreinte
    reste. Un message qui ne le contiendrait pas le rendrait inutilisable."""
    bot = await _bot()

    envoi = await _envoyer(bot)

    trace = await bot.store.pour(EMPIRE).motdepasse_page()
    assert verifie(trace, _lire_le_mot_de_passe(envoi)) is True


async def test_un_champ_despaces_tire_aussi():
    """Un espace pris dans le collage ne doit pas devenir un mot de passe d'un
    caractère refusé : rien de tapé veut dire rien de tapé."""
    bot = await _bot()

    envoi = await _envoyer(bot, "   ")

    trace = await bot.store.pour(EMPIRE).motdepasse_page()
    assert verifie(trace, _lire_le_mot_de_passe(envoi)) is True


async def test_le_mot_de_passe_nest_montre_qua_qui_le_demande():
    """Éphémère, sans quoi il resterait dans l'historique du salon — lisible par
    tout le serveur, et par tout nouveau membre."""
    bot = await _bot()

    envoi = await _envoyer(bot)

    messages = [*envoi.response.messages, *envoi.followup.messages]
    assert messages
    assert all(message.get("ephemeral") for message in messages)


async def test_la_commande_regle_le_serveur_ou_elle_est_tapee():
    """Écrit dans le commun, le mot de passe n'ouvrirait aucune entreprise : la
    page lit celui de l'entreprise choisie dans son menu."""
    bot = await _bot()

    await _envoyer(bot)

    assert await bot.store.pour(EMPIRE).motdepasse_page() is not None
    assert await bot.store.pour(VOISIN).motdepasse_page() is None
    assert await bot.store.motdepasse_page() is None


async def test_la_commande_dit_ou_taper_le_mot_de_passe():
    """Un mot de passe sans l'adresse de la page ne sert à rien."""
    bot = await _bot()

    envoi = await _envoyer(bot)

    assert CHEMIN in " ".join(envoi.textes)


async def test_le_nouveau_mot_de_passe_previent_quil_coupe_les_navigateurs():
    """Le cookie est signé avec l'empreinte : le retirer coupe les navigateurs
    déjà identifiés. Retaper la commande pour relire le mot de passe oublié
    déconnecte donc les autres postes, et il faut le savoir avant."""
    bot = await _bot()

    envoi = await _envoyer(bot)

    assert "navigateur" in " ".join(envoi.textes).casefold()


# --- Le mot de passe choisi -------------------------------------------------


async def test_un_mot_de_passe_choisi_est_enregistre():
    """Le but : en régler un qu'on retient, sans aller le rechercher dans un
    message éphémère fermé depuis longtemps."""
    bot = await _bot()

    await _envoyer(bot, "frais-du-soir")

    trace = await bot.store.pour(EMPIRE).motdepasse_page()
    assert verifie(trace, "frais-du-soir") is True


async def test_le_mot_de_passe_choisi_nest_pas_repete_dans_la_reponse():
    """Celui qui le tape le connaît déjà.

    Le répéter le laisserait à l'écran, dans un message qu'on ne pense pas à
    fermer — visible par-dessus l'épaule, et dans un partage d'écran. Et la
    réponse doit dire qu'il n'est pas relisible : sinon on compterait sur le bot
    pour le rappeler un jour.
    """
    bot = await _bot()

    envoi = await _envoyer(bot, "frais-du-soir")

    textes = " ".join(envoi.textes)
    assert "frais-du-soir" not in textes
    assert "remplace" in textes.casefold()


async def test_les_espaces_autour_dun_mot_de_passe_choisi_sont_enleves():
    """Un mot de passe collé emporte souvent un espace. Gardé, il donnerait un
    mot de passe intapable : la page enlève les espaces de ce qu'on lui donne."""
    bot = await _bot()

    await _envoyer(bot, "  frais-du-soir  ")

    trace = await bot.store.pour(EMPIRE).motdepasse_page()
    assert verifie(trace, "frais-du-soir") is True


async def test_un_mot_de_passe_choisi_trop_faible_est_refuse():
    """Le plancher, et la seule chose qui compte quand il refuse : **rien ne
    change**. Écraser l'ancien au passage fermerait la page sans le dire."""
    bot = await _bot()
    ancien = await bot.store.pour(EMPIRE).definir_motdepasse_page()

    envoi = await _envoyer(bot, "1234")

    trace = await bot.store.pour(EMPIRE).motdepasse_page()
    assert verifie(trace, ancien) is True
    assert "minimum" in " ".join(envoi.textes).casefold()


async def test_le_refus_dit_ce_qui_manque():
    """« ❌ refusé » sans raison ferait retaper le même, ou abandonner."""
    bot = await _bot()

    envoi = await _envoyer(bot, "aaaaaaaaaaaa")

    assert await bot.store.pour(EMPIRE).motdepasse_page() is None
    assert "différents" in " ".join(envoi.textes).casefold()


async def test_retirer_referme_la_page():
    bot = await _bot()
    await bot.store.pour(EMPIRE).definir_motdepasse_page()
    interaction = _interaction(EMPIRE)

    await _commande(bot, "reglages motdepasse").callback(interaction, retirer=True)

    assert await bot.store.pour(EMPIRE).motdepasse_page() is None
    assert not MOTIF.search(" ".join(interaction.textes))


async def test_retirer_sans_mot_de_passe_le_dit():
    """« ✅ retiré » ferait croire une page refermée qui ne l'a jamais été."""
    bot = await _bot()
    interaction = _interaction(EMPIRE)

    await _commande(bot, "reglages motdepasse").callback(interaction, retirer=True)

    assert "aucun" in " ".join(interaction.textes).casefold()


async def test_seul_un_administrateur_regle_le_mot_de_passe():
    """Le mot de passe s'emporte hors de Discord : le donner revient à ajouter
    quelqu'un à la liste d'accès en écriture, et cela ne s'accorde pas soi-même.

    Refusé avant même la fenêtre : ouvrir un formulaire pour refuser son envoi
    ferait taper un mot de passe pour rien.
    """
    bot = await _bot()
    interaction = _non_admin(EMPIRE)

    await _commande(bot, "reglages motdepasse").callback(interaction)

    assert interaction.response.modale is None
    assert await bot.store.pour(EMPIRE).motdepasse_page() is None
    assert not MOTIF.search(" ".join(interaction.textes))
    assert "administrateur" in " ".join(interaction.textes).casefold()


async def test_le_role_perdu_entre_la_fenetre_et_lenvoi_ferme_la_porte():
    """La fenêtre reste ouverte un quart d'heure : le droit est donc revérifié à
    l'envoi, qui est le moment où l'on écrit.

    C'est aussi ce qui tient si l'envoi arrive d'ailleurs que de la fenêtre — un
    formulaire Discord se rejoue, la vérification d'un droit ne se délègue pas au
    fait d'avoir vu le formulaire.
    """
    bot = await _bot()
    modale = await _ouvrir(bot, _interaction(EMPIRE))
    _taper(modale, "frais-du-soir")

    await modale.on_submit(_non_admin(EMPIRE))

    assert await bot.store.pour(EMPIRE).motdepasse_page() is None


async def test_un_non_administrateur_ne_referme_pas_la_page():
    """Le refus doit porter sur la commande, pas seulement sur le tirage : sinon
    un membre autorisé couperait la page de tout le monde."""
    bot = await _bot()
    await bot.store.pour(EMPIRE).definir_motdepasse_page()

    await _commande(bot, "reglages motdepasse").callback(
        _non_admin(EMPIRE), retirer=True
    )

    assert await bot.store.pour(EMPIRE).motdepasse_page() is not None
