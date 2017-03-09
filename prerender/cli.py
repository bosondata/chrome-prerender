import os
import sys
import logging.config

from .prerender import app


DEBUG = os.environ.get('DEBUG', 'false').lower() in ('true', 'yes', '1')


def main():
    LOGGING = {
        'version': 1,
        'disable_existing_loggers': False,
        'root': {
            'handlers': ['console'],
            'level': 'DEBUG' if DEBUG else 'INFO'
        },
        'handlers': {
            'console': {
                'level': 'DEBUG' if DEBUG else 'INFO',
                'class': 'logging.StreamHandler',
                'formatter': 'default',
                'stream': sys.stderr,
            }
        },
        'formatters': {
            'default': {
                'format': '%(asctime)s %(levelname)-2s %(name)s.%(funcName)s:%(lineno)-5d %(message)s',  # NOQA
            },
        },
    }
    logging.config.dictConfig(LOGGING)

    app.run(host="0.0.0.0", port=8000, debug=DEBUG)


if __name__ == '__main__':
    main()
