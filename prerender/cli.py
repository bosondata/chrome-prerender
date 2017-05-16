import os
import signal
import faulthandler

from .app import app


DEBUG: bool = os.environ.get('DEBUG', 'false').lower() in ('true', 'yes', '1')
HOST: str = os.environ.get('HOST', '0.0.0.0')
PORT: int = int(os.environ.get('PORT', 8000))


def main() -> None:
    faulthandler.register(signal.SIGUSR1)
    app.run(host=HOST, port=PORT, debug=DEBUG, log_config=None)


if __name__ == '__main__':
    main()
