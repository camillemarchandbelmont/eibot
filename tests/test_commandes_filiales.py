"""`/frais` avec un nom de filiale, et le groupe `/filiales`.

`/frais` reste **une seule commande** : sans nom de filiale, c'est la
calculatrice sans état d'avant ; avec un nom, elle calcule, enregistre et
confirme. Ce qui se vérifie ici, c'est que les deux moitiés ne se marchent pas
dessus — une saisie qui n'enregistre rien, ou un calcul qui écrit en base par
accident, seraient l'un comme l'autre invisibles jusqu'au tableau du soir.

Le groupe `/filiales` porte les réglages du tableau (heure, salons) et son
entretien (liste, retirer) — jamais l'ajout, qui appartient à `/frais`.
"""

from decimal import Decimal

import pytest

from tests.test_commandes_fourchettes import (
    InteractionFactice,
    SalonFactice,
    _bot,
    _commande,
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


# --- /frais sans filiale : la calculatrice n'a pas changé -------------------


async def test_frais_sans_filiale_calcule_sans_rien_enregistrer():
    """C'est la calculatrice : elle ne doit rien laisser derrière elle."""
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "frais").callback(interaction, montant="2,71P")

    assert "189.70 TØ" in _texte(interaction)
    assert await bot.store.filiales() == []


async def test_frais_sans_filiale_reste_prive():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "frais").callback(interaction, montant="1P")

    assert all(m.get("ephemeral") for m in interaction.response.messages)


# --- /frais avec filiale : calcule et enregistre ----------------------------


async def test_frais_avec_filiale_enregistre_le_releve():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "frais").callback(
        interaction, montant="1000", filiale="ARMEE  DE TERRE"
    )

    filiales = await bot.store.filiales()
    assert [f.nom for f in filiales] == ["ARMEE  DE TERRE"]
    assert filiales[0].frais == Decimal(70)


async def test_frais_avec_filiale_confirme_avec_le_nom_et_le_montant():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "frais").callback(interaction, montant="1000", filiale="ARMEE")

    texte = _texte(interaction)
    assert "ARMEE" in texte
    assert "70 Ø" in texte


async def test_frais_avec_filiale_donne_le_montant_recopiable():
    """Ce qu'on paie dans le jeu, pas « 189.74 TØ »."""
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "frais").callback(
        interaction, montant="2 710 572 934 559 948", filiale="ARMEE"
    )

    assert "189 740 105 419 196" in _texte(interaction)


async def test_frais_avec_filiale_annonce_le_total_de_toutes_les_filiales():
    """Le total est la raison d'être du tableau : le voir monter à chaque saisie
    évite d'attendre le post du soir pour savoir où on en est."""
    bot = await _bot()
    await bot.store.enregistrer_filiale("A", Decimal(1000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "frais").callback(interaction, montant="2000", filiale="B")

    # 70 + 140
    assert "210 Ø" in _texte(interaction)


async def test_une_ressaisie_le_dit_au_lieu_d_annoncer_un_ajout():
    """Sans ça, on ne saurait pas qu'on vient d'écraser un relevé — ni si la
    filiale a été saisie deux fois sous deux orthographes."""
    bot = await _bot()
    await bot.store.enregistrer_filiale("ARMEE", Decimal(1000), "2026-08-09")
    interaction = InteractionFactice()

    await _commande(bot, "frais").callback(interaction, montant="2000", filiale="armee")

    texte = _texte(interaction).lower()
    assert "mise à jour" in texte or "remplac" in texte
    assert len(await bot.store.filiales()) == 1


async def test_frais_avec_filiale_en_perte_enregistre_zero_et_le_signale():
    """Le jeu ne rembourse pas. Une filiale à 0 Ø sans explication se lirait
    comme une saisie ratée."""
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "frais").callback(interaction, montant="-500", filiale="PERTE")

    assert (await bot.store.filiales())[0].frais == Decimal(0)
    assert "perte" in _texte(interaction).lower()


