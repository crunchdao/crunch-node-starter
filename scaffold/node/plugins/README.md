# plugins (node-private)

Use this folder for **node-side integrations** that should not live in the public challenge package.

## Put code here when

- you call private/external APIs (keys/secrets live in node env)
- you need infrastructure-specific data shaping
- logic is operational, not challenge-contract logic

## Typical use cases

- Custom feed providers beyond the built-in Binance provider
- External API integrations for data enrichment
- Infrastructure-specific adapters

Most scoring and ground-truth customization belongs in
`config/crunch_config.py` on the `CrunchConfig`.
