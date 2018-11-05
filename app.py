import logging

from wkqb import WKQB


def main():
    logging.basicConfig(level="DEBUG")
    wkqb = WKQB()
    wkqb.run()


if __name__ == "__main__":
    main()
