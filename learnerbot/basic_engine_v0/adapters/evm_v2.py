from __future__ import annotations

import time
from decimal import Decimal, ROUND_CEILING
from typing import Any

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

from ..core import Candidate, ExecutionResult, Quote, SimulationResult
from ..csv_config import BasicEngineCsvError, EvmV2DryRunSettings


V2_ROUTER_ABI = [
    {
        "type": "function",
        "name": "getAmountsOut",
        "stateMutability": "view",
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"name": "path", "type": "address[]"},
        ],
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
    },
    {
        "type": "function",
        "name": "swapExactTokensForTokens",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"name": "amountOutMin", "type": "uint256"},
            {"name": "path", "type": "address[]"},
            {"name": "to", "type": "address"},
            {"name": "deadline", "type": "uint256"},
        ],
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
    },
]

ERC20_READ_ABI = [
    {
        "type": "function",
        "name": "balanceOf",
        "stateMutability": "view",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "type": "function",
        "name": "allowance",
        "stateMutability": "view",
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "spender", "type": "address"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]

WEI = Decimal(10**18)


class EvmV2DryRunError(RuntimeError):
    pass


def _to_wei(amount: Decimal) -> int:
    if amount <= 0:
        raise EvmV2DryRunError("amount must be positive")
    return int((amount * WEI).to_integral_value(rounding=ROUND_CEILING))


def _from_wei(amount: int) -> Decimal:
    return Decimal(int(amount)) / WEI


class EvmV2ReadOnlyAdapter:
    """Read-only EVM V2 quote + eth_call adapter.

    This class has no private-key parameter and no send_raw_transaction path.
    It can use a public address for balance/allowance checks and `eth_call`.
    """

    def __init__(self, settings: EvmV2DryRunSettings, *, web3: Any | None = None) -> None:
        if not settings.enabled:
            raise BasicEngineCsvError(
                f"basic engine v0 disabled in basic_engine_v0_settings.csv for {settings.chain_slug}"
            )
        self.settings = settings
        self.w3 = web3 or Web3(
            Web3.HTTPProvider(settings.rpc_url, request_kwargs={"timeout": 15})
        )
        if web3 is None and settings.chain_id in {56, 137}:
            self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        self.router_address = Web3.to_checksum_address(settings.router_address)
        self.wrapped = Web3.to_checksum_address(settings.wrapped_base_address)
        self.router = self.w3.eth.contract(address=self.router_address, abi=V2_ROUTER_ABI)
        self.wrapped_token = self.w3.eth.contract(address=self.wrapped, abi=ERC20_READ_ABI)
        self._validated = False

    def _validate_rpc(self) -> None:
        if self._validated:
            return
        if not self.w3.is_connected():
            raise EvmV2DryRunError("RPC_UNAVAILABLE")
        rpc_chain = int(self.w3.eth.chain_id)
        if rpc_chain != self.settings.chain_id:
            raise EvmV2DryRunError(
                f"RPC_CHAIN_MISMATCH:{rpc_chain}!={self.settings.chain_id}"
            )
        if not self.w3.eth.get_code(self.router_address):
            raise EvmV2DryRunError("V2_ROUTER_HAS_NO_CODE")
        self._validated = True

    def _candidate_path(self, candidate: Candidate) -> list[str]:
        if candidate.strategy != "atomic_arbitrage":
            raise EvmV2DryRunError("WRONG_STRATEGY")
        if candidate.chain != self.settings.chain_slug:
            raise EvmV2DryRunError("WRONG_CHAIN")
        raw = tuple(candidate.payload.get("path") or ())
        if len(raw) < 3:
            raise EvmV2DryRunError("INVALID_PATH")
        try:
            path = [Web3.to_checksum_address(str(item)) for item in raw]
        except Exception as exc:
            raise EvmV2DryRunError("INVALID_TOKEN_ADDRESS") from exc
        if path[0] != self.wrapped or path[-1] != self.wrapped:
            raise EvmV2DryRunError("ROUTE_NOT_WRAPPED_NATIVE_ROUND_TRIP")
        return path

    def _candidate_amount_raw(self, candidate: Candidate) -> int:
        try:
            amount = Decimal(str(candidate.payload.get("input_value")))
        except Exception as exc:
            raise EvmV2DryRunError("INVALID_INPUT_VALUE") from exc
        return _to_wei(amount)

    def _reference_price_impact_bps(self, amount_raw: int, path: list[str], full_out: int) -> int:
        probe_in = max(1, amount_raw // self.settings.reference_probe_divisor)
        probe = self.router.functions.getAmountsOut(probe_in, path).call()
        if not probe or int(probe[-1]) <= 0:
            return 10_000
        full_rate = Decimal(int(full_out)) / Decimal(amount_raw)
        probe_rate = Decimal(int(probe[-1])) / Decimal(probe_in)
        if probe_rate <= 0 or full_rate >= probe_rate:
            return 0
        impact = ((probe_rate - full_rate) / probe_rate) * Decimal(10_000)
        return min(10_000, max(0, int(impact)))

    def _public_sender(self) -> str | None:
        if not self.settings.simulation_from:
            return None
        try:
            return Web3.to_checksum_address(self.settings.simulation_from)
        except Exception as exc:
            raise EvmV2DryRunError("INVALID_SIMULATION_FROM") from exc

    def _preliminary_min_out(self, amount_raw: int) -> int:
        floor = (
            _from_wei(amount_raw)
            + self.settings.min_net_profit_native
            + self.settings.safety_buffer_native
        )
        return _to_wei(floor)

    def _estimate_gas_units(self, amount_raw: int, path: list[str]) -> tuple[int, bool]:
        sender = self._public_sender()
        if sender is None:
            return self.settings.fallback_gas_units, True
        deadline = int(time.time()) + self.settings.deadline_seconds
        fn = self.router.functions.swapExactTokensForTokens(
            amount_raw,
            self._preliminary_min_out(amount_raw),
            path,
            sender,
            deadline,
        )
        try:
            estimate = int(fn.estimate_gas({"from": sender}))
        except Exception:
            return self.settings.fallback_gas_units, True
        adjusted = (
            estimate * self.settings.gas_limit_multiplier_bps + 9_999
        ) // 10_000
        return max(estimate, adjusted), False

    def quote(self, candidate: Candidate) -> Quote:
        self._validate_rpc()
        path = self._candidate_path(candidate)
        amount_raw = self._candidate_amount_raw(candidate)
        amounts = self.router.functions.getAmountsOut(amount_raw, path).call()
        if not amounts or len(amounts) != len(path) or int(amounts[-1]) <= 0:
            return Quote(
                candidate_id=candidate.candidate_id,
                executable=False,
                input_value=_from_wei(amount_raw),
                expected_output_value=Decimal("0"),
                route_id=str(candidate.payload.get("route_id") or ""),
                metadata={"reason": "NO_V2_QUOTE"},
            )

        output_raw = int(amounts[-1])
        impact_bps = self._reference_price_impact_bps(amount_raw, path, output_raw)
        gas_units, gas_fallback = self._estimate_gas_units(amount_raw, path)
        gas_price = int(self.w3.eth.gas_price)
        gas_cost = _from_wei(gas_units * gas_price)

        return Quote(
            candidate_id=candidate.candidate_id,
            executable=True,
            input_value=_from_wei(amount_raw),
            expected_output_value=_from_wei(output_raw),
            estimated_fees=gas_cost,
            price_impact_bps=impact_bps,
            route_id=str(candidate.payload.get("route_id") or ""),
            metadata={
                "adapter": "evm_v2_read_only",
                "router": self.router_address,
                "path": tuple(path),
                "amounts_raw": tuple(int(v) for v in amounts),
                "gas_units": gas_units,
                "gas_price_wei": gas_price,
                "gas_estimate_fallback": gas_fallback,
                "broadcast": False,
            },
        )

    def simulate(self, candidate: Candidate, quote: Quote) -> SimulationResult:
        self._validate_rpc()
        sender = self._public_sender()
        if sender is None:
            return SimulationResult(
                ok=False,
                expected_profit=quote.expected_profit,
                reason="MISSING_SIMULATION_FROM",
                transaction_preview={"broadcast": False},
            )

        path = self._candidate_path(candidate)
        amount_raw = self._candidate_amount_raw(candidate)
        balance = int(self.wrapped_token.functions.balanceOf(sender).call())
        if balance < amount_raw:
            return SimulationResult(
                ok=False,
                expected_profit=quote.expected_profit,
                reason="INSUFFICIENT_WRAPPED_BALANCE",
                transaction_preview={"from": sender, "broadcast": False},
            )
        allowance = int(
            self.wrapped_token.functions.allowance(sender, self.router_address).call()
        )
        if allowance < amount_raw:
            return SimulationResult(
                ok=False,
                expected_profit=quote.expected_profit,
                reason="INSUFFICIENT_ROUTER_ALLOWANCE",
                transaction_preview={"from": sender, "broadcast": False},
            )

        gas_cost = quote.estimated_fees
        minimum_output = (
            _from_wei(amount_raw)
            + gas_cost
            + self.settings.min_net_profit_native
            + self.settings.safety_buffer_native
        )
        amount_out_min_raw = _to_wei(minimum_output)
        deadline = int(time.time()) + self.settings.deadline_seconds
        fn = self.router.functions.swapExactTokensForTokens(
            amount_raw,
            amount_out_min_raw,
            path,
            sender,
            deadline,
        )
        preview = {
            "from": sender,
            "to": self.router_address,
            "value": 0,
            "path": tuple(path),
            "amount_in_raw": amount_raw,
            "amount_out_min_raw": amount_out_min_raw,
            "deadline": deadline,
            "gas_units": quote.metadata.get("gas_units"),
            "gas_price_wei": quote.metadata.get("gas_price_wei"),
            "broadcast": False,
        }
        try:
            result = fn.call({"from": sender})
        except Exception as exc:
            return SimulationResult(
                ok=False,
                expected_profit=quote.expected_profit,
                reason=f"ETH_CALL_REVERT:{type(exc).__name__}",
                transaction_preview=preview,
            )

        return SimulationResult(
            ok=True,
            expected_profit=quote.expected_profit,
            reason="ETH_CALL_OK",
            transaction_preview={**preview, "call_result": result},
        )


class NoBroadcastExecutor:
    """Sentinel executor for v0. It can never submit a transaction."""

    def execute(
        self,
        candidate: Candidate,
        quote: Quote,
        simulation: SimulationResult,
    ) -> ExecutionResult:
        return ExecutionResult(
            submitted=False,
            reason="V0_DRY_RUN_ONLY_NO_BROADCAST",
            metadata={"broadcast": False},
        )
