# prerender

Render JavaScript-rendered page as HTML using headless Chrome

## Install Chrome Headless

Chrome Headless broweser can be easily installed using Docker:

```bash
$ docker pull yukinying/chrome-headless
```

## Start Chrome Headless

```bash
$ docker run -i -t --shm-size=256m --rm --name=chrome-headless -p=127.0.0.1:9222:9222 yukinying/chrome-headless "about:blank"
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

To run it under gunicorn, first install some dependencies:

```bash
$ pip install sanic-gunicorn
```

Then:

```bash
$ gunicorn --bind 0.0.0.0:3000 --worker-class sanic_gunicorn.Worker prerender.app:app
```

## Configure client

Please view the original NodeJs version [prerender](https://github.com/prerender/prerender#official-middleware) README.
