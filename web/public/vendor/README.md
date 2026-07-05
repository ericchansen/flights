# Vendored front-end libraries

These files are checked in so the fare map runs **offline and build-free** (see
`PRODUCT.md`): the deploy uploads `web/public/` as-is with no bundler or CDN.
They are third-party, minified/generated assets — do not hand-edit them, and keep
them out of the ESLint/Prettier passes (they are already ignored).

To update one, replace the file with the corresponding release artifact and bump
the version below.

## JavaScript libraries

| File | Project | Version | License | Source |
| --- | --- | --- | --- | --- |
| `d3.min.js` | D3 | 7.9.0 | ISC | <https://github.com/d3/d3/releases/tag/v7.9.0> |
| `topojson-client.min.js` | topojson-client | 3.1.0 | ISC | <https://github.com/topojson/topojson-client/releases/tag/v3.1.0> |

Versions are taken verbatim from each file's banner comment
(`// https://d3js.org v7.9.0 …`, `// …/topojson-client v3.1.0 …`).
Both are © Mike Bostock, released under the ISC License.

## Geographic atlases (TopoJSON)

| File | Package | Resolution | Package license | Underlying data |
| --- | --- | --- | --- | --- |
| `countries-50m.json` | [world-atlas](https://github.com/topojson/world-atlas) | 1:50m | ISC | [Natural Earth](https://www.naturalearthdata.com/) — public domain |
| `states-10m.json` | [us-atlas](https://github.com/topojson/us-atlas) | 1:10m | ISC | US Census Bureau (TIGER/Line) — public domain |

These are the standard pre-built atlases published by the TopoJSON project
(© Mike Bostock, ISC). The map geometry they contain is derived from
public-domain government/Natural Earth datasets. `app.js` consumes them via
`topojson.feature(...)` to draw land, state borders, and the country outline.

## License note

The ISC License requires the copyright and permission notice to be preserved.
Each `.min.js` keeps its original banner comment, which satisfies this. The
atlas JSON files carry no embedded notice; this README records their provenance
and license.
