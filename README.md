# prerender

Render JavaScript-rendered page as HTML/PDF/mhtml/png/jpeg using headless Chrome

## Install Chrome

Headless mode is supported in Chrome unstable/dev channel, you should be able to install it via:

https://www.google.com/chrome/browser/beta.html?platform=linux

## Start Chrome Headless

```bash
$ google-chrome-unstable --headless --remote-debugging-port=9222 --disable-gpu "about:blank"
```

## Install Prerender

```bash
$ pip install -U prerender
```

## Start Prerender

As standalone application:

```bash
$ prerender
```

To run it under gunicorn:

```bash
$ gunicorn --bind 0.0.0.0:3000 --worker-class sanic.worker.GunicornWorker prerender.app:app
```

## How does it work

Say you deployed Prerender under `http://prerender.example.com:8000`, to render `http://example.com` you can do:

```bash
$ # render HTML
$ curl http://prerender.example.com:8000/http://example.com
$ curl http://prerender.example.com:8000/html/http://example.com
$ # render mhtml
$ curl http://prerender.example.com:8000/mhtml/http://example.com
$ # render PDF
$ curl http://prerender.example.com:8000/pdf/http://example.com
$ # render png
$ curl http://prerender.example.com:8000/png/http://example.com
$ # render jpeg
$ curl http://prerender.example.com:8000/jpeg/http://example.com
```

## Configuration

Settings are mostly configured by environment variables.

| ENV                        | default value    | description                                         |
|----------------------------|------------------|-----------------------------------------------------|
| HOST                       | 0.0.0.0          | Prerender listen host                               |
| PORT                       | 8000             | Prerender listen port                               |
| DEBUG                      | false            | Toggle debug mode                                   |
| PRERENDER_TIMEOUT          | 30               | renderring timeout                                  |
| CONCURRENCY                | 2 * CPU count    | Chrome pages count                                  |
| MAX_ITERATIONS             | 200              | Restart Chrome page after rendering this many pages |
| CHROME_HOST                | localhost        | Chrome remote debugging host                        |
| CHROME_PORT                | 9222             | Chrome remote debugging port                        |
| USER_AGENT                 |                  | Chrome User Agent                                   |
| ALLOWED_DOMAINS            |                  | Domains allowed for renderring, comma seperated     |
| CACHE_BACKEND              | dummy            | Cache backend, `dummy`, `disk`, `s3`                |
| CACHE_LIVE_TIME            | 3600             | Disk cache live seconds                             |
| CACHE_ROOT_DIR             | /tmp/prerender   | Disk cache root directory                           |
| S3_SERVER                  | s3.amazonaws.com | S3 server address                                   |
| S3_ACCESS_KEY              |                  | S3 access key                                       |
| S3_SECRET_KEY              |                  | S3 secret key                                       |
| S3_REGION                  |                  | S3 region                                           |
| S3_BUCKET                  | prerender        | S3 bucket name                                      |
| SENTRY_DSN                 |                  | Sentry DSN, for exception monitoring                |

## Configure client

Please view the original NodeJs version [prerender](https://github.com/prerender/prerender#official-middleware) README.

## License

MIT
