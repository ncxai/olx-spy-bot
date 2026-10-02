import logging
import urllib.parse
import re
import statistics
from typing import List, Dict, Optional, Any
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class TLSAdapter(HTTPAdapter):
    """
    Custom TLS adapter configuring cipher order to reliably bypass
    CloudFront / WAF bot restrictions on OLX.
    """
    def init_poolmanager(self, *args, **kwargs):
        context = create_urllib3_context(
            ciphers="ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20"
        )
        kwargs["ssl_context"] = context
        return super().init_poolmanager(*args, **kwargs)


def get_http_session() -> requests.Session:
    """Creates a configured requests session with headers and TLS adapter."""
    session = requests.Session()
    session.mount("https://", TLSAdapter())
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1"
    })
    return session


def extract_numeric_price(price_text: str) -> Optional[int]:
    """Extracts integer price in sums from price text if present."""
    if not price_text:
        return None
    # Remove whitespace
    clean = re.sub(r"\s+", "", price_text)
    # Check if currency is in sum/uzs (not USD or free/exchange)
    nums = re.findall(r"\d+", clean)
    if nums:
        try:
            val = int("".join(nums))
            # Filter out unrealistically small numbers (e.g. 10 y.e.)
            if val > 1000:
                return val
        except ValueError:
            pass
    return None


def parse_olx(
    query: str,
    limit: Optional[int] = None,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    location: Optional[str] = "list",
    stop_words: Optional[str] = None,
    seller_type: Optional[str] = "all",
    sort_by: Optional[str] = "created_at:desc",
) -> List[Dict[str, Any]]:
    """
    Scrapes OLX.uz search results with full filter support:
        - location (city slug)
        - price range (min_price, max_price)
        - stop words (excludes unwanted keywords)
        - seller type ('private', 'business', 'all')
        - sorting ('created_at:desc', 'filter_float_price:asc')
        - photo image extraction
        - smart deal detector (% below median price)

    Returns a list of dicts with:
        - title, price, price_num, link, photo, is_deal, deal_discount
    """
    clean_query = query.strip()
    if not clean_query:
        return []

    loc_slug = (location or "list").strip().lower()
    if loc_slug in ("all", "uzbekistan", "uz", ""):
        loc_slug = "list"

    formatted_slug = urllib.parse.quote(clean_query.replace(" ", "-"))
    url = f"https://www.olx.uz/{loc_slug}/q-{formatted_slug}/"

    params = {}
    if min_price is not None and min_price > 0:
        params["search[filter_float_price:from]"] = int(min_price)
    if max_price is not None and max_price > 0:
        params["search[filter_float_price:to]"] = int(max_price)

    if seller_type in ("private", "business"):
        params["search[private_business]"] = seller_type

    if sort_by and sort_by in ("created_at:desc", "filter_float_price:asc"):
        params["search[order]"] = sort_by

    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"

    logger.info("Fetching OLX page: %s", url)

    try:
        session = get_http_session()
        response = session.get(url, timeout=15)
        response.raise_for_status()
    except requests.exceptions.Timeout:
        logger.error("Timeout occurred while requesting OLX: %s", url)
        return []
    except requests.exceptions.ConnectionError:
        logger.error("Connection error while requesting OLX: %s", url)
        return []
    except requests.exceptions.HTTPError as e:
        logger.error("HTTP error while scraping OLX: %s", e)
        return []
    except requests.exceptions.RequestException as e:
        logger.error("Network error while scraping OLX: %s", e)
        return []
    except Exception as e:
        logger.error("Unexpected error fetching OLX page: %s", e, exc_info=True)
        return []

    # Parse stop words list
    stops = []
    if stop_words:
        stops = [s.strip().lower() for s in stop_words.split(",") if s.strip()]

    raw_items = []
    try:
        soup = BeautifulSoup(response.text, "html.parser")
        cards = soup.find_all("div", attrs={"data-cy": "l-card"})

        for card in cards:
            link_tag = card.find("a")
            if not link_tag or not link_tag.get("href"):
                continue

            raw_link = link_tag.get("href", "").strip()
            if not raw_link or "google.com" in raw_link:
                continue

            if raw_link.startswith("/"):
                link = f"https://www.olx.uz{raw_link}"
            else:
                link = raw_link

            if "?" in link and "/d/obyavlenie/" in link:
                link = link.split("?")[0]

            title_tag = card.find("h4") or card.find("h6") or link_tag
            title = title_tag.get_text(strip=True) if title_tag else "Без названия"

            # Check stop-words
            if stops and any(s in title.lower() for s in stops):
                continue

            price_tag = card.find("p", attrs={"data-testid": "ad-price"})
            if not price_tag:
                price_tag = card.find("p")
            price = price_tag.get_text(strip=True) if price_tag else "Цена не указана"
            price_num = extract_numeric_price(price)

            # Extract card photo
            photo = None
            img_tag = card.find("img")
            if img_tag:
                raw_img = img_tag.get("src") or img_tag.get("data-src")
                if raw_img and not raw_img.endswith(".svg") and "no_thumbnail" not in raw_img:
                    # Upgrade thumbnail resolution from 216x152 to higher quality
                    photo = raw_img.replace("s=216x152", "s=1000x700")

            raw_items.append({
                "title": title,
                "price": price,
                "price_num": price_num,
                "link": link,
                "photo": photo,
                "is_deal": False,
                "deal_discount": 0,
            })

    except Exception as e:
        logger.error("Failed to parse OLX HTML structure: %s", e, exc_info=True)

    # Smart Deal Detector: calculate median price among items with valid prices
    valid_prices = [item["price_num"] for item in raw_items if item["price_num"] and item["price_num"] > 10000]
    if len(valid_prices) >= 4:
        median_price = statistics.median(valid_prices)
        for item in raw_items:
            p = item["price_num"]
            # If price is at least 20% lower than median, mark as smart deal
            if p and p <= median_price * 0.8:
                discount = int(round((1 - p / median_price) * 100))
                item["is_deal"] = True
                item["deal_discount"] = discount

    if limit is not None:
        return raw_items[:limit]
    return raw_items
