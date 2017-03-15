import os

from .app import app


DEBUG = os.environ.get('DEBUG', 'false').lower() in ('true', 'yes', '1')
HOST = os.environ.get('HOST', '0.0.0.0')
PORT = int(os.environ.get('PORT', 8000))


def main():
    app.run(host=HOST, port=PORT, debug=DEBUG)


if __name__ == '__main__':
    main()
