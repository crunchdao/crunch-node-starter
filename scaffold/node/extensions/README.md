# extensions (node-private)

Use this folder for **node-specific callable overrides**.

Most customization should go in `config/crunch_config.py`
by overriding fields on the `CrunchConfig`.

This folder is for edge cases where you need additional Python
modules available to the runtime (e.g., custom feed providers,
specialized scoring helpers).
