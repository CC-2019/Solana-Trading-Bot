import asyncio
import datetime
import time

from jito_searcher_client import get_async_searcher_client
from jito_searcher_client.convert import tx_to_protobuf_packet, versioned_tx_to_protobuf_packet
from jito_searcher_client.generated.bundle_pb2 import Bundle
from jito_searcher_client.generated.searcher_pb2 import (

    NextScheduledLeaderRequest,
    NextScheduledLeaderResponse,
    SendBundleRequest,

)
from solana.rpc.types import TokenAccountOpts
from solders.pubkey import Pubkey
from solana.rpc.commitment import  Confirmed
from solana.rpc.api import RPCException
from solana.rpc.api import Client
from solders.keypair import Keypair
from solders.compute_budget import set_compute_unit_price, set_compute_unit_limit
from utils.create_close_account import fetch_pool_keys, get_token_account, make_swap_instruction
from utils.birdeye import getSymbol
from solana.rpc.async_api import AsyncClient
from solders.transaction import VersionedTransaction
from solders.message import MessageV0
from solders.system_program import transfer, TransferParams

from utils.pool_information import gen_pool, getpoolIdByMint
import os
from dotenv import load_dotenv

# Load.env file
load_dotenv()

# config = dotenv_values(".env")

RPC_HTTPS_URL = os.getenv("RPC_HTTPS_URL")
solana_client = Client(os.getenv("RPC_HTTPS_URL"))
async_solana_client = AsyncClient(os.getenv("RPC_HTTPS_URL"))
payer = Keypair.from_base58_string(os.getenv("PrivateKey"))
Wsol_TokenAccount = os.getenv('WSOL_TokenAccount')

AMM_PROGRAM_ID = Pubkey.from_string('675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8')
SERUM_PROGRAM_ID = Pubkey.from_string('srmqPvymJeFKQ4zGQed1GFppgkRHL9kaELCbyksJtPX')
LAMPORTS_PER_SOL = 1000000000
MAX_RETRIES = 2
RETRY_DELAY = 3


class style():
    BLACK = '\033[30m'
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    WHITE = '\033[37m'
    UNDERLINE = '\033[4m'
    RESET = '\033[0m'


def getTimestamp():
    while True:
        timeStampData = datetime.datetime.now()
        currentTimeStamp = "[" + timeStampData.strftime("%H:%M:%S.%f")[:-3] + "]"
        return currentTimeStamp


async def get_specific_token_account(owner_pubkey: str, mint_pubkey: str):
    async with AsyncClient(RPC_HTTPS_URL) as client:
        owner_pubkey_obj = Pubkey.from_string(owner_pubkey)
        mint_pubkey_obj = Pubkey.from_string(mint_pubkey)
        # Using get_token_accounts_by_owner to fetch token accounts
        opts = TokenAccountOpts(mint=mint_pubkey_obj)
        response = await client.get_token_accounts_by_owner(owner_pubkey_obj, opts)
        if len(response.value) == 1:
            return response.value[0].pubkey  # Return the first account found
    return None


