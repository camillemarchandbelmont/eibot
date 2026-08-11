"""Cœur des frais par filiale : calcul, enregistrement, total.

Ce module ne connaît ni Discord ni Postgres. Ce qui se vérifie ici, c'est la
règle du jeu — les frais valent 7 % des bénéfices, une filiale en perte ne paie
rien — et le fait qu'une filiale ressaisie **remplace** la précédente au lieu
d'en créer une seconde.

Le nom est la clé d'import du jeu : il doit ressortir caractère pour caractère,
doubles espaces compris.
"""

from decimal import Decimal
from random import Random

import pytest

from src.money import format_money
from src.filiales import (
    FIN_DE_LIGNE_IMPORT,
    SEPARATEUR_IMPORT,
    Filiale,
    FilialeError,
    benefices_aleatoires,
    calculer,
    depuis_json,
    enregistrer,
    index_de,
    nom_pour_import,
    noms_separes,
    remettre_a_zero,
    retirer_plusieurs,
    total_frais,
    valeurs_aleatoires,
    vers_import,
    vers_json,
)


# --- calculer ---------------------------------------------------------------


def test_les_frais_valent_sept_pour_cent_des_benefices():
    filiale = calculer("ARMEE DE TERRE", Decimal("2710572934559948"), "2026-08-11")

    # La même valeur que `/frais` sur ce montant : la règle n'est pas recopiée.
    assert filiale.frais == Decimal("189740105419196")


def test_les_frais_sont_arrondis_au_plus_proche_sans_decimales():
    """Le jeu ne facture pas de fraction d'Ø, et arrondit au plus proche.

    1 007 Ø × 7 % = 70,49 → 70. 1 008 Ø × 7 % = 70,56 → 71.
    """
    assert calculer("A", Decimal(1007), "2026-08-11").frais == Decimal(70)
    assert calculer("A", Decimal(1008), "2026-08-11").frais == Decimal(71)


def test_une_filiale_en_perte_ne_paie_rien():
    """Le jeu ne rend pas d'argent : des frais négatifs seraient inventés."""
    filiale = calculer("EN PERTE", Decimal(-5000), "2026-08-11")

    assert filiale.frais == Decimal(0)
    assert filiale.en_perte


def test_des_benefices_nuls_comptent_comme_une_perte():
    """Zéro n'est pas un bénéfice : rien à prélever, et la filiale doit être
    signalée comme les autres qui ne rapportent rien."""
    filiale = calculer("A ZERO", Decimal(0), "2026-08-11")

    assert filiale.frais == Decimal(0)
    assert filiale.en_perte


def test_une_filiale_qui_gagne_n_est_pas_en_perte():
    assert not calculer("A", Decimal(1), "2026-08-11").en_perte


def test_le_nom_garde_ses_doubles_espaces():
    """C'est la clé d'import du jeu : « ARMEE  DE TERRE » n'est pas
    « ARMEE DE TERRE », et une normalisation silencieuse le rendrait
    introuvable."""
    filiale = calculer("  ARMEE  DE TERRE  ", Decimal(100), "2026-08-11")

    assert filiale.nom == "ARMEE  DE TERRE"


def test_un_nom_vide_est_refuse():
    """Sans nom, la ligne serait anonyme dans le tableau et impossible à
    remplacer par une ressaisie."""
    with pytest.raises(FilialeError):
        calculer("   ", Decimal(100), "2026-08-11")


def test_la_date_de_saisie_est_conservee():
    """Elle rend visible une filiale qu'on a oublié de mettre à jour."""
    assert calculer("A", Decimal(100), "2026-08-09").date == "2026-08-09"


# --- enregistrer ------------------------------------------------------------


def test_une_filiale_inconnue_est_ajoutee_a_la_fin():
    liste = enregistrer([], calculer("A", Decimal(100), "2026-08-11"))
    liste = enregistrer(liste, calculer("B", Decimal(200), "2026-08-11"))

    assert [f.nom for f in liste] == ["A", "B"]


def test_une_ressaisie_remplace_la_precedente():
    """Sinon le tableau afficherait deux lignes pour la même filiale, et le
    total serait compté deux fois."""
    liste = enregistrer([], calculer("A", Decimal(100), "2026-08-09"))
    liste = enregistrer(liste, calculer("A", Decimal(200), "2026-08-11"))

    assert len(liste) == 1
    assert liste[0].benefices == Decimal(200)
    assert liste[0].date == "2026-08-11"


