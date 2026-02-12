import gspread
import pandas as pd
import json
import os

SHEET_NAME = 'AC_Refrigerant_DB'
CREDENTIALS_FILE = '/mnt/c/pyy/credentials.json'

def get_gspread_client():
    return gspread.service_account(filename=CREDENTIALS_FILE)

client = get_gspread_client()
spreadsheet = client.open(SHEET_NAME)
sheet = spreadsheet.sheet1
data = sheet.get_all_records()
if data:
    df = pd.DataFrame(data)
    print("Columns in Google Sheet:", df.columns.tolist())
    print("First row:", df.iloc[0].to_dict())
    print("Total rows:", len(df))
else:
    print("Sheet is empty or no data found.")
