import os
import logging
import sys

from wkqb import WKQB


def main():
    logging.basicConfig(level=os.environ.get("LOGLEVEL") or "INFO", format='%(asctime)s %(levelname)s [%(threadName)s] %(name)s : %(message)s')
    #logging.getLogger("urllib3.connectionpool").setLevel(logging.INFO)
    #logging.getLogger("schedule").setLevel(logging.INFO)
    try:
        wkqb = WKQB()
        wkqb.run()
    except Exception as e:
        logging.error(e)
        sys.exit(1)


if __name__ == "__main__":
    main()