def test_la_ressaisie_garde_la_place_de_la_filiale():
    """Remonter en fin de liste ferait danser le tableau d'un jour à l'autre."""
    liste = [
        calculer("A", Decimal(1), "2026-08-11"),
        calculer("B", Decimal(2), "2026-08-11"),
        calculer("C", Decimal(3), "2026-08-11"),
    ]
    liste = enregistrer(liste, calculer("B", Decimal(9), "2026-08-11"))

    assert [f.nom for f in liste] == ["A", "B", "C"]
    assert liste[1].benefices == Decimal(9)


def test_la_ressaisie_ignore_la_casse_et_garde_le_nom_saisi():
    """« armee » et « ARMEE » sont la même filiale du jeu ; deux lignes
    indistinguables à l'œil seraient pires qu'un remplacement."""
    liste = enregistrer([], calculer("ARMEE", Decimal(100), "2026-08-09"))
    liste = enregistrer(liste, calculer("armee", Decimal(200), "2026-08-11"))

    assert len(liste) == 1
    assert liste[0].nom == "armee"


def test_enregistrer_ne_modifie_pas_la_liste_donnee():
    """La liste vient de la base : la muter en place ferait divergier ce qui est
    affiché de ce qui est enregistré si l'écriture échoue ensuite."""
    origine = [calculer("A", Decimal(1), "2026-08-11")]

    enregistrer(origine, calculer("B", Decimal(2), "2026-08-11"))

    assert [f.nom for f in origine] == ["A"]


# --- index_de ---------------------------------------------------------------


def test_index_de_trouve_la_filiale_sans_egard_a_la_casse():
    liste = [calculer("ARMEE  DE TERRE", Decimal(1), "2026-08-11")]

    assert index_de(liste, "armee  de terre") == 0
    assert index_de(liste, "  ARMEE  DE TERRE ") == 0


def test_index_de_renvoie_moins_un_quand_la_filiale_est_absente():
    assert index_de([], "A") == -1


def test_index_de_ne_confond_pas_les_espaces_internes():
    """Un espace en moins, c'est une autre filiale pour le jeu."""
    liste = [calculer("ARMEE  DE TERRE", Decimal(1), "2026-08-11")]

    assert index_de(liste, "ARMEE DE TERRE") == -1


# --- total_frais ------------------------------------------------------------


def test_le_total_additionne_les_frais():
    liste = [
        calculer("A", Decimal(1000), "2026-08-11"),  # 70
        calculer("B", Decimal(2000), "2026-08-11"),  # 140
    ]

    assert total_frais(liste) == Decimal(210)


def test_le_total_d_une_liste_vide_vaut_zero():
    assert total_frais([]) == Decimal(0)


def test_le_total_ne_perd_pas_de_precision():
    """Vingt-un chiffres : les bénéfices du Mégapôle, où un `float` casse.

    Dix-sept chiffres ne suffisaient pas à ce test : ils tiennent encore dans la
    mantisse d'un `float64`, donc une somme en flottants y donnait le bon
    résultat et le test passait quand même.
    """
    liste = [calculer("A", Decimal("173019538387120000000"), "2026-08-11")] * 3

    assert total_frais(liste) == Decimal("36334103061295200000")


# --- JSON -------------------------------------------------------------------


def test_l_aller_retour_json_conserve_tout():
    liste = [
        calculer("ARMEE  DE TERRE", Decimal("2710572934559948"), "2026-08-11"),
        calculer("EN PERTE", Decimal(-1), "2026-08-09"),
    ]

    relues = depuis_json(vers_json(liste))

    assert relues == liste


def test_les_montants_sont_stockes_en_chaine():
    """JSONB n'a pas d'entier de 17 chiffres : un nombre JSON passerait par un
    flottant et perdrait les derniers."""
    json = vers_json([calculer("A", Decimal("2710572934559948"), "2026-08-11")])

    assert json[0]["benefices"] == "2710572934559948"
    assert isinstance(json[0]["frais"], str)


