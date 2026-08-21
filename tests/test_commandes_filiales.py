"""Le groupe `/filiales` : le tableau des frais, sa saisie et son entretien.

La saisie d'un relevé est `/filiales releve`, et `/convertir frais` n'est plus
qu'une calculatrice. C'était une seule commande, qui écrivait en base ou pas
selon qu'une case facultative était remplie : rien dans son nom ne prévenait
celui qui la tapait, et les deux moitiés étaient invisibles l'une à l'autre
jusqu'au tableau du soir. Les premiers tests d'ici verrouillent donc les deux
moitiés **séparées** : la calculatrice ne doit plus rien laisser derrière elle,
et un relevé doit avoir sa propre commande pour être saisi.

Le reste du groupe porte l'entretien du tableau (liste, retirer, vider, export)
et, par le vocabulaire commun des publications, ses réglages (heure, salons,
aperçu, publier) — ceux-là sont éprouvés sur une publication d'essai dans
`tests/test_commandes_publication.py` ; ici on vérifie qu'ils sont bien branchés
sur les données du tableau.
"""

from decimal import Decimal

from src.filiales import vers_import
from src.schedule import maintenant_local

from tests.test_commandes_fourchettes import (
    InteractionFactice,
    SalonFactice,
    _bot,
    _commande,
    _magasin,
)


def _autocomplete(commande, parametre: str):
    """Le rappel d'autocomplétion d'un paramètre.

    `Parameter.autocomplete` publique ne dit que *s'il y en a un* (un booléen) ;
    l'appeler passe par `_params`. Vérifier le booléen seul laisserait passer une
    complétion qui ne propose rien.
    """
    return commande._params[parametre].autocomplete


def _texte(interaction: InteractionFactice) -> str:
    """Tout ce que la commande a répondu, espaces insécables normalisés."""
    parties = list(interaction.textes)
    for embed in interaction.embeds:
        parties += [embed.title or "", embed.description or ""]
        for champ in embed.fields:
            parties += [champ.name or "", champ.value or ""]
        if embed.footer:
            parties.append(embed.footer.text or "")
    return " ".join(parties).replace("\xa0", " ")


# --- La calculatrice ne touche plus au tableau ------------------------------


async def test_la_calculatrice_calcule_sans_rien_enregistrer():
    """C'est une calculatrice : elle ne doit rien laisser derrière elle."""
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "convertir frais").callback(interaction, montant="2,71P")

    assert "189.70 TØ" in _texte(interaction)
    assert await _magasin(bot).filiales() == []


async def test_la_calculatrice_n_offre_plus_de_case_filiale():
    """Éprouvé sur les paramètres Discord et pas seulement sur l'effet : tant que
    la case existe, elle est proposée dans le menu, et celui qui la remplit
    attend un enregistrement qui n'aura pas lieu."""
    bot = await _bot()

    assert [p.name for p in _commande(bot, "convertir frais").parameters] == ["montant"]


async def test_la_calculatrice_reste_privee():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "convertir frais").callback(interaction, montant="1P")

    assert all(m.get("ephemeral") for m in interaction.response.messages)


# --- /filiales releve : calcule et enregistre -------------------------------


async def test_releve_enregistre_le_releve():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "filiales releve").callback(
        interaction, filiale="ARMEE  DE TERRE", montant="1000"
    )

    filiales = await _magasin(bot).filiales()
    assert [f.nom for f in filiales] == ["ARMEE  DE TERRE"]
    assert filiales[0].frais == Decimal(70)


async def test_releve_confirme_avec_le_nom_et_le_montant():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "filiales releve").callback(
        interaction, filiale="ARMEE", montant="1000"
    )

    texte = _texte(interaction)
    assert "ARMEE" in texte
    assert "70 Ø" in texte


async def test_releve_donne_le_montant_recopiable():
    """Ce qu'on paie dans le jeu, pas « 189.74 TØ »."""
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "filiales releve").callback(
        interaction, filiale="ARMEE", montant="2 710 572 934 559 948"
    )

    assert "189 740 105 419 196" in _texte(interaction)


