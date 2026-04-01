from pymilvus import connections, db, Collection, CollectionSchema, FieldSchema, DataType, utility

# -------------------------------
# 1. Connect to Milvus
# -------------------------------
connections.connect(
    alias="default",
    host="localhost",
    port="19530"
)
print("✅ Connected!")

# -------------------------------
# 2. Create Database
# -------------------------------
db_name = "securecarebot_db"

existing_dbs = db.list_database()

if db_name not in existing_dbs:
    db.create_database(db_name)
    print(f"✅ Database '{db_name}' created")

# Switch to DB
db.using_database(db_name)
print(f"✅ Using database: {db_name}")



# Load collection script
# python3 - <<EOF
# from pymilvus import MilvusClient
#
# client = MilvusClient(uri="http://localhost:19530", db_name="securecarebot_db")
#
# collection = "meddataollama"
#
# print("Loading collection...")
# client.load_collection(collection)
#
# print("Done")
# EOF