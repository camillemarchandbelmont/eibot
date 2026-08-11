"""L'embed du tableau des frais.

Ce qui compte ici est ce qu'on lit dans Discord : le total à payer, chaque
filiale avec ses frais, et les montants sous une forme **recopiable dans le
jeu** — on ne paie pas « 189.70 TØ ».

Une filiale en perte doit être visible comme telle : la voir à 0 Ø sans
explication laisserait croire à un oubli de saisie.
"""

from decimal import Decimal

from src.filiales import calculer
from src.publish_filiales import (
    LIMITE_LIGNES,
    date_courte,
    date_longue,
    embed_filiales,
    lignes_tableau,
)


def _filiale(nom: str, benefices: str, date: str = "2026-08-11"):
    return calculer(nom, Decimal(benefices), date)


# --- lignes_tableau ---------------------------------------------------------


def test_chaque_filiale_donne_une_ligne_avec_ses_frais():
    lignes = lignes_tableau([_filiale("ARMEE", "1000"), _filiale("MARINE", "2000")])

    assert len(lignes) == 2
    # Classées par frais décroissants : MARINE (140 Ø) avant ARMEE (70 Ø).
    assert "MARINE" in lignes[0]
    assert "140 Ø" in lignes[0].replace(" ", " ")
    assert "ARMEE" in lignes[1]
    assert "70 Ø" in lignes[1].replace(" ", " ")


def test_les_filiales_sont_classees_par_frais_decroissants():
    """Le plus gros poste en premier : c'est celui qu'on regarde."""
    lignes = lignes_tableau(
        [_filiale("PETITE", "1000"), _filiale("GROSSE", "1000000")]
    )

    assert "GROSSE" in lignes[0]
    assert "PETITE" in lignes[1]


def test_une_filiale_en_perte_est_signalee():
    """À 0 Ø sans marque, on croirait à une saisie oubliée.

    La filiale ne s'appelle **pas** « EN PERTE » : le mot viendrait alors du nom
    et le test passerait même sans aucun marquage.
    """
    lignes = lignes_tableau([_filiale("ARMEE", "-500")])

    assert "ARMEE" in lignes[0]
    assert "perte" in lignes[0].lower()


def test_une_filiale_en_perte_n_affiche_pas_de_montant_a_payer():
    """« 0 Ø » à côté d'un nom se lit comme une somme due de zéro, ce qui est
    vrai, mais laisse croire à une saisie ratée plutôt qu'à une perte."""
    lignes = lignes_tableau([_filiale("ARMEE", "-500")])

    assert "0 Ø" not in lignes[0].replace(" ", " ")


def test_chaque_ligne_donne_les_frais_en_entier_pour_les_recopier():
    """La notation courte ne suffit pas pour payer : « 189.74 TØ » n'est pas un
    montant qu'on saisit dans le jeu. Le total seul ne suffirait pas — on paie
    filiale par filiale."""
    lignes = lignes_tableau([_filiale("ARMEE", "2710572934559948")])

    ligne = lignes[0].replace(" ", " ")
    assert "189.74 TØ" in ligne
    assert "189 740 105 419 196" in ligne


def test_une_filiale_dont_la_saisie_date_d_un_autre_jour_est_datee():
    """Sans ça, un relevé d'avant-hier se lirait comme celui du jour."""
    lignes = lignes_tableau(
        [_filiale("VIEILLE", "1000", date="2026-08-09")], aujourdhui="2026-08-11"
    )

    assert "9 août" in lignes[0]


def test_une_filiale_saisie_aujourd_hui_n_est_pas_datee():
    """La date de toutes les lignes serait du bruit : elle ne sert qu'à repérer
    celles qui n'ont pas été remises à jour."""
    lignes = lignes_tableau(
        [_filiale("FRAICHE", "1000", date="2026-08-11")], aujourdhui="2026-08-11"
    )

    assert "11 août" not in lignes[0]


def test_le_nom_garde_ses_doubles_espaces():
    """C'est la clé d'import du jeu ; Discord les afficherait autrement mais la
    ligne doit les porter."""
    lignes = lignes_tableau([_filiale("ARMEE  DE TERRE", "1000")])

    assert "ARMEE  DE TERRE" in lignes[0]


# --- embed_filiales ---------------------------------------------------------


def test_l_embed_porte_le_total_a_payer():
    embed = embed_filiales(
        [_filiale("A", "1000"), _filiale("B", "2000")], "2026-08-11"
    )

    texte = _tout_le_texte(embed)
    assert "210 Ø" in texte.replace(" ", " ")