def test_depuis_json_ignore_une_ligne_illisible():
    """La base est du JSON retouchable à la main : une ligne cassée doit coûter
    sa filiale, pas le tableau du jour."""
    liste = depuis_json(
        [
            {"nom": "A", "benefices": "100", "frais": "7", "date": "2026-08-11"},
            {"nom": "B", "benefices": "pouet", "frais": "7", "date": "2026-08-11"},
            "pas un objet",
            {"nom": "", "benefices": "100", "frais": "7", "date": "2026-08-11"},
        ]
    )

    assert [f.nom for f in liste] == ["A"]


def test_depuis_json_recalcule_les_frais_incoherents():
    """Les frais enregistrés viennent du calcul ; s'ils ne collent plus aux
    bénéfices (retouche à la main, taux changé), le calcul fait foi — sinon le
    tableau afficherait un montant que le jeu ne réclamera pas."""
    liste = depuis_json(
        [{"nom": "A", "benefices": "1000", "frais": "999", "date": "2026-08-11"}]
    )

    assert liste[0].frais == Decimal(70)


def test_depuis_json_accepte_une_liste_absente():
    assert depuis_json(None) == []


# --- remettre_a_zero --------------------------------------------------------


def test_la_remise_a_zero_garde_les_noms():
    """Les noms sont la clé d'import du jeu : les perdre obligerait à les
    retaper, alors qu'un nouveau cycle ne change que les montants."""
    liste = [_f("ARMEE", "1000"), _f("MARINE", "2000")]

    remise = remettre_a_zero(liste, "2026-08-12")

    assert [f.nom for f in remise] == ["ARMEE", "MARINE"]


def test_la_remise_a_zero_annule_benefices_et_frais():
    liste = [_f("ARMEE", "1000")]

    remise = remettre_a_zero(liste, "2026-08-12")

    assert remise[0].benefices == Decimal(0)
    assert remise[0].frais == Decimal(0)


def test_une_filiale_remise_a_zero_compte_comme_en_perte():
    """C'est ce qui la fait marquer dans le tableau : à 0 Ø sans marque, on
    croirait à une saisie oubliée plutôt qu'à un cycle qui commence."""
    remise = remettre_a_zero([_f("ARMEE", "1000")], "2026-08-12")

    assert remise[0].en_perte


def test_la_remise_a_zero_date_du_jour_de_la_remise():
    """Et non de la saisie d'avant : le relevé remis à zéro est un fait
    nouveau, et garder l'ancienne date le ferait passer pour périmé le jour
    même où on vient de le remettre."""
    remise = remettre_a_zero([_f("ARMEE", "1000", date="2026-08-01")], "2026-08-12")

    assert remise[0].date == "2026-08-12"


def test_la_remise_a_zero_conserve_l_ordre():
    """Le tableau se classe par frais, mais la liste garde l'ordre de première
    saisie : le mélanger ferait danser l'autocomplétion."""
    liste = [_f("A", "1000"), _f("B", "2000"), _f("C", "3000")]

    assert [f.nom for f in remettre_a_zero(liste, "2026-08-12")] == ["A", "B", "C"]


def test_la_remise_a_zero_ne_touche_pas_la_liste_d_origine():
    """La liste vient de la base : la muter ferait divergier l'affiché de
    l'enregistré si l'écriture échouait ensuite."""
    liste = [_f("ARMEE", "1000")]

    remettre_a_zero(liste, "2026-08-12")

    assert liste[0].benefices == Decimal(1000)


def test_la_remise_a_zero_d_une_liste_vide_ne_leve_pas():
    assert remettre_a_zero([], "2026-08-12") == []


def test_le_total_apres_remise_a_zero_est_nul():
    liste = [_f("A", "1000"), _f("B", "2710572934559948")]

    assert total_frais(remettre_a_zero(liste, "2026-08-12")) == Decimal(0)


# --- retirer_plusieurs ------------------------------------------------------


def test_retirer_plusieurs_enleve_toutes_les_filiales_nommees():
    liste = [_f("A", "1000"), _f("B", "2000"), _f("C", "3000")]

    restantes = retirer_plusieurs(liste, ["A", "C"])

    assert [f.nom for f in restantes] == ["B"]


def test_retirer_plusieurs_ignore_la_casse_comme_le_retrait_simple():
    """Deux lignes que rien ne distingue à l'œil seraient pires qu'un retrait
    qu'on croyait avoir fait."""
    restantes = retirer_plusieurs([_f("ARMEE", "1000")], ["armee"])

    assert restantes == []


