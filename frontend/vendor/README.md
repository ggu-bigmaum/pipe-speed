# Browser libraries

Pinned official npm release distributions, fetched with HTTPS and verified against npm's SHA-512 integrity values. These files are served locally; users do not need npm on the server.

- OpenLayers 10.11.0: `ol-10.11.0.js`, `ol-10.11.0.css`, BSD-2-Clause (`ol-LICENSE.txt`). https://openlayers.org/download/
- GeoTIFF.js 3.0.4: `geotiff-3.0.4.js`, MIT (`geotiff-LICENSE.txt`). https://geotiffjs.github.io/geotiff.js/

Load GeoTIFF.js before the OpenLayers full build. OpenLayers' GeoTIFF source uses the global GeoTIFF distribution.
