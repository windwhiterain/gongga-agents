#!/usr/bin/env python3
"""`deepseek-official` balance: the DeepSeek platform's own wallet.

`GET https://api.deepseek.com/user/balance` answers with every wallet the account
holds; the reading is the CNY one (the currency this deployment is billed in), or
the first wallet when there is no CNY wallet. `is_available` false becomes an
`error` note rather than a zero: the number the API reports is still the number it
reports.

https://api-docs.deepseek.com/api/get-user-balance/

Port of `dsh-balance/deepseek.mjs`.
"""

import os
import sys

import contract

PROVIDER = "deepseek-official"
BASE_URL = "https://api.deepseek.com"
ENV_NAME = "DEEPSEEK_API_KEY"


def parse(payload):
    """Read one wallet out of the balance response.

    Raises `BalanceError` when no wallet carries a usable amount.
    """
    wallets = contract.as_list(payload, "balance_infos")
    if not wallets:
        raise contract.BalanceError("the response carries no balance_infos")
    cny = next((wallet for wallet in wallets if contract.field(wallet, "currency") == "CNY"), None)
    wallet = cny if cny is not None else wallets[0]
    stated = contract.raw(wallet, "total_balance")
    remaining = contract.js_number(stated)
    if not contract.is_finite(remaining):
        raise contract.BalanceError(f"total_balance is not a number: {contract.json_text(stated)}")
    currency = contract.field(wallet, "currency")
    detail = f"{contract.num_text(remaining)} {currency if currency is not None else '?'}"
    granted = contract.raw(wallet, "granted_balance")
    if granted is not contract.ABSENT:
        detail += f" (granted {contract.js_string(granted)})"
    topped_up = contract.raw(wallet, "topped_up_balance")
    if topped_up is not contract.ABSENT:
        detail += f", topped up {contract.js_string(topped_up)})"
    reading = {"remaining": remaining, "detail": detail}
    if contract.field(payload, "is_available") is False:
        reading["error"] = "the provider reports the account as unavailable"
    return reading


def read_balance(options, fetch=None):
    """Query the balance endpoint and return the reading."""
    try:
        payload = contract.get_json(f"{options.base_url}/user/balance", options.key, options.timeout_ms, fetch=fetch)
        return {"reading": parse(payload), "payload": payload}
    except contract.BalanceError as error:
        refused = contract.refused_reading(error)
        if refused is None:
            raise
        return {"reading": refused}


def main():
    options = contract.read_options(
        sys.argv[1:], os.environ, ENV_NAME, BASE_URL, PROVIDER, script_name=os.path.basename(__file__))
    if options is None:
        return
    result = read_balance(options)
    contract.emit_reading(result["reading"], raw=options.raw, payload=result.get("payload"))


if __name__ == "__main__":
    sys.exit(contract.run(main))
