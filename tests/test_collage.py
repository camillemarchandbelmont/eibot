"""Lire le tableau des filiales collé depuis le jeu.

Le jeu affiche un tableau de cinq colonnes que l'on sélectionne à la souris et
que l'on colle dans la page. Ce module en tire ce que le bot sait déjà traiter :
un nom et des bénéfices par filiale.

Trois pièges, et ils viennent tous des vraies données :

- **les noms contiennent des doubles espaces** (`ARMEE  DE TERRE`). C'est la clé
  d'import du jeu : normalisés, l'import échouerait de son côté. Interdit donc de
  découper les colonnes sur les espaces — la première colonne y perdrait la
  moitié de son nom, sans que rien ne le signale.
- **des noms finissent par un chiffre** (`EMF AZOU 1`). Deviner les colonnes en
  ramassant les nombres depuis la droite mangerait ce chiffre et enregistrerait
  une filiale « EMF AZOU », inconnue du jeu.
- **les montants atteignent dix-neuf chiffres.** Un `float` en perdrait les
  derniers, et le fichier d'import demanderait un montant que le jeu ne réclame
  pas.

D'où la règle : les colonnes se séparent aux **tabulations**, celles que le
navigateur pose en collant un tableau. Une ligne sans tabulation est refusée et
montrée, jamais devinée.
"""

from decimal import Decimal

from src.collage import lire_collage, vers_filiales

#: Le vrai tableau du jeu, tel qu'il se colle : 13 filiales, cinq colonnes.
#:
#: Gardé en entier plutôt que réduit à deux lignes : les doubles espaces, les
#: noms finissant par un chiffre et les trésoreries à onze chiffres au milieu de
#: voisines à dix-neuf sont dans ces données-là, pas dans un échantillon qu'on
#: écrirait soi-même.
ENTETE = ("Filiale", "Trésorerie", "Résultat d'exploitation", "Résultat NET",
          "Bénéfices ou pertes")

TABLEAU = (
    ("ARMEE  DE LAIR ET DE L ESPACE", "3196169776647940996",
     "506844214710768353", "344582317616911946", "344582317616911946"),
    ("ARMEE  DE TERRE", "3232623780266847694",
     "433138277798906106", "294736049105276347", "294736049105276347"),
    ("BASE  AERIENNE  AIX EN PROVENCE", "83581155686",
     "1330418183645633761", "871669657735225808", "871669657735225808"),
    ("BASE  MILITAIRE ROCHEFORT", "3461182468658120540",
     "546123768061310243", "369721231761258756", "369721231761258756"),
    ("BASE  MILITAIRE SAINT MAIXENT L ECOLE", "71010688251",
     "679057308319138469", "454798697526268821", "454798697526268821"),
    ("BASE AERIENNE MARSEILLE", "3440341156445156085",
     "551649701569326127", "373257829206388922", "373257829206388922"),
    ("BASE MILITAIRE LA VALBONNE", "4374432672992550934",
     "726199097803999479", "484969442796579867", "484969442796579867"),
    ("BASE NAVALE BORDEAUX", "3061726177655369451",
     "289334376475304267", "196949396205227096", "196949396205227096"),
    ("BASE NAVALE BREST", "3259247848151095774",
     "509676140745977207", "346394750279445613", "346394750279445613"),
    ("EMF AZOU 1", "8987921942084377477",
     "1540138584214186411", "1005890714099099504", "1005890714099099504"),
    ("EMF AZOU 2", "8970807798650265366",
     "1508806664422095920", "985838285432161589", "985838285432161589"),
    ("EMF AZOU 3", "8942737820946257245",
     "1523068071741241474", "994965586116414744", "994965586116414744"),
    ("MARINE  NATIONALE", "3172949192738062677",
     "313660665572666826", "213491272791433636", "213491272791433636"),
)