async def test_releve_annonce_le_total_de_toutes_les_filiales():
    """Le total est la raison d'être du tableau : le voir monter à chaque saisie
    évite d'attendre le post du soir pour savoir où on en est."""
    bot = await _bot()
    await _magasin(bot).enregistrer_filiale("A", Decimal(1000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales releve").callback(
        interaction, filiale="B", montant="2000"
    )

    # 70 + 140
    assert "210 Ø" in _texte(interaction)


async def test_une_ressaisie_le_dit_au_lieu_d_annoncer_un_ajout():
    """Sans ça, on ne saurait pas qu'on vient d'écraser un relevé — ni si la
    filiale a été saisie deux fois sous deux orthographes."""
    bot = await _bot()
    await _magasin(bot).enregistrer_filiale("ARMEE", Decimal(1000), "2026-08-09")
    interaction = InteractionFactice()

    await _commande(bot, "filiales releve").callback(
        interaction, filiale="armee", montant="2000"
    )

    texte = _texte(interaction).lower()
    assert "mise à jour" in texte or "remplac" in texte
    assert len(await _magasin(bot).filiales()) == 1


async def test_releve_en_perte_enregistre_zero_et_le_signale():
    """Le jeu ne rembourse pas. Une filiale à 0 Ø sans explication se lirait
    comme une saisie ratée."""
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "filiales releve").callback(
        interaction, filiale="PERTE", montant="-500"
    )

    assert (await _magasin(bot).filiales())[0].frais == Decimal(0)
    assert "perte" in _texte(interaction).lower()


async def test_releve_montant_illisible_n_enregistre_rien():
    """Une filiale enregistrée à un montant faux serait pire qu'un refus : elle
    figurerait dans le tableau et fausserait le total."""
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "filiales releve").callback(
        interaction, filiale="ARMEE", montant="beaucoup"
    )

    texte = _texte(interaction)
    assert "❌" in texte
    assert "12.25M" in texte  # l'aide sur les formats
    assert await _magasin(bot).filiales() == []


async def test_releve_avec_un_nom_vide_est_refuse():
    """Discord accepte une chaîne d'espaces : la ligne serait anonyme."""
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "filiales releve").callback(
        interaction, filiale="   ", montant="1000"
    )

    assert "❌" in _texte(interaction)
    assert await _magasin(bot).filiales() == []


async def test_releve_rappelle_ou_le_tableau_sortira():
    """Une saisie qui n'ira nulle part doit se voir tout de suite, pas au moment
    où l'on s'étonne de ne rien recevoir."""
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "filiales releve").callback(
        interaction, filiale="A", montant="1000"
    )

    assert "/filiales salon" in _texte(interaction)


async def test_releve_ne_previent_plus_quand_un_salon_est_regle():
    bot = await _bot()
    await _magasin(bot).ajouter_salon_filiales("123")
    interaction = InteractionFactice()

    await _commande(bot, "filiales releve").callback(
        interaction, filiale="A", montant="1000"
    )

    assert "/filiales salon" not in _texte(interaction)


async def test_releve_reste_prive():
    """Les résultats de l'entreprise n'ont pas à s'afficher dans le salon."""
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "filiales releve").callback(
        interaction, filiale="A", montant="1000"
    )

    messages = [*interaction.response.messages, *interaction.followup.messages]
    assert messages and all(m.get("ephemeral") for m in messages)


async def test_releve_demande_les_deux_cases():
    """Un relevé sans montant, ou sans nom, n'est pas un relevé : les deux sont
    obligatoires. C'est précisément ce que l'ancienne case facultative de
    `/convertir frais` ne peut pas exprimer."""
    bot = await _bot()

    parametres = _commande(bot, "filiales releve").parameters

    assert [p.name for p in parametres] == ["filiale", "montant"]
    assert all(p.required for p in parametres)