async def test_frais_avec_filiale_montant_illisible_n_enregistre_rien():
    """Une filiale enregistrée à un montant faux serait pire qu'un refus : elle
    figurerait dans le tableau et fausserait le total."""
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "frais").callback(
        interaction, montant="beaucoup", filiale="ARMEE"
    )

    texte = _texte(interaction)
    assert "❌" in texte
    assert "12.25M" in texte  # l'aide sur les formats
    assert await bot.store.filiales() == []


async def test_frais_avec_nom_de_filiale_vide_est_refuse():
    """Discord accepte une chaîne d'espaces : la ligne serait anonyme."""
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "frais").callback(interaction, montant="1000", filiale="   ")

    assert "❌" in _texte(interaction)
    assert await bot.store.filiales() == []


async def test_frais_avec_filiale_rappelle_ou_le_tableau_sortira():
    """Une saisie qui n'ira nulle part doit se voir tout de suite, pas au moment
    où l'on s'étonne de ne rien recevoir."""
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "frais").callback(interaction, montant="1000", filiale="A")

    assert "/filiales salon" in _texte(interaction)


async def test_frais_avec_filiale_ne_previent_plus_quand_un_salon_est_regle():
    bot = await _bot()
    await bot.store.ajouter_salon_filiales("123")
    interaction = InteractionFactice()

    await _commande(bot, "frais").callback(interaction, montant="1000", filiale="A")

    assert "/filiales salon" not in _texte(interaction)


async def test_frais_avec_filiale_reste_prive():
    """Les résultats de l'entreprise n'ont pas à s'afficher dans le salon."""
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "frais").callback(interaction, montant="1000", filiale="A")

    messages = [*interaction.response.messages, *interaction.followup.messages]
    assert messages and all(m.get("ephemeral") for m in messages)


async def test_le_parametre_filiale_est_facultatif():
    """C'est tout le contrat de la commande : `/frais montant:…` seul doit
    rester valide côté Discord, pas seulement côté Python."""
    bot = await _bot()
    parametre = next(
        p for p in _commande(bot, "frais").parameters if p.name == "filiale"
    )

    assert not parametre.required


async def test_le_parametre_filiale_se_complete_avec_les_filiales_connues():
    """Le nom est la clé du jeu : le retaper à chaque fois inviterait la faute
    de frappe, qui créerait une seconde filiale au lieu d'en mettre une à jour."""
    bot = await _bot()
    await bot.store.enregistrer_filiale("ARMEE  DE TERRE", Decimal(1000), "2026-08-11")
    await bot.store.enregistrer_filiale("MARINE", Decimal(1000), "2026-08-11")

    completer = _autocomplete(_commande(bot, "frais"), "filiale")
    choix = await completer(InteractionFactice(), "mar")

    assert [c.value for c in choix] == ["MARINE"]


# --- /filiales liste --------------------------------------------------------


