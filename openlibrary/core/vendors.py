from __future__ import annotations
import logging
import re
import time
from typing import Any, Literal

import requests
from dateutil import parser as isoparser
from infogami.utils.view import public
from paapi5_python_sdk.api.default_api import DefaultApi
from paapi5_python_sdk.get_items_request import GetItemsRequest
from paapi5_python_sdk.get_items_resource import GetItemsResource
from paapi5_python_sdk.partner_type import PartnerType
from paapi5_python_sdk.rest import ApiException
from paapi5_python_sdk.search_items_request import SearchItemsRequest

from openlibrary import accounts
from openlibrary.catalog.add_book import load
from openlibrary.core import cache
from openlibrary.core import helpers as h
from openlibrary.utils import dateutil
from openlibrary.utils.isbn import (
    isbn_10_to_isbn_13,
    isbn_13_to_isbn_10,
    normalize_isbn,
)

logger = logging.getLogger("openlibrary.vendors")

BETTERWORLDBOOKS_BASE_URL = 'https://betterworldbooks.com'
BETTERWORLDBOOKS_API_URL = (
    'https://products.betterworldbooks.com/service.aspx?IncludeAmazon=True&ItemId='
)
affiliate_server_url = None
BWB_AFFILIATE_LINK = f"http://www.anrdoezrs.net/links/{h.affiliate_id('betterworldbooks')}/type/dlg/http://www.betterworldbooks.com/-id-%s"
AMAZON_FULL_DATE_RE = re.compile(r'\d{4}-\d\d-\d\d')
ISBD_UNIT_PUNCT = ' : '  # ISBD cataloging title-unit separator punctuation


def setup(config):
    global affiliate_server_url
    affiliate_server_url = config.get('affiliate_server')


