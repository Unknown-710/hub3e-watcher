# -*- coding: utf-8 -*-
"""
Surveillance des offres d'alternance sur Hub3E (ITII Alsace).

Ce script charge la page publique des offres avec un vrai navigateur
automatise (Playwright), attend que la liste JavaScript se charge,
puis extrait chaque offre affichee (ID + texte).

Il compare cette liste a la derniere liste connue (fichier
offres_connues.json, commite dans le depot) : si de nouveaux IDs
d'offres apparaissent, un email d'alerte est envoye et le fichier
est mis a jour.

Ce script fait UNE seule verification puis s'arrete (concu pour etre
declenche periodiquement par GitHub Actions, pas pour tourner en
boucle infinie).
"""

import json
import os
import re
import smtplib
import subprocess
import sys
from email.mime.text import MIMEText
from datetime import datetime

from playwright.sync_api import sync_playwright

# ============================================================
# CONFIGURATION
# ============================================================

URL = (
    "https://app.hub3e.com/public/school/69/offers-sharing/1006/"
    "133191e9-898c-43d0-8fe9-7a4ae305c837/2025-05-20?idcand=4581"
)

FICHIER_OFFRES_CONNUES = "offres_connues.json"

EMAIL_EXPEDITEUR = os.environ.get("GMAIL_EXPEDITEUR")
MOT_DE_PASSE_APPLICATION = os.environ.get("GMAIL_MOT_DE_PASSE_APPLICATION")
EMAIL_DESTINATAIRE = os.environ.get("GMAIL_DESTINATAIRE")

SMTP_SERVEUR = "smtp.gmail.com"
SMTP_PORT = 587

# Regex pour extraire l'ID de l'offre a la fin du href
# ex: /public/school/69/offers-sharing/1006/.../offer/66529?idcand=4581
MOTIF_ID_OFFRE = re.compile(r"/offer/(\d+)")


# ============================================================
# FONCTIONS
# ============================================================


def recuperer_offres_affichees():
    """
    Ouvre la page avec Playwright, attend le chargement JS,
    et retourne un dict {id_offre: texte_de_la_ligne}.
    """
    offres = {}

    with sync_playwright() as p:
        navigateur = p.chromium.launch()
        page = navigateur.new_page()
        page.goto(URL, wait_until="networkidle", timeout=60000)

        # On attend explicitement qu'au moins une ligne d'offre apparaisse.
        # Si aucune offre n'existe actuellement, ce timeout expirera :
        # on continue quand meme (liste vide), ce n'est pas une erreur.
        try:
            page.wait_for_selector("a.public-batch-row", timeout=15000)
        except Exception:
            print(f"[{datetime.now()}] Aucune ligne d'offre detectee "
                  "(page vide ou selecteur different).")

        lignes = page.query_selector_all("a.public-batch-row")

        for ligne in lignes:
            href = ligne.get_attribute("href") or ""
            correspondance = MOTIF_ID_OFFRE.search(href)
            if not correspondance:
                continue
            id_offre = correspondance.group(1)
            texte = ligne.inner_text().strip().replace("\n", " | ")
            offres[id_offre] = texte

        navigateur.close()

    return offres


def charger_offres_connues():
    """Charge la liste des IDs d'offres deja vus depuis le fichier local."""
    if not os.path.exists(FICHIER_OFFRES_CONNUES):
        return {}
    try:
        with open(FICHIER_OFFRES_CONNUES, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def sauvegarder_offres_connues(offres):
    """Ecrit la liste actuelle des offres dans le fichier local."""
    with open(FICHIER_OFFRES_CONNUES, "w", encoding="utf-8") as f:
        json.dump(offres, f, ensure_ascii=False, indent=2)


def envoyer_email(nouvelles_offres):
    """Envoie un email listant les nouvelles offres detectees."""
    if not all([EMAIL_EXPEDITEUR, MOT_DE_PASSE_APPLICATION, EMAIL_DESTINATAIRE]):
        print(f"[{datetime.now()}] ERREUR : identifiants email manquants.")
        sys.exit(1)

    nombre = len(nouvelles_offres)
    sujet = f"🎓 {nombre} nouvelle(s) offre(s) d'alternance sur Hub3E !"

    lignes_corps = []
    for id_offre, texte in nouvelles_offres.items():
        lignes_corps.append(f"- {texte}")

    corps = (
        "Bonjour,\n\n"
        f"{nombre} nouvelle(s) offre(s) d'alternance viennent d'apparaitre "
        "sur Hub3E (ITII Alsace) :\n\n"
        + "\n\n".join(lignes_corps)
        + f"\n\nLien : {URL}\n"
    )

    message = MIMEText(corps, "plain", "utf-8")
    message["Subject"] = sujet
    message["From"] = EMAIL_EXPEDITEUR
    message["To"] = EMAIL_DESTINATAIRE

    with smtplib.SMTP(SMTP_SERVEUR, SMTP_PORT) as serveur:
        serveur.starttls()
        serveur.login(EMAIL_EXPEDITEUR, MOT_DE_PASSE_APPLICATION)
        serveur.send_message(message)

    print(f"[{datetime.now()}] Email envoye avec succes.")


def commiter_fichier_offres():
    """
    Commite et pousse le fichier offres_connues.json mis a jour,
    pour que la memoire persiste d'une execution GitHub Actions a l'autre.
    """
    try:
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
        subprocess.run(
            ["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"],
            check=True,
        )
        subprocess.run(["git", "add", FICHIER_OFFRES_CONNUES], check=True)

        resultat = subprocess.run(
            ["git", "diff", "--cached", "--quiet"]
        )
        if resultat.returncode == 0:
            print(f"[{datetime.now()}] Aucun changement a committer.")
            return

        subprocess.run(
            ["git", "commit", "-m", "Mise a jour automatique des offres connues"],
            check=True,
        )
        subprocess.run(["git", "push"], check=True)
        print(f"[{datetime.now()}] offres_connues.json mis a jour et pousse.")
    except subprocess.CalledProcessError as erreur:
        print(f"[{datetime.now()}] ERREUR lors du commit/push : {erreur}")


def verification_unique():
    """Effectue UNE verification puis se termine."""
    print(f"[{datetime.now()}] Verification des offres Hub3E en cours...")

    offres_actuelles = recuperer_offres_affichees()
    print(f"[{datetime.now()}] {len(offres_actuelles)} offre(s) trouvee(s) sur la page.")

    offres_connues = charger_offres_connues()

    nouveaux_ids = set(offres_actuelles.keys()) - set(offres_connues.keys())

    if nouveaux_ids:
        nouvelles_offres = {id_: offres_actuelles[id_] for id_ in nouveaux_ids}
        print(f"[{datetime.now()}] {len(nouvelles_offres)} nouvelle(s) offre(s) detectee(s) !")
        for id_, texte in nouvelles_offres.items():
            print(f"  -> [{id_}] {texte[:100]}")

        envoyer_email(nouvelles_offres)

        sauvegarder_offres_connues(offres_actuelles)
        commiter_fichier_offres()
    else:
        print(f"[{datetime.now()}] Aucune nouvelle offre.")


# ============================================================
# POINT D'ENTREE
# ============================================================

if __name__ == "__main__":
    verification_unique()
