import os
from dotenv import load_dotenv

load_dotenv()

print("PGHOST =", os.getenv("PGHOST"))
print("PGDATABASE =", os.getenv("PGDATABASE"))
print("PGUSER =", os.getenv("PGUSER"))

