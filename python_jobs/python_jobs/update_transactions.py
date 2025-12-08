import os
from dotenv import load_dotenv, find_dotenv
from datetime import datetime

import plaid
from plaid.api import plaid_api
from plaid.model.transactions_sync_request import TransactionsSyncRequest
from plaid.model.item_get_request import ItemGetRequest
from plaid.model.institutions_get_by_id_request import InstitutionsGetByIdRequest
from plaid.model.country_code import CountryCode

from lib.postgres import PostgresManager

load_dotenv(find_dotenv())

PLAID_ENV = os.environ['PLAID_ENV']
PLAID_CLIENT_ID = os.environ['PLAID_CLIENT_ID']
PLAID_SECRET = os.environ['PLAID_SECRET']


if PLAID_ENV == 'sandbox':
    host = plaid.Environment.Sandbox

if PLAID_ENV == 'development':
    host = plaid.Environment.Development

if PLAID_ENV == 'production':
    host = plaid.Environment.Production


configuration = plaid.Configuration(
    host=host,
    api_key={
        'clientId': PLAID_CLIENT_ID,
        'secret': PLAID_SECRET,
        'plaidVersion': '2020-09-14'
    }
)


def get_item_institution_id(client, access_token):
    request = ItemGetRequest(access_token=access_token)
    response = client.item_get(request)
    institution_id = response['item']['institution_id']
    return institution_id


def get_institution_name(client, access_token):
    institution_id = get_item_institution_id(client, access_token)
    request = InstitutionsGetByIdRequest(
        institution_id=institution_id,
        country_codes=[CountryCode('US')]
    )
    response = client.institutions_get_by_id(request)
    return response['institution']['name']


def create_account_if_not_exist(db, account):
    query = f"""
                SELECT id
                FROM accounts
                WHERE "account_id" = '{account['account_id']}'
            """
    results = db.select(query)
    if results:
        return results[0]['id']
    else:
        db.insert('accounts', [account])
        return create_account_if_not_exist(db, account)


# def create_account_balance(db, accountId, balances):
    # balances['accountId'] = accountId
    # balances['createdAt'] = datetime.now()
    # balances['updatedAt'] = datetime.now()
    # keep_fields = ['available', 'current', 'iso_currency_code',
                   # 'limit', 'unofficial_currency_code',
                   # 'createdAt', 'updatedAt', 'accountId']
    # balances = {k: v for k, v in balances.items()
                # if k in keep_fields}
    # db.insert('account_balances', [balances])


def make_clean_transaction(t, account_id_map):
    # Truncate the name to 255 characters
    t['name'] = t['name'][0:255]

    date = t['authorized_datetime']
    if not t['authorized_datetime']:
        date = t['authorized_date']
    if not t['authorized_date']:
        date = t['datetime']
    if not t['datetime']:
        date = t['date']

    clean_transaction = {
            "amount": t['amount'],
            "date": date,
            "name": t['name'],
            "merchant_name": t['merchant_name'],
            "createdAt": datetime.now(),
            "updatedAt": datetime.now(),
            "accountId": account_id_map[t['account_id']],
            "primary_category": t['personal_finance_category']['primary'],
            "detailed_category": t['personal_finance_category']['detailed'],
            "transaction_id": t['transaction_id']
            }
    return clean_transaction


def get_transactions(db, client, access_token, 
                     itemId, has_more, cursor):
    request = TransactionsSyncRequest(
            access_token=access_token,
            cursor=cursor,
            )
    response = client.transactions_sync(request).to_dict()
    removed = response['removed']
    transactions = response['added']
    has_more = response['has_more']
    cursor = response['next_cursor']

    # Get account_id to accountId mapping
    account_id_map = {}
    keep_fields = ['account_id', 'name', 'official_name', 'type']
    for a in response['accounts']:
        a = {k: v for k, v in a.items()
             if k in keep_fields}
        a["createdAt"] = datetime.now()
        a["updatedAt"] = datetime.now()
        a['itemId'] = itemId
        if not a['official_name']:
            a['official_name'] = a['name']

        accountId = create_account_if_not_exist(db, a)
        account_id_map[a['account_id']] = accountId

    clean_transactions = []
    pending_transactions = []
    for t in transactions:
        clean_transaction = make_clean_transaction(t, account_id_map)

        if t['pending']:
            pending_transactions.append(clean_transaction)
        else:
            clean_transactions.append(clean_transaction)

    removed = [{
        "transaction_id": x['transaction_id'],
        "accountId": account_id_map[x['account_id']]
    } for x in removed]

    return clean_transactions, pending_transactions, removed, has_more, cursor 


