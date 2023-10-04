"""
StatsD client to be used in the application to log various metrics

Based on the code in http://www.monkinetic.com/2011/02/statsd.html (pystatsd client)

"""

# statsd.py

# Steve Ivy <steveivy@gmail.com>
# http://monkinetic.com

import logging

from statsd import StatsClient

from infogami import config

pystats_logger = logging.getLogger("openlibrary.pystats")


def create_stats_client(cfg=config):
    "Create the client which can be used for logging statistics"
    logger = logging.getLogger("pystatsd.client")
    logger.addHandler(logging.StreamHandler())
    try:
        if stats_server := cfg.get("admin", {}).get("statsd_server", None):
            host, port = stats_server.rsplit(":", 1)
            return StatsClient(host, port)
        else:
            logger.critical("Couldn't find statsd_server section in config")
            return False
    except Exception as e:
        logger.critical("Couldn't create stats client - %s", e, exc_info=True)
        return False


def put(key, value, rate=1.0):
    "Records this ``value`` with the given ``key``. It is stored as a millisecond count"
    global client
    if client:
        pystats_logger.debug(f"Putting {value} as {key}")
        client.timing(key, value, rate)


def increment(key, n=1, rate=1.0):
    "Increments the value of ``key`` by ``n``"
    global client
    if client:
        pystats_logger.debug(f"Incrementing {key}")
        for _ in range(n):
            try:
                client.increment(key, sample_rate=rate)
            except AttributeError:
                client.incr(key, rate=rate)


client = create_stats_client()
