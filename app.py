import logging

from wkqb import WKQB


def main():
    logging.basicConfig(level="DEBUG", format='%(asctime)s %(levelname)s [%(threadName)s] %(name)s : %(message)s')
    logging.getLogger("urllib3.connectionpool").setLevel(logging.INFO)
    logging.getLogger("schedule").setLevel(logging.INFO)
    wkqb = WKQB()
    wkqb.run()


if __name__ == "__main__":
    main()
