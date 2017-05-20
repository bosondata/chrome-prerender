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

## Configure client

Please view the original NodeJs version [prerender](https://github.com/prerender/prerender#official-middleware) README.

## License

MIT
