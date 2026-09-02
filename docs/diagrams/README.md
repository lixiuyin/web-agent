# Documentation diagram sources

The `.dot` files in this directory are the editable sources for the architecture and
workflow diagrams embedded in the English and Chinese READMEs. They intentionally use a
flat, high-contrast visual language so the diagrams remain legible on GitHub and their
control-flow semantics remain reviewable in ordinary code review.

Regenerate every SVG from the repository root with:

```bash
scripts/render_docs_diagrams.sh
```

The renderer requires Graphviz. Generated SVGs live under `docs/assets/` and are checked
by `scripts/check_docs.py` through the normal Markdown-link validation.
