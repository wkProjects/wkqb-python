import re
from time import time, localtime, strftime


class Message:
    def __init__(self, user, message):
        self.time = time()
        self.user = user
        self.message = message
        self.type = None

    def __str__(self):
        return "(" + strftime("%a, %d %b %Y %H:%M:%S", localtime(self.time)) + ") " + self.user + ": " + self.message

    def has_command(self):
        return re.match(r'^!\w+', self.message)

    def get_command(self):
        command = {}
        m = re.match(r'^!(\w+)(?: (.+))?', self.message)
        command["command"] = m.group(1)
        command["paramstring"] = m.group(2)
        command["params"] = m.group(2).split(" ") if command["paramstring"] is not None else []
        return command
