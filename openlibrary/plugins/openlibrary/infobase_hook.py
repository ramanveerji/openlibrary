"""Infobase hook for openlibrary.

* Log all modified book pages as required for the search engine.
"""

from infogami.infobase import config
from infogami.infobase.logger import Logger

import datetime

root = getattr(config, 'booklogroot', 'booklog')

_logger = Logger(root)


def hook(object):
    """
    Add this hook to infobase.hooks to log all book modifications.
    """
    if object.type.key == '/type/edition':
        timestamp = datetime.datetime.utcnow()
        d = object._get_data(expand=True)
        # save some space by not expanding type
        d['type'] = {'key': '/type/edition'}
        site = object._site
        _logger.write('book', site.name, timestamp, d)

    # TODO: take care of author modifications
