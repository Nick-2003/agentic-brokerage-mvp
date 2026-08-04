# SnapTrade contract fixtures

These are synthetic, sanitised examples shaped to SnapTrade's documented current
responses for account details, balances, and unified positions. They are not a
copy of any user's brokerage account.

All UUIDs use the reserved `00000000-...` fixture namespace, account numbers are
masked, descriptions are synthetic, and the files contain no client ID, consumer
key, user ID, userSecret, connection URL, real brokerage account ID, or person.

Before replacing or extending these with a response captured from a test account:

1. capture only in an operator-controlled local environment;
2. remove `userId`, `userSecret`, authorization/account/instrument/currency UUIDs,
   institution account IDs, raw account numbers, names, orders, transactions,
   tax lots, and personally identifying descriptions;
3. replace values and timestamps with obviously synthetic examples;
4. run `scripts/test_093_snaptrade_fixtures.py` before committing;
5. inspect the staged diff manually and run repository secret scanning.

Documentation checked on 2026-08-04:

- https://docs.snaptrade.com/reference/Account%20Information/AccountInformation_getUserAccountDetails
- https://docs.snaptrade.com/reference/Account%20Information/AccountInformation_getUserAccountBalance
- https://docs.snaptrade.com/reference/Account%20Information/AccountInformation_getAllAccountPositions
