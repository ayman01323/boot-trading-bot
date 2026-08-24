from decimal import Decimal

from learnerbot.basic_engine_v0.adapters.evm_v2 import (
    EvmV2ReadOnlyAdapter,
    NoBroadcastExecutor,
)
from learnerbot.basic_engine_v0.core import ExecutionResult
from learnerbot.basic_engine_v0.csv_config import EvmV2DryRunSettings
from learnerbot.basic_engine_v0.strategies import AtomicArbitrageRoute


WRAPPED = "0x4200000000000000000000000000000000000006"
TOKEN_A = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
TOKEN_B = "0x3055913c90Fcc1A6CE9a358911721eEb942013A1"
ROUTER = "0x8cFe327CEc66d1C090Dd72bd0FF11d690C33a2Eb"
SENDER = "0x1111111111111111111111111111111111111111"


class _Call:
    def __init__(self, value):
        self.value = value

    def call(self, *_args, **_kwargs):
        return self.value


class _SwapCall:
    def __init__(self, result):
        self.result = result

    def estimate_gas(self, *_args, **_kwargs):
        return 200_000

    def call(self, *_args, **_kwargs):
        return self.result


class _RouterFunctions:
    def getAmountsOut(self, amount_in, path):
        # Full-size cycle returns +2%; probe returns +3%, producing non-zero
        # size impact while remaining profitable.
        multiplier = Decimal("1.02") if amount_in >= 10**17 else Decimal("1.03")
        out = int(Decimal(amount_in) * multiplier)
        mids = [amount_in]
        for _ in path[1:-1]:
            mids.append(amount_in)
        mids.append(out)
        return _Call(mids)

    def swapExactTokensForTokens(self, amount_in, amount_out_min, path, to, deadline):
        return _SwapCall([amount_in, amount_in, amount_in, amount_out_min + 1])


class _TokenFunctions:
    def balanceOf(self, _sender):
        return _Call(10**21)

    def allowance(self, _sender, _router):
        return _Call(10**21)


class _Router:
    functions = _RouterFunctions()


class _Token:
    functions = _TokenFunctions()


class _Eth:
    chain_id = 8453
    gas_price = 1_000_000_000

    def get_code(self, _address):
        return b"code"

    def contract(self, address, abi):
        if str(address).lower() == ROUTER.lower():
            return _Router()
        return _Token()


class _FakeWeb3:
    eth = _Eth()

    def is_connected(self):
        return True


def _settings(simulation_from=SENDER):
    return EvmV2DryRunSettings(
        chain_id=8453,
        chain_slug="base",
        rpc_url="https://unused.example",
        wrapped_base_address=WRAPPED,
        router_address=ROUTER,
        simulation_from=simulation_from,
        enabled=True,
        input_amount_native=Decimal("1"),
        min_net_profit_native=Decimal("0.001"),
        safety_buffer_native=Decimal("0.001"),
        max_price_impact_bps=500,
        gas_limit_multiplier_bps=13000,
        fallback_gas_units=350000,
        deadline_seconds=120,
        reference_probe_divisor=1000,
    )


def _candidate():
    return AtomicArbitrageRoute(
        route_id="base-test",
        chain="base",
        path=(WRAPPED, TOKEN_A, TOKEN_B, WRAPPED),
        input_value=Decimal("1"),
    ).to_candidate()


def test_read_only_adapter_quotes_gas_and_eth_call_without_broadcast():
    adapter = EvmV2ReadOnlyAdapter(_settings(), web3=_FakeWeb3())
    quote = adapter.quote(_candidate())
    assert quote.executable is True
    assert quote.expected_output_value == Decimal("1.02")
    assert quote.metadata["gas_units"] == 260000
    assert quote.metadata["gas_estimate_fallback"] is False
    assert quote.metadata["broadcast"] is False
    assert quote.estimated_fees == Decimal("0.00026")
    assert 0 < quote.price_impact_bps <= 500

    simulation = adapter.simulate(_candidate(), quote)
    assert simulation.ok is True
    assert simulation.reason == "ETH_CALL_OK"
    assert simulation.transaction_preview["broadcast"] is False
    assert simulation.transaction_preview["value"] == 0


def test_missing_public_simulation_address_never_claims_ready_simulation():
    adapter = EvmV2ReadOnlyAdapter(_settings(simulation_from=None), web3=_FakeWeb3())
    quote = adapter.quote(_candidate())
    assert quote.executable is True
    assert quote.metadata["gas_estimate_fallback"] is True

    simulation = adapter.simulate(_candidate(), quote)
    assert simulation.ok is False
    assert simulation.reason == "MISSING_SIMULATION_FROM"
    assert simulation.transaction_preview["broadcast"] is False


def test_no_broadcast_executor_is_hard_sentinel():
    result = NoBroadcastExecutor().execute(_candidate(), None, None)
    assert isinstance(result, ExecutionResult)
    assert result.submitted is False
    assert result.reason == "V0_DRY_RUN_ONLY_NO_BROADCAST"
    assert result.metadata["broadcast"] is False
