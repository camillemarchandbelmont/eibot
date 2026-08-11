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

    assert "2026-08-09" in lignes[0]


def test_une_filiale_saisie_aujourd_hui_n_est_pas_datee():
    """La date de toutes les lignes serait du bruit : elle ne sert qu'à repérer
    celles qui n'ont pas été remises à jour."""
    lignes = lignes_tableau(
        [_filiale("FRAICHE", "1000", date="2026-08-11")], aujourdhui="2026-08-11"
    )

    assert "2026-08-11" not in lignes[0]


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
    embed = embed_filiales([_filiale("A", "1000")], "2026-08-11")

    assert "2026-08-11" in _tout_le_texte(embed)


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


def _tout_le_texte(embed) -> str:
    """Tout le texte d'un `discord.Embed`, quelle que soit sa structure."""
    parties = [embed.title or "", embed.description or ""]
    for champ in embed.fields:
        parties += [champ.name or "", champ.value or ""]
    if embed.footer:
        parties.append(embed.footer.text or "")
    return "\n".join(parties)
