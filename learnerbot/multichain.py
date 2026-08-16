from __future__ import annotations

from dataclasses import dataclass
from .config import AppSettings, ChainConfig, ChainSettings, load_chains
from .db import connect
from .rpc import RPCClient

@dataclass
class ChainContext:
    config: ChainConfig
    settings: ChainSettings
    conn: object
    rpc: RPCClient|None

def contexts(app:AppSettings, enabled_only=True, with_rpc=False):
    out=[]
    for chain in load_chains(app,enabled_only=enabled_only):
        settings=ChainSettings.from_app_chain(app,chain)
        conn=connect(settings.db_path)
        rpc=None
        if with_rpc:
            if chain.type!="EVM":
                rpc=None
            elif chain.rpc_urls:
                rpc=RPCClient(chain.rpc_urls,settings.rpc_timeout_seconds,settings.rpc_delay_ms)
        out.append(ChainContext(chain,settings,conn,rpc))
    return out

def by_slug(app, slug, with_rpc=False):
    for ctx in contexts(app,enabled_only=False,with_rpc=with_rpc):
        if ctx.config.slug==slug: return ctx
    raise KeyError(slug)


def close_contexts(items):
    for ctx in items:
        try:
            ctx.conn.close()
        except Exception:
            pass