class AmazonAPI:
    """
    Amazon Product Advertising API 5.0 wrapper for Python
    See https://webservices.amazon.com/paapi5/documentation/
    """

    RESOURCES = {
        'all': [  # Hack: pulls all resource consts from GetItemsResource
            getattr(GetItemsResource, v) for v in vars(GetItemsResource) if v.isupper()
        ],
        'import': [
            GetItemsResource.IMAGES_PRIMARY_LARGE,
            GetItemsResource.ITEMINFO_BYLINEINFO,
            GetItemsResource.ITEMINFO_CONTENTINFO,
            GetItemsResource.ITEMINFO_MANUFACTUREINFO,
            GetItemsResource.ITEMINFO_PRODUCTINFO,
            GetItemsResource.ITEMINFO_TITLE,
            GetItemsResource.ITEMINFO_CLASSIFICATIONS,
            GetItemsResource.OFFERS_LISTINGS_PRICE,
        ],
        'prices': [GetItemsResource.OFFERS_LISTINGS_PRICE],
    }

    def __init__(
        self,
        key: str,
        secret: str,
        tag: str,
        host: str = 'webservices.amazon.com',
        region: str = 'us-east-1',
        throttling: float = 0.9,
    ) -> None:
        """
        Creates an instance containing your API credentials.

        :param str key: affiliate key
        :param str secret: affiliate secret
        :param str tag: affiliate string
        :param str host: which server to query
        :param str region: which regional host to query
        :param float throttling: Reduce this value to wait longer between API calls.
        """
        self.tag = tag
        self.throttling = throttling
        self.last_query_time = time.time()

        self.api = DefaultApi(
            access_key=key, secret_key=secret, host=host, region=region
        )

    def search(self, keywords):
        """Adding method to test amz searches from the CLI, unused otherwise"""
        return self.api.search_items(
            SearchItemsRequest(
                partner_tag=self.tag,
                partner_type=PartnerType.ASSOCIATES,
                keywords=keywords,
            )
        )

    def get_product(self, asin: str, serialize: bool = False, **kwargs):
        if products := self.get_products([asin], **kwargs):
            return next(self.serialize(p) if serialize else p for p in products)

    def get_products(
        self,
        asins: list | str,
        serialize: bool = False,
        marketplace: str = 'www.amazon.com',
        resources: Any | None = None,
        **kwargs,
    ) -> list | None:
        """
        :param str asins: One or more ItemIds like ASIN that uniquely identify an item
        or product URL. (Max 10) Separated by comma or as a list.
        """
        # Wait before doing the request
        wait_time = 1 / self.throttling - (time.time() - self.last_query_time)
        if wait_time > 0:
            time.sleep(wait_time)
        self.last_query_time = time.time()

        item_ids = asins if isinstance(asins, list) else [asins]
        _resources = self.RESOURCES[resources or 'import']
        try:
            request = GetItemsRequest(
                partner_tag=self.tag,
                partner_type=PartnerType.ASSOCIATES,
                marketplace=marketplace,
                item_ids=item_ids,
                resources=_resources,
                **kwargs,
            )
        except ApiException:
            logger.error(
                f"Amazon fetch failed for: {', '.join(item_ids)}", exc_info=True
            )
            return None
        response = self.api.get_items(request)
        products = (
            [p for p in response.items_result.items if p]
            if response.items_result
            else []
        )
        return products if not serialize else [self.serialize(p) for p in products]

    @staticmethod
    def serialize(product: Any) -> dict:
        """Takes a full Amazon product Advertising API returned AmazonProduct
        with multiple ResponseGroups, and extracts the data we are
        interested in.

        :param AmazonAPI product:
        :return: Amazon metadata for one product

        {
          'price': '$54.06',
          'price_amt': 5406,
          'physical_format': 'hardcover',
          'authors': [{'name': 'Guterson, David'}],
          'publish_date': 'Jan 21, 2020',
          #'dimensions': {
          #  'width': [1.7, 'Inches'],
          #  'length': [8.5, 'Inches'],
          #  'weight': [5.4, 'Pounds'],
          #  'height': [10.875, 'Inches']
          # },
          'publishers': ['Victory Belt Publishing'],
          'source_records': ['amazon:1628603976'],
          'title': 'Boundless: Upgrade Your Brain, Optimize Your Body & Defy Aging',
          'url': 'https://www.amazon.com/dp/1628603976/?tag=internetarchi-20',
          'number_of_pages': 640,
          'cover': 'https://m.media-amazon.com/images/I/51IT9MV3KqL._AC_.jpg',
          'languages': ['English']
          'edition_num': '1'
        }

        """
        if not product:
            return {}  # no match?

        item_info = getattr(product, 'item_info')
        images = getattr(product, 'images')
        edition_info = item_info and getattr(item_info, 'content_info')
        attribution = item_info and getattr(item_info, 'by_line_info')
        price = (
            getattr(product, 'offers')
            and product.offers.listings
            and product.offers.listings[0].price
        )
        brand = (
            attribution
            and getattr(attribution, 'brand')
            and getattr(attribution.brand, 'display_value')
        )
        manufacturer = (
            item_info
            and getattr(item_info, 'by_line_info')
            and getattr(item_info.by_line_info, 'manufacturer')
            and item_info.by_line_info.manufacturer.display_value
        )
        product_group = (
            item_info
            and getattr(
                item_info,
                'classifications',
            )
            and getattr(item_info.classifications, 'product_group')
            and item_info.classifications.product_group.display_value
        )
        try:
            publish_date = (
                edition_info
                and edition_info.publication_date
                and isoparser.parse(
                    edition_info.publication_date.display_value
                ).strftime('%b %d, %Y')
            )
        except Exception:
            logger.exception(f"serialize({product})")
            publish_date = None

        return {
            'url': f"https://www.amazon.com/dp/{product.asin}/?tag={h.affiliate_id('amazon')}",
            'source_records': [f'amazon:{product.asin}'],
            'isbn_10': [product.asin],
            'isbn_13': [isbn_10_to_isbn_13(product.asin)],
            'price': price and price.display_amount,
            'price_amt': price and price.amount and int(100 * price.amount),
            'title': (
                item_info
                and item_info.title
                and getattr(item_info.title, 'display_value')
            ),
            'cover': images.primary.large.url
            if images
            and images.primary
            and images.primary.large
            and images.primary.large.url
            and '/01RmK+J4pJL.' not in images.primary.large.url
            else None,
            'authors': attribution
            and [
                {'name': contrib.name}
                for contrib in attribution.contributors or []
            ],
            'publishers': list({p for p in (brand, manufacturer) if p}),
            'number_of_pages': (
                edition_info
                and edition_info.pages_count
                and edition_info.pages_count.display_value
            ),
            'edition_num': (
                edition_info
                and edition_info.edition
                and edition_info.edition.display_value
            ),
            'publish_date': publish_date,
            'product_group': product_group,
            'physical_format': (
                item_info
                and item_info.classifications
                and getattr(
                    item_info.classifications.binding, 'display_value', ''
                ).lower()
            ),
        }


