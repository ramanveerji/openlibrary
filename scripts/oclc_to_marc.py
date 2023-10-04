"""Find marc record URL from oclc number.

Usage: python oclc_to_marc.py oclc_1 oclc_2
"""
import requests

import urllib


root = "https://openlibrary.org"


def wget(path):
    return requests.get(root + path).json()


def find_marc_url(d):
    if d.get('source_records'):
        return d['source_records'][0]

    if result := wget(
        '%s.json?m=history&offset=%d' % (d['key'], d['revision'] - 3)
    ):
        return result[-1]['machine_comment'] or ""
    else:
        return ""


def main(oclc):
    query = urllib.parse.urlencode(
        {'type': '/type/edition', 'oclc_numbers': oclc, '*': ''}
    )
    result = wget(f'/query.json?{query}')

    for d in result:
        print("\t".join([oclc, d['key'], find_marc_url(d)]))


if __name__ == "__main__":
    import sys

    if len(sys.argv) == 1 or "-h" in sys.argv or "--help" in sys.argv:
        print(__doc__, file=sys.stderr)
    else:
        for oclc in sys.argv[1:]:
            main(oclc)
