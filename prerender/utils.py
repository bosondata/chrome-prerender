import re

_SCRIPT_TAG_RE = re.compile(r'<script(.*?)>([\S\s]*?)<\/script>', re.I)


def remove_script_tags(html: str) -> str:
    def _repl(match):
        if 'application/ld+json' not in match.group(1):
            return ''
        return match.group(0)

    return _SCRIPT_TAG_RE.sub(_repl, html)
