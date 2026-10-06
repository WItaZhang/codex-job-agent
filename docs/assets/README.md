# Architecture figure sources

The four SVGs share one standard-library generator:

```sh
uv run --locked python docs/assets/build_architecture.py
```

English and Chinese each have a wide diagram and a compact composition for
viewports up to 700 px. Text remains selectable in the SVG source; no fonts,
scripts or images are fetched externally. The READMEs select the composition
with an HTML `picture` element.

Panels distinguish personalized context, the Codex action–observation loop, and
evidence-based evaluation. Solid arrows show runtime interaction; dashed arrows
show evidence and review. Runtime sampled review and development validation are
separate. The compact composition omits secondary routes while preserving the
decision and execution responsibilities.

Edit the generator and regenerate all four files together. Check both languages
at README width and in the narrow layout before publishing.
