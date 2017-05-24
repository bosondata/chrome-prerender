
class PrerenderException(Exception):
    pass


class TemporaryBrowserFailure(PrerenderException):
    pass


class TooManyResponseError(PrerenderException):
    pass
