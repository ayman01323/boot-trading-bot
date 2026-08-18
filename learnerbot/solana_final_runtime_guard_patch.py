from __future__ import annotations

from . import solana_live_patch as _live
from . import solana_position_wallet_binding_patch as _binding
from . import solana_sibot as _sol


def install():
    """Final startup invariant for Solana LIVE exits.

    All LIVE exit paths must resolve through the wallet-binding/reconciliation
    implementation.  This is intentionally loaded after every other Solana/UI
    patch so a later compatibility layer cannot restore the legacy active-wallet
    exit behaviour.
    """
    _live._close_live = _binding._close_bound_live
    # The monitor function lives in solana_live_patch and performs a dynamic
    # global lookup of _close_live. Re-assert its public binding too so the
    # scheduler cannot retain an older monitor implementation.
    _sol.monitor_positions = _live.monitor_positions
    print(
        "[solana-final-runtime-guard] "
        "close_live=wallet_bound_reconcile monitor=solana_live_patch"
    )


install()
