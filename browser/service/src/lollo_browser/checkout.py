"""Checkout recognition (design decision 9): a guess, and advisory.

Before an agent's click, or Enter in a form field, the target is scored on
three signals: its accessible name and text against phrases that place an
order in English, German, French, Spanish, Italian and Portuguese; its form's fields
(card number, expiry, security code); and the page URL (checkout, payment,
order...). A score of THRESHOLD or more is a checkout. A page can name its
button anything, so this can miss: the limits that hold are the vault's
approval for a vault card, the bank's confirmation and the card's own limits.

During a take-over, the service cannot see what the person clicks, so a
POST that the page sends from a checkout-looking page, or to a checkout-looking
URL, counts as a checkout submitted (`submit_looks_like_checkout`)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

THRESHOLD = 3

# Names that place an order by themselves (score 3). Matched on folded text
# (lower case, no accents, single spaces), as whole words.
_STRONG = [
    # English
    r"place (?:my |your |the )?order(?: (?:and )?pay| now)?",  # "&" folds to a space
    r"complete (?:my |your |the )?(?:order|purchase|payment|checkout)",
    r"confirm (?:and pay|(?:my |your |the )?(?:order|purchase|payment))",
    r"submit (?:my |your |the )?(?:order|payment)",
    r"buy (?:it )?now",
    r"pay now",
    r"pay(?: and order| securely)?",
    r"pay with .*",
    r"order (?:now|and pay)",
    r"purchase(?: now)?",
    r"checkout now",
    # German
    # The wording §312j BGB asks of the final button, and its usual variants.
    r"(?:jetzt )?(?:zahlungspflichtig|kostenpflichtig) (?:bestellen|kaufen|buchen|abschliessen)",
    r"kaufen",
    r"jetzt kaufen",
    r"(?:jetzt )?bestellen(?: und bezahlen)?",
    r"bestellung (?:abschicken|absenden|bestatigen|abschliessen)",
    r"(?:jetzt )?(?:bezahlen|zahlen)",
    r"kauf abschliessen",
    # French
    r"commander(?: et payer| maintenant)?",
    r"(?:valider|confirmer|passer) (?:la |ma |votre )?commande",
    r"payer(?: maintenant| la commande)?",
    r"acheter(?: maintenant)?",
    r"finaliser (?:l'achat|la commande|mon achat)",
    # Spanish
    r"(?:realizar|confirmar|finalizar|tramitar) (?:el |mi )?pedido",
    r"(?:finalizar|confirmar) (?:la |mi )?compra",
    r"comprar (?:ahora|ya)",
    r"pagar(?: ahora| y finalizar)?",
    # Italian
    r"(?:conferma|completa|invia) (?:l'|il |l )?ordine",
    r"(?:acquista|compra|ordina) e paga",
    r"(?:acquista|compra)(?: ora| adesso| subito)?",
    r"(?:paga|pagare)(?: ora| adesso)?",
    r"ordina(?: ora| adesso)?",
    r"concludi (?:l'|l )?(?:acquisto|ordine)",
    r"procedi (?:al pagamento|all'acquisto)",
    # Portuguese
    r"(?:confirmar|finalizar|concluir|efetuar|efectuar|fazer|submeter) (?:a |o |a minha |o meu )?encomenda",
    r"encomendar(?: agora| ja)?",
    r"(?:finalizar|concluir|efetuar|efectuar|confirmar) (?:a |o )?(?:compra|pagamento|pedido)",
    r"(?:fazer|efetuar|efectuar) (?:o )?pedido",
    r"comprar (?:agora|ja)",
    r"pagar (?:agora|encomenda)",
]
_STRONG_RE = re.compile(r"^(?:" + "|".join(_STRONG) + r")$")
# A bare "buy" (score 2): on many shops it only adds to the cart ("COMPRAR" on
# a product page), so it counts as a checkout only with a checkout page or card
# fields as well.
_BUY_RE = re.compile(r"^comprar$")
# Words that point at a checkout without placing one (score 1).
_WEAK_RE = re.compile(
    r"\b(?:order|checkout|purchase|payment|pay|bestell\w*|kasse|bezahl\w*|zahlung\w*|commande|paiement|"
    r"pedido|pago|compra|ordine|pagamento|acquisto|encomenda\w*)\b"
)
# Form fields of a payment (score 2).
_CARD_FIELD_RE = re.compile(
    r"\b(?:cc-(?:number|exp|csc|exp-month|exp-year)|card.?number|cardnumber|cvv|cvc|csc|security.?code|"
    r"kartennummer|prufnummer|numero de (?:la )?carte|cryptogramme|numero de tarjeta|numero (?:della )?carta|iban)\b"
)
# URL path words (score 1).
_URL_RE = re.compile(
    r"(?<![a-z])(?:checkout|pay|payment|place.?order|order|kasse|bestellung|bestellen|commande|paiement|pago|"
    r"pedido|cassa|ordine|pagamento|encomenda)(?![a-z])"
)


# An amount in a button's name ("Pay 34.90 EUR"), dropped before matching.
_AMOUNT_RE = re.compile(r"(?:[€$£]\s*)?\d[\d.,]*\s*(?:eur|euro|usd|gbp|chf|€|\$|£)?")


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower().replace("ß", "ss")
    text = re.sub(r"[’`]", "'", text)
    return " ".join(re.sub(r"[^\w'.,€$£ -]+", " ", text).split())


@dataclass
class Features:
    """What the page says about the target, gathered by FEATURES_JS."""

    names: list[str] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)
    url: str = ""

    @classmethod
    def from_page(cls, data: Any, descriptor: str, url: str) -> Features:
        data = data if isinstance(data, dict) else {}
        names = [str(n) for n in data.get("names", []) if isinstance(n, str)][:10]
        m = re.search(r'"(.*)"', descriptor or "")
        if m:
            names.append(m.group(1))
        fields = [str(f) for f in data.get("fields", []) if isinstance(f, str)][:200]
        return cls(names=names, fields=fields, url=url)


@dataclass(frozen=True)
class Verdict:
    score: int
    reasons: tuple[str, ...]

    @property
    def is_checkout(self) -> bool:
        return self.score >= THRESHOLD


def _url_path(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    return fold(parts.path + " " + parts.query).replace(" ", "-")


def recognise(features: Features) -> Verdict:
    score, reasons = 0, []
    names = [_AMOUNT_RE.sub(" ", fold(n)) for n in features.names if n and n.strip()]
    names = [" ".join(n.split()).strip(" -:.,()") for n in names]
    if any(_STRONG_RE.match(n[:80]) for n in names):
        score += 3
        reasons.append("the button places an order")
    elif any(_BUY_RE.match(n) for n in names):
        score += 2
        reasons.append("the button buys, which may only add to the cart")
    elif any(_WEAK_RE.search(n) for n in names):
        score += 1
        reasons.append("the button mentions an order or payment")
    if any(_CARD_FIELD_RE.search(fold(f)) for f in features.fields):
        score += 2
        reasons.append("the form asks for card details")
    if _URL_RE.search(_url_path(features.url)):
        score += 1
        reasons.append("the page is a checkout page")
    return Verdict(score, tuple(reasons))


def submit_looks_like_checkout(page_url: str, request_url: str, page_has_card_fields: bool) -> bool:
    """A POST the page sent while the person had control."""
    on_checkout = bool(_URL_RE.search(_url_path(page_url)))
    to_checkout = bool(_URL_RE.search(_url_path(request_url)))
    return (on_checkout and (to_checkout or page_has_card_fields)) or (to_checkout and page_has_card_fields)


# Gathers the target's names and its form's fields, in the page's own world:
# the page could lie, and recognition is a guess anyway. `key` is null for a
# click, or the key pressed in a field (Enter submits its form, through the
# form's first submit button).
FEATURES_JS = """(el, key) => {
  const names = [];
  const add = (v) => { if (v && typeof v === 'string') names.push(v.slice(0, 200)); };
  let target = el;
  const form = el.form || (el.closest ? el.closest('form') : null);
  if (key !== null && form) {
    target = form.querySelector('button:not([type]), button[type=submit], input[type=submit], input[type=image]');
  }
  if (target) {
    add(target.innerText); add(target.value); add(target.getAttribute && target.getAttribute('aria-label'));
    add(target.getAttribute && target.getAttribute('title'));
    const owner = target.closest ? target.closest('button, a, [role=button]') : null;
    if (owner && owner !== target) add(owner.innerText);
  }
  const fields = [];
  const scope = form || (target && target.closest ? target.closest('form') : null);
  if (scope) {
    for (const f of Array.from(scope.querySelectorAll('input, select, textarea')).slice(0, 200)) {
      fields.push([f.name, f.id, f.getAttribute('autocomplete'), f.getAttribute('aria-label'), f.placeholder]
        .filter(Boolean).join(' ').slice(0, 300));
    }
  }
  return {names, fields};
}"""

# Whether a page holds card fields anywhere (for take-over submits).
CARD_FIELDS_JS = """() => Array.from(document.querySelectorAll('input, select')).slice(0, 300).map(f =>
  [f.name, f.id, f.getAttribute('autocomplete'), f.getAttribute('aria-label'), f.placeholder]
    .filter(Boolean).join(' ').slice(0, 300))"""


def has_card_fields(fields: Any) -> bool:
    return isinstance(fields, list) and any(isinstance(f, str) and _CARD_FIELD_RE.search(fold(f)) for f in fields)
