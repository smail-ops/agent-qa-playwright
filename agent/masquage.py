"""Masquage des données personnelles avant tout envoi au modèle.

La page testée peut afficher de vraies données : l'adresse e-mail d'un client,
un numéro de téléphone, de carte bancaire ou un IBAN. Elles n'ont rien à faire
chez le fournisseur du modèle. Avant chaque envoi, le programme les remplace
par un repère : « [e-mail masqué] », « [téléphone masqué] », « [carte masquée] »
ou « [IBAN masqué] ».

Une valeur écrite dans le cas de test reste visible : le testeur l'a fournie
lui-même, et l'agent en a besoin pour la saisir ou la vérifier.

Pour éviter de masquer n'importe quel nombre, un numéro de carte doit passer
le contrôle de Luhn et un IBAN celui de sa clé (modulo 97), comme dans les
logiciels bancaires.

Les noms et les adresses postales n'ont pas de format fixe. Trois indices, sans
outil de reconnaissance de texte :
- la structure de la page : la valeur d'un champ Nom, Prénom, Adresse, Code
  postal, Ville (reconnu par son libellé, son nom ou son attribut autocomplete) ;
- le format : une adresse comme « 12 rue de la Paix, 75002 Paris » ;
- le contexte : le nom qui suit « Bonjour », « Welcome » ou « Logged in as ».

Avec la vision, les mêmes données sont cachées sous un rectangle noir sur la
capture d'écran envoyée au modèle, ainsi que la valeur d'un champ de nom ou
d'adresse (voir browser.vision_screenshot).

Limites : un nom au milieu d'une phrase (« commande de Jean Dupont expédiée »)
passe, et une donnée coupée en deux par la mise en forme de la page (une partie
en gras) n'est pas cachée sur la capture.
"""

from __future__ import annotations

import re
from collections.abc import Callable

_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b")
# 13 à 19 chiffres d'un bloc, ou par groupes de 4 (et 4-6-5 pour American Express)
_CARD = re.compile(r"(?<![\d-])(?:\d{13,19}|\d{4}(?:[ -]\d{4}){2}[ -]\d{1,7}|\d{4}[ -]\d{6}[ -]\d{4,5})(?![\d-])")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_IMAGE = re.compile(r"\.(?:png|jpe?g|gif|svg|webp|avif)$", re.IGNORECASE)
# Numéro français (06 12 34 56 78, +33 6 12 34 56 78), ou international (+44 20 7946 0958)
_PHONE = re.compile(r"(?<![\d+])(?:(?:\+33[ .-]?(?:\(0\)[ .-]?)?|0)[1-9](?:[ .-]?\d{2}){4}"
                    r"|\+\d{1,3}(?:[ .-]?\(?\d{1,4}\)?){2,5})(?![\d])")


# Le nom qui suit une salutation : « Bonjour Jean », « Logged in as Alfred Campagne »
_UPPER = "A-ZÀ-ÖØ-Þ"
_WORD = f"[{_UPPER}][A-Za-zÀ-ÖØ-öø-ÿ'’-]+"
_GREETING = re.compile(rf"\b(?:Bonjour|Bonsoir|Bienvenue|Salut|Hello|Hi|Welcome|Logged in as|Signed in as|"
                       rf"Connecté en tant que|Connectée en tant que)\b[ ,:]*({_WORD}(?:[ -]{_WORD}){{0,2}})")
# Une adresse postale : numéro et type de voie, puis éventuellement code postal et ville
_LINK = r"(?:(?:de|du|des|la|le|l['’]|d['’])\s*)*"
_STREET_FR = (rf"\b\d{{1,4}}(?:\s?(?:bis|ter))?,?\s+(?i:rue|avenue|av\.|boulevard|bd|chemin|impasse|allée|allee|place|"
              rf"route|quai|cours|square|passage|voie|résidence|residence|lotissement|hameau)\s+{_LINK}{_WORD}"
              rf"(?:\s+{_LINK}{_WORD}){{0,4}}")
_STREET_EN = (r"\b\d{1,5}\s+(?:[A-Z][a-z]+\s+){1,3}(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Way|"
              r"Court|Ct|Place|Pl)\b\.?")
_ADDRESS = re.compile(rf"(?:{_STREET_FR}|{_STREET_EN})(?:,?\s+\d{{5}}\s+{_WORD}(?:[\s-]{_WORD})*)?")
NAME_TOKEN, ADDRESS_TOKEN, FIELD_TOKEN = "[nom masqué]", "[adresse masquée]", "[donnée personnelle masquée]"

# Une ligne d'élément envoyée au modèle, pour un champ de nom ou d'adresse : sa valeur est masquée
_FIELD_HINT = re.compile(r'autocomplete="[^"]+"|(?:name|id|label|placeholder|aria-label|data-test)="[^"]*(?<![a-z])'
                         r"(?:prénom|prenom|nom|first.?name|last.?name|full.?name|surname|adresse|address|rue|street|"
                         r'ville|city|code postal|postal|zip)(?![a-z])[^"]*"', re.IGNORECASE)
