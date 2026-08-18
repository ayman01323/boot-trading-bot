from __future__ import annotations

import time
from contextlib import closing

from . import sibot as _sibot


def poll_leader_blocks_reliable(app, chain) -> list[dict]:
    leaders = _sibot._leader_set(app, chain.chain_id)
    if not leaders:
        return []
    w3 = _sibot._rpc(chain)
    latest = int(w3.eth.block_number)
    with closing(_sibot.connect(app)) as conn:
        key = f"leader_last_block:{chain.chain_id}"
        last = _sibot._int(_sibot._state(conn, key, 0), 0)
        if last <= 0:
            _sibot._set_state(conn, key, latest)
            return []
        start = last + 1
        if latest - start > 20:
            start = max(start, latest - 20)

    routers = _sibot._routers(app, chain)
    events = []
    processed = last
    for bn in range(start, latest + 1):
        try:
            block = w3.eth.get_block(bn, full_transactions=True)
            ts = int(block.get("timestamp") or time.time())
        except Exception as exc:
            # Stop at the first unavailable block and retry it next pass. Advancing
            # beyond it would permanently lose any leader transaction in that block.
            print(f"[sibot-monitor:{chain.slug}:block]", bn, type(exc).__name__, str(exc)[:180])
            break
        processed = bn
        for tx in block.get("transactions", []):
            frm = str(tx.get("from") or "").lower()
            to = str(tx.get("to") or "").lower()
            if frm not in leaders or (routers and to not in routers) or ts < leaders[frm]:
                continue
            try:
                receipt = w3.eth.get_transaction_receipt(tx.get("hash"))
                if int(receipt.get("status") or 0) != 1:
                    continue
                events.extend(_sibot._record_event(app, chain, frm, tx, receipt, ts, w3=w3))
            except Exception as exc:
                # Receipt/classification uncertainty is not allowed to make the
                # block disappear. Leave the event absent but retain diagnostics.
                print(f"[sibot-monitor:{chain.slug}:tx]", type(exc).__name__, str(exc)[:180])
                continue

    if processed > last:
        with closing(_sibot.connect(app)) as conn:
            _sibot._set_state(conn, f"leader_last_block:{chain.chain_id}", processed)
    for event in events:
        _sibot.process_leader_event(app, event)
    return events


def install():
    _sibot.poll_leader_blocks = poll_leader_blocks_reliable
    print("[sibot-evm-reliability] failed_block_retry=true cursor_no_skip=true")


install()