async def test_le_nom_du_releve_se_complete_avec_les_filiales_connues():
    """Le nom est la clé du jeu : le retaper à chaque fois inviterait la faute
    de frappe, qui créerait une seconde filiale au lieu d'en mettre une à jour."""
    bot = await _bot()
    await _magasin(bot).enregistrer_filiale(
        "ARMEE  DE TERRE", Decimal(1000), "2026-08-11"
    )
    await _magasin(bot).enregistrer_filiale("MARINE", Decimal(1000), "2026-08-11")

    completer = _autocomplete(_commande(bot, "filiales releve"), "filiale")
    choix = await completer(InteractionFactice(), "mar")

    assert [c.value for c in choix] == ["MARINE"]


# --- /filiales liste --------------------------------------------------------


async def test_liste_montre_les_filiales_et_le_total():
    bot = await _bot()
    await _magasin(bot).enregistrer_filiale("A", Decimal(1000), "2026-08-11")
    await _magasin(bot).enregistrer_filiale("B", Decimal(2000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales liste").callback(interaction)

    texte = _texte(interaction)
    assert "A" in texte and "B" in texte
    assert "210 Ø" in texte


async def test_liste_vide_dit_comment_ajouter():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "filiales liste").callback(interaction)

    assert "/filiales releve" in _texte(interaction)


# --- /filiales retirer : un nom, un lot, ou tout ----------------------------
#
# Une seule commande là où il y en avait deux. `retirer` et `retirer-plusieurs`
# faisaient le même geste, l'une refusant ce que l'autre acceptait : il fallait
# savoir laquelle prendre avant de savoir combien de noms on allait donner.


async def test_retirer_supprime_la_filiale():
    bot = await _bot()
    await _magasin(bot).enregistrer_filiale("A", Decimal(1000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales retirer").callback(interaction, filiales="a")

    assert await _magasin(bot).filiales() == []
    assert "✅" in _texte(interaction)


async def test_retirer_un_seul_nom_ne_demande_pas_de_confirmation():
    """Une cérémonie sur un geste d'un mot apprend à cocher sans lire, et la
    case ne protégerait plus le lot qu'elle est là pour protéger."""
    bot = await _bot()
    await _magasin(bot).enregistrer_filiale("A", Decimal(1000), "2026-08-11")

    await _commande(bot, "filiales retirer").callback(
        InteractionFactice(), filiales="A"
    )

    assert await _magasin(bot).filiales() == []


async def test_retirer_se_complete_avec_les_filiales_connues():
    """Retaper le nom pour supprimer, c'est risquer de ne rien supprimer."""
    bot = await _bot()
    await _magasin(bot).enregistrer_filiale("MARINE", Decimal(1000), "2026-08-11")

    completer = _autocomplete(_commande(bot, "filiales retirer"), "filiales")

    assert [c.value for c in await completer(InteractionFactice(), "")] == ["MARINE"]


async def test_retirer_une_filiale_inconnue_liste_les_connues():
    """Sinon on ne sait pas si c'est une faute de frappe ou une filiale jamais
    saisie."""
    bot = await _bot()
    await _magasin(bot).enregistrer_filiale("ARMEE", Decimal(1000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales retirer").callback(interaction, filiales="MARINE")

    texte = _texte(interaction)
    assert "❌" in texte
    assert "ARMEE" in texte


async def test_retirer_supprime_tout_le_lot():
    bot = await _bot()
    for nom in ("A", "B", "C"):
        await _magasin(bot).enregistrer_filiale(nom, Decimal(1000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales retirer").callback(
        interaction, filiales="a, C", confirmer=True
    )

    assert [f.nom for f in await _magasin(bot).filiales()] == ["B"]


async def test_un_lot_sans_confirmation_ne_touche_a_rien():
    """La case ne protège que ce qui la mérite : plus d'un nom d'un coup.

    C'est là que le geste devient irrattrapable de mémoire — on ne se souvient
    pas des montants de cinq filiales.
    """
    bot = await _bot()
    await _magasin(bot).enregistrer_filiale("A", Decimal(1000), "2026-08-11")
    await _magasin(bot).enregistrer_filiale("B", Decimal(2000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales retirer").callback(interaction, filiales="A, B")

    assert len(await _magasin(bot).filiales()) == 2
    texte = _texte(interaction)
    assert "❌" in texte
    # Ce qui allait partir, nommément : « 2 filiales » ne permettrait pas de voir
    # qu'on s'est trompé de lot avant de le perdre.
    assert "A" in texte and "B" in texte


async def test_retirer_tout_demande_une_confirmation_meme_pour_une_filiale():
    """`tout` ne nomme pas ce qu'il emporte : celui qui le tape ne sait pas
    forcément combien de lignes le tableau contient. Compter les noms saisis
    laisserait donc passer le geste le plus large de tous."""
    bot = await _bot()
    await _magasin(bot).enregistrer_filiale("A", Decimal(1000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales retirer").callback(interaction, filiales="tout")

    assert len(await _magasin(bot).filiales()) == 1
    assert "❌" in _texte(interaction)


async def test_retirer_dit_les_noms_inconnus():
    """Sans ça, on croirait une filiale supprimée alors qu'elle reste dans le
    tableau du soir."""
    bot = await _bot()
    await _magasin(bot).enregistrer_filiale("A", Decimal(1000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales retirer").callback(
        interaction, filiales="A, MARINE", confirmer=True
    )

    texte = _texte(interaction)
    assert "MARINE" in texte
    # Et le retrait valide du même lot a bien eu lieu.
    assert await _magasin(bot).filiales() == []


async def test_retirer_accepte_le_tout():
    """Vider la liste d'un geste est le cas qui a motivé la saisie multiple."""
    bot = await _bot()
    for nom in ("A", "B", "C"):
        await _magasin(bot).enregistrer_filiale(nom, Decimal(1000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales retirer").callback(
        interaction, filiales="tout", confirmer=True
    )

    assert await _magasin(bot).filiales() == []
    assert "3" in _texte(interaction)


async def test_retirer_sans_nom_ne_vide_pas_le_tableau():
    """Une saisie vide est un accident : l'interpréter comme « tout » serait la
    pire lecture possible.

    Le message est éprouvé autant que l'absence de retrait : sans nom saisi,
    aucun nom ne serait retiré de toute façon, et un test qui s'arrêterait là
    passerait aussi bien sans la garde. Ce qu'elle apporte, c'est de dire ce qui
    manque — faute de quoi la commande annonce « aucune filiale enregistrée »
    alors qu'il y en a une.
    """
    bot = await _bot()
    await _magasin(bot).enregistrer_filiale("A", Decimal(1000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales retirer").callback(
        interaction, filiales="  ", confirmer=True
    )

    assert len(await _magasin(bot).filiales()) == 1
    texte = _texte(interaction)
    assert "❌" in texte
    assert "aucune filiale enregistrée" not in texte.lower(), (
        "le message ment : une filiale est enregistrée"
    )
    # Dit comment saisir un lot, sinon le refus est un cul-de-sac.
    assert "virgule" in texte.lower()


async def test_retirer_reconnait_tout_quelle_que_soit_la_casse():
    """`TOUT` tapé en majuscules serait sinon pris pour un nom de filiale, et la
    commande répondrait qu'elle ne le connaît pas — en laissant le tableau
    intact alors qu'on demandait de le vider."""
    bot = await _bot()
    await _magasin(bot).enregistrer_filiale("A", Decimal(1000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales retirer").callback(
        interaction, filiales="TOUT", confirmer=True
    )

    assert await _magasin(bot).filiales() == []


async def test_retirer_garde_les_doubles_espaces_du_nom():
    """La clé d'import du jeu passe par le découpage sans être normalisée."""
    bot = await _bot()
    await _magasin(bot).enregistrer_filiale(
        "ARMEE  DE TERRE", Decimal(1000), "2026-08-11"
    )
    interaction = InteractionFactice()

    await _commande(bot, "filiales retirer").callback(
        interaction, filiales="ARMEE  DE TERRE"
    )

    assert await _magasin(bot).filiales() == []


# --- /filiales heure --------------------------------------------------------


async def test_heure_regle_l_heure_du_tableau():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "filiales heure").callback(interaction, heure="20:30")

    assert await _magasin(bot).heure_filiales() == "20:30"
    assert "20:30" in _texte(interaction)


async def test_heure_invalide_est_refusee():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "filiales heure").callback(interaction, heure="25:00")

    assert "❌" in _texte(interaction)
    assert await _magasin(bot).heure_filiales() == "09:00"


async def test_heure_ne_touche_pas_a_celle_des_promotions():
    """Deux posts, deux horaires : régler l'un en déplaçant l'autre serait une
    surprise découverte le lendemain matin."""
    bot = await _bot()

    await _commande(bot, "filiales heure").callback(InteractionFactice(), heure="20:30")

    assert (await _magasin(bot).config())["heure"] == "09:00"


async def test_regler_l_heure_oublie_la_marque_du_jour():
    """Régler l'heure exprime l'intention de publier à la nouvelle heure : un
    post déjà sorti bloquerait sinon ce nouvel horaire jusqu'à demain."""
    bot = await _bot()
    await _magasin(bot).marquer_publie_filiales("2026-08-11")

    await _commande(bot, "filiales heure").callback(InteractionFactice(), heure="20:30")

    assert await _magasin(bot).derniere_publication_filiales() is None


# --- /filiales salon --------------------------------------------------------


async def test_salon_ajouter_enregistre_le_salon():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "filiales salon ajouter").callback(
        interaction, salon=SalonFactice(123)
    )

    assert await _magasin(bot).salons_filiales() == ["123"]
    assert "✅" in _texte(interaction)


async def test_salon_ajouter_refuse_sans_permission():
    """Vérifié au réglage : une permission manquante découverte à l'heure du
    post est un tableau perdu."""
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "filiales salon ajouter").callback(
        interaction, salon=SalonFactice(123, peut_ecrire=False)
    )

    assert "❌" in _texte(interaction)
    assert await _magasin(bot).salons_filiales() == []


async def test_salon_ajouter_deux_fois_le_dit():
    bot = await _bot()
    salon = SalonFactice(123)
    await _commande(bot, "filiales salon ajouter").callback(
        InteractionFactice(), salon=salon
    )
    interaction = InteractionFactice()

    await _commande(bot, "filiales salon ajouter").callback(interaction, salon=salon)

    assert "déjà" in _texte(interaction)
    assert await _magasin(bot).salons_filiales() == ["123"]


async def test_salon_retirer_retire_le_salon():
    bot = await _bot()
    await _magasin(bot).ajouter_salon_filiales("123")
    interaction = InteractionFactice()

    await _commande(bot, "filiales salon retirer").callback(
        interaction, salon=SalonFactice(123)
    )

    assert await _magasin(bot).salons_filiales() == []


async def test_salon_retirer_un_salon_absent_le_dit():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "filiales salon retirer").callback(
        interaction, salon=SalonFactice(123)
    )

    assert "❌" in _texte(interaction)


async def test_les_salons_du_tableau_ne_touchent_pas_a_ceux_des_promotions():
    bot = await _bot()

    await _commande(bot, "filiales salon ajouter").callback(
        InteractionFactice(), salon=SalonFactice(123)
    )

    assert await _magasin(bot).salons() == []


# --- /filiales apercu ------------------------------------------------------


async def test_apercu_montre_le_tableau_sans_publier():
    bot = await _bot()
    await _magasin(bot).ajouter_salon_filiales("1")
    await _magasin(bot).enregistrer_filiale("A", Decimal(1000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales apercu").callback(interaction)

    assert "70 Ø" in _texte(interaction)
    # Rien n'a été marqué : le post du jour doit encore pouvoir sortir.
    assert await _magasin(bot).derniere_publication_filiales() is None


async def test_apercu_sans_salon_dit_qu_il_ne_sortirait_rien():
    """La question de l'aperçu est « qu'est-ce que le bot va poster ? ».

    Sans salon, la réponse est « rien », et montrer le tableau quand même
    laisserait croire l'inverse. Pour le regarder sans le publier, c'est
    `/filiales liste` — le même embed, sans promesse d'envoi.
    """
    bot = await _bot()
    await _magasin(bot).enregistrer_filiale("A", Decimal(1000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales apercu").callback(interaction)

    texte = _texte(interaction)
    assert "❌" in texte
    assert "salon" in texte


async def test_apercu_reste_prive():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "filiales apercu").callback(interaction)

    messages = [*interaction.response.messages, *interaction.followup.messages]
    assert messages and all(m.get("ephemeral") for m in messages)


# --- /filiales vider -------------------------------------------------------
#
# `vider` vide les **montants**, pas la liste : les noms sont la clé d'import du
# jeu, et les reperdre à chaque cycle obligerait à les retaper un par un. Pour
# perdre les noms aussi, c'est `retirer tout`.


async def test_vider_annule_les_montants_et_garde_les_noms():
    """Un nouveau cycle ne change que les montants : reperdre les noms
    obligerait à retaper les clés d'import du jeu."""
    bot = await _bot()
    await _magasin(bot).enregistrer_filiale("A", Decimal(1000), "2026-08-11")
    await _magasin(bot).enregistrer_filiale("B", Decimal(2000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales vider").callback(interaction, confirmer=True)

    filiales = await _magasin(bot).filiales()
    assert [f.nom for f in filiales] == ["A", "B"]
    assert all(f.benefices == Decimal(0) for f in filiales)


async def test_vider_sans_confirmer_ne_touche_a_rien():
    """Effacer tous les relevés du jour par mégarde coûterait une ressaisie
    complète : la case doit être cochée pour que ça parte."""
    bot = await _bot()
    await _magasin(bot).enregistrer_filiale("A", Decimal(1000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales vider").callback(interaction, confirmer=False)

    assert (await _magasin(bot).filiales())[0].benefices == Decimal(1000)
    texte = _texte(interaction)
    assert "❌" in texte
    # Dit combien de relevés étaient en jeu : c'est ce qui fait mesurer le geste.
    assert "1" in texte


async def test_vider_sans_filiale_le_dit():
    """Annoncer une remise faite sur une liste vide laisserait croire qu'il y
    avait quelque chose à remettre."""
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "filiales vider").callback(interaction, confirmer=True)

    assert "aucune" in _texte(interaction).lower()


async def test_vider_confirme_combien_de_filiales():
    bot = await _bot()
    await _magasin(bot).enregistrer_filiale("A", Decimal(1000), "2026-08-11")
    await _magasin(bot).enregistrer_filiale("B", Decimal(2000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales vider").callback(interaction, confirmer=True)

    texte = _texte(interaction)
    assert "✅" in texte
    assert "2" in texte


async def test_vider_garde_les_noms_pour_la_completion():
    """C'est tout l'intérêt de garder les noms : le cycle suivant ne demande que
    les montants, et l'autocomplétion propose encore les filiales."""
    bot = await _bot()
    await _magasin(bot).enregistrer_filiale("MARINE", Decimal(1000), "2026-08-11")
    await _commande(bot, "filiales vider").callback(InteractionFactice(), confirmer=True)

    completer = _autocomplete(_commande(bot, "filiales releve"), "filiale")

    assert [c.value for c in await completer(InteractionFactice(), "")] == ["MARINE"]


async def test_vider_reste_prive():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "filiales vider").callback(interaction, confirmer=True)

    assert all(m.get("ephemeral") for m in interaction.response.messages)



# --- /filiales export -------------------------------------------------------


def _fichiers(interaction: InteractionFactice) -> list:
    """Les pieces jointes envoyees, quelle que soit la voie.

    `Reponse.send_message` range tous ses `**options` dans le message : le
    `file=` y est donc tel quel, sans avoir a etendre le faux.
    """
    return [
        message["file"]
        for message in [*interaction.response.messages, *interaction.followup.messages]
        if message.get("file")
    ]


def _octets(fichier) -> bytes:
    """Le contenu exact de la piece jointe.

    Les octets et non le texte : c'est le seul niveau ou le CRLF se voit, et le
    format du jeu se joue precisement la.
    """
    fichier.fp.seek(0)
    return fichier.fp.read()


async def test_export_joint_un_fichier():
    """Une piece jointe et non un bloc de code.

    Discord normalise les fins de ligne du contenu d'un message : un bloc ne
    pourrait pas porter le CRLF que le jeu attend, et la tabulation ne s'y
    saisit meme pas.
    """
    bot = await _bot()
    await _magasin(bot).enregistrer_filiale("MEGAPOLE", Decimal(1000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales export").callback(interaction)

    assert len(_fichiers(interaction)) == 1, interaction.response.messages


async def test_export_rend_exactement_le_format_d_import():
    """L'egalite et non un `in` : c'est le format entier qui est en jeu.

    Un `in` laisserait passer un en-tete, une ligne de total, ou n'importe quoi
    ajoute autour — autant de choses que le jeu refuserait.
    """
    bot = await _bot()
    await _magasin(bot).enregistrer_filiale("MEGAPOLE", Decimal(1000), "2026-08-11")
    await _magasin(bot).enregistrer_filiale("EN PERTE", Decimal(-500), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales export").callback(interaction)

    attendu = vers_import(await _magasin(bot).filiales())
    assert _octets(_fichiers(interaction)[0]) == attendu.encode("utf-8")
    assert b"MEGAPOLE\t70\r\nEN PERTE\t0\r\n" == _octets(_fichiers(interaction)[0])


async def test_export_n_ecrit_pas_de_bom():
    """Un BOM se collerait au premier nom de filiale et casserait la cle d'import.

    Invisible dans un editeur, il ne se verrait que par le refus du jeu.
    """
    bot = await _bot()
    await _magasin(bot).enregistrer_filiale("MEGAPOLE", Decimal(1000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales export").callback(interaction)

    assert not _octets(_fichiers(interaction)[0]).startswith(b"\xef\xbb\xbf")


async def test_export_nomme_le_fichier_avec_la_date():
    """Deux exports se confondraient dans le fil sous un nom fixe."""
    bot = await _bot()
    await _magasin(bot).enregistrer_filiale("MEGAPOLE", Decimal(1000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales export").callback(interaction)

    aujourdhui = maintenant_local(
        (await _magasin(bot).config())["fuseau"]
    ).strftime("%Y-%m-%d")
    assert _fichiers(interaction)[0].filename == f"frais-{aujourdhui}.txt"


async def test_export_sans_filiale_ne_joint_rien():
    """Un `.txt` vide se lirait comme une panne du bot.

    Le message dit qu'il n'y a rien et rappelle par ou on remplit le tableau,
    sans quoi il faudrait deviner quelle commande saisit un releve.
    """
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "filiales export").callback(interaction)

    assert _fichiers(interaction) == []
    annonce = " ".join(interaction.textes)
    assert "aucune" in annonce.lower(), annonce
    assert "/filiales releve" in annonce, annonce


async def test_export_dit_les_noms_qu_il_a_du_modifier():
    """Une tabulation collee dans un nom est neutralisee — mais pas en silence.

    Sans le dire, le fichier partirait avec un nom que le jeu ne reconnaitrait
    pas, et rien n'indiquerait pourquoi l'import echoue.
    """
    bot = await _bot()
    await _magasin(bot).enregistrer_filiale(
        "ARMEE\tDE TERRE", Decimal(1000), "2026-08-11"
    )
    interaction = InteractionFactice()

    await _commande(bot, "filiales export").callback(interaction)

    annonce = " ".join(interaction.textes)
    assert "ARMEE DE TERRE" in annonce, annonce


async def test_export_reste_prive():
    bot = await _bot()
    await _magasin(bot).enregistrer_filiale("MEGAPOLE", Decimal(1000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales export").callback(interaction)

    assert all(m.get("ephemeral") for m in interaction.response.messages)
