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

Limite : avec la vision, la capture d'écran envoyée au modèle n'est pas masquée.
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
    for pattern, valid, normal, token in _KINDS:
        for match in pattern.finditer(text):
            start, end = match.span()
            if any(start < other_end and other_start < end for other_start, other_end, _, _ in spans):
                continue
            if valid(match.group()):
                spans.append((start, end, f"{token}{normal(match.group())}", token))
    return sorted(spans)


def mask(text: str, known: str = "") -> str:
    """Remplace les données personnelles du texte par un repère, sauf celles qui figurent dans « known »."""
    if not text:
        return text
    allowed = {key for _, _, key, _ in _found(known)} if known else set()
    parts, last = [], 0
    for start, end, key, token in _found(text):
        if key in allowed:
            continue
        parts += [text[last:start], token]
        last = end
    return "".join(parts) + text[last:] if parts else text
