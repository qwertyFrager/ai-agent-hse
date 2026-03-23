import argparse
import json
import os

from dotenv import load_dotenv

from app.indexer import index_path


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="Index local documents into Postgres")
    parser.add_argument("--path", default=None, help="Path to data directory")
    args = parser.parse_args()

    path = args.path or os.getenv("DATA_DIR", "./docs")
    result = index_path(path)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