async def sell(solana_client, TOKEN_TO_SWAP_SELL, payer):
    retry_count = 0
    while retry_count < MAX_RETRIES:
        try:
            token_symbol, SOl_Symbol = getSymbol(TOKEN_TO_SWAP_SELL)
            mint = Pubkey.from_string(TOKEN_TO_SWAP_SELL)
            pool_keys=None

            # mint= TOKEN_TO_SWAP_SELL
            sol = Pubkey.from_string("So11111111111111111111111111111111111111112")
            TOKEN_PROGRAM_ID = solana_client.get_account_info_json_parsed(mint).value.owner

            try:

                tokenPool_ID = await getpoolIdByMint(mint, AsyncClient(RPC_HTTPS_URL, commitment=Confirmed))

                if tokenPool_ID is not False:

                    fetch_pool_key = await gen_pool(str(tokenPool_ID), AsyncClient(RPC_HTTPS_URL, commitment=Confirmed))
                    pool_keys = fetch_pool_key
                    # print(pool_keys)
                else:
                    print("AMMID NOT FOUND SEARCHING WILL BE FETCHING WITH RAYDIUM SDK.. THis happens")

                    # pool_keys= fetch_pool_keys(str(mint))
            except Exception as e:
                print(e)




            opts = TokenAccountOpts(mint=mint)
            response = await async_solana_client.get_token_accounts_by_owner(payer.pubkey(), opts)
            tokenAccount = response.value[0].pubkey
            print(tokenAccount)
            balance = await async_solana_client.get_token_account_balance(tokenAccount)

            amount_in = int(balance.value.amount)
            print("Token Balance : ", amount_in)

            if int(amount_in) == 0:
                return "NO BALANCE"


            WSOL_token_account, WSOL_token_account_Instructions = get_token_account(solana_client, payer.pubkey(), sol)

            print("3. Create Swap Instructions...")
            instructions_swap = make_swap_instruction(amount_in,
                                                      tokenAccount,
                                                      WSOL_token_account,
                                                      pool_keys,
                                                      mint,
                                                      solana_client,
                                                      payer
                                                      )

            swap_tx = []
            if WSOL_token_account_Instructions != None:
                swap_tx.append(WSOL_token_account_Instructions)
            swap_tx.extend([instructions_swap,set_compute_unit_price(25_500),set_compute_unit_limit(200_337)])

            ###SENDING THROUGH JITO
            print("Sending Through Jito")

            jito_payer = Keypair.from_base58_string(os.getenv("JITO_PRIVATE_KEY"))
            BLOCK_ENGINE_URL = "frankfurt.mainnet.block-engine.jito.wtf"
            jito_client = await get_async_searcher_client(BLOCK_ENGINE_URL, jito_payer) #Update this line to avoid rate limit due to private key of jito Auth been made available publicly here.
            txs = []
            tip_account_pubkey = Pubkey.from_string(os.getenv("TIP_ACCOUNT_PUBKEY"))

            print("Converting Transactions")
            # jito_client = get_searcher_client(BLOCK_ENGINE_URL, payer)

            is_leader_slot = False
            print("waiting for jito leader...")
            while not is_leader_slot:
                time.sleep(0.5)
                next_leader: NextScheduledLeaderResponse = await jito_client.GetNextScheduledLeader(
                    NextScheduledLeaderRequest())
                num_slots_to_leader = next_leader.next_leader_slot - next_leader.current_slot
                print(f"waiting {num_slots_to_leader} slots to jito leader")
                is_leader_slot = num_slots_to_leader <= 5

            ix = transfer(
                TransferParams(
                    from_pubkey=payer.pubkey(), to_pubkey=tip_account_pubkey,
                    lamports=int(0.00020002 * LAMPORTS_PER_SOL)  # TIP AMOUNT
                )
            )
            swap_tx.append(ix)

            msg = MessageV0.try_compile(
                payer.pubkey(),
                swap_tx,
                [],
                solana_client.get_latest_blockhash().value.blockhash,
            )


            tx1 = VersionedTransaction(msg, [payer])

            txs.append(tx1)

            packets = [versioned_tx_to_protobuf_packet(tx) for tx in txs]
            uuid_response = await jito_client.SendBundle(SendBundleRequest(bundle=Bundle(header=None, packets=packets)))

            print(f"bundle uuid: {uuid_response.uuid}")
            block_height = solana_client.get_block_height(Confirmed).value
            print(f"Block height: {block_height}")

            for tx in txs:

                confirmation_resp = await async_solana_client.confirm_transaction(
                    tx.signatures[0],
                    commitment=Confirmed,
                    sleep_seconds=0.5,
                    last_valid_block_height=block_height + 15

                )

                if confirmation_resp.value[0].err == None and str(
                        confirmation_resp.value[0].confirmation_status) == "TransactionConfirmationStatus.Confirmed":
                    print(style.GREEN + "Transaction Confirmed", style.RESET)
                    print(style.GREEN, f"Transaction Signature: https://solscan.io/tx/{tx.signatures[0]}", style.RESET)

                    return tx.signatures[0]

            return True


        except asyncio.TimeoutError:
            print("Transaction confirmation timed out. Retrying...")
            retry_count += 1
            time.sleep(RETRY_DELAY)
        except RPCException as e:
            print(f"RPC Error: [{e.args[0].message}]... Retrying...")
            retry_count += 1
            time.sleep(RETRY_DELAY)
        except Exception as e:
            print(f"Unhandled exception: {e}. Retrying...")
            retry_count += 1
            time.sleep(RETRY_DELAY)

        print("Failed to confirm transaction after maximum retries.")
        return False


async def main():
    token_toBuy = "RUpbmGF6p42AAeN1QvhFReZejQry1cLkE1PUYFVVpnL"
    print(payer.pubkey())
    await sell(solana_client, token_toBuy, payer)


asyncio.run(main())