def test_le_total_est_donne_en_entier_pour_le_recopier():
    """La notation courte ne suffit pas pour payer : « 189.70 TØ » n'est pas un
    montant qu'on saisit dans le jeu."""
    embed = embed_filiales([_filiale("A", "2710572934559948")], "2026-08-11")

    texte = _tout_le_texte(embed).replace(" ", " ")
    assert "189 740 105 419 196" in texte


def test_l_embed_compte_les_filiales():
    embed = embed_filiales(
        [_filiale("A", "1000"), _filiale("B", "2000")], "2026-08-11"
    )

    assert "2" in _tout_le_texte(embed)


def test_l_embed_porte_la_date_du_jour():
    """En français : le post est lu par un humain, pas relu par une machine."""
    embed = embed_filiales([_filiale("A", "1000")], "2026-08-11")

    assert "11 août 2026" in _tout_le_texte(embed)


def test_l_embed_sans_filiale_le_dit_au_lieu_d_etre_vide():
    """Un embed vide se lirait comme une panne du bot."""
    embed = embed_filiales([], "2026-08-11")

    texte = _tout_le_texte(embed).lower()
    assert "aucune" in texte
    # La commande qui y remédie, sinon le message est un cul-de-sac.
    assert "/frais" in texte


def test_l_embed_reste_sous_la_limite_de_discord():
    """4096 caractères par description : 200 filiales aux noms longs doivent
    passer, sinon le post échouerait un jour sans prévenir."""
    filiales = [_filiale(f"FILIALE NUMERO {i:03d} DU MONDE M8", "1000") for i in range(200)]

    embed = embed_filiales(filiales, "2026-08-11")

    assert len(_tout_le_texte(embed)) <= 4096


def test_l_embed_tient_aussi_avec_des_noms_longs_et_des_releves_perimes():
    """Le pire cas réel, et celui qu'un simple plafond de lignes laisse passer.

    Un nom de filiale est libre, un montant monte à 21 chiffres et un relevé
    oublié ajoute sa date : quarante lignes de ce gabarit dépassent les 4096
    caractères, et Discord refuse alors le tableau **en entier** — on ne perdrait
    pas quelques lignes, on perdrait le post.
    """
    filiales = [
        _filiale(
            f"CONSORTIUM INTERNATIONAL DES CHANTIERS NAVALS {i:03d}",
            "173019538387120000000",
            date="2026-08-09",
        )
        for i in range(200)
    ]

    embed = embed_filiales(filiales, "2026-08-11")

    assert len(embed.description) <= 4096


def test_la_description_est_mesuree_en_unites_utf16_comme_chez_discord():
    """Discord compte en UTF-16 : un emoji hors du BMP y pèse deux unités, là où
    `len()` de Python n'en voit qu'une. Mesurer comme Python laisserait passer
    un embed que l'API refuse."""
    filiales = [
        _filiale(f"FILIALE {i:03d} AUX CHANTIERS NAVALS REUNIS", "173019538387120000000")
        for i in range(200)
    ]

    description = embed_filiales(filiales, "2026-08-11").description

    assert len(description.encode("utf-16-le")) // 2 <= 4096


def test_les_filiales_en_trop_sont_comptees_et_non_tues():
    """Tronquer en silence ferait croire que le total ne porte que sur ce qui
    est affiché."""
    filiales = [_filiale(f"F{i:03d}", "1000") for i in range(LIMITE_LIGNES + 5)]

    texte = _tout_le_texte(embed_filiales(filiales, "2026-08-11"))
    assert "+5" in texte


def test_le_total_inclut_les_filiales_non_affichees():
    """C'est ce qu'on paie, pas ce qui tient dans l'embed."""
    filiales = [_filiale(f"F{i:03d}", "1000") for i in range(LIMITE_LIGNES + 5)]

    texte = _tout_le_texte(embed_filiales(filiales, "2026-08-11")).replace(" ", " ")
    attendu = 70 * (LIMITE_LIGNES + 5)
    assert f"{attendu}" in texte.replace(" ", "")


# --- les dates en français --------------------------------------------------


def test_la_date_longue_est_en_francais():
    """`strftime('%A')` rendrait « Tuesday » : la locale du serveur Render n'est
    pas la nôtre et ne doit pas décider de la langue du post."""
    assert date_longue("2026-08-11") == "mardi 11 août 2026"


def test_la_date_longue_ne_met_pas_de_zero_devant_le_jour():
    """« 09 août » se lit comme une référence, pas comme une date."""
    assert date_longue("2026-08-09") == "dimanche 9 août 2026"


def test_la_date_courte_omet_l_annee_et_le_jour_de_la_semaine():
    """Elle sert à dater un relevé oublié, en bout de ligne : le jour de la
    semaine et l'année y seraient du bruit."""
    assert date_courte("2026-08-09") == "9 août"


