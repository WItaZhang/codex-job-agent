# Product overview figure sources

The four SVGs share one standard-library generator:

```sh
uv run --locked python docs/assets/build_architecture.py
```

English and Chinese each have a wide diagram and a compact composition for
viewports up to 700 px. Text remains selectable in the SVG source; no fonts,
scripts or images are fetched externally. The READMEs select the composition
with an HTML `picture` element.

The figure follows the user experience: personal goals inform broad discovery
and tailored applications; the user's submission rules select automatic
application or review before applying; progress stays visible to the user.
Strong matches receive extra preparation, independently of the chosen review
boundary. Solid arrows show the main flow. Dashed arrows show user control and
feedback. Both compositions preserve this product-level story; engineering
details live in `docs/agent/ARCHITECTURE.md`.

Edit the generator and regenerate all four files together. Check both languages
at README width and in the narrow layout before publishing.