def test_retirer_plusieurs_ignore_les_espaces_de_bordure():
    """Les noms arrivent d'un copier-coller ou d'une liste séparée par des
    virgules, où les espaces autour sont invisibles."""
    restantes = retirer_plusieurs([_f("ARMEE", "1000")], ["  ARMEE  "])

    assert restantes == []


def test_retirer_plusieurs_garde_les_espaces_internes_significatifs():
    """`ARMEE DE TERRE` et `ARMEE  DE TERRE` sont deux clés d'import
    différentes : le jeu compte les doubles espaces."""
    restantes = retirer_plusieurs([_f("ARMEE  DE TERRE", "1000")], ["ARMEE DE TERRE"])

    assert [f.nom for f in restantes] == ["ARMEE  DE TERRE"]


def test_retirer_plusieurs_ignore_un_nom_inconnu():
    """La commande dira lesquels étaient inconnus ; le cœur, lui, ne doit pas
    abandonner les retraits valides à cause d'une faute de frappe."""
    restantes = retirer_plusieurs([_f("A", "1000")], ["A", "JAMAIS VUE"])

    assert restantes == []


def test_retirer_plusieurs_sans_nom_ne_retire_rien():
    """Une liste vide de noms est probablement une saisie ratée : tout effacer
    serait la pire interprétation possible."""
    liste = [_f("A", "1000"), _f("B", "2000")]

    assert [f.nom for f in retirer_plusieurs(liste, [])] == ["A", "B"]


def test_retirer_plusieurs_ne_touche_pas_la_liste_d_origine():
    liste = [_f("A", "1000"), _f("B", "2000")]

    retirer_plusieurs(liste, ["A"])

    assert len(liste) == 2


def test_retirer_plusieurs_ne_boucle_pas_sur_les_doublons():
    """Le même nom deux fois ne doit pas retirer deux filiales : elles n'ont
    qu'une entrée chacune, et un second passage viserait une voisine."""
    liste = [_f("A", "1000"), _f("B", "2000")]

    restantes = retirer_plusieurs(liste, ["A", "A"])

    assert [f.nom for f in restantes] == ["B"]


# --- des chiffres au hasard, pour les essais --------------------------------


def test_deux_tirages_de_meme_graine_donnent_les_memes_chiffres():
    """Le hasard est injecté et non pris au module : un test qui ne peut pas
    rejouer un tirage ne peut rien affirmer dessus."""
    assert benefices_aleatoires(Random(7)) == benefices_aleatoires(Random(7))


def test_les_tirages_couvrent_plusieurs_ordres_de_grandeur():
    """C'est tout l'intérêt de l'essai : le tableau doit être vu avec des
    montants de tailles différentes, sans quoi il ne serait jamais éprouvé ni
    sur son tri, ni sur les notations d'échelle du jeu."""
    alea = Random(1234)
    tailles = {len(str(abs(benefices_aleatoires(alea)))) for _ in range(200)}

    assert len(tailles) >= 10, f"tirages trop resserrés : {sorted(tailles)}"


def test_un_tirage_monte_jusqu_aux_montants_ou_un_float_casse():
    """Les bénéfices du jeu atteignent vingt-un chiffres, au-delà de la mantisse
    d'un `float64` : des essais plafonnés plus bas ne mettraient jamais à
    l'épreuve ce que la production doit encaisser."""
    alea = Random(1234)
    plus_gros = max(abs(benefices_aleatoires(alea)) for _ in range(200))

    assert plus_gros > Decimal(10) ** 17


def test_un_tirage_descend_aussi_a_des_petits_montants():
    """Sinon toutes les lignes s'afficheraient dans la même échelle et le tri
    ne serait pas éprouvé non plus."""
    alea = Random(1234)
    plus_petit = min(abs(benefices_aleatoires(alea)) for _ in range(200))

    assert plus_petit < Decimal(10) ** 8


def test_les_tirages_comportent_des_pertes_et_des_gains():
    """Une filiale en perte se marque autrement dans le tableau : sans perte
    tirée, la moitié de l'affichage resterait invisible à l'essai."""
    alea = Random(1234)
    tirages = [benefices_aleatoires(alea) for _ in range(200)]

    assert any(t <= 0 for t in tirages), "aucune perte tirée"
    assert any(t > 0 for t in tirages), "aucun gain tiré"