def set_db_cursor(db, cursorId, cursor):
    db.update(f"""
              UPDATE transaction_cursors
              SET transaction_cursor = '{cursor}'
              WHERE id = '{cursorId}'
              """)


def update_transactions(db, client, access_token,
                        transactionCursor, cursorId, itemId):
    keep_fields = ['amount', 'date',
                   'name', 'merchant_name',
                   'createdAt', 'updatedAt',
                   'accountId', 'primary_category',
                   'detailed_category', 'transaction_id']

    has_more = True
    cursor = transactionCursor
    while has_more:
        transactions, pending_transactions, removed, has_more, cursor = get_transactions(
                db, client, access_token, itemId,
                has_more, cursor)
        transactions = [{k: v for k, v in x.items()
                         if k in keep_fields}
                        for x in transactions]
        if transactions:
            write_transactions_to_db(db, transactions)
        else:
            print("No transactions to write")

        remove_accountsIds = [x['accountId'] for x in removed]
        if removed:
            for accountId in set(remove_accountsIds):
                loop_transaction_ids = [x['transaction_id']
                                        for x in removed
                                        if x['accountId'] == accountId]

                sql = f"""
                    DELETE FROM pending_transactions
                    WHERE "transaction_id" IN ('{
                        "', '".join(loop_transaction_ids)}')
                    AND "accountId" = {accountId}
                """
                db.execute(sql)

        pending_transactions = [{k: v for k, v in x.items()
                                if k in keep_fields}
                                for x in pending_transactions]
        if pending_transactions:
            write_pending_transactions_to_db(db, pending_transactions)
        else:
            print("No pending transactions to write")

    set_db_cursor(db, cursorId, cursor)


def write_transactions_to_db(db, transactions):
    # do not insert transactions that already exist
    transaction_ids = [x['transaction_id'] for x in transactions]
    query = f"""
        SELECT transaction_id
        FROM transactions
        WHERE transaction_id IN ('{ "', '".join(transaction_ids)}')
    """
    existing_transactions = db.select(query)
    existing_transaction_ids = [x['transaction_id']
                                for x in existing_transactions]
    transactions = [x for x in transactions
                    if x['transaction_id'] not in existing_transaction_ids]
    db.insert('transactions', transactions)


def write_pending_transactions_to_db(db, pending_transactions):
    # do not insert transactions that already exist
    transaction_ids = [x['transaction_id'] for x in pending_transactions]
    query = f"""
        SELECT transaction_id
        FROM pending_transactions
        WHERE transaction_id IN ('{ "', '".join(transaction_ids)}')
    """
    existing_transactions = db.select(query)
    existing_transaction_ids = [x['transaction_id']
                                for x in existing_transactions]
    pending_transactions = [x for x in pending_transactions
                            if x['transaction_id'] not in
                            existing_transaction_ids]
    db.insert('pending_transactions', pending_transactions)


def get_user_access_tokens(db, user_id):
    query = f"""
                SELECT access_token, c.transaction_cursor,
                c.id as cursorId,
                i.id
                FROM access_tokens a
                JOIN items i
                  ON a.id = i."accessTokenId"
                JOIN transaction_cursors c
                  ON a.id = c."accessTokenId"
                WHERE "userId" = {user_id}
            """
    results = db.select(query)
    return results


def get_all_users(db):
    query = """SELECT "firstName", "id", "telegramChatId" from users"""
    users = db.select(query)
    return users


def update_all_transactions(db, client, user_id):
    items = get_user_access_tokens(db, user_id)
    error_happened = False
    for token, transactionCursor, cursorId, itemId in items:
        try:
            update_transactions(db, client, token,
                                transactionCursor, cursorId, itemId)
        except Exception as e:
            print(f"Error: {e}")
            error_happened = True
    return error_happened


def attempt_update_all_transactions(db, client, user):
    user_id = user['id']
    error_happened = update_all_transactions(db, client, user_id)
    return error_happened


def main():
    db = PostgresManager(os.environ['DATABASE_URL'])
    api_client = plaid.ApiClient(configuration)
    client = plaid_api.PlaidApi(api_client)
    users = get_all_users(db)
    any_errors = False
    user_ids_with_errors = []
    for user in users:
        error = attempt_update_all_transactions(
                db, client, user)
        if error:
            any_errors = True
            user_ids_with_errors.append(user['id'])
    if any_errors:
        raise Exception("There were errors updating user transactions"
                        " for users with ids:"
                        f" {', '.join(map(str, user_ids_with_errors))}")

    db.close()


if __name__ == '__main__':
    main()
