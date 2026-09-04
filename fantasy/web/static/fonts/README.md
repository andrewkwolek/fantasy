# Fonts

Inter and Inter Tight, variable (weight-axis) `woff2`, latin and latin-ext
subsets — the exact files Google Fonts serves, vendored here rather than
linked.

Self-hosted on purpose: the site is meant to work offline from a local
SQLite database, and the static export ships with `noindex` /
`no-referrer`, so pulling a stylesheet and four font files from a CDN on
every page view would leak every visitor's IP for no benefit.

`app.css` references them with **relative** `url("fonts/…")` paths. The
static exporter rewrites site-absolute `href`/`src` in HTML but never
`url()` inside CSS, so a relative path is what keeps them resolving when
the site is published under a sub-path such as `/fantasy`.

Licensed under the SIL Open Font License 1.1 — see
<https://github.com/rsms/inter/blob/master/LICENSE.txt>. The OFL permits
redistribution alongside the site; the fonts are unmodified.

Refresh them with:

    curl -H 'User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 \
      (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36' \
      'https://fonts.googleapis.com/css2?family=Inter:wght@400..700&family=Inter+Tight:wght@500..700&display=swap'

and pull the `latin` and `latin-ext` `woff2` URLs out of the response.
