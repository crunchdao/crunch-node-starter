# To-Do

## crunch_node (engine package)

- [ ] **config_loader.py:_resolve_config() — raise on broken operator config instead of falling back**

  When `found=True` but `config is None`, the operator's config exists but is broken. This should raise an error, not fall back to engine defaults. The current behavior silently seeds wrong defaults (caused the BTCUSDT-60 bug).

  Current (bad): logs a warning, then falls back to `CrunchConfig()` with engine defaults.

  Fix: raise `RuntimeError` when `found=True` and config failed to instantiate. The fallback to engine defaults should only happen when `found=False` (no operator config exists at all, e.g. a vanilla scaffold).

  ```python
  # Should be:
  if found:
      raise RuntimeError(
          "Operator config at config.crunch_config failed to instantiate. "
          "Fix the validation errors above. Refusing to fall back to engine defaults."
      )
  ```