def _coller(*rangees: tuple[str, ...]) -> str:
    """Le collage tel qu'un navigateur le produit : tabulations et tabulation finale.

    La tabulation de fin est celle que laisse une sélection à la souris qui
    dépasse la dernière colonne. Elle ouvre une sixième cellule, vide : sans
    tolérance, la dernière colonne lue serait celle-là et tous les montants
    seraient illisibles.
    """
    return "".join("\t".join(rangee) + "\t\n" for rangee in rangees)


COLLAGE = _coller(ENTETE, *TABLEAU)


def _noms(lecture) -> list[str]:
    return [releve.nom for releve in lecture.releves]


def _par_nom(lecture, nom: str) -> Decimal:
    return next(r.benefices for r in lecture.releves if r.nom == nom)


# --- Le vrai collage ---------------------------------------------------------


def test_le_collage_du_jeu_donne_une_filiale_par_ligne():
    """Treize filiales collées, treize relevés, et rien de refusé.

    Le test qui dit que la page marche : tout le reste raffine des cas
    particuliers, celui-ci porte sur les données qu'on collera vraiment.
    """
    lecture = lire_collage(COLLAGE)

    assert len(lecture.releves) == 13
    assert lecture.refuses == []


def test_lentete_du_jeu_nest_pas_prise_pour_une_filiale():
    """La ligne de titres fait partie de la sélection : on la colle toujours.

    Comptée comme une filiale, elle enregistrerait une ligne « Filiale » dans le
    tableau du soir ; signalée comme illisible, elle ferait chercher une erreur
    dans un collage parfaitement bon.
    """
    lecture = lire_collage(COLLAGE)

    assert "Filiale" not in _noms(lecture)
    assert lecture.refuses == []


def test_les_doubles_espaces_des_noms_sont_conserves():
    """Le nom est la clé d'import du jeu, doubles espaces compris.

    Normalisé, il ne correspondrait à aucune filiale de son côté : l'import
    passerait sans rien mettre à jour, ce qui ne se voit pas.
    """
    lecture = lire_collage(COLLAGE)

    assert "ARMEE  DE TERRE" in _noms(lecture)
    assert "BASE  AERIENNE  AIX EN PROVENCE" in _noms(lecture)


def test_un_nom_qui_finit_par_un_chiffre_reste_entier():
    """« EMF AZOU 1 », et les trois du même nom.

    Ramasser les nombres depuis la droite pour deviner les colonnes emporterait
    ce `1` : trois filiales « EMF AZOU » indistinctes, et un import muet.
    """
    lecture = lire_collage(COLLAGE)

    assert ["EMF AZOU 1", "EMF AZOU 2", "EMF AZOU 3"] == [
        nom for nom in _noms(lecture) if nom.startswith("EMF")
    ]


def test_les_montants_gardent_leurs_dix_neuf_chiffres():
    """Passé par un `float`, un montant de dix-neuf chiffres perd les derniers.

    Le fichier d'import réclamerait alors un montant que le jeu ne connaît pas,
    et l'écart serait invisible à l'œil.
    """
    lecture = lire_collage(COLLAGE)

    assert _par_nom(lecture, "EMF AZOU 1") == Decimal("1005890714099099504")


# --- Quelle colonne ---------------------------------------------------------


def test_les_benefices_sont_la_derniere_colonne():
    """« Bénéfices ou pertes » est la dernière du tableau du jeu.

    Dans le vrai collage elle est égale au résultat net, si bien qu'un test sur
    ces données ne distinguerait pas les deux. Ici elles diffèrent : prendre le
    résultat net calculerait les frais sur un montant que le jeu ne prélève pas.
    """
    lecture = lire_collage(_coller(("MEGAPOLE", "10", "20", "30", "40")))

    assert _par_nom(lecture, "MEGAPOLE") == Decimal(40)


def test_lentete_designe_la_colonne_quand_elle_nest_pas_la_derniere():
    """Une colonne ajoutée à droite par le jeu ne doit pas déplacer la lecture.

    La dernière colonne n'est le bon choix que par défaut ; quand l'en-tête
    nomme celle des bénéfices, c'est elle qui tranche. Sinon, le jour où le jeu
    ajoute une colonne, la page prélèverait 7 % de n'importe quoi sans que rien
    ne change à l'écran.
    """
    lecture = lire_collage(
        _coller(("Filiale", "Bénéfices ou pertes", "Employés"),
                ("MEGAPOLE", "1000", "12"))
    )

    assert _par_nom(lecture, "MEGAPOLE") == Decimal(1000)