@public
def get_amazon_metadata(
    id_: str,
    id_type: Literal['asin', 'isbn'] = 'isbn',
    resources: Any = None,
    retries: int = 0,
) -> dict | None:
    """Main interface to Amazon LookupItem API. Will cache results.

    :param str id_: The item id: isbn (10/13), or Amazon ASIN.
    :param str id_type: 'isbn' or 'asin'.
    :return: A single book item's metadata, or None.
    """
    return cached_get_amazon_metadata(
        id_, id_type=id_type, resources=resources, retries=retries
    )


def search_amazon(title: str = '', author: str = '') -> dict:  # type: ignore[empty-body]
    """Uses the Amazon Product Advertising API ItemSearch operation to search for
    books by author and/or title.
    https://docs.aws.amazon.com/AWSECommerceService/latest/DG/ItemSearch.html
    XXX! Broken while migrating from paapi 4.0 to 5.0
    :return: dict of "results", a list of one or more found books, with metadata.
    """
    pass


def _get_amazon_metadata(
    id_: str,
    id_type: Literal['asin', 'isbn'] = 'isbn',
    resources: Any = None,
    retries: int = 0,
    sleep_sec: float = 1,
) -> dict | None:
    """Uses the Amazon Product Advertising API ItemLookup operation to locate a
    specific book by identifier; either 'isbn' or 'asin'.
    https://webservices.amazon.com/paapi5/documentation/get-items.html

    :param str id_: The item id: isbn (10/13), or Amazon ASIN.
    :param str id_type: 'isbn' or 'asin'.
    :param Any resources: Used for AWSE Commerce Service lookup
           See https://webservices.amazon.com/paapi5/documentation/get-items.html
    :param int retries: Number of times to query affiliate server before returning None
    :param float sleep_sec: Delay time.sleep(sleep_sec) seconds before each retry
    :return: A single book item's metadata, or None.
    """
    # TMP: This is causing a bunch of duplicate imports
    return None


def split_amazon_title(full_title: str) -> tuple[str, str | None]:
    """
    Splits an Amazon title into (title, subtitle | None) and strips parenthetical
    tags.
    """

    # strip parenthetical blocks wherever they occur
    # can handle 1 level of nesting
    re_parens_strip = re.compile(r'\(([^\)\(]*|[^\(]*\([^\)]*\)[^\)]*)\)')
    full_title = re.sub(re_parens_strip, '', full_title)

    titles = full_title.split(':')
    subtitle = titles.pop().strip() if len(titles) > 1 else None
    title = ISBD_UNIT_PUNCT.join([unit.strip() for unit in titles])
    return (title, subtitle)


def clean_amazon_metadata_for_load(metadata: dict) -> dict:
    """This is a bootstrapping helper method which enables us to take the
    results of get_amazon_metadata() and create an OL book catalog record.

    :param dict metadata: Metadata representing an Amazon product.
    :return: A dict representing a book suitable for importing into OL.
    """

    # TODO: convert languages into /type/language list
    conforming_fields = [
        'title',
        'authors',
        'publish_date',
        'source_records',
        'number_of_pages',
        'publishers',
        'cover',
        'isbn_10',
        'isbn_13',
        'physical_format',
    ]
    conforming_metadata = {
        k: metadata[k]
        for k in conforming_fields
        if metadata.get(k) is not None
    }
    if source_records := metadata.get('source_records'):
        asin = source_records[0].replace('amazon:', '')
        if asin[0].isalpha():
            # Only store asin if it provides more information than ISBN
            conforming_metadata['identifiers'] = {'amazon': [asin]}
    title, subtitle = split_amazon_title(metadata['title'])
    conforming_metadata['title'] = title
    if subtitle:
        conforming_metadata['full_title'] = f'{title}{ISBD_UNIT_PUNCT}{subtitle}'
        conforming_metadata['subtitle'] = subtitle
    # Record original title if some content has been removed (i.e. parentheses)
    if metadata['title'] != conforming_metadata.get('full_title', title):
        conforming_metadata['notes'] = f"Source title: {metadata['title']}"

    return conforming_metadata


