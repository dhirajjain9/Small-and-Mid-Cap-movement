# BSE scrip list (optional, manual)

BSE's full list of listed scrips can't be downloaded from GitHub's servers, so companies
listed only on BSE that haven't traded in the past year are missing from the screener
unless you add the list here.

1. On bseindia.com open **Markets → Equity → List of Scrips** (or search "List of Scrips").
2. Set **Segment: Equity** and **Status: Active**. Download as CSV and save it here as
   `bse_scrips_active.csv`.
3. Repeat with **Status: Suspended** and save it as `bse_scrips_suspended.csv`.
4. Commit and push. The daily job picks the files up on its next run (pushing to this
   folder also triggers a run).

Any file named `bse_scrips*.csv` is read. Re-download every month or two; the list
changes slowly. Expected columns (matched loosely): Security Code, Issuer Name,
Security Id, Security Name, Status, Group, Face Value, ISIN No, Industry.