def test_sans_entete_la_derniere_colonne_fait_foi():
    """Une sélection qui commence sous les titres reste lisible.

    C'est le cas d'un deuxième collage, où l'on ne resélectionne pas l'en-tête.
    """
    lecture = lire_collage(_coller(("MEGAPOLE", "10", "20", "30", "40")))

    assert len(lecture.releves) == 1
    assert lecture.refuses == []


def test_deux_colonnes_suffisent():
    """Le format minimal : un nom, un montant.

    C'est ce qu'on tape à la main pour corriger une filiale, et ce que la page
    doit accepter sans exiger de recoller les cinq colonnes du jeu.
    """
    lecture = lire_collage("MEGAPOLE\t1000\n")

    assert _par_nom(lecture, "MEGAPOLE") == Decimal(1000)


# --- Ce qui est refusé, et ce qui est ignoré --------------------------------


def test_les_lignes_vides_ne_sont_ni_lues_ni_signalees():
    """Un collage se termine par un retour à la ligne, et en contient parfois.

    Signalées, ces lignes feraient chercher une faute là où il n'y a qu'un
    passage à la ligne.
    """
    lecture = lire_collage("\nMEGAPOLE\t1000\n\n   \n")

    assert len(lecture.releves) == 1
    assert lecture.refuses == []


def test_une_ligne_sans_tabulation_est_refusee_et_montree():
    """Les colonnes ne se devinent pas sur les espaces : les noms en contiennent.

    Refusée et rendue telle quelle, la ligne se corrige. Devinée, elle
    enregistrerait un nom tronqué qu'aucun import ne retrouverait.
    """
    lecture = lire_collage("MEGAPOLE 1000\n")

    assert lecture.releves == []
    assert len(lecture.refuses) == 1
    assert lecture.refuses[0].ligne == "MEGAPOLE 1000"
    assert "tabulation" in lecture.refuses[0].raison.casefold()


def test_un_montant_illisible_est_refuse_avec_sa_ligne():
    """Une cellule qui n'est pas un montant coûte sa ligne, pas le collage.

    Les autres filiales du même collage doivent passer : tout annuler pour une
    ligne obligerait à recoller les treize.
    """
    lecture = lire_collage(_coller(("ABIMEE", "12x34"), ("MEGAPOLE", "1000")))

    assert _noms(lecture) == ["MEGAPOLE"]
    assert len(lecture.refuses) == 1
    assert lecture.refuses[0].ligne.startswith("ABIMEE")


def test_un_nom_sans_montant_nest_pas_lu_comme_un_montant():
    """La ligne qu'on tape à la main et qu'on laisse en plan.

    Le repli sur la dernière cellule remplie ne doit jamais atteindre la
    première : le nom y passerait pour un montant, et « ARMEE 2 » enregistrerait
    des frais de deux Ø au lieu d'être refusée.
    """
    lecture = lire_collage("MEGAPOLE\t\n")

    assert lecture.releves == []
    assert "aucun montant" in lecture.refuses[0].raison.casefold()


def test_une_ligne_sans_nom_est_refusee():
    """Une filiale anonyme ne se retrouve ni dans le tableau ni à l'import."""
    lecture = lire_collage("\t1000\n")

    assert lecture.releves == []
    assert len(lecture.refuses) == 1


def test_le_numero_dune_ligne_refusee_est_celui_du_collage():
    """Le numéro sert à retrouver la ligne dans la zone de texte.

    Compté sur les seules lignes lues, il désignerait la mauvaise dès qu'une
    ligne vide ou l'en-tête précède — et l'on corrigerait une filiale saine.
    """
    lecture = lire_collage(
        _coller(ENTETE, ("MEGAPOLE", "1000")) + "\nABIMEE 500\n"
    )

    # 1 en-tête, 2 Mégapôle, 3 vide, 4 la fautive.
    assert lecture.refuses[0].numero == 4