def test_un_tirage_est_un_montant_entier():
    """Le jeu ne connaît pas de fraction d'Ø de bénéfices."""
    tirage = benefices_aleatoires(Random(7))

    assert isinstance(tirage, Decimal)
    assert tirage == tirage.to_integral_value()


def test_un_gros_tirage_garde_ses_derniers_chiffres():
    """Passé par un `float`, un montant à vingt-un chiffres perdrait sa queue.

    `isinstance(..., Decimal)` ne suffit pas à le voir : `Decimal(float(x))`
    reste un `Decimal` entier, seulement arrondi. Ce qui trahit le passage par un
    flottant, c'est le nombre de **bits significatifs** : la mantisse d'un
    `float64` en tient cinquante-trois, donc au-delà les bits de poids faible
    sont forcément à zéro.

    Un seuil de divisibilité ne suffisait pas : près de 10¹⁸ un `float64`
    représente encore les multiples de 128, si bien qu'un test cherchant des
    multiples de 1024 passait aussi sur les valeurs arrondies.
    """
    alea = Random(1234)
    tirages = [int(abs(benefices_aleatoires(alea))) for _ in range(300)]
    gros = [t for t in tirages if t.bit_length() > 53]

    assert gros, "aucun tirage au-delà de la mantisse : le test ne prouverait rien"
    assert any(_bits_significatifs(t) > 53 for t in gros), (
        "aucun tirage ne porte plus de 53 bits significatifs : "
        "ils sont tous passés par un flottant"
    )


def _bits_significatifs(entier: int) -> int:
    """Bits porteurs d'information dans un entier, zéros de queue exclus.

    C'est la mesure que la mantisse d'un `float64` plafonne à cinquante-trois.
    """
    zeros_de_queue = (entier & -entier).bit_length() - 1
    return entier.bit_length() - zeros_de_queue


def test_les_valeurs_aleatoires_gardent_les_noms_et_l_ordre():
    """Les noms sont la clé d'import du jeu : un essai ne doit pas obliger à
    tous les ressaisir ensuite."""
    liste = [_f("ARMEE", "1000"), _f("MARINE", "2000"), _f("AIR", "3000")]

    essai = valeurs_aleatoires(liste, "2026-08-12", Random(7))

    assert [f.nom for f in essai] == ["ARMEE", "MARINE", "AIR"]


def test_les_valeurs_aleatoires_remplacent_les_montants():
    """Un essai qui laisserait les vrais relevés en place n'éprouverait rien."""
    liste = [_f("A", "1000"), _f("B", "1000"), _f("C", "1000")]

    essai = valeurs_aleatoires(liste, "2026-08-12", Random(1234))

    assert len({f.benefices for f in essai}) > 1


def test_les_valeurs_aleatoires_recalculent_les_frais():
    """Sinon le tableau d'essai afficherait des frais sans rapport avec les
    bénéfices affichés à côté, et ne prouverait rien du calcul."""
    essai = valeurs_aleatoires([_f("A", "1000")], "2026-08-12", Random(7))

    attendu = calculer("A", essai[0].benefices, "2026-08-12").frais
    assert essai[0].frais == attendu


def test_les_valeurs_aleatoires_datent_du_jour_de_l_essai():
    """Datées de la veille, toutes les lignes s'afficheraient comme périmées et
    l'essai ne montrerait pas le tableau tel qu'il sort d'ordinaire."""
    essai = valeurs_aleatoires([_f("A", "1000", date="2026-08-01")], "2026-08-12", Random(7))

    assert essai[0].date == "2026-08-12"


def test_les_valeurs_aleatoires_ne_touchent_pas_la_liste_d_origine():
    liste = [_f("A", "1000")]

    valeurs_aleatoires(liste, "2026-08-12", Random(7))

    assert liste[0].benefices == Decimal(1000)


def test_les_valeurs_aleatoires_d_une_liste_vide_ne_levent_pas():
    assert valeurs_aleatoires([], "2026-08-12", Random(7)) == []



# --- noms_separes -----------------------------------------------------------