def test_une_date_illisible_est_rendue_telle_quelle():
    """Un relevé d'une version antérieure ne doit pas faire échouer le post
    entier : mieux vaut une date brute qu'aucun tableau."""
    assert date_longue("n'importe quoi") == "n'importe quoi"
    assert date_courte("") == ""


# --- les emojis portent un état --------------------------------------------


def test_une_filiale_en_perte_et_une_filiale_payante_ne_portent_pas_le_meme_emoji():
    """L'emoji doit dire l'état, sinon il ne fait que décorer.

    On compare les deux lignes plutôt que de chercher un emoji précis : le test
    resterait vrai si l'on changeait de pictogramme, et faux dès qu'ils
    cessent de se distinguer.
    """
    perte = lignes_tableau([_filiale("A", "-500")])[0]
    payante = lignes_tableau([_filiale("A", "1000")])[0]

    assert _emojis(perte), "la ligne en perte ne porte aucun emoji"
    assert _emojis(payante), "la ligne payante ne porte aucun emoji"
    assert _emojis(perte) != _emojis(payante)


def test_la_filiale_la_plus_lourde_se_distingue_des_suivantes():
    """Le poste principal est celui qu'on regarde ; il doit sauter aux yeux
    sans avoir à comparer les montants soi-même."""
    lignes = lignes_tableau(
        [_filiale("GROSSE", "1000000"), _filiale("MOYENNE", "1000")]
    )

    assert _emojis(lignes[0]) != _emojis(lignes[1])


def test_une_filiale_datee_d_un_autre_jour_porte_un_emoji_d_alerte():
    """La date seule en bout de ligne se remarque mal dans une liste de vingt."""
    vieille = lignes_tableau([_filiale("A", "1000", date="2026-08-09")], aujourdhui="2026-08-11")[0]
    fraiche = lignes_tableau([_filiale("A", "1000", date="2026-08-11")], aujourdhui="2026-08-11")[0]

    assert _emojis(vieille) != _emojis(fraiche)


def test_le_total_de_l_embed_porte_un_emoji():
    embed = embed_filiales([_filiale("A", "1000")], "2026-08-11")

    total = [c for c in embed.fields if "total" in (c.name or "").lower()]
    assert total, "aucun champ de total"
    assert _emojis(total[0].name)


def test_l_embed_vide_ne_se_deguise_pas_en_tableau_rempli():
    """Son emoji doit dire « rien à afficher », pas « voici tes frais »."""
    vide = embed_filiales([], "2026-08-11")
    rempli = embed_filiales([_filiale("A", "1000")], "2026-08-11")

    assert _emojis(vide.description) != _emojis(rempli.description or "")


# --- le montant recopiable --------------------------------------------------


def test_le_montant_a_recopier_est_en_code_pour_etre_copie_d_un_geste():
    """Dans Discord, un appui long sur du `code inline` le copie seul ; noyé
    dans une phrase, il faudrait sélectionner à la main 21 chiffres."""
    ligne = lignes_tableau([_filiale("A", "2710572934559948")])[0]

    assert "`189 740 105 419 196 Ø`" in ligne.replace(" ", " ")


def test_le_total_a_recopier_est_aussi_en_code():
    embed = embed_filiales([_filiale("A", "2710572934559948")], "2026-08-11")

    texte = _tout_le_texte(embed).replace(" ", " ")
    assert "`189 740 105 419 196 Ø`" in texte


def test_l_embed_porte_la_date_en_francais():
    """C'est ce qu'on lit dans le post ; « 2026-08-11 » est une clé, pas une
    date pour un humain."""
    texte = _tout_le_texte(embed_filiales([_filiale("A", "1000")], "2026-08-11"))

    assert "mardi 11 août 2026" in texte


def _emojis(texte: str) -> str:
    """Les pictogrammes d'une ligne, à l'exclusion de la ponctuation typographique.

    Les traits de séparation (`━`, `─`) sont écartés : ils vivent au-dessus de
    0x2000 comme les emojis, et sans ce filtre deux lignes se distingueraient
    par un décor commun plutôt que par un pictogramme d'état.
    """
    decor = "━─–—…‰·"
    return "".join(
        c
        for c in texte
        if ord(c) > 0x2000 and c not in decor and not c.isspace()
    )


def _tout_le_texte(embed) -> str:
    """Tout le texte d'un `discord.Embed`, quelle que soit sa structure."""
    parties = [embed.title or "", embed.description or ""]
    for champ in embed.fields:
        parties += [champ.name or "", champ.value or ""]
    if embed.footer:
        parties.append(embed.footer.text or "")
    return "\n".join(parties)
