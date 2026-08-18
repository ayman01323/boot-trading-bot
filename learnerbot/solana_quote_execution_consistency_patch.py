from __future__ import annotations

import os

from . import solana_sibot as _sol


def jupiter_quote_executable(app, input_mint: str, output_mint: str, amount_raw: int):
    """Quote the same router universe the single-signer LIVE executor can use."""
    if int(amount_raw) <= 0:
        raise ValueError("Jupiter quote amount must be positive")
    cfg = _sol.settings(app)
    url = cfg.get("jupiter_order_url") or _sol.DEFAULT_JUPITER_ORDER
    api_key = os.getenv("JUPITER_API_KEY", "").strip()
    _sol._jupiter_throttle(api_key)
    headers = {"User-Agent": "BOOT-SiBot-Solana/1.2"}
    if api_key:
        headers["x-api-key"] = api_key
    params = {
        "inputMint": str(input_mint),
        "outputMint": str(output_mint),
        "amount": str(int(amount_raw)),
        # The LIVE executor rejects JupiterZ/RFQ because this canary signing path
        # requires exactly one wallet signer. Preflight must evaluate that same
        # executable router universe rather than accepting a route we later refuse.
        "excludeRouters": "jupiterz",
    }
    r = _sol.requests.get(url, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get("error") or data.get("errorCode"):
        raise RuntimeError(
            "Jupiter quote: "
            + str(data.get("error") or data.get("errorMessage") or data.get("errorCode"))[:300]
        )
    out = _sol._int(data.get("outAmount") or data.get("outputAmount"), 0)
    if out <= 0:
        raise RuntimeError("Jupiter quote returned no output amount")
    return data


def install():
    _sol.jupiter_quote = jupiter_quote_executable
    print("[solana-quote-consistency] excludeRouters=jupiterz preflight_matches_live=true")


install()
