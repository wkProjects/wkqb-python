import re
from time import time, localtime, strftime
from typing import Dict

from commands import Command


class Incoming:
    def __init__(self, user, message):
        self.time = time()
        self.user = user
        self.message = message
        self.type = None
        self.from_guest = True if "(Gast)" in self.user else False

    def __str__(self):
        return "(" + strftime("%a, %d %b %Y %H:%M:%S", localtime(self.time)) + ") " + self.user + ": " + self.message

    def has_command(self):
        return re.match(r'^!\w+', self.message)

    def get_command(self):
        m = re.match(r'^!(\w+)(?: (.+))?', self.message)
        command = Command(m.group(1))
        command.param_string = m.group(2) if m.group(2) is not None else ""
        return command


class Outgoing:
    def __init__(self, message, replacements=None):
        if replacements is None:
            replacements = {}
        self.message = message
        self.message = self.replace_tokens(message, replacements)

    def replace_tokens(self, message: str, replacements: Dict[str, any]):
        for k, v in replacements.items():
            pattern = re.compile(re.escape("%" + k + "%"), re.IGNORECASE)
            message = pattern.sub(str(v), message)
        return message
