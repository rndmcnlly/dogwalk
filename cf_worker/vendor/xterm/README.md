# Vendored xterm.js

Browser builds for the Human Takeover terminal page (`src/terminal_page.ts`), served from
`/pair/assets/` under content-hashed names. Each file is the package's UMD build (or CSS) copied
verbatim with the trailing `//# sourceMappingURL` line removed and `.txt` appended, so the
wrangler Text rule bundles it as a string instead of a Worker module.

| File | Package | Version | License |
| --- | --- | --- | --- |
| `xterm.js.txt`, `xterm.css.txt` | `@xterm/xterm` (`lib/xterm.js`, `css/xterm.css`) | 6.0.0 | MIT |
| `addon-fit.js.txt` | `@xterm/addon-fit` | 0.11.0 | MIT |
| `addon-webgl.js.txt` | `@xterm/addon-webgl` | 0.19.0 | MIT |
| `addon-unicode11.js.txt` | `@xterm/addon-unicode11` | 0.9.0 | MIT |
| `addon-web-links.js.txt` | `@xterm/addon-web-links` | 0.12.0 | MIT |
| `addon-clipboard.js.txt` | `@xterm/addon-clipboard` (bundles `js-base64`, BSD-3-Clause) | 0.2.0 | MIT |

`LICENSE.txt` is the `@xterm/xterm` license. Each addon ships the same MIT text with its own
copyright year, all held by "The xterm.js authors" (https://github.com/xtermjs/xterm.js).

To update: bump the exact versions in `package.json`, `npm install`, then from `cf_worker/`:

```sh
for f in xterm/lib/xterm.js xterm/css/xterm.css addon-fit/lib/addon-fit.js \
  addon-webgl/lib/addon-webgl.js addon-unicode11/lib/addon-unicode11.js \
  addon-web-links/lib/addon-web-links.js addon-clipboard/lib/addon-clipboard.js; do
  sed '/^\/\/# sourceMappingURL=/d' node_modules/@xterm/$f > vendor/xterm/$(basename $f).txt
done
cp node_modules/@xterm/xterm/LICENSE vendor/xterm/LICENSE.txt
```

Asset URLs carry a hash of the file contents, so a new copy busts caches without a manual bump.