def test_les_noms_se_separent_par_des_virgules():
    """Une seule commande pour retirer un lot : Discord n'offre pas de champ
    répétable, donc les noms arrivent dans une chaîne."""
    assert noms_separes("A, B, C") == ["A", "B", "C"]


def test_les_noms_se_separent_aussi_par_des_retours_a_la_ligne():
    """Un lot vient souvent d'une liste collée, une par ligne."""
    assert noms_separes("A\nB") == ["A", "B"]


def test_les_espaces_autour_des_noms_sont_retires():
    """Ils sont invisibles à la saisie, et un nom qui les garde ne
    correspondrait à aucune filiale."""
    assert noms_separes("  A ,  B  ") == ["A", "B"]


def test_les_doubles_espaces_internes_survivent_au_decoupage():
    """C'est la clé d'import du jeu : `ARMEE  DE TERRE` n'est pas
    `ARMEE DE TERRE`, et le nom retiré doit être celui du tableau."""
    assert noms_separes("ARMEE  DE TERRE, MARINE") == ["ARMEE  DE TERRE", "MARINE"]


def test_une_virgule_en_trop_ne_donne_pas_de_nom_vide():
    """Un nom vide ne désignerait rien, et gonflerait le compte des inconnus
    d'une entrée que personne n'a saisie."""
    assert noms_separes("A, B,") == ["A", "B"]


def test_une_saisie_vide_ne_donne_aucun_nom():
    """Et non une liste d'un nom vide : `retirer_plusieurs` sans nom ne retire
    rien, ce qui est la bonne lecture d'une saisie ratée."""
    assert noms_separes("   ") == []


def test_les_noms_repetes_ne_sont_gardes_qu_une_fois():
    """Deux fois le même nom compterait deux retraits, ou un « inconnu » pour un
    nom qu'on vient de retirer."""
    assert noms_separes("A, a, A") == ["A"]



# --- le tirage d'essai borné à un palier ------------------------------------


def test_un_tirage_a_un_palier_tient_dans_ce_palier():
    """L'unité demandée fixe l'ordre de grandeur : tirer hors du palier
    afficherait un autre symbole que celui qu'on a choisi de voir.

    De 1 à 999 fois le palier, comme `format_money` : à 1000 il rebascule sur le
    symbole du dessus.
    """
    alea = Random(1234)
    palier = Decimal(10) ** 15  # P

    for _ in range(200):
        tirage = abs(benefices_aleatoires(alea, exposant=15))
        assert palier <= tirage < 1000 * palier, f"{tirage} hors du palier P"


class _AleaAuPlafond:
    """Generateur truque qui rend toujours la plus grande valeur possible.

    Le haut du palier est atteint une fois sur deux cent mille : une graine ne
    l'exhiberait jamais, et le defaut passerait en production pour se voir un
    jour au hasard.
    """

    def randrange(self, bas, haut):
        return haut - 1

    def randint(self, bas, haut):
        return haut

    def random(self):
        return 1.0  # jamais de perte : c'est le signe qu'on veut lire


class _AleaAuPlancher:
    """Generateur truque qui rend toujours la plus petite valeur possible.

    Symetrique de `_AleaAuPlafond`, et pour la meme raison : un plancher tombe
    trop bas ne se verrait qu'une fois sur mille avec une vraie graine, donc pas
    de facon fiable.
    """

    def randrange(self, bas, haut):
        return bas

    def randint(self, bas, haut):
        return bas

    def random(self):
        return 1.0  # jamais de perte : c'est le signe qu'on veut lire


def test_le_bas_d_un_palier_s_affiche_encore_dans_ce_palier():
    """Au plancher du palier, le montant doit deja porter le symbole demande.

    `format_money` prend le plus grand palier qui tient : un tirage sous 10^15
    s'afficherait en TØ, GØ ou meme sans symbole, alors qu'on a demande a voir le
    tableau en PØ.
    """
    montant = benefices_aleatoires(_AleaAuPlancher(), exposant=15)

    assert format_money(montant).endswith("PØ"), format_money(montant)