def create_edition_from_amazon_metadata(
    id_: str, id_type: Literal['asin', 'isbn'] = 'isbn', retries: int = 0
) -> str | None:
    """Fetches Amazon metadata by id from Amazon Product Advertising API, attempts to
    create OL edition from metadata, and returns the resulting edition key `/key/OL..M`
    if successful or None otherwise.

    :param str id_: The item id: isbn (10/13), or Amazon ASIN.
    :param str id_type: 'isbn' or 'asin'.
    :return: Edition key '/key/OL..M' or None
    """

    md = get_amazon_metadata(id_, id_type=id_type, retries=retries)

    if md and md.get('product_group') == 'Book':
        with accounts.RunAs('ImportBot'):
            reply = load(
                clean_amazon_metadata_for_load(md), account_key='account/ImportBot'
            )
            if reply and reply.get('success'):
                return reply['edition'].get('key')
    return None


def cached_get_amazon_metadata(*args, **kwargs):
    """If the cached data is `None`, it's likely a 503 throttling occurred on
    Amazon's side. Try again to fetch the value instead of using the
    cached value. It may 503 again, in which case the next access of
    this page will trigger another re-cache. If the Amazon API call
    succeeds but the book has no price data, then {"price": None} will
    be cached as to not trigger a re-cache (only the value `None`
    will cause re-cache)
    """

    # fetch/compose a cache controller obj for
    # "upstream.code._get_amazon_metadata"
    memoized_get_amazon_metadata = cache.memcache_memoize(
        _get_amazon_metadata,
        "upstream.code._get_amazon_metadata",
        timeout=dateutil.WEEK_SECS,
    )
    # fetch cached value from this controller
    result = memoized_get_amazon_metadata(*args, **kwargs)
    # if no result, then recache / update this controller's cached value
    return result or memoized_get_amazon_metadata.update(*args, **kwargs)[0]


@public
def get_betterworldbooks_metadata(isbn: str) -> dict | None:
    """
    :param str isbn: Unnormalisied ISBN10 or ISBN13
    :return: Metadata for a single BWB book, currently lited on their catalog, or
             an error dict.
    """

    isbn = normalize_isbn(isbn) or isbn
    if isbn is None:
        return None

    try:
        return _get_betterworldbooks_metadata(isbn)
    except Exception:
        logger.exception(f"_get_betterworldbooks_metadata({isbn})")
        return betterworldbooks_fmt(isbn)


def _get_betterworldbooks_metadata(isbn: str) -> dict | None:
    """Returns price and other metadata (currently minimal)
    for a book currently available on betterworldbooks.com

    :param str isbn: Normalised ISBN10 or ISBN13
    :return: Metadata for a single BWB book currently listed on their catalog,
            or an error dict.
    """

    url = BETTERWORLDBOOKS_API_URL + isbn
    response = requests.get(url)
    if response.status_code != requests.codes.ok:
        return {'error': response.text, 'code': response.status_code}
    text = response.text
    new_qty = re.findall("<TotalNew>([0-9]+)</TotalNew>", text)
    new_price = re.findall(r"<LowestNewPrice>\$([0-9.]+)</LowestNewPrice>", text)
    used_price = re.findall(r"<LowestUsedPrice>\$([0-9.]+)</LowestUsedPrice>", text)
    used_qty = re.findall("<TotalUsed>([0-9]+)</TotalUsed>", text)
    market_price = re.findall(
        r"<LowestMarketPrice>\$([0-9.]+)</LowestMarketPrice>", text
    )
    price = qlt = None

    if used_qty and used_qty[0] and used_qty[0] != '0':
        price = used_price[0] if used_price else ''
        qlt = 'used'

    if new_qty and new_qty[0] and new_qty[0] != '0':
        _price = new_price[0] if new_price else None
        if _price and (not price or float(_price) < float(price)):
            price = _price
            qlt = 'new'

    market_price = f'${market_price[0]}' if market_price else None
    return betterworldbooks_fmt(isbn, qlt, price, market_price)


def betterworldbooks_fmt(
    isbn: str,
    qlt: str | None = None,
    price: str | None = None,
    market_price: list[str] | None = None,
) -> dict | None:
    """Defines a standard interface for returning bwb price info

    :param str qlt: Quality of the book, e.g. "new", "used"
    :param str price: Price of the book as a decimal str, e.g. "4.28"
    """
    price_fmt = f"${price} ({qlt})" if price and qlt else None
    return {
        'url': BWB_AFFILIATE_LINK % isbn,
        'isbn': isbn,
        'market_price': market_price,
        'price': price_fmt,
        'price_amt': price,
        'qlt': qlt,
    }


cached_get_betterworldbooks_metadata = cache.memcache_memoize(
    _get_betterworldbooks_metadata,
    "upstream.code._get_betterworldbooks_metadata",
    timeout=dateutil.HALF_DAY_SECS,
)