# --- Les montants, dans la grammaire du bot ---------------------------------


def test_une_perte_est_lue_comme_un_montant_negatif():
    """« Bénéfices ou pertes » : la colonne porte les deux.

    Lue en valeur absolue, une perte donnerait des frais à payer sur de
    l'argent perdu.
    """
    lecture = lire_collage("MEGAPOLE\t-1234\n")

    assert _par_nom(lecture, "MEGAPOLE") == Decimal(-1234)


def test_la_notation_du_jeu_est_acceptee():
    """`2,71 PØ` recopié depuis le jeu, comme dans `/filiales releve`.

    Une seule grammaire de montant dans tout le bot : la page ne doit pas
    inventer la sienne.
    """
    lecture = lire_collage("MEGAPOLE\t2,71 PØ\n")

    assert _par_nom(lecture, "MEGAPOLE") == Decimal("2.71e15")


def test_les_espaces_de_milliers_sont_acceptes():
    """Le jeu affiche parfois ses montants groupés par milliers."""
    lecture = lire_collage("MEGAPOLE\t1 000 000\n")

    assert _par_nom(lecture, "MEGAPOLE") == Decimal(1000000)


# --- Du collage aux relevés du bot ------------------------------------------


def test_les_frais_valent_sept_pour_cent_des_benefices():
    """Le même calcul que `/filiales releve`, appelé et non recopié."""
    filiales = vers_filiales(lire_collage("MEGAPOLE\t1000\n"), "2026-08-31")

    assert [(f.nom, f.frais) for f in filiales] == [("MEGAPOLE", Decimal(70))]


def test_une_filiale_en_perte_ne_paie_rien():
    """Le jeu ne rembourse pas : des frais négatifs seraient un montant inventé."""
    filiales = vers_filiales(lire_collage("MEGAPOLE\t-1000\n"), "2026-08-31")

    assert filiales[0].frais == Decimal(0)


def test_les_releves_portent_la_date_donnee():
    """La date est celle du collage : sans elle, le tableau du soir croirait ses
    lignes périmées le jour même où on vient de les saisir."""
    filiales = vers_filiales(lire_collage("MEGAPOLE\t1000\n"), "2026-08-31")

    assert filiales[0].date == "2026-08-31"


def test_une_filiale_collee_deux_fois_ne_compte_quune_fois():
    """Un collage peut se répéter — deux sélections qui se chevauchent.

    Le dernier montant gagne, comme une ressaisie, et la place du premier est
    gardée : deux lignes du même nom donneraient deux fois les mêmes frais dans
    le total, et un fichier d'import que le jeu lirait deux fois.
    """
    filiales = vers_filiales(
        lire_collage("MEGAPOLE\t1000\nTECHNOPOLE\t500\nMEGAPOLE\t2000\n"),
        "2026-08-31",
    )

    assert [(f.nom, f.benefices) for f in filiales] == [
        ("MEGAPOLE", Decimal(2000)),
        ("TECHNOPOLE", Decimal(500)),
    ]


def test_le_collage_du_jeu_donne_treize_releves_calcules():
    """Bout en bout sur les vraies données : treize filiales, treize frais.

    Aucune n'est en perte dans ce tableau, donc chacune doit payer quelque
    chose : un zéro ici trahirait une colonne mal lue.
    """
    filiales = vers_filiales(lire_collage(COLLAGE), "2026-08-31")

    assert len(filiales) == 13
    assert all(f.frais > 0 for f in filiales)


def test_un_collage_sans_rien_de_lisible_ne_rend_aucun_releve():
    """Rien de lisible, aucun relevé — et pas d'exception.

    Ce qu'on fait d'une liste vide est une décision de la page : elle refusera
    d'enregistrer, parce qu'écrire du vide effacerait les relevés du jour. Le
    cœur, lui, n'a rien à dire là-dessus.
    """
    assert vers_filiales(lire_collage("\n\n"), "2026-08-31") == []