def test_le_haut_d_un_palier_s_affiche_encore_dans_ce_palier():
    """Au plafond du palier, le montant doit garder le symbole demande.

    `format_money` arrondit la mantisse a deux decimales **puis** rebascule sur
    le palier du dessus si elle atteint 1000 : 999,996 PØ s'affiche `1.00 EØ`. Un
    tirage montant jusqu'a 1000 fois le palier afficherait donc parfois un autre
    symbole que celui qu'on a choisi de voir.
    """
    montant = benefices_aleatoires(_AleaAuPlafond(), exposant=15)

    assert format_money(montant).endswith("PØ"), format_money(montant)


def test_un_tirage_a_un_palier_varie_quand_meme():
    """Un palier imposé ne doit pas donner deux cents fois le même montant : le
    tableau serait alors plat et n'éprouverait pas son tri."""
    alea = Random(1234)
    tirages = {benefices_aleatoires(alea, exposant=15) for _ in range(50)}

    assert len(tirages) > 40


def test_un_tirage_a_un_palier_comporte_des_pertes():
    """Le palier borne l'ordre de grandeur, pas le signe : une filiale en perte
    se marque autrement dans le tableau et doit rester visible à l'essai."""
    alea = Random(1234)
    tirages = [benefices_aleatoires(alea, exposant=15) for _ in range(200)]

    assert any(t < 0 for t in tirages), "aucune perte tirée"
    assert any(t > 0 for t in tirages), "aucun gain tiré"


def test_un_tirage_au_palier_le_plus_haut_reste_un_entier_exact():
    """Le septilliard fait quarante-six chiffres, très au-delà d'un `float64` :
    c'est le palier où un flottant perdrait le plus."""
    alea = Random(1234)

    for _ in range(50):
        tirage = abs(benefices_aleatoires(alea, exposant=45))
        assert isinstance(tirage, Decimal)
        assert tirage == tirage.to_integral_value()


def test_un_tirage_a_l_unite_descend_sous_le_millier():
    """L'unité est un palier comme les autres : sans lui, on ne pourrait pas
    voir le tableau avec des montants à trois chiffres."""
    alea = Random(1234)

    for _ in range(50):
        assert abs(benefices_aleatoires(alea, exposant=0)) < 1000


def test_sans_palier_le_tirage_couvre_toute_l_echelle():
    """Le comportement d'avant reste le défaut : c'est celui qui éprouve les
    notations d'échelle du jeu, faute de quoi elles ne seraient jamais vues."""
    alea = Random(1234)
    tailles = {len(str(abs(benefices_aleatoires(alea)))) for _ in range(200)}

    assert len(tailles) >= 10


def test_les_valeurs_aleatoires_transmettent_le_palier():
    """Sinon l'unité choisie dans la commande serait ignorée en silence."""
    essai = valeurs_aleatoires(
        [_f("A", "1000"), _f("B", "2000")], "2026-08-12", Random(1234), exposant=15
    )

    palier = Decimal(10) ** 15
    assert all(palier <= abs(f.benefices) < 1000 * palier for f in essai)


# --- vers_import : le format d'import du jeu --------------------------------


def test_une_ligne_par_filiale_avec_un_seul_tab():
    """Le jeu refuse une ligne qui porte plus d'une tabulation.

    L'assertion porte sur **chaque** ligne : compter les tabulations du texte
    entier laisserait passer une ligne à deux tabs compensée par une ligne à
    zéro.
    """
    texte = vers_import([_f("MEGAPOLE", "2710572934559948"), _f("MARINE", "175000000")])

    lignes = [ligne for ligne in texte.split(FIN_DE_LIGNE_IMPORT) if ligne]
    assert len(lignes) == 2, lignes
    assert all(ligne.count(SEPARATEUR_IMPORT) == 1 for ligne in lignes), lignes


def test_les_lignes_sont_separees_par_des_crlf():
    """CRLF est le séparateur officiel du jeu, y compris pour la dernière ligne.

    Terminée elle aussi : un lecteur qui découpe sur le séparateur perdrait
    sinon la dernière filiale, celle qu'on verrait le moins manquer.
    """
    texte = vers_import([_f("MEGAPOLE", "2710572934559948"), _f("MARINE", "175000000")])

    assert texte.endswith("\r\n"), repr(texte[-4:])
    assert texte.count("\r\n") == 2, repr(texte)