_FIELD_VALUE = re.compile(r'valeur="([^"]*)"')
# Le nom d'un champ de nom ou d'adresse (name, id, libellé...), pour cacher sa valeur sur la capture d'écran
PERSONAL_FIELD = re.compile(r"(?<![a-z])(?:prénom|prenom|nom|first.?name|last.?name|full.?name|surname|adresse|address|"
                            r"rue|street|ville|city|code postal|postal|zip)(?![a-z])", re.IGNORECASE)


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _luhn(value: str) -> bool:
    """Contrôle de Luhn, celui de tous les numéros de carte bancaire."""
    digits = [int(d) for d in _digits(value)]
    total = 0
    for position, digit in enumerate(reversed(digits)):
        if position % 2:
            digit = digit * 2 - 9 if digit > 4 else digit * 2
        total += digit
    return len(digits) >= 13 and total % 10 == 0


def _iban(value: str) -> bool:
    """Clé de l'IBAN : les 4 premiers caractères passent à la fin, les lettres deviennent des nombres, modulo 97."""
    compact = value.replace(" ", "").upper()
    if not 15 <= len(compact) <= 34:
        return False
    moved = compact[4:] + compact[:4]
    return int("".join(str(int(ch, 36)) for ch in moved)) % 97 == 1


def _phone(value: str) -> str:
    """Forme comparable d'un numéro : +33 6 12... et 06 12... désignent la même ligne."""
    digits = _digits(value.replace("(0)", ""))
    return "0" + digits[2:] if digits.startswith("33") and len(digits) == 11 else digits


# (motif, contrôle, forme comparable, repère), dans l'ordre : un IBAN contient des chiffres qui ressemblent
# à un téléphone, un numéro de carte aussi. Un passage déjà reconnu n'est plus examiné par les motifs suivants.
_KINDS: tuple[tuple[re.Pattern, Callable[[str], bool], Callable[[str], str], str], ...] = (
    (_IBAN, _iban, lambda v: v.replace(" ", "").upper(), "[IBAN masqué]"),
    (_CARD, _luhn, _digits, "[carte masquée]"),
    (_EMAIL, lambda v: not _IMAGE.search(v), str.lower, "[e-mail masqué]"),  # logo@2x.png n'est pas une adresse
    (_PHONE, lambda v: 9 <= len(_digits(v)) <= 15, _phone, "[téléphone masqué]"),
)


def _found(text: str) -> list[tuple[int, int, str, str]]:
    """Les données personnelles du texte : (début, fin, forme comparable, repère), sans chevauchement."""
    spans: list[tuple[int, int, str, str]] = []

    def add(start: int, end: int, key: str, token: str) -> None:
        if not any(start < other_end and other_start < end for other_start, other_end, _, _ in spans):
            spans.append((start, end, key, token))

    for pattern, valid, normal, token in _KINDS:
        for match in pattern.finditer(text):
            if valid(match.group()):
                add(*match.span(), f"{token}{normal(match.group())}", token)
    for match in _ADDRESS.finditer(text):
        add(*match.span(), f"{ADDRESS_TOKEN}{match.group().lower()}", ADDRESS_TOKEN)
    for match in _GREETING.finditer(text):
        add(*match.span(1), f"{NAME_TOKEN}{match.group(1).lower()}", NAME_TOKEN)
    return sorted(spans)


def _mask_fields(text: str, known: str) -> str:
    """Dans la liste des éléments, la valeur d'un champ de nom ou d'adresse, sauf si le cas de test la donne."""
    lines = []
    for line in text.splitlines(keepends=True):
        attributes = line.split('valeur="')[0]
        if line.startswith("[") and 'valeur="' in line and _FIELD_HINT.search(attributes):
            line = _FIELD_VALUE.sub(lambda match: match.group(0) if not match.group(1) or match.group(1).lower() in known
                                    else f'valeur="{FIELD_TOKEN}"', line)
        lines.append(line)
    return "".join(lines)


def sensitive_values(text: str, known: str = "") -> list[str]:
    """Les données personnelles du texte que mask() remplacerait, telles qu'elles s'affichent.

    Avec la vision, elles sont cachées sur la capture d'écran envoyée au modèle (voir browser.vision_screenshot).
    """
    lowered = known.lower()
    allowed = {key for _, _, key, _ in _found(known)} if known else set()
    values = []
    for start, end, key, token in _found(text):
        value = text[start:end]
        if key in allowed or (token in (NAME_TOKEN, ADDRESS_TOKEN) and value.lower() in lowered):
            continue
        values.append(value)
    return list(dict.fromkeys(values))


def mask(text: str, known: str = "") -> str:
    """Remplace les données personnelles du texte par un repère, sauf celles qui figurent dans « known »."""
    if not text:
        return text
    lowered = known.lower()
    text = _mask_fields(text, lowered)
    allowed = {key for _, _, key, _ in _found(known)} if known else set()
    parts, last = [], 0
    for start, end, key, token in _found(text):
        if key in allowed:
            continue
        if token in (NAME_TOKEN, ADDRESS_TOKEN) and text[start:end].lower() in lowered:  # donné par le cas de test
            continue
        parts += [text[last:start], token]
        last = end
    return "".join(parts) + text[last:] if parts else text
