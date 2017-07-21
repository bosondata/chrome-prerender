import re
from functools import reduce

_SCRIPT_TAG_RE = re.compile(r'<script(.*?)>([\S\s]*?)<\/script>', re.I)
_META_FRAGMENT_TAG_RE = re.compile(r'<meta[^<>]*name=[\'"]fragment[\'"][^<>]*content=[\'"]\![\'"][^<>]*>', re.I)


def apply_filters(html: str, filters: list) -> str:
    return reduce(lambda x, y: y(x), filters, html)

def remove_script_tags(html: str) -> str:
    def _repl(match):
        if 'application/ld+json' not in match.group(1):
            return ''
        return match.group(0)

    return _SCRIPT_TAG_RE.sub(_repl, html)

def remove_meta_fragment_tag(html: str) -> str:
    return _META_FRAGMENT_TAG_RE.sub('', html)
