import os
import shutil
import subprocess
import time
from pathlib import Path

from web3 import Web3
from solcx import compile_source, install_solc

# NO API NEEDED HERE, BUT A LOCAL BLOCKCHAIN IS
# 1. Install Ganache: npm install -g ganache
# 2. The demo starts Ganache automatically if port 8545 is free.
#    On Windows PowerShell, `ganache` may fail (ExecutionPolicy blocks ganache.ps1);
#    this client uses ganache.cmd instead.

GANACHE_URL = "http://127.0.0.1:8545"
CONTRACT_PATH = Path(__file__).resolve().parent / "contract.sol"
_GANACHE_PROCESS: subprocess.Popen | None = None

install_solc("0.8.0")


def _ganache_executable() -> str | None:
    if os.name == "nt":
        # ganache.ps1 is often blocked by PowerShell ExecutionPolicy.
        return shutil.which("ganache.cmd") or shutil.which("npx.cmd")
    return shutil.which("ganache") or shutil.which("npx")


def _start_ganache() -> None:
    global _GANACHE_PROCESS
    executable = _ganache_executable()
    if not executable:
        raise ConnectionError(
            "Ganache not reachable and no ganache.cmd/npx found. "
            "Install with: npm install -g ganache"
        )

    args = [executable]
    name = Path(executable).name.lower()
    if name.startswith("npx"):
        args.extend(["--yes", "ganache"])
    args.extend(["--host", "127.0.0.1", "--port", "8545"])

    kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    print("Ganache not running; starting local chain on port 8545...")
    _GANACHE_PROCESS = subprocess.Popen(args, **kwargs)

    for _ in range(40):
        time.sleep(0.5)
        w3 = Web3(Web3.HTTPProvider(GANACHE_URL))
        if w3.is_connected():
            print("Ganache is ready.")
            return

    raise ConnectionError(
        "Started Ganache but it did not become ready on port 8545. "
        "On Windows, try: ganache.cmd --host 127.0.0.1 --port 8545"
    )


def get_web3() -> Web3:
    w3 = Web3(Web3.HTTPProvider(GANACHE_URL))
    if w3.is_connected():
        return w3
    _start_ganache()
    w3 = Web3(Web3.HTTPProvider(GANACHE_URL))
    if not w3.is_connected():
        raise ConnectionError("Ganache not reachable - is it running on port 8545?")
    return w3


def compile_contract():
    with open(CONTRACT_PATH, encoding="utf-8") as f:
        source = f.read()

    compiled = compile_source(source, output_values=["abi", "bin"], solc_version="0.8.0")
    _, contract_interface = compiled.popitem()
    return contract_interface["abi"], contract_interface["bin"]


def deploy_contract(w3: Web3, account: str):
    abi, bytecode = compile_contract()
    contract = w3.eth.contract(abi=abi, bytecode=bytecode)

    tx_hash = contract.constructor().transact({"from": account})
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    deployed = w3.eth.contract(address=receipt.contractAddress, abi=abi)
    return deployed, receipt.contractAddress


def submit_hash(w3: Web3, contract, account: str, hash_hex: str):
    hash_bytes = bytes.fromhex(hash_hex)
    tx_hash = contract.functions.submitHash(hash_bytes).transact({"from": account})
    return w3.eth.wait_for_transaction_receipt(tx_hash)


def is_verified(contract, hash_hex: str) -> bool:
    hash_bytes = bytes.fromhex(hash_hex)
    return contract.functions.isVerified(hash_bytes).call()
