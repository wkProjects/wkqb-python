import re


class REMatcher:
    def __init__(self, matchstring):
        self.matchstring = matchstring
        self.research = None

    def search(self, regexp):
        self.research = re.search(regexp, self.matchstring)
        return bool(self.research)

    def group(self, i):
        return self.research.group(i)