def test_aucun_saut_de_ligne_seul():
    """Un `\\n` sans son `\\r` serait un CRLF à moitié écrit.

    Sans ce cas, un texte assemblé en LF puis simplement terminé par un CRLF
    passerait le test précédent.
    """
    texte = vers_import([_f("A", "1000"), _f("B", "2000"), _f("C", "3000")])

    assert texte.replace("\r\n", "").count("\n") == 0, repr(texte)
    assert texte.replace("\r\n", "").count("\r") == 0, repr(texte)


def test_les_montants_sont_des_chiffres_seuls():
    """Le jeu lit des chiffres, pas la notation d'échelle qu'il affiche.

    Vingt-un chiffres de bénéfices, parce que c'est là qu'un arrondi se voit :
    `format_money` rendrait `9,67 EØ` et le jeu recevrait un montant que
    personne ne doit.
    """
    texte = vers_import([_f("MEGAPOLE", "138131471904669765329")])

    assert "MEGAPOLE\t9669203033326883573\r\n" == texte, repr(texte)


def test_ce_sont_les_frais_et_non_les_benefices():
    """Le format demande les frais de gestion : ce qu'on doit, pas ce qu'on gagne.

    Confondre les deux ferait payer quatorze fois trop, sans que le fichier ait
    l'air faux.
    """
    texte = vers_import([_f("MARINE", "1000")])

    assert texte == "MARINE\t70\r\n", repr(texte)


def test_une_filiale_en_perte_sort_a_zero():
    """Une ligne par filiale : le fichier doit refléter le tableau entier.

    Zéro est le montant exact — il n'y a rien à prélever sur une perte — et
    omettre la ligne laisserait croire à un export incomplet.
    """
    texte = vers_import([_f("A", "1000"), _f("EN PERTE", "-5000"), _f("B", "2000")])

    assert "EN PERTE\t0\r\n" in texte, repr(texte)
    assert len([ligne for ligne in texte.split("\r\n") if ligne]) == 3, repr(texte)


def test_les_doubles_espaces_du_nom_sont_conserves():
    """Le nom est la clé d'import du jeu : normalisé, il ne correspondrait plus."""
    texte = vers_import([_f("ARMEE  DE TERRE", "1000")])

    assert texte.startswith("ARMEE  DE TERRE\t"), repr(texte)


def test_un_tab_colle_dans_un_nom_est_neutralise():
    """Un nom peut porter un tab **collé** : `calculer` ne retire que les bords.

    Laissé tel quel, il ouvrirait une deuxième colonne et le jeu refuserait la
    ligne entière.
    """
    texte = vers_import([_f("ARMEE\tDE TERRE", "1000")])

    assert texte.count(SEPARATEUR_IMPORT) == 1, repr(texte)
    assert texte == "ARMEE DE TERRE\t70\r\n", repr(texte)


def test_un_retour_a_la_ligne_colle_dans_un_nom_est_neutralise():
    """Même raison : il couperait la ligne en deux, dont une sans montant."""
    texte = vers_import([_f("ARMEE\r\nDE TERRE", "1000")])

    assert texte == "ARMEE DE TERRE\t70\r\n", repr(texte)


def test_sans_filiale_le_texte_est_vide():
    """Vide, et non un CRLF solitaire : le jeu lirait une ligne sans nom."""
    assert vers_import([]) == ""


def test_l_ordre_enregistre_est_conserve():
    """Le fichier est une entrée machine, pas un classement.

    Trié par montant, deux exports des mêmes filiales différeraient dès qu'un
    montant bouge, et l'on ne saurait plus lequel est à jour.
    """
    texte = vers_import([_f("PETITE", "1000"), _f("GROSSE", "2710572934559948")])

    assert texte.index("PETITE") < texte.index("GROSSE"), repr(texte)


def test_nom_pour_import_garde_les_espaces_internes():
    """La règle est isolée pour que la commande puisse dire ce qu'elle a modifié.

    Sans elle exposée, la commande recopierait la neutralisation et les deux
    finiraient par divergier.
    """
    assert nom_pour_import("ARMEE  DE TERRE") == "ARMEE  DE TERRE"
    assert nom_pour_import("ARMEE\tDE TERRE") == "ARMEE DE TERRE"


def _f(nom: str, benefices: str, date: str = "2026-08-11") -> Filiale:
    """Un relevé, pour alléger les cas ci-dessus."""
    return calculer(nom, Decimal(benefices), date)