async def test_liste_montre_les_filiales_et_le_total():
    bot = await _bot()
    await bot.store.enregistrer_filiale("A", Decimal(1000), "2026-08-11")
    await bot.store.enregistrer_filiale("B", Decimal(2000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales liste").callback(interaction)

    texte = _texte(interaction)
    assert "A" in texte and "B" in texte
    assert "210 Ø" in texte


async def test_liste_vide_dit_comment_ajouter():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "filiales liste").callback(interaction)

    assert "/frais" in _texte(interaction)


# --- /filiales retirer ------------------------------------------------------


async def test_retirer_supprime_la_filiale():
    bot = await _bot()
    await bot.store.enregistrer_filiale("A", Decimal(1000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales retirer").callback(interaction, filiale="a")

    assert await bot.store.filiales() == []
    assert "✅" in _texte(interaction)


async def test_retirer_se_complete_avec_les_filiales_connues():
    """Retaper le nom pour supprimer, c'est risquer de ne rien supprimer."""
    bot = await _bot()
    await bot.store.enregistrer_filiale("MARINE", Decimal(1000), "2026-08-11")

    completer = _autocomplete(_commande(bot, "filiales retirer"), "filiale")

    assert [c.value for c in await completer(InteractionFactice(), "")] == ["MARINE"]


async def test_retirer_une_filiale_inconnue_liste_les_connues():
    """Sinon on ne sait pas si c'est une faute de frappe ou une filiale jamais
    saisie."""
    bot = await _bot()
    await bot.store.enregistrer_filiale("ARMEE", Decimal(1000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales retirer").callback(interaction, filiale="MARINE")

    texte = _texte(interaction)
    assert "❌" in texte
    assert "ARMEE" in texte


# --- /filiales heure --------------------------------------------------------


async def test_heure_regle_l_heure_du_tableau():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "filiales heure").callback(interaction, heure="20:30")

    assert await bot.store.heure_filiales() == "20:30"
    assert "20:30" in _texte(interaction)


async def test_heure_invalide_est_refusee():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "filiales heure").callback(interaction, heure="25:00")

    assert "❌" in _texte(interaction)
    assert await bot.store.heure_filiales() == "09:00"


async def test_heure_ne_touche_pas_a_celle_des_promotions():
    """Deux posts, deux horaires : régler l'un en déplaçant l'autre serait une
    surprise découverte le lendemain matin."""
    bot = await _bot()

    await _commande(bot, "filiales heure").callback(InteractionFactice(), heure="20:30")

    assert (await bot.store.config())["heure"] == "09:00"


async def test_regler_l_heure_oublie_la_marque_du_jour():
    """Régler l'heure exprime l'intention de publier à la nouvelle heure : un
    post déjà sorti bloquerait sinon ce nouvel horaire jusqu'à demain."""
    bot = await _bot()
    await bot.store.marquer_publie_filiales("2026-08-11")

    await _commande(bot, "filiales heure").callback(InteractionFactice(), heure="20:30")

    assert await bot.store.derniere_publication_filiales() is None


# --- /filiales salon --------------------------------------------------------


async def test_salon_ajouter_enregistre_le_salon():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "filiales salon ajouter").callback(
        interaction, salon=SalonFactice(123)
    )

    assert await bot.store.salons_filiales() == ["123"]
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
    assert await bot.store.salons_filiales() == []


async def test_salon_ajouter_deux_fois_le_dit():
    bot = await _bot()
    salon = SalonFactice(123)
    await _commande(bot, "filiales salon ajouter").callback(
        InteractionFactice(), salon=salon
    )
    interaction = InteractionFactice()

    await _commande(bot, "filiales salon ajouter").callback(interaction, salon=salon)

    assert "déjà" in _texte(interaction)
    assert await bot.store.salons_filiales() == ["123"]


async def test_salon_retirer_retire_le_salon():
    bot = await _bot()
    await bot.store.ajouter_salon_filiales("123")
    interaction = InteractionFactice()

    await _commande(bot, "filiales salon retirer").callback(
        interaction, salon=SalonFactice(123)
    )

    assert await bot.store.salons_filiales() == []


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

    assert await bot.store.salons() == []


# --- /filiales apercu ------------------------------------------------------


async def test_apercu_montre_le_tableau_sans_publier():
    bot = await _bot()
    await bot.store.enregistrer_filiale("A", Decimal(1000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales apercu").callback(interaction)

    assert "70 Ø" in _texte(interaction)
    # Rien n'a été marqué : le post du jour doit encore pouvoir sortir.
    assert await bot.store.derniere_publication_filiales() is None


async def test_apercu_reste_prive():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "filiales apercu").callback(interaction)

    messages = [*interaction.response.messages, *interaction.followup.messages]
    assert messages and all(m.get("ephemeral") for m in messages)


# --- /filiales remise-a-zero ------------------------------------------------


async def test_remise_a_zero_annule_les_montants_et_garde_les_noms():
    """Un nouveau cycle ne change que les montants : reperdre les noms
    obligerait à retaper les clés d'import du jeu."""
    bot = await _bot()
    await bot.store.enregistrer_filiale("A", Decimal(1000), "2026-08-11")
    await bot.store.enregistrer_filiale("B", Decimal(2000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales remise-a-zero").callback(interaction, confirmer=True)

    filiales = await bot.store.filiales()
    assert [f.nom for f in filiales] == ["A", "B"]
    assert all(f.benefices == Decimal(0) for f in filiales)


async def test_remise_a_zero_sans_confirmer_ne_touche_a_rien():
    """Effacer tous les relevés du jour par mégarde coûterait une ressaisie
    complète : la case doit être cochée pour que ça parte."""
    bot = await _bot()
    await bot.store.enregistrer_filiale("A", Decimal(1000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales remise-a-zero").callback(interaction, confirmer=False)

    assert (await bot.store.filiales())[0].benefices == Decimal(1000)
    texte = _texte(interaction)
    assert "❌" in texte
    # Dit combien de relevés étaient en jeu : c'est ce qui fait mesurer le geste.
    assert "1" in texte


async def test_remise_a_zero_sans_filiale_le_dit():
    """Annoncer une remise faite sur une liste vide laisserait croire qu'il y
    avait quelque chose à remettre."""
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "filiales remise-a-zero").callback(interaction, confirmer=True)

    assert "aucune" in _texte(interaction).lower()


async def test_remise_a_zero_confirme_combien_de_filiales():
    bot = await _bot()
    await bot.store.enregistrer_filiale("A", Decimal(1000), "2026-08-11")
    await bot.store.enregistrer_filiale("B", Decimal(2000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales remise-a-zero").callback(interaction, confirmer=True)

    texte = _texte(interaction)
    assert "✅" in texte
    assert "2" in texte


async def test_remise_a_zero_reste_privee():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "filiales remise-a-zero").callback(interaction, confirmer=True)

    assert all(m.get("ephemeral") for m in interaction.response.messages)


# --- /filiales retirer-plusieurs --------------------------------------------


async def test_retirer_plusieurs_supprime_tout_le_lot():
    bot = await _bot()
    await bot.store.enregistrer_filiale("A", Decimal(1000), "2026-08-11")
    await bot.store.enregistrer_filiale("B", Decimal(2000), "2026-08-11")
    await bot.store.enregistrer_filiale("C", Decimal(3000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales retirer-plusieurs").callback(
        interaction, filiales="a, C", confirmer=True
    )

    assert [f.nom for f in await bot.store.filiales()] == ["B"]


async def test_retirer_plusieurs_sans_confirmer_ne_touche_a_rien():
    bot = await _bot()
    await bot.store.enregistrer_filiale("A", Decimal(1000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales retirer-plusieurs").callback(
        interaction, filiales="A", confirmer=False
    )

    assert len(await bot.store.filiales()) == 1
    assert "❌" in _texte(interaction)


async def test_retirer_plusieurs_dit_les_noms_inconnus():
    """Sans ça, on croirait une filiale supprimée alors qu'elle reste dans le
    tableau du soir."""
    bot = await _bot()
    await bot.store.enregistrer_filiale("A", Decimal(1000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales retirer-plusieurs").callback(
        interaction, filiales="A, MARINE", confirmer=True
    )

    texte = _texte(interaction)
    assert "MARINE" in texte
    # Et le retrait valide du même lot a bien eu lieu.
    assert await bot.store.filiales() == []


async def test_retirer_plusieurs_accepte_le_tout():
    """Vider le tableau d'un geste est le cas qui a motivé la commande."""
    bot = await _bot()
    for nom in ("A", "B", "C"):
        await bot.store.enregistrer_filiale(nom, Decimal(1000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales retirer-plusieurs").callback(
        interaction, filiales="tout", confirmer=True
    )

    assert await bot.store.filiales() == []
    assert "3" in _texte(interaction)


async def test_retirer_plusieurs_sans_nom_ne_vide_pas_le_tableau():
    """Une saisie vide est un accident : l'interpréter comme « tout » serait la
    pire lecture possible."""
    bot = await _bot()
    await bot.store.enregistrer_filiale("A", Decimal(1000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales retirer-plusieurs").callback(
        interaction, filiales="  ", confirmer=True
    )

    assert len(await bot.store.filiales()) == 1
    assert "❌" in _texte(interaction)


async def test_retirer_plusieurs_garde_les_doubles_espaces_du_nom():
    """La clé d'import du jeu passe par le découpage sans être normalisée."""
    bot = await _bot()
    await bot.store.enregistrer_filiale("ARMEE  DE TERRE", Decimal(1000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales retirer-plusieurs").callback(
        interaction, filiales="ARMEE  DE TERRE", confirmer=True
    )

    assert await bot.store.filiales() == []


async def test_retirer_plusieurs_se_complete_avec_les_filiales_connues():
    """Un lot se construit un nom à la fois : sans complétion, chaque nom est
    une occasion de faute de frappe."""
    bot = await _bot()
    await bot.store.enregistrer_filiale("MARINE", Decimal(1000), "2026-08-11")

    completer = _autocomplete(_commande(bot, "filiales retirer-plusieurs"), "filiales")

    assert [c.value for c in await completer(InteractionFactice(), "")] == ["MARINE"]


# --- /filiales test ---------------------------------------------------------


async def test_test_remplace_les_montants_par_des_chiffres_au_hasard():
    """Ce qu'on veut voir, c'est le tableau avec des montants d'ordres de
    grandeur variés — pas des filiales inventées à retirer ensuite."""
    bot = await _bot()
    for nom in ("A", "B", "C"):
        await bot.store.enregistrer_filiale(nom, Decimal(1000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales test").callback(interaction, confirmer=True)

    filiales = await bot.store.filiales()
    assert [f.nom for f in filiales] == ["A", "B", "C"]
    assert len({f.benefices for f in filiales}) > 1


async def test_test_sans_confirmer_ne_touche_a_rien():
    """La commande écrit dans la base **courante**, production comprise : elle
    écrase de vrais relevés, donc elle se confirme."""
    bot = await _bot()
    await bot.store.enregistrer_filiale("A", Decimal(1000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales test").callback(interaction, confirmer=False)

    assert (await bot.store.filiales())[0].benefices == Decimal(1000)
    assert "❌" in _texte(interaction)


async def test_test_previent_que_les_vrais_releves_sont_ecrases():
    """Le mot « test » laisserait croire à un bac à sable ; il n'y en a pas."""
    bot = await _bot()
    await bot.store.enregistrer_filiale("A", Decimal(1000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales test").callback(interaction, confirmer=False)

    assert "écras" in _texte(interaction).lower()


async def test_test_sans_filiale_dit_qu_il_n_y_a_rien_a_tirer():
    """Le tirage porte sur les filiales enregistrées : sans aucune, il ne se
    passe rien, et le silence se lirait comme une panne."""
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "filiales test").callback(interaction, confirmer=True)

    texte = _texte(interaction).lower()
    assert "aucune" in texte
    assert "/frais" in texte


async def test_test_montre_le_tableau_obtenu():
    """Sinon il faudrait enchaîner sur `/filiales liste` pour voir le résultat
    de l'essai qu'on vient de demander."""
    bot = await _bot()
    await bot.store.enregistrer_filiale("A", Decimal(1000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales test").callback(interaction, confirmer=True)

    assert interaction.embeds, "aucun tableau montré"
    assert "A" in _texte(interaction)


async def test_test_rappelle_comment_revenir_a_un_tableau_propre():
    """Des chiffres au hasard laissés en base seraient publiés le soir : la
    sortie doit être dite dans le message même."""
    bot = await _bot()
    await bot.store.enregistrer_filiale("A", Decimal(1000), "2026-08-11")
    interaction = InteractionFactice()

    await _commande(bot, "filiales test").callback(interaction, confirmer=True)

    assert "remise-a-zero" in _texte(interaction)


async def test_test_reste_prive():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "filiales test").callback(interaction, confirmer=True)

    assert all(m.get("ephemeral") for m in interaction.response.messages)
