# prerender

Render JavaScript-rendered page as HTML using headless Chrome

**Currently only the `window.prerenderReady` inspection is supported.**

## Install Chrome Headless

Chrome Headless broweser can be easily installed using Docker:

```bash
$ docker pull yukinying/chrome-headless
```

## Start Chrome Headless

```bash
$ docker run -i -t --shm-size=256m --rm --name=chrome-headless -p=127.0.0.1:9222:9222 yukinying/chrome-headless "about:blank"
```

Or you can download a Headless Chrome binary for Ubuntu 16.04 from GitHub release and run it:

```bash
$ wget https://github.com/bosondata/prerender/releases/download/v0.3.0/ChromeHeadless.ubuntu-16.04-x86_64.tar.gz
$ tar zxvf ChromeHeadless.ubuntu-16.04-x86_64.tar.gz
$ cd ChromeHeadless
$ ./headless_shell --remote-debugging-port=9222 --disable-gpu "about:blank"
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

## License

MIT
