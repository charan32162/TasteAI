import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse


PARSER_VERSION = "schema-menu-jsonld-v1"
MAX_HTML_BYTES = 1_000_000
MAX_JSONLD_SCRIPTS = 64
MAX_JSONLD_NODES = 10_000
MAX_JSONLD_DEPTH = 32
MAX_DISH_NAME_LENGTH = 160
MAX_PRICE = Decimal("1000000")


class StructuredMenuParseError(ValueError):
    """A bounded, safe-to-display structured menu parsing failure."""


@dataclass(frozen=True)
class MenuPriceEvidence:
    amount: Decimal | None
    currency: str | None
    variant: str | None = None
    minimum: Decimal | None = None
    maximum: Decimal | None = None


@dataclass(frozen=True)
class StructuredMenuItem:
    name: str
    prices: tuple[MenuPriceEvidence, ...]
    source_url: str
    source_type: str
    evidence_status: str
    captured_at: datetime
    content_sha256: str
    parser_version: str
    description: str | None = None
    category: str | None = None
    cuisine: str | None = None
    food_type: str | None = None
    dietary_attributes: tuple[str, ...] = ()


@dataclass(frozen=True)
class StructuredMenuParseResult:
    source_url: str
    captured_at: datetime
    content_sha256: str
    parser_version: str
    items: tuple[StructuredMenuItem, ...]
    warnings: tuple[str, ...]


class _JsonLdCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.scripts: list[str] = []
        self._parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "script" or self._parts is not None:
            return
        attributes = {key.lower(): value for key, value in attrs}
        media_type = (attributes.get("type") or "").split(";", 1)[0].strip().lower()
        if media_type == "application/ld+json":
            if len(self.scripts) >= MAX_JSONLD_SCRIPTS:
                raise StructuredMenuParseError("Menu source contains too many structured-data blocks.")
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._parts is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._parts is not None:
            self.scripts.append("".join(self._parts))
            self._parts = None

    def close(self) -> None:
        super().close()
        if self._parts is not None:
            self.scripts.append("".join(self._parts))
            self._parts = None


