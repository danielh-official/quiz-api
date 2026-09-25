"""Business logic shared by the REST routes and the MCP tools."""


class NotFound(Exception):
    """Missing or not the caller's — deliberately indistinguishable."""


class Invalid(Exception):
    def __init__(self, field: str, message: str):
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")


class Forbidden(Exception):
    pass
