# Documentation diagram sources

The `.dot` files in this directory are the editable sources for the architecture and
workflow diagrams embedded in the English and Chinese READMEs. They use one consistent,
high-contrast visual language so the diagrams remain legible on GitHub and their
control-flow semantics remain reviewable in ordinary code review:

- blue: browser/runtime inputs and actions;
- violet: planner and document-intelligence work;
- amber: authorization, decisions, and write-ahead safety;
- green: persisted evidence, successful outputs, and judgments;
- red: explicit blocked or failed terminal paths.

The main path runs from top to bottom. Side branches represent dependencies or optional
evaluation, and every decision edge is labelled. Keep generated assets synchronized with
their `.dot` sources; do not edit the SVG files directly.

Regenerate every SVG from the repository root with:

```bash
scripts/render_docs_diagrams.sh
```

The renderer requires Graphviz. Generated SVGs live under `docs/assets/` and are checked
by `scripts/check_docs.py` through the normal Markdown-link validation.