def parse_schema_menu(
    html: str,
    *,
    source_url: str,
    captured_at: datetime | None = None,
) -> StructuredMenuParseResult:
    """Extract explicitly declared Schema.org MenuItem names and prices from supplied HTML.

    This function parses content already supplied by a caller. It never fetches URLs,
    follows links, persists source content, or infers availability or food attributes.
    """
    safe_source_url = _validated_source_url(source_url)
    if not isinstance(html, str):
        raise StructuredMenuParseError("Menu source must be text.")
    try:
        content = html.encode("utf-8")
    except UnicodeEncodeError as error:
        raise StructuredMenuParseError("Menu source contains invalid text.") from error
    if len(content) > MAX_HTML_BYTES:
        raise StructuredMenuParseError("Menu source exceeds the size limit.")

    timestamp = captured_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise StructuredMenuParseError("Capture time must include a timezone.")
    timestamp = timestamp.astimezone(timezone.utc)
    content_sha256 = hashlib.sha256(content).hexdigest()

    collector = _JsonLdCollector()
    try:
        collector.feed(html)
        collector.close()
    except StructuredMenuParseError:
        raise
    except (RecursionError, ValueError) as error:
        raise StructuredMenuParseError("Menu source HTML could not be parsed.") from error

    warnings: list[str] = []
    roots: list[Any] = []
    for script in collector.scripts:
        try:
            roots.append(json.loads(script))
        except (json.JSONDecodeError, RecursionError):
            warnings.append("malformed_jsonld")

    items: list[StructuredMenuItem] = []
    visited = 0
    stack = [(root, 0) for root in reversed(roots)]
    scheduled = len(stack)
    while stack:
        value, depth = stack.pop()
        visited += 1
        if visited > MAX_JSONLD_NODES:
            raise StructuredMenuParseError("Structured menu data exceeds the complexity limit.")
        if depth > MAX_JSONLD_DEPTH:
            raise StructuredMenuParseError("Structured menu data exceeds the nesting limit.")
        if isinstance(value, dict):
            if _has_type(value.get("@type"), "MenuItem"):
                item = _menu_item(
                    value,
                    source_url=safe_source_url,
                    captured_at=timestamp,
                    content_sha256=content_sha256,
                )
                if item is None:
                    warnings.append("invalid_menu_item")
                else:
                    items.append(item)
                    if _has_invalid_offer(value):
                        warnings.append("invalid_offer")
                    if _has_invalid_currency(value):
                        warnings.append("invalid_currency")
            children = list(value.values())
        elif isinstance(value, list):
            children = value
        else:
            continue
        scheduled += len(children)
        if scheduled > MAX_JSONLD_NODES:
            raise StructuredMenuParseError("Structured menu data exceeds the complexity limit.")
        stack.extend((child, depth + 1) for child in reversed(children))

    return StructuredMenuParseResult(
        source_url=safe_source_url,
        captured_at=timestamp,
        content_sha256=content_sha256,
        parser_version=PARSER_VERSION,
        items=tuple(items),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _validated_source_url(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise StructuredMenuParseError("Menu source URL is invalid.")
    try:
        parsed = urlparse(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise StructuredMenuParseError("Menu source URL is invalid.") from error
    if (
        parsed.scheme.lower() != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        raise StructuredMenuParseError("Menu source URL must be an HTTPS URL on the standard port.")
    normalized_host = host.lower().rstrip(".")
    if (
        normalized_host == "localhost"
        or normalized_host.endswith((".localhost", ".local", ".internal"))
    ):
        raise StructuredMenuParseError("Menu source URL must use a public hostname.")
    try:
        address = ipaddress.ip_address(normalized_host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise StructuredMenuParseError("Menu source URL must use a public hostname.")
    return value


def _has_type(value: Any, expected: str) -> bool:
    values = value if isinstance(value, list) else [value]
    for item in values:
        if not isinstance(item, str):
            continue
        local_name = re.split(r"[/#:]", item.rstrip("/#"))[-1]
        if local_name.casefold() == expected.casefold():
            return True
    return False


def _menu_item(
    value: dict[str, Any],
    *,
    source_url: str,
    captured_at: datetime,
    content_sha256: str,
) -> StructuredMenuItem | None:
    raw_name = value.get("name")
    if not isinstance(raw_name, str):
        return None
    name = " ".join(raw_name.split())
    if not name or len(name) > MAX_DISH_NAME_LENGTH:
        return None
    prices = tuple(
        price
        for offer in _offers(value)
        if isinstance(offer, dict)
        if (price := _offer_price(offer)) is not None
    )
    return StructuredMenuItem(
        name=name,
        prices=prices,
        source_url=source_url,
        source_type="schema.org JSON-LD",
        evidence_status="supported",
        captured_at=captured_at,
        content_sha256=content_sha256,
        parser_version=PARSER_VERSION,
        description=_bounded_explicit_text(value.get("description"), 500),
        category=_explicit_category(value),
        cuisine=_bounded_explicit_text(
            value.get("recipeCuisine", value.get("cuisine")), 120
        ),
        food_type=_bounded_explicit_text(value.get("foodType"), 120),
        dietary_attributes=_explicit_dietary_attributes(value.get("suitableForDiet")),
    )


def _offer_price(offer: dict[str, Any]) -> MenuPriceEvidence | None:
    raw_price = offer.get("price")
    raw_currency = offer.get("priceCurrency")
    specification = offer.get("priceSpecification")
    if raw_price is None and isinstance(specification, dict):
        raw_price = specification.get("price")
        if raw_currency is None:
            raw_currency = specification.get("priceCurrency")
    amount = _price_decimal(raw_price)
    minimum = _price_decimal(specification.get("minPrice")) if isinstance(specification, dict) else None
    maximum = _price_decimal(specification.get("maxPrice")) if isinstance(specification, dict) else None
    if raw_price is not None and amount is None:
        return None
    if amount is None and minimum is None and maximum is None:
        return None
    if minimum is not None and maximum is not None and minimum > maximum:
        return None
    currency = raw_currency.strip().upper() if isinstance(raw_currency, str) else None
    if currency and not re.fullmatch(r"[A-Z]{3}", currency):
        currency = None
    variant = _bounded_explicit_text(offer.get("name"), 80)
    if variant is None and isinstance(specification, dict):
        variant = _bounded_explicit_text(specification.get("name"), 80)
    return MenuPriceEvidence(
        amount=amount,
        currency=currency,
        variant=variant,
        minimum=minimum,
        maximum=maximum,
    )


def _price_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    price_text = str(value).strip()
    if not price_text or len(price_text) > 32:
        return None
    try:
        amount = Decimal(price_text)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount <= 0 or amount > MAX_PRICE:
        return None
    return amount


def _bounded_explicit_text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    return normalized if normalized and len(normalized) <= limit else None


def _explicit_category(value: dict[str, Any]) -> str | None:
    category = _bounded_explicit_text(value.get("category"), 120)
    if category:
        return category
    section = value.get("menuSection")
    if isinstance(section, dict):
        return _bounded_explicit_text(section.get("name"), 120)
    return _bounded_explicit_text(section, 120)


def _explicit_dietary_attributes(value: Any) -> tuple[str, ...]:
    known = {
        "glutenfreediet": "gluten_free",
        "lowcaloriediet": "low_calorie",
        "lowfatdiet": "low_fat",
        "lowsodiumdiet": "low_sodium",
        "vegan diet": "vegan",
        "vegandiet": "vegan",
        "vegetariandiet": "vegetarian",
    }
    raw_values = value if isinstance(value, list) else [value]
    attributes = set()
    for item in raw_values:
        if isinstance(item, dict):
            item = item.get("@id") or item.get("name")
        if not isinstance(item, str):
            continue
        local_name = re.split(r"[/#:]", item.rstrip("/#"))[-1].casefold()
        normalized = known.get(local_name)
        if normalized:
            attributes.add(normalized)
    return tuple(sorted(attributes))


def _offers(item: dict[str, Any]) -> list[Any]:
    raw_offers = item.get("offers", [])
    return raw_offers if isinstance(raw_offers, list) else [raw_offers]


def _has_invalid_offer(item: dict[str, Any]) -> bool:
    for offer in _offers(item):
        if not isinstance(offer, dict):
            continue
        specification = offer.get("priceSpecification")
        has_price = "price" in offer or (
            isinstance(specification, dict)
            and any(key in specification for key in ("price", "minPrice", "maxPrice"))
        )
        if has_price and _offer_price(offer) is None:
            return True
    return False


def _has_invalid_currency(item: dict[str, Any]) -> bool:
    for offer in _offers(item):
        if not isinstance(offer, dict) or _offer_price(offer) is None:
            continue
        specification = offer.get("priceSpecification")
        currency = offer.get("priceCurrency")
        if currency is None and isinstance(specification, dict):
            currency = specification.get("priceCurrency")
        if currency is not None and (
            not isinstance(currency, str)
            or not re.fullmatch(r"[A-Z]{3}", currency.strip().upper())
        ):
            return True
    return False